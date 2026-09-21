"""基于 Docker CLI 的任务级隔离 Runtime。"""  # 说明本模块负责容器执行后端。

from __future__ import annotations  # 启用延迟类型标注。

import asyncio  # 使用异步子进程执行 Docker CLI。
import codecs  # 增量解码 UTF-8 管道输出以支持跨块字符。
import json  # 序列化传入容器的路径策略。
import os  # 获取当前非 root 用户 ID 和最小环境。
import time  # 记录命令墙钟耗时。
from pathlib import Path  # 规范化只读源仓库路径。
from uuid import uuid4  # 为每个任务生成不可预测的容器名。

from pydantic import BaseModel, ConfigDict, Field  # 校验外部容器配置。

from patchflow.domain.enums import RepositoryKind  # 限制首版为本地仓库任务。
from patchflow.domain.runtime import CommandResult, PatchResult  # 复用领域执行结果。
from patchflow.domain.task import TaskSpec  # 接收现有任务模型。
from patchflow.runtime.errors import (  # 复用运行时异常。
    PathViolationError,  # 导入文件路径违规异常。
    RuntimeNotStartedError,  # 导入生命周期异常。
    WorkspaceSafetyError,  # 导入隔离环境异常。
)  # 结束运行时异常导入列表。
from patchflow.runtime.output import OutputAccumulator  # 在读取管道时就限制内存占用。
from patchflow.runtime.paths import WorkspacePathResolver  # 在宿主侧执行保守路径策略检查。

_REPOSITORY = "/work/repo"  # 固定容器内的可写仓库路径。
_CONTROL_OUTPUT_LIMIT = 100_000  # 控制命令使用独立输出预算。
_READ_SCRIPT = "\n".join(  # 构造在容器内执行的路径验证和按行读取程序。
    (  # 每个字符串对应容器内程序的一行。
        "import json, pathlib, sys",  # 导入路径、命令行和策略解析模块。
        "root = pathlib.Path('/work/repo').resolve(strict=True)",  # 获取容器内真实仓库根目录。
        "path = (root / sys.argv[1]).resolve(strict=True)",  # 解析请求路径及全部符号链接。
        "if not path.is_relative_to(root): raise SystemExit(23)",  # 拒绝指向容器工作区之外的路径。
        "policy = json.loads(sys.argv[4])",  # 读取与宿主任务一致的路径策略。
        "allowed = [(root / item).resolve(strict=False) for item in policy['allowed_paths']]",  # 解析容器内允许路径。
        "denied = [(root / item).resolve(strict=False) for item in policy['denied_paths']]",  # 解析容器内拒绝路径。
        "if not any(path == item or path.is_relative_to(item) for item in allowed): raise SystemExit(25)",  # 拒绝不在允许范围内的目标。
        "if any(path == item or path.is_relative_to(item) for item in denied): raise SystemExit(26)",  # 拒绝指向 .git 等禁止路径的符号链接。
        "if not path.is_file(): raise SystemExit(24)",  # 拒绝目录、设备或其他非文件目标。
        "lines = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)",  # 读取 UTF-8 文本并保留换行。
        "sys.stdout.write(''.join(lines[int(sys.argv[2]) - 1:int(sys.argv[3])]))",  # 输出包含式行范围。
    )  # 结束容器内程序逐行定义。
)  # 完成容器读取程序。


async def _drain_stream(stream: asyncio.StreamReader, accumulator: OutputAccumulator) -> None:  # 流式排空一个管道。
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")  # 创建可处理拆分 UTF-8 字符的解码器。
    while chunk := await stream.read(65_536):  # 分块读取以避免缓存完整命令输出。
        accumulator.append(decoder.decode(chunk))  # 只保留有限头尾字符并统计总长度。
    accumulator.append(decoder.decode(b"", final=True))  # 输出可能残留的不完整末尾字符。


class DockerRuntimeConfig(BaseModel):  # 定义容器资源与镜像配置。
    """拒绝未知字段，并为安全选项提供保守默认值。"""  # 说明配置边界。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 禁止静默忽略配置拼写错误。
    image: str = Field(default="patchflow-runtime:py311", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")  # 限制镜像名称字符。
    cpus: float = Field(default=1.0, gt=0, le=16.0)  # 限制单容器可用 CPU 数。
    memory_mb: int = Field(default=1024, ge=128, le=65536)  # 限制单容器内存。
    pids_limit: int = Field(default=128, ge=16, le=4096)  # 限制容器总进程数。
    workspace_mb: int = Field(default=512, ge=64, le=32768)  # 限制内存工作区大小。
    temp_mb: int = Field(default=128, ge=16, le=4096)  # 限制容器临时目录大小。
    max_output_chars: int = Field(default=20_000, ge=1, le=1_000_000)  # 限制每个输出流长度。


class DockerRuntime:  # 实现领域 Runtime 协议的 Docker 后端。
    """只读挂载输入仓库，在容器 tmpfs 中生成独立可写副本。"""  # 描述关键隔离方式。

    def __init__(self, source_repository: Path, config: DockerRuntimeConfig | None = None) -> None:  # 接收仓库和配置。
        self._source = source_repository.resolve(strict=True)  # 保存真实源仓库路径。
        if not self._source.is_dir() or self._source == Path(self._source.anchor):  # 拒绝文件及根目录挂载。
            raise WorkspaceSafetyError("source_repository 必须是非根目录的现有仓库")  # 阻止过宽宿主挂载。
        self._config = config or DockerRuntimeConfig()  # 使用经过 Pydantic 校验的默认配置。
        self._container_name: str | None = None  # 记录当前任务的容器名称。
        self._base_commit: str | None = None  # 保存启动时验证的基础提交。
        self._resolver: WorkspacePathResolver | None = None  # 保存宿主只读仓库的路径策略解析器。
        self._started = False  # 初始化生命周期为未启动。

    async def start(self, task: TaskSpec) -> None:  # 创建并验证单任务容器。
        if self._started or self._container_name is not None:  # 防止复用已启动或半启动实例。
            raise WorkspaceSafetyError("DockerRuntime 已启动或仍持有容器")  # 避免任务之间状态混用。
        if os.name != "posix" or os.getuid() == 0:  # 首版要求 Linux/WSL 非 root 用户。
            raise WorkspaceSafetyError("DockerRuntime 必须在非 root Linux/WSL 用户下启动")  # 拒绝 root 运行。
        if task.repo_spec.kind is not RepositoryKind.LOCAL:  # 首版仅接受本地仓库任务。
            raise WorkspaceSafetyError("DockerRuntime 当前只接受本地仓库任务")  # 拒绝尚未适配的数据源。
        if Path(task.repo_spec.location).resolve(strict=True) != self._source:  # 确认任务与指定源仓库一致。
            raise WorkspaceSafetyError("TaskSpec 仓库与 DockerRuntime 源仓库不一致")  # 阻止挂载错库。
        self._resolver = WorkspacePathResolver(self._source, task.path_policy)  # 准备路径策略校验器。
        name = f"patchflow-{uuid4().hex}"  # 为任务生成唯一容器名称。
        command = self._build_run_command(name)  # 构造固定安全参数的 Docker 启动命令。
        try:  # 在任意初始化失败时确保容器被销毁。
            self._container_name = name  # 在调用前登记容器名，以便 Docker run 超时后仍可清理。
            launched = await self._docker_call(command, timeout_seconds=60.0)  # 启动后台容器。
            if not launched.succeeded:  # 检查镜像、daemon 和挂载是否可用。
                raise WorkspaceSafetyError(f"Docker 容器启动失败: {launched.stderr}")  # 返回基础设施诊断。
            copied = await self._container_call(("cp", "-R", "--", "/source/.", _REPOSITORY), timeout_seconds=120.0, workdir="/work")  # 以非 root 身份复制输入。
            self._require_success(copied, "复制任务仓库")  # 检查仓库副本是否建立。
            root = await self._git("rev-parse", "--show-toplevel")  # 获取容器内仓库根路径。
            if root.stdout.strip() != _REPOSITORY:  # 确认没有意外使用上级 Git 仓库。
                raise WorkspaceSafetyError("容器工作区必须是 Git 仓库根目录")  # 拒绝隐式父仓库。
            base = await self._git("rev-parse", "--verify", f"{task.base_commit}^{{commit}}")  # 解析完整基础提交。
            head = await self._git("rev-parse", "HEAD")  # 查询当前 HEAD。
            if base.stdout.strip() != head.stdout.strip():  # 要求副本位于指定基础提交。
                raise WorkspaceSafetyError("容器仓库 HEAD 与 base_commit 不一致")  # 不自动切换提交。
            status = await self._git("status", "--porcelain=v1", "--untracked-files=all")  # 查询所有工作区修改。
            if status.stdout.strip():  # 拒绝带用户修改的源仓库副本。
                raise WorkspaceSafetyError("DockerRuntime 启动前要求仓库完全干净")  # 不丢弃输入变化。
            self._base_commit = base.stdout.strip()  # 保存经过容器内验证的提交。
            self._started = True  # 所有检查成功后才开放命令执行。
        except BaseException:  # 无论普通异常还是取消都应清理已创建容器。
            await self.close()  # 避免留下后台容器和任务副本。
            raise  # 把原始失败交给调用方处理。

    async def execute(self, command: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:  # 在容器内执行参数数组。
        self._ensure_started()  # 拒绝生命周期错误。
        if not command or any(not item for item in command):  # 检查可执行文件和参数非空。
            raise ValueError("command 必须包含非空参数")  # 避免空命令传给 Docker CLI。
        result = await self._container_call(command, timeout_seconds=timeout_seconds, output_limit=self._config.max_output_chars)  # 执行用户命令。
        if result.timed_out:  # 命令超时可能留下容器内子进程。
            await self.close()  # 直接销毁整个任务容器，禁止继续使用污染状态。
        return result  # 返回完整结构化命令结果。

    async def read_file(self, path: str, *, start_line: int = 1, end_line: int | None = None) -> str:  # 读取容器内代码。
        self._ensure_started()  # 拒绝启动前读取。
        if start_line < 1 or (end_line is not None and end_line < start_line):  # 验证一开始计数的行范围。
            raise ValueError("文件行范围无效")  # 拒绝反向或非正行号。
        resolver = self._require_resolver()  # 获取任务路径策略。
        safe_path = resolver.relative_name(resolver.resolve(path, must_exist=False))  # 执行宿主侧保守路径检查。
        stop = end_line if end_line is not None else 2_147_483_647  # 使用大整数表达读取到文件末尾。
        policy_json = json.dumps(resolver.policy.model_dump(), ensure_ascii=False)  # 把任务策略传入容器二次校验。
        result = await self._container_call(  # 在容器内部重新解析真实路径并读取。
            ("python", "-c", _READ_SCRIPT, safe_path, str(start_line), str(stop), policy_json),  # 固定读取程序及策略参数。
            timeout_seconds=30.0,  # 限制读取耗时。
            output_limit=self._config.max_output_chars,  # 限制读入 Agent 上下文的内容。
        )  # 完成容器内读取命令。
        if not result.succeeded:  # 容器内路径可能已变为符号链接或不存在。
            raise PathViolationError(f"容器内文件读取被拒绝: {path}; {result.stderr}")  # 返回安全错误。
        if result.output_truncated:  # Runtime 协议目前无法单独返回读取截断标记。
            raise WorkspaceSafetyError("文件片段超过 Runtime 输出上限，请缩小行范围")  # 防止上层误把不完整代码当完整文件。
        return result.stdout  # 返回有限长度的文件片段。

    async def apply_patch(self, patch: str) -> PatchResult:  # 在容器副本中应用统一 diff。
        self._ensure_started()  # 拒绝启动前修改。
        if not patch.strip():  # 明确拒绝空补丁。
            return PatchResult(applied=False, rejection_reason="补丁不能为空")  # 返回结构化拒绝。
        try:  # 把路径策略错误转换成 Agent 可消费结果。
            changed = self._validate_patch_paths(patch)  # 检查每个旧路径和新路径。
        except PathViolationError as error:  # 捕获路径越界或只读修改。
            return PatchResult(applied=False, rejection_reason=str(error))  # 解释拒绝原因。
        checked = await self._container_call(  # 先验证补丁完整可应用。
            ("git", "apply", "--check", "--whitespace=nowarn", "-"),  # 使用 Git 标准输入预检。
            timeout_seconds=30.0,  # 限制预检耗时。
            input_text=patch,  # 向 Docker exec 传递原补丁。
        )  # 完成预检命令。
        if not checked.succeeded:  # 检查补丁是否被 Git 拒绝。
            return PatchResult(applied=False, stdout=checked.stdout, stderr=checked.stderr, rejection_reason="git apply --check 未通过")  # 不修改仓库。
        applied = await self._container_call(  # 应用已预检的补丁。
            ("git", "apply", "--whitespace=nowarn", "-"),  # 使用与预检一致的 Git 参数。
            timeout_seconds=30.0,  # 限制应用耗时。
            input_text=patch,  # 发送相同补丁内容。
        )  # 完成正式应用。
        if not applied.succeeded:  # 处理预检后偶发竞态或容器故障。
            await self.reset()  # 恢复基础提交以消除部分状态。
            return PatchResult(applied=False, stdout=applied.stdout, stderr=applied.stderr, rejection_reason="补丁应用失败，已回滚")  # 明确回滚。
        return PatchResult(applied=True, changed_files=changed, stdout=applied.stdout, stderr=applied.stderr)  # 返回修改文件。

    async def get_diff(self) -> str:  # 导出容器工作区的候选补丁。
        self._ensure_started()  # 拒绝启动前访问。
        result = await self._git("diff", "--no-ext-diff", "--binary", self._require_base(), "--")  # 禁用外部 diff 程序。
        if result.output_truncated:  # 检查导出的补丁是否完整。
            raise WorkspaceSafetyError("Git diff 超过控制面输出上限，不能作为最终补丁")  # 禁止提交损坏 diff。
        untracked = await self._git("ls-files", "--others", "--exclude-standard", "-z")  # 枚举未跟踪且未忽略的新增文件。
        patches = [result.stdout]  # 首先保存已跟踪文件的差异。
        for path in filter(None, untracked.stdout.split("\x00")):  # 按 Git 返回顺序逐个处理新文件。
            added = await self._container_call(  # 与空设备比较以生成 Git new file patch。
                ("git", "diff", "--no-ext-diff", "--no-index", "--binary", "--", os.devnull, path),  # 不修改容器索引。
                timeout_seconds=30.0,  # 限制单文件 diff 耗时。
                output_limit=_CONTROL_OUTPUT_LIMIT,  # 保持完整控制面输出。
            )  # 完成新文件补丁生成。
            if added.timed_out or added.return_code != 1 or added.output_truncated:  # 只接受 Git 有差异的正常退出。
                raise WorkspaceSafetyError(f"无法完整导出新增文件: {path}")  # 禁止漏掉新增文件。
            patches.append(added.stdout)  # 追加当前新文件补丁。
            if sum(len(item) for item in patches) > _CONTROL_OUTPUT_LIMIT:  # 检查最终补丁总长度。
                raise WorkspaceSafetyError("最终 Git diff 超过控制面输出上限")  # 避免返回不完整补丁。
        return "".join(patches)  # 返回已跟踪修改和新增文件的完整统一 diff。

    async def reset(self) -> None:  # 丢弃当前容器副本中的候选修改。
        self._ensure_started()  # 仅允许已验证的任务容器执行重置。
        await self._git("reset", "--hard", self._require_base())  # 恢复已跟踪文件到基础提交。
        await self._git("clean", "-fd")  # 移除候选新增的未跟踪文件。

    async def close(self) -> None:  # 销毁任务容器并清除生命周期状态。
        name = self._container_name  # 暂存需要销毁的容器名。
        self._container_name = None  # 防止清理重入时重复持有容器。
        self._started = False  # 立即禁止新命令进入容器。
        self._base_commit = None  # 清除前一任务的基础提交。
        self._resolver = None  # 清除前一任务的路径策略。
        if name is not None:  # 仅对当前实例创建的容器执行清理。
            await self._docker_call(("rm", "-f", name), timeout_seconds=30.0)  # 强制终止所有进程并删除容器。

    def _build_run_command(self, name: str) -> tuple[str, ...]:  # 生成固定顺序的 Docker run 参数。
        uid = os.getuid()  # 读取宿主非 root 用户 ID。
        gid = os.getgid()  # 读取宿主非 root 用户组 ID。
        return (  # 返回没有 Shell 拼接的参数元组。
            "run", "-d", "--rm", "--pull", "never", "--name", name,  # 仅运行本地显式构建的镜像。
            "--network", "none",  # 默认禁止容器网络。
            "--read-only",  # 禁止修改镜像根文件系统。
            "--cap-drop", "ALL",  # 移除全部 Linux capabilities。
            "--security-opt", "no-new-privileges",  # 禁止进程提权。
            "--user", f"{uid}:{gid}",  # 以当前非 root 数字用户运行。
            "--cpus", str(self._config.cpus),  # 设置 CPU 配额。
            "--memory", f"{self._config.memory_mb}m",  # 设置内存上限。
            "--memory-swap", f"{self._config.memory_mb}m",  # 禁止额外 swap 扩大内存预算。
            "--pids-limit", str(self._config.pids_limit),  # 设置进程数量上限。
            "--tmpfs", f"/work:rw,exec,nosuid,size={self._config.workspace_mb}m,uid={uid},gid={gid},mode=0700",  # 建立私有可写工作区。
            "--tmpfs", f"/tmp:rw,exec,nosuid,size={self._config.temp_mb}m,uid={uid},gid={gid},mode=0700",  # 建立有上限临时目录。
            "--mount", f"type=bind,source={self._source},target=/source,readonly",  # 只读挂载宿主输入仓库。
            "--workdir", "/work",  # 在容器私有工作区启动。
            "--env", "HOME=/tmp",  # 避免容器程序访问宿主家目录。
            "--env", "PYTHONDONTWRITEBYTECODE=1",  # 减少源码目录缓存写入。
            self._config.image,  # 使用显式配置的运行镜像。
            "python", "-c", "import time; time.sleep(86400)",  # 保持任务容器在单次运行期间存活。
        )  # 完成 Docker run 参数构造。

    def _ensure_started(self) -> None:  # 验证运行时已准备完成。
        if not self._started or self._container_name is None:  # 检查启动和容器标识。
            raise RuntimeNotStartedError("DockerRuntime 尚未启动或已关闭")  # 返回生命周期错误。

    def _require_base(self) -> str:  # 读取已验证的基础提交。
        if self._base_commit is None:  # 防御启动前读取。
            raise RuntimeNotStartedError("base_commit 尚未验证")  # 返回明确状态错误。
        return self._base_commit  # 返回完整提交 SHA。

    def _require_resolver(self) -> WorkspacePathResolver:  # 读取任务路径解析器。
        if self._resolver is None:  # 防御启动前读取。
            raise RuntimeNotStartedError("路径策略尚未初始化")  # 返回明确状态错误。
        return self._resolver  # 返回启动时绑定的解析器。

    def _validate_patch_paths(self, patch: str) -> tuple[str, ...]:  # 检查补丁包含的宿主相对路径。
        resolver = self._require_resolver()  # 取得任务策略解析器。
        changed: list[str] = []  # 按补丁顺序记录受影响路径。
        headers = 0  # 统计统一 diff 文件头数量。
        for line in patch.splitlines():  # 扫描补丁每一行。
            if not line.startswith(("--- ", "+++ ")):  # 只读取旧文件与新文件头。
                continue  # 跳过普通代码内容。
            headers += 1  # 记录一个可识别文件头。
            path = line[4:].split("\t", 1)[0].strip()  # 去掉前缀和可选时间戳。
            if path == "/dev/null":  # 识别新增或删除文件的空设备标记。
                continue  # 同一文件块的另一文件头会提供真实路径。
            if path.startswith(("a/", "b/")):  # 识别标准 Git diff 前缀。
                path = path[2:]  # 还原工作区相对路径。
            if not path or path.startswith('"'):  # 拒绝无法可靠解析的引号转义路径。
                raise PathViolationError("暂不支持空路径或 Git 引号转义路径")  # 阻止策略绕过。
            resolved = resolver.resolve(path, write=True, must_exist=False)  # 执行允许、拒绝和只读策略。
            relative = resolver.relative_name(resolved)  # 得到规范化工作区相对名称。
            if relative not in changed:  # 避免普通修改重复记录路径。
                changed.append(relative)  # 保存本次受影响路径。
        if headers < 2 or not changed:  # 确保补丁包含完整文件头。
            raise PathViolationError("补丁没有可识别的完整目标文件头")  # 拒绝无法检查的补丁。
        return tuple(changed)  # 返回稳定不可变修改集合。

    async def _git(self, *arguments: str) -> CommandResult:  # 在容器内执行控制面 Git 命令。
        result = await self._container_call(("git", *arguments), timeout_seconds=30.0, output_limit=_CONTROL_OUTPUT_LIMIT)  # 运行 Git。
        self._require_success(result, "Git 控制命令")  # 将失败归类为基础设施错误。
        if result.output_truncated and arguments[0] != "diff":  # 控制面结果必须能完整解析。
            raise WorkspaceSafetyError("Git 控制命令输出超过上限")  # 禁止基于不完整状态继续运行。
        return result  # 返回命令原始结构化结果。

    @staticmethod  # 声明结果校验不读取实例状态。
    def _require_success(result: CommandResult, operation: str) -> None:  # 统一处理控制命令失败。
        if not result.succeeded:  # 检查退出码和超时状态。
            raise WorkspaceSafetyError(f"{operation}失败: {result.stderr or result.termination_reason}")  # 保留诊断。

    async def _container_call(  # 向当前任务容器提交一个命令。
        self,  # 接收当前 Runtime。
        command: tuple[str, ...],  # 接收不经 Shell 的容器内参数元组。
        *,  # 强制执行选项使用关键字。
        timeout_seconds: float,  # 设置单命令硬超时。
        input_text: str | None = None,  # 可选向容器命令传入标准输入。
        output_limit: int | None = None,  # 可选控制面输出预算。
        workdir: str = _REPOSITORY,  # 默认在仓库副本执行，初始化复制时改为 /work。
    ) -> CommandResult:  # 返回领域层命令结果。
        if self._container_name is None:  # 确认容器已创建。
            raise RuntimeNotStartedError("Docker 容器尚未创建")  # 拒绝无目标执行。
        prefix = ("exec", "-w", workdir)  # 固定容器工作目录为调用方允许的内部路径。
        if input_text is not None:  # 标准输入需要保持开放以发送 patch。
            prefix += ("-i",)  # 为 Docker exec 开启标准输入转发。
        docker_result = await self._docker_call(  # 调用宿主 Docker CLI。
            (*prefix, self._container_name, *command),  # 拼接容器 ID 与原始参数数组。
            timeout_seconds=timeout_seconds,  # 传递硬超时。
            input_text=input_text,  # 传递可选标准输入。
            output_limit=output_limit,  # 传递可选输出上限。
        )  # 完成 Docker exec 调用。
        if docker_result.timed_out:  # 任何容器命令超时都可能留下后台子进程。
            await self.close()  # 立即销毁容器，不允许继续使用不确定状态。
        return CommandResult(  # 把 Docker CLI 结果重新标记为容器命令结果。
            command=command,  # 对 Agent 展示容器内命令而不是 Docker CLI 实现细节。
            return_code=docker_result.return_code,  # 透传退出码。
            stdout=docker_result.stdout,  # 透传标准输出。
            stderr=docker_result.stderr,  # 透传错误输出。
            elapsed_seconds=docker_result.elapsed_seconds,  # 透传执行耗时。
            timed_out=docker_result.timed_out,  # 透传硬超时状态。
            output_truncated=docker_result.output_truncated,  # 透传输出截断标记。
            termination_reason=docker_result.termination_reason,  # 透传终止原因。
        )  # 完成结果归一化。

    async def _docker_call(  # 定义不经过 Shell 的 Docker CLI 执行器。
        self,  # 接收当前 Runtime。
        arguments: tuple[str, ...],  # 接收 Docker 子命令及其参数。
        *,  # 强制执行选项使用关键字。
        timeout_seconds: float,  # 设置 CLI 硬超时。
        input_text: str | None = None,  # 可选补丁标准输入。
        output_limit: int | None = None,  # 可选输出预算覆盖。
    ) -> CommandResult:  # 返回完整执行结果。
        if timeout_seconds <= 0:  # 拒绝无效超时。
            raise ValueError("timeout_seconds 必须大于零")  # 返回明确配置错误。
        command = ("docker", *arguments)  # 构造无需 Shell 解析的 Docker 参数元组。
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL"}}  # 仅保留 CLI 必需环境。
        started_at = time.perf_counter()  # 记录单调时钟起点。
        try:  # 捕获 Docker CLI 缺失等启动错误。
            process = await asyncio.create_subprocess_exec(  # 启动异步 Docker CLI 子进程。
                *command,  # 把每个参数独立交给操作系统。
                env=environment,  # 避免传入模型 API key 等环境变量。
                stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,  # 按需启用输入管道。
                stdout=asyncio.subprocess.PIPE,  # 捕获 CLI 标准输出。
                stderr=asyncio.subprocess.PIPE,  # 捕获 CLI 错误输出。
            )  # 完成 CLI 进程创建。
        except OSError as error:  # Docker 二进制可能不存在。
            return CommandResult(command, None, "", str(error), time.perf_counter() - started_at, termination_reason="process_start_failed")  # 返回结构化启动失败。
        limit = output_limit or self._config.max_output_chars  # 选择控制面或 Agent 观察的字符预算。
        stdout_buffer = OutputAccumulator(limit)  # 为标准输出分配固定上限的头尾缓存。
        stderr_buffer = OutputAccumulator(limit)  # 为标准错误分配独立固定上限的头尾缓存。
        stdout_task = asyncio.create_task(_drain_stream(process.stdout, stdout_buffer))  # 异步持续排空标准输出。
        stderr_task = asyncio.create_task(_drain_stream(process.stderr, stderr_buffer))  # 异步持续排空错误输出。

        async def send_input() -> None:  # 定义可选标准输入发送任务。
            if process.stdin is None or input_text is None:  # 没有输入管道时无需写入。
                return  # 直接结束可选发送任务。
            try:  # 容器命令可能在接收完整输入前退出。
                process.stdin.write(input_text.encode("utf-8"))  # 将补丁编码并写入 Docker stdin。
                await process.stdin.drain()  # 等待输入缓冲区被持续消费。
            except (BrokenPipeError, ConnectionResetError):  # 捕获容器进程提前退出。
                pass  # 后续依靠真实退出码和 stderr 报告失败。
            finally:  # 无论命令结果如何都关闭标准输入。
                process.stdin.close()  # 告知容器补丁输入已结束。

        input_task = asyncio.create_task(send_input())  # 与两个输出读取任务同时发送输入。
        try:  # 对完整通信施加硬超时。
            await asyncio.wait_for(  # 等待进程退出、输入结束与两个输出管道排空。
                asyncio.gather(process.wait(), stdout_task, stderr_task, input_task),  # 并发推进所有管道任务。
                timeout=timeout_seconds,  # 应用本次命令时限。
            )  # 完成通信等待。
        except TimeoutError:  # 捕获 Docker CLI 或容器命令超时。
            if process.returncode is None:  # 防御进程恰好在超时边界自行退出。
                process.kill()  # 立即终止本地 Docker CLI 进程。
            await process.wait()  # 回收本地子进程，避免僵尸进程。
            stdout, stdout_cut = stdout_buffer.finish()  # 提取超时前的有限标准输出。
            stderr, stderr_cut = stderr_buffer.finish()  # 提取超时前的有限错误输出。
            return CommandResult(command, process.returncode, stdout, stderr, time.perf_counter() - started_at, True, stdout_cut or stderr_cut, "timeout")  # 返回超时状态。
        except asyncio.CancelledError:  # 外层任务被取消时同样要清理宿主 CLI 进程。
            if process.returncode is None:  # 只终止仍在运行的进程。
                process.kill()  # 阻止取消后留下 Docker CLI 子进程。
            await process.wait()  # 回收已终止进程。
            raise  # 保留原始取消语义。
        stdout, stdout_cut = stdout_buffer.finish()  # 获取完整或受限的标准输出。
        stderr, stderr_cut = stderr_buffer.finish()  # 获取完整或受限的错误输出。
        return CommandResult(command, process.returncode, stdout, stderr, time.perf_counter() - started_at, output_truncated=stdout_cut or stderr_cut)  # 返回普通命令结果。
