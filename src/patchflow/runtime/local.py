"""只用于受控隔离目录的本地 Runtime。"""  # 说明本实现不是正式不可信代码沙箱。

from __future__ import annotations  # 启用延迟解析类型标注。

import asyncio  # 导入异步子进程和超时控制能力。
import os  # 导入平台判断、环境变量和进程组操作。
import signal  # 导入 Linux 进程组终止信号。
import time  # 导入高精度耗时统计。
from pathlib import Path  # 导入跨平台路径对象。

from patchflow.domain.enums import RepositoryKind  # 导入本地仓库类型标识。
from patchflow.domain.runtime import CommandResult, PatchResult  # 导入稳定的领域结果类型。
from patchflow.domain.task import TaskSpec  # 导入统一任务定义。
from patchflow.runtime.errors import (  # 导入运行时可识别异常。
    DirtyWorkspaceError,  # 导入工作区不干净异常。
    PathViolationError,  # 导入 patch 路径违规异常。
    RuntimeNotStartedError,  # 导入生命周期使用异常。
    WorkspaceSafetyError,  # 导入隔离范围异常。
)  # 结束异常导入列表。
from patchflow.runtime.output import truncate_text  # 导入确定性输出截断逻辑。
from patchflow.runtime.paths import WorkspacePathResolver  # 导入工作区路径解析器。

_ENVIRONMENT_ALLOWLIST = frozenset(  # 定义允许传递给任务进程的宿主环境变量。
    {  # 开始环境变量名称集合。
        "CONDA_DEFAULT_ENV",  # 保留当前 Conda 环境名称。
        "CONDA_PREFIX",  # 保留当前 Conda 环境路径。
        "HOME",  # 保留部分工具定位用户配置所需的家目录。
        "LANG",  # 保留默认字符编码和本地化设置。
        "LC_ALL",  # 保留显式覆盖的本地化设置。
        "PATH",  # 保留 Python、Git 和测试命令的查找路径。
        "PYTHONPATH",  # 保留调用方显式设置的 Python 导入路径。
        "TERM",  # 保留部分命令判断终端能力所需的信息。
        "TMPDIR",  # 保留 Linux 临时目录位置。
        "VIRTUAL_ENV",  # 兼容使用 venv 的开发环境。
    }  # 结束环境变量名称集合。
)  # 完成不可变允许列表定义。

_CONTROL_OUTPUT_CHARS = 100_000  # 为仓库根路径和提交等控制面结果保留独立解析预算。


class LocalRuntime:  # 定义本地隔离运行时。
    """在显式 isolation root 子目录中执行可信开发任务。

    LocalRuntime 依然共享宿主机内核和用户权限，因此不能替代 Docker 沙箱。
    它只服务于单元测试、开发调试和已经复制到隔离目录的可信仓库。
    """  # 解释该实现的安全边界。

    def __init__(  # 定义运行时构造函数。
        self,  # 接收当前实例。
        workspace: Path,  # 接收具体任务仓库目录。
        isolation_root: Path,  # 接收允许发生破坏性重置的隔离根目录。
        *,  # 强制安全参数使用关键字传递。
        max_output_chars: int = 20_000,  # 设置 stdout 和 stderr 各自的最大字符数。
        termination_grace_seconds: float = 0.5,  # 设置 SIGTERM 到 SIGKILL 的宽限时间。
    ) -> None:  # 完成构造函数签名。
        if max_output_chars < 1:  # 验证输出限制有效。
            raise ValueError("max_output_chars 必须大于零")  # 拒绝无法保留任何输出的设置。
        if termination_grace_seconds <= 0:  # 验证进程终止宽限时间有效。
            raise ValueError("termination_grace_seconds 必须大于零")  # 拒绝无效宽限时间。
        self._workspace = workspace.resolve(strict=True)  # 保存真实存在的任务工作区。
        self._isolation_root = isolation_root.resolve(strict=True)  # 保存真实存在的隔离根目录。
        self._max_output_chars = max_output_chars  # 保存输出字符上限。
        self._termination_grace_seconds = termination_grace_seconds  # 保存进程终止宽限时间。
        self._started = False  # 初始化生命周期状态为尚未启动。
        self._base_commit: str | None = None  # 初始化解析后的基础提交为空。
        self._resolver: WorkspacePathResolver | None = None  # 初始化任务路径解析器为空。
        self._environment = self._build_environment()  # 构造最小化的子进程环境变量。
        self._validate_workspace_scope()  # 在任何命令执行前检查隔离目录边界。

    @property  # 将工作区作为只读属性暴露给编排层。
    def workspace(self) -> Path:  # 定义工作区属性。
        """返回经过解析的任务工作区。"""  # 说明属性语义。

        return self._workspace  # 返回不可重新绑定的 Path 值。

    async def start(self, task: TaskSpec) -> None:  # 实现 Runtime 启动协议。
        """校验本地 Git 仓库、基础提交和干净状态。"""  # 说明启动阶段只校验不重置。

        if self._started:  # 防止同一实例被重复启动并覆盖状态。
            raise WorkspaceSafetyError("LocalRuntime 已经启动")  # 拒绝重复启动。
        if task.repo_spec.kind is not RepositoryKind.LOCAL:  # 检查任务是否声明本地仓库。
            raise WorkspaceSafetyError("LocalRuntime 只接受本地仓库任务")  # 拒绝远程或镜像任务。
        task_workspace = Path(task.repo_spec.location).resolve(strict=True)  # 解析 TaskSpec 中的仓库路径。
        if task_workspace != self._workspace:  # 比较任务仓库与构造时指定的隔离工作区。
            raise WorkspaceSafetyError("TaskSpec 仓库与 LocalRuntime 工作区不一致")  # 阻止运行时指向错误目录。
        git_root_result = await self._run_process(  # 请求 Git 返回真实仓库根目录。
            ("git", "rev-parse", "--show-toplevel"),  # 使用参数数组避免 Shell 拼接。
            timeout_seconds=10.0,  # 为轻量 Git 检查设置短超时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 防止较小 Agent 输出预算截断仓库绝对路径。
        )  # 完成仓库根目录命令。
        if not git_root_result.succeeded:  # 检查工作区是否是有效 Git 仓库。
            raise WorkspaceSafetyError(f"工作区不是有效 Git 仓库：{git_root_result.stderr}")  # 返回明确诊断。
        git_root = Path(git_root_result.stdout.strip()).resolve(strict=True)  # 规范化 Git 返回的根目录。
        if git_root != self._workspace:  # 首版要求 Runtime 工作区就是仓库根目录。
            raise WorkspaceSafetyError("LocalRuntime 工作区必须指向 Git 仓库根目录")  # 拒绝隐式父仓库。
        base_result = await self._run_process(  # 把短 SHA、标签或分支解析为完整提交。
            #确认 base_commit 存在、能解析、且确实指向一个 commit，然后返回它的完整 SHA。
            ("git", "rev-parse", "--verify", f"{task.base_commit}^{{commit}}"),  # 要求引用必须解析为提交。
            timeout_seconds=10.0,  # 限制提交解析耗时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 保证完整读取基础提交 SHA。
        )  # 完成基础提交解析命令。
        if not base_result.succeeded:  # 检查基础提交是否存在。
            raise WorkspaceSafetyError(f"无法解析 base_commit：{base_result.stderr}")  # 拒绝不稳定任务起点。
        head_result = await self._run_process(  # 查询工作区当前 HEAD。
            ("git", "rev-parse", "HEAD"),  # 请求当前完整提交 SHA。
            timeout_seconds=10.0,  # 限制 HEAD 查询耗时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 保证完整读取当前提交 SHA。
        )  # 完成 HEAD 查询命令。
        if not head_result.succeeded:  # 检查 HEAD 是否可用。仓库现在实际停在哪个 commit
            raise WorkspaceSafetyError(f"无法读取仓库 HEAD：{head_result.stderr}")  # 返回明确诊断。
        resolved_base = base_result.stdout.strip()  # 保存规范化基础提交 SHA。任务要求从哪个 commit 开始
        if head_result.stdout.strip() != resolved_base:  # 确认仓库已经位于任务基础提交。
            raise WorkspaceSafetyError("仓库 HEAD 与 TaskSpec.base_commit 不一致")  # 避免自动重置用户仓库。
        status_result = await self._run_process(  # 查询已跟踪和未跟踪修改。
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),  # 使用稳定机器可读格式。
            timeout_seconds=10.0,  # 限制状态检查耗时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 让控制面状态检查不受 Agent 观察预算影响。
        )  # 完成 Git 状态命令。
        if not status_result.succeeded:  # 检查 Git 状态命令是否成功。
            raise WorkspaceSafetyError(f"无法读取 Git 状态：{status_result.stderr}")  # 返回明确诊断。
        if status_result.stdout.strip():  # 检查是否存在任何工作区变化。
            raise DirtyWorkspaceError("LocalRuntime 启动前要求工作区完全干净")  # 防止 reset 删除用户修改。
        self._base_commit = resolved_base  # 保存后续 reset 使用的完整基础提交。
        self._resolver = WorkspacePathResolver(self._workspace, task.path_policy)  # 创建任务专属路径解析器。
        self._started = True  # 所有安全检查通过后才标记运行时已启动。

    async def execute(  # 实现受控命令执行协议。
        self,  # 接收当前实例。
        command: tuple[str, ...],  # 接收不经过 Shell 解析的参数数组。
        *,  # 强制超时使用关键字参数。
        timeout_seconds: float,  # 接收本次命令的硬超时。
    ) -> CommandResult:  # 返回结构化命令结果。
        """在固定工作区和最小环境变量中执行命令。"""  # 说明公开执行语义。

        self._ensure_started()  # 阻止启动前执行任意命令。
        return await self._run_process(command, timeout_seconds=timeout_seconds)  # 委托统一子进程实现。

    async def read_file(  # 实现安全文件片段读取协议。
        self,  # 接收当前实例。
        path: str,  # 接收工作区相对路径。
        *,  # 强制行范围使用关键字参数。
        start_line: int = 1,  # 指定从一开始计数的起始行。
        end_line: int | None = None,  # 指定包含式结束行或读取到文件末尾。
    ) -> str:  # 返回请求范围的原始文本。
        """读取通过路径策略校验的 UTF-8 文本片段。"""  # 说明读取语义。

        self._ensure_started()  # 阻止启动前访问工作区文件。
        if start_line < 1:  # 检查起始行采用一开始计数。
            raise ValueError("start_line 必须大于等于 1")  # 拒绝零或负数行号。
        if end_line is not None and end_line < start_line:  # 检查结束行不早于起始行。
            raise ValueError("end_line 不能小于 start_line")  # 拒绝反向行范围。
        resolver = self._require_resolver()  # 获取启动后创建的路径解析器。
        resolved = resolver.resolve(path, write=False, must_exist=True)  # 执行越界、拒绝和符号链接检查。
        if not resolved.is_file():  # 确认目标是普通文件而不是目录。
            raise PathViolationError(f"读取目标不是文件：{path}")  # 拒绝目录和特殊路径。
        content = await asyncio.to_thread(resolved.read_text, encoding="utf-8", errors="replace")  # 在线程中读取文本。
        lines = content.splitlines(keepends=True)  # 保留原始换行以便稳定重建片段。
        stop_index = end_line if end_line is not None else len(lines)  # 把包含式行号转换为切片终点。
        return "".join(lines[start_line - 1 : stop_index])  # 返回指定的一开始计数行范围。

    async def apply_patch(self, patch: str) -> PatchResult:  # 实现安全统一 diff 应用协议。
        """先检查路径和可应用性，再原子式应用补丁。"""  # 说明失败补丁不会留下部分修改。

        self._ensure_started()  # 阻止启动前修改工作区。
        if not patch.strip():  # 检查补丁是否包含有效文本。
            return PatchResult(applied=False, rejection_reason="补丁不能为空")  # 用结构化结果拒绝空补丁。
        try:  # 将策略违规转换为 PatchResult 而不是泄漏异常。
            changed_paths = self._validate_patch_paths(patch)  # 校验补丁涉及的每个文件路径。
        except PathViolationError as error:  # 捕获工作区越界、拒绝或只读错误。
            return PatchResult(applied=False, rejection_reason=str(error))  # 返回可反馈给 Agent 的拒绝原因。
        check_result = await self._run_process(  # 先让 Git 验证补丁能否完整应用。
            ("git", "apply", "--check", "--whitespace=nowarn", "-"),  # 使用标准输入而非临时补丁文件。
            timeout_seconds=30.0,  # 限制补丁检查耗时。
            input_text=patch,  # 将补丁文本写入 Git 标准输入。
        )  # 完成补丁预检查。
        if not check_result.succeeded:  # 检查补丁是否能完整应用。
            return PatchResult(  # 构造明确的补丁拒绝结果。
                applied=False,  # 标记补丁未修改工作区。
                stdout=check_result.stdout,  # 保留 Git 的标准输出。
                stderr=check_result.stderr,  # 保留 Git 的错误诊断。
                rejection_reason="git apply --check 未通过",  # 提供稳定的高层失败原因。
            )  # 完成预检查失败结果。
        apply_result = await self._run_process(  # 预检查通过后正式应用同一补丁。
            ("git", "apply", "--whitespace=nowarn", "-"),  # 仍然避免 Shell 和磁盘临时文件。
            timeout_seconds=30.0,  # 限制正式应用耗时。
            input_text=patch,  # 将已检查补丁写入 Git 标准输入。
        )  # 完成正式补丁应用。
        if not apply_result.succeeded:  # 防御预检查与正式应用之间的罕见失败。
            await self.reset()  # 回到基础提交，确保不保留任何部分状态。
            return PatchResult(  # 构造正式应用失败结果。
                applied=False,  # 标记补丁最终未保留。
                stdout=apply_result.stdout,  # 保存 Git 标准输出。
                stderr=apply_result.stderr,  # 保存 Git 错误输出。
                rejection_reason="补丁应用失败，工作区已回滚",  # 明确说明已经执行回滚。
            )  # 完成应用失败结果。
        return PatchResult(  # 构造成功补丁结果。
            applied=True,  # 标记修改已经进入候选工作区。
            changed_files=changed_paths,  # 返回经过策略验证的修改文件。
            stdout=apply_result.stdout,  # 保存可能存在的 Git 标准输出。
            stderr=apply_result.stderr,  # 保存可能存在的 Git 警告。
        )  # 完成成功结果。

    async def get_diff(self) -> str:  # 实现标准 Git diff 导出协议。
        """返回已跟踪修改和新增文件的完整统一 diff。"""  # 说明最终 patch 包含新增文件。

        self._ensure_started()  # 阻止启动前读取未定义工作区状态。
        result = await self._run_process(  # 请求 Git 生成相对于基础提交的 diff。
            ("git", "diff", "--no-ext-diff", "--binary", self._require_base_commit(), "--"),  # 禁止外部 diff 工具。
            timeout_seconds=30.0,  # 限制 diff 生成耗时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 使用独立预算保存完整控制面补丁。
        )  # 完成 diff 命令。
        if not result.succeeded or result.output_truncated:  # 检查 Git diff 是否完整生成。
            raise WorkspaceSafetyError(f"无法生成 Git diff：{result.stderr}")  # 将基础设施问题显式上抛。
        untracked = await self._run_process(  # 查询 Git 未跟踪且未被忽略的新增文件。
            ("git", "ls-files", "--others", "--exclude-standard", "-z"),  # 用 NUL 分隔保证空格文件名不被误拆。
            timeout_seconds=30.0,  # 限制文件枚举耗时。
            output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 保证文件列表不被静默截断。
        )  # 完成新增文件枚举。
        if not untracked.succeeded or untracked.output_truncated:  # 检查文件列表是否完整。
            raise WorkspaceSafetyError("无法完整枚举新增文件")  # 禁止遗漏新文件后输出不完整 patch。
        patches = [result.stdout]  # 先保存已跟踪文件修改。
        for path in filter(None, untracked.stdout.split("\x00")):  # 按 Git 返回顺序处理每个新增文件。
            added = await self._run_process(  # 为单个新增文件生成标准可应用的 Git diff。
                ("git", "diff", "--no-ext-diff", "--no-index", "--binary", "--", os.devnull, path),  # 与空设备比较得到 new file patch。
                timeout_seconds=30.0,  # 限制单文件 diff 耗时。
                output_limit_chars=_CONTROL_OUTPUT_CHARS,  # 防止大文件补丁被截断。
            )  # 完成新增文件 diff。
            if added.timed_out or added.return_code != 1 or added.output_truncated:  # Git diff 退出码一表示有差异。
                raise WorkspaceSafetyError(f"无法完整导出新增文件：{path}")  # 禁止提交缺失或损坏的新增文件。
            patches.append(added.stdout)  # 将新增文件 patch 追加到最终结果。
            if sum(len(item) for item in patches) > _CONTROL_OUTPUT_CHARS:  # 检查合并后的总补丁大小。
                raise WorkspaceSafetyError("最终 Git diff 超过控制面输出上限")  # 明确要求缩小补丁或提高预算。
        return "".join(patches)  # 返回可被 git apply 消费的完整统一 diff。

    async def reset(self) -> None:  # 实现候选工作区回滚协议。
        """把隔离工作区恢复到启动时验证的基础提交。"""  # 明确该方法具有破坏性但只作用于隔离目录。

        self._ensure_started()  # 只有通过隔离检查的工作区才允许重置。
        base_commit = self._require_base_commit()  # 获取启动时解析的不可变基础提交。
        reset_result = await self._run_process(  # 丢弃已跟踪文件的工作区和暂存区修改。
            ("git", "reset", "--hard", base_commit),  # 精确重置到验证过的基础提交。
            timeout_seconds=30.0,  # 限制重置耗时。
        )  # 完成已跟踪文件重置。
        if not reset_result.succeeded:  # 检查重置命令是否成功。
            raise WorkspaceSafetyError(f"无法重置工作区：{reset_result.stderr}")  # 不隐瞒破坏性操作失败。
        clean_result = await self._run_process(  # 清理候选生成的未跟踪文件和目录。
            ("git", "clean", "-fd"),  # 不使用 -x，避免删除被忽略的环境和缓存。
            timeout_seconds=30.0,  # 限制清理耗时。
        )  # 完成未跟踪文件清理。
        if not clean_result.succeeded:  # 检查清理命令是否成功。
            raise WorkspaceSafetyError(f"无法清理工作区：{clean_result.stderr}")  # 返回明确基础设施错误。

    async def close(self) -> None:  # 实现 Runtime 关闭协议。
        """释放当前轻量 Runtime 的生命周期状态。"""  # 说明当前实现没有常驻进程需要回收。

        self._started = False  # 阻止关闭后继续执行命令或读写文件。
        self._resolver = None  # 丢弃与前一个任务绑定的路径策略。

    def _validate_workspace_scope(self) -> None:  # 定义隔离目录结构检查。
        if self._isolation_root == Path(self._isolation_root.anchor):  # 检查隔离根是否误设为文件系统根。
            raise WorkspaceSafetyError("isolation_root 不能是文件系统根目录")  # 阻止全盘破坏范围。
        if self._workspace == self._isolation_root:  # 检查工作区是否直接等于隔离根。
            raise WorkspaceSafetyError("workspace 必须是 isolation_root 的严格子目录")  # 保留明确的边界层级。
        if not self._workspace.is_relative_to(self._isolation_root):  # 检查工作区是否真实位于隔离根下。
            raise WorkspaceSafetyError("workspace 不在 isolation_root 内部")  # 阻止 reset 作用到任意目录。
        if not self._workspace.is_dir():  # 检查工作区类型。
            raise WorkspaceSafetyError("workspace 必须是已存在目录")  # 拒绝文件或缺失路径。

    @staticmethod  # 声明环境构造不读取实例状态。
    def _build_environment() -> dict[str, str]:  # 定义子进程环境变量允许列表。
        environment = {  # 开始构造只包含允许名称的环境字典。
            name: value  # 保留环境变量原始名称和值。
            for name, value in os.environ.items()  # 遍历当前 PatchFlow 进程环境。
            if name in _ENVIRONMENT_ALLOWLIST  # 只选择显式允许的变量。
        }  # 完成允许变量筛选。
        environment.setdefault("PATH", os.defpath)  # 在调用方没有 PATH 时使用系统安全默认值。
        environment["PYTHONUNBUFFERED"] = "1"  # 让测试和脚本及时输出日志。
        return environment  # 返回不会包含 API key 等未知变量的最小环境。

    def _ensure_started(self) -> None:  # 定义生命周期守卫。
        if not self._started:  # 检查启动安全验证是否完成。
            raise RuntimeNotStartedError("LocalRuntime 尚未启动或已经关闭")  # 拒绝非法生命周期调用。

    def _require_resolver(self) -> WorkspacePathResolver:  # 定义已初始化路径解析器读取方法。
        if self._resolver is None:  # 防御启动前或关闭后访问。
            raise RuntimeNotStartedError("路径解析器尚未初始化")  # 返回明确生命周期错误。
        return self._resolver  # 返回任务专属路径解析器。

    def _require_base_commit(self) -> str:  # 定义已解析基础提交读取方法。
        if self._base_commit is None:  # 防御启动前访问。
            raise RuntimeNotStartedError("base_commit 尚未解析")  # 返回明确生命周期错误。
        return self._base_commit  # 返回完整基础提交 SHA。

    def _validate_patch_paths(self, patch: str) -> tuple[str, ...]:  # 定义 patch 文件路径提取与策略校验。
        resolver = self._require_resolver()  # 获取任务专属路径解析器。
        changed_paths: list[str] = []  # 创建保持 patch 顺序的路径列表。
        header_count = 0  # 记录可识别的旧版或新版文件头数量。
        for line in patch.splitlines():  # 逐行扫描统一 diff 文件头。
            if not (line.startswith("--- ") or line.startswith("+++ ")):  # 只处理旧版和新版文件路径头。
                continue  # 跳过上下文、增删代码行和其他元数据。
            header_count += 1  # 记录已经发现一个统一 diff 文件头。
            header_path = line[4:].split("\t", 1)[0].strip()  # 去掉前缀以及可选时间戳字段。
            if header_path == "/dev/null":  # 识别新增或删除文件使用的特殊空设备路径。
                continue  # 由同一文件块中的另一个真实路径完成策略校验。
            if header_path.startswith(("a/", "b/")):  # 识别标准 Git diff 的旧版或新版路径前缀。
                header_path = header_path[2:]  # 去掉不属于真实仓库路径的 a/ 或 b/。
            if not header_path or header_path.startswith('"'):  # 首版拒绝空路径和 Git 引号转义路径。
                raise PathViolationError("暂不支持空路径或带 Git 引号转义的 patch 路径")  # 避免错误解析安全边界。
            resolved = resolver.resolve(header_path, write=True, must_exist=False)  # 校验越界、拒绝和只读策略。
            relative = resolver.relative_name(resolved)  # 转换为稳定的工作区相对路径。
            if relative not in changed_paths:  # 避免普通修改的旧新版路径在结果中重复出现。
                changed_paths.append(relative)  # 按首次出现顺序记录修改、创建、删除或重命名路径。
        if header_count < 2 or not changed_paths:  # 检查补丁至少有一组文件头和一个真实路径。
            raise PathViolationError("补丁没有可识别的完整目标文件头")  # 拒绝无法执行路径策略检查的补丁。
        return tuple(changed_paths)  # 返回不可变修改路径集合。

    async def _run_process(  # 定义所有命令共享的底层异步执行器。
        self,  # 接收当前实例。
        command: tuple[str, ...],  # 接收不经过 Shell 的参数数组。
        *,  # 强制执行选项使用关键字参数。
        timeout_seconds: float,  # 接收本次执行的硬超时。
        input_text: str | None = None,  # 可选向标准输入写入 patch 等文本。
        output_limit_chars: int | None = None,  # 可选为内部控制命令覆盖 Agent 可见输出预算。
    ) -> CommandResult:  # 返回统一命令结果。
        if not command or any(not argument for argument in command):  # 检查命令和每个参数都非空。
            raise ValueError("command 必须包含至少一个非空参数")  # 拒绝不明确的命令请求。
        if timeout_seconds <= 0:  # 检查超时设置有效。
            raise ValueError("timeout_seconds 必须大于零")  # 拒绝立即超时或负数超时。
        effective_output_limit = output_limit_chars or self._max_output_chars  # 选择控制面或默认输出预算。
        if effective_output_limit < 1:  # 防御内部调用传入无效覆盖值。
            raise ValueError("output_limit_chars 必须大于零")  # 拒绝无法保留任何输出的设置。
        started_at = time.perf_counter()  # 记录不受系统时间调整影响的开始时间。
        stdin_setting = asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL  # 选择标准输入模式。
        try:  # 捕获命令不存在和进程创建失败。
            process = await asyncio.create_subprocess_exec(  # 创建不经过 Shell 的异步子进程。
                *command,  # 将参数数组逐项传给操作系统。
                cwd=str(self._workspace),  # 把命令工作目录固定在隔离仓库根。
                env=self._environment,  # 只传递允许列表中的环境变量。
                stdin=stdin_setting,  # 根据调用需求配置标准输入管道。
                stdout=asyncio.subprocess.PIPE,  # 捕获标准输出供 Agent 观察。
                stderr=asyncio.subprocess.PIPE,  # 捕获错误输出供诊断。
                start_new_session=os.name == "posix",  # 在 Linux 创建独立会话以便终止整个进程组。
            )  # 完成子进程创建。
        except OSError as error:  # 捕获可执行文件不存在或系统拒绝创建进程。
            elapsed = time.perf_counter() - started_at  # 计算失败前消耗的时间。
            stderr, truncated = truncate_text(str(error), effective_output_limit)  # 按本次预算限制错误文本。
            return CommandResult(  # 构造命令未启动结果。
                command=command,  # 原样记录请求参数数组。
                return_code=None,  # 使用空退出码表示进程从未成功启动。
                stdout="",  # 未启动进程没有标准输出。
                stderr=stderr,  # 保存系统创建进程错误。
                elapsed_seconds=elapsed,  # 保存启动失败耗时。
                output_truncated=truncated,  # 记录错误文本是否被截断。
                termination_reason="process_start_failed",  # 提供稳定机器可读原因。
            )  # 完成命令未启动结果。
        input_bytes = input_text.encode("utf-8") if input_text is not None else None  # 把可选输入编码为字节。
        communication = asyncio.create_task(process.communicate(input_bytes))  # 启动并持续排空两个输出管道。
        timed_out = False  # 初始化超时标志。
        termination_reason: str | None = None  # 初始化终止原因。
        try:  # 等待命令在硬超时内完成。
            stdout_bytes, stderr_bytes = await asyncio.wait_for(  # 对通信任务施加超时。
                asyncio.shield(communication),  # 防止 wait_for 取消后无法继续排空管道。
                timeout=timeout_seconds,  # 使用调用方提供的单次命令限制。
            )  # 完成正常通信等待。
        except TimeoutError:  # 捕获 Python 3.11 的异步超时。
            timed_out = True  # 标记命令超过硬时限。
            termination_reason = "timeout"  # 保存稳定终止原因。
            await self._terminate_process_tree(process)  # 终止进程及其在 Linux 中创建的子进程组。
            stdout_bytes, stderr_bytes = await communication  # 回收终止前已经产生的全部输出。
        elapsed = time.perf_counter() - started_at  # 计算命令完整墙钟耗时。
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")  # 容错解码标准输出。
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")  # 容错解码错误输出。
        stdout, stdout_truncated = truncate_text(stdout_text, effective_output_limit)  # 限制标准输出长度。
        stderr, stderr_truncated = truncate_text(stderr_text, effective_output_limit)  # 限制错误输出长度。
        return CommandResult(  # 构造最终结构化执行结果。
            command=command,  # 保存未经 Shell 修改的原始参数数组。
            return_code=process.returncode,  # 保存退出码或被信号终止后的负数状态。
            stdout=stdout,  # 保存受限标准输出。
            stderr=stderr,  # 保存受限错误输出。
            elapsed_seconds=elapsed,  # 保存执行耗时。
            timed_out=timed_out,  # 保存是否触发硬超时。
            output_truncated=stdout_truncated or stderr_truncated,  # 汇总两个输出流的截断状态。
            termination_reason=termination_reason,  # 保存超时等机器可读原因。
        )  # 完成最终结果。

    async def _terminate_process_tree(self, process: asyncio.subprocess.Process) -> None:  # 定义超时进程树终止逻辑。
        if process.returncode is not None:  # 检查进程是否已经自然结束。
            return  # 已结束进程无需再次发送信号。
        if os.name == "posix":  # 在 Linux 和其他 POSIX 系统使用独立进程组。
            try:  # 防止进程恰好在发送信号前退出。
                os.killpg(process.pid, signal.SIGTERM)  # 先请求整个进程组优雅终止。
            except ProcessLookupError:  # 捕获进程组已经消失的竞争条件。
                return  # 目标已经结束时无需继续处理。
        else:  # Windows 本地开发退化为终止直接子进程。
            process.terminate()  # 请求直接子进程退出。
        try:  # 等待宽限时间让进程自行清理。
            await asyncio.wait_for(process.wait(), timeout=self._termination_grace_seconds)  # 等待优雅退出。
            return  # 进程已经结束时直接返回。
        except TimeoutError:  # 捕获宽限时间耗尽。
            pass  # 继续执行强制终止。
        if os.name == "posix":  # 在 POSIX 系统强制终止完整进程组。
            try:  # 防止进程在两次信号之间退出。
                os.killpg(process.pid, signal.SIGKILL)  # 强制杀死整个进程组。
            except ProcessLookupError:  # 捕获进程组已经消失的竞争条件。
                return  # 目标已经结束时无需继续等待。
        else:  # Windows 本地开发强制终止直接子进程。
            process.kill()  # 强制杀死直接子进程。
        await process.wait()  # 回收最终退出状态，避免僵尸进程。
