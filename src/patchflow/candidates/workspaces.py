"""从同一基础提交创建互不污染的候选 Git 工作区。"""  # 说明本模块的隔离边界。

from __future__ import annotations  # 允许在定义前引用类型。

import asyncio  # 使用异步子进程和候选创建并发。
import hashlib  # 为候选补丁生成稳定摘要。
import shutil  # 清理创建失败的候选目录。
from dataclasses import dataclass  # 保存不可变候选工作区信息。
from pathlib import Path  # 使用跨平台路径对象。

from patchflow.domain.task import TaskSpec  # 将仓库位置替换为候选副本位置。


class CandidateWorkspaceError(RuntimeError):  # 表示候选工作区无法安全建立。
    """候选源仓库、克隆或清理过程出现错误时抛出。"""  # 说明异常的调用方含义。


@dataclass(frozen=True, slots=True)  # 防止工作区描述在调度后被意外修改。
class CandidateWorkspace:  # 表示一个独立可变候选目录。
    candidate_id: str  # 保存稳定候选标识。
    path: Path  # 保存候选 Git 仓库绝对路径。
    patch: str  # 保存候选原始统一 diff。
    patch_digest: str  # 保存用于去重和审计的补丁摘要。

    def task_for(self, task: TaskSpec) -> TaskSpec:  # 构造指向本候选仓库的任务副本。
        repository = task.repo_spec.model_copy(update={"location": str(self.path)})  # 只替换仓库位置，保留任务其他约束。
        return task.model_copy(update={"repo_spec": repository})  # 返回不可变任务的新实例。


def normalize_patch(patch: str) -> str:  # 规范化补丁文本以识别表面差异下的重复候选。
    lines = [line.rstrip() for line in patch.strip().splitlines()]  # 去掉尾部空格但保留补丁结构。
    return "\n".join(lines) + ("\n" if lines else "")  # 统一换行结尾以便稳定哈希。


def patch_digest(patch: str) -> str:  # 计算候选补丁的不可逆稳定摘要。
    normalized = normalize_patch(patch).encode("utf-8")  # 使用规范化后的 UTF-8 字节作为摘要输入。
    return hashlib.sha256(normalized).hexdigest()  # 返回 SHA-256 十六进制字符串。


class CandidateWorkspaceManager:  # 管理同一基础提交上的独立候选目录。
    def __init__(self, source_repository: Path, isolation_root: Path, *, max_candidates: int = 4) -> None:  # 接收源仓库和隔离根。
        self._source = source_repository.resolve(strict=True)  # 保存源仓库真实路径。
        self._root = isolation_root.resolve()  # 保存所有候选必须位于的根目录。
        self._max_candidates = max_candidates  # 保存单轮候选数量上限。
        if not self._source.is_dir():  # 拒绝把普通文件作为源仓库。
            raise CandidateWorkspaceError("候选源必须是目录")  # 返回清晰的输入错误。
        if self._source == self._root:  # 防止候选目录覆盖源仓库。
            raise CandidateWorkspaceError("候选隔离根不能等于源仓库")  # 阻止破坏源仓库。
        if self._root.is_relative_to(self._source):  # 候选目录不能写入源仓库内部。
            raise CandidateWorkspaceError("候选隔离根不能位于源仓库内部")  # 避免污染源仓库和 Git 状态。
        if max_candidates < 1:  # 验证候选数量下限。
            raise ValueError("max_candidates 必须大于零")  # 拒绝无候选配置。
        self._root.mkdir(parents=True, exist_ok=True)  # 创建候选目录的父目录。
        self._workspaces: list[CandidateWorkspace] = []  # 保存已成功创建的候选。

    @property  # 暴露只读的成功候选列表。
    def workspaces(self) -> tuple[CandidateWorkspace, ...]:  # 返回不可变工作区快照。
        return tuple(self._workspaces)  # 防止调用方修改内部列表。

    async def create_many(self, patches: tuple[str, ...] | list[str], *, base_commit: str) -> tuple[CandidateWorkspace, ...]:  # 从统一基础提交创建候选。
        unique: list[tuple[str, str, str]] = []  # 保存候选 ID、补丁和摘要三元组。
        seen: set[str] = set()  # 保存已遇到的规范化补丁摘要。
        for patch in patches:  # 按模型生成顺序检查候选补丁。
            digest = patch_digest(patch)  # 计算当前补丁的规范化摘要。
            if not patch.strip() or digest in seen:  # 拒绝空补丁并去掉重复补丁。
                continue  # 不为无效或重复候选创建工作区。
            seen.add(digest)  # 记录当前摘要避免后续重复。
            unique.append((f"candidate-{digest[:12]}", patch, digest))  # 生成可读且稳定的候选 ID。
        if not unique:  # 没有可用候选时返回空元组。
            return ()  # 让上层将空候选分类为策略失败。
        if len(unique) > self._max_candidates:  # 检查候选宽度硬上限。
            raise CandidateWorkspaceError("候选数量超过本轮上限")  # 防止隐式扩大模型和测试成本。
        await self._validate_source(base_commit)  # 在复制前验证源仓库是干净基础提交。
        semaphore = asyncio.Semaphore(min(len(unique), self._max_candidates))  # 限制克隆同时占用的资源。

        async def create_one(item: tuple[str, str, str]) -> CandidateWorkspace:  # 定义单候选创建任务。
            async with semaphore:  # 在候选创建期间占用一个并发槽。
                return await self._create_one(*item, base_commit=base_commit)  # 创建独立 Git 副本。

        try:  # 确保部分创建失败时清理全部候选。
            created = await asyncio.gather(*(create_one(item) for item in unique))  # 并发创建各个候选目录。
        except BaseException:  # 捕获取消和普通异常避免残留目录。
            await self.cleanup()  # 删除本次已经创建的候选。
            raise  # 保留原始错误供上层分类。
        self._workspaces.extend(created)  # 记录成功创建的候选工作区。
        return tuple(created)  # 返回稳定创建顺序的候选描述。

    async def _validate_source(self, base_commit: str) -> None:  # 验证源仓库与任务基础提交一致。
        root = await self._git("-C", str(self._source), "rev-parse", "--show-toplevel")  # 查询源仓库根目录。
        if root.strip() != str(self._source):  # 确认传入路径就是仓库根目录。
            raise CandidateWorkspaceError("候选源必须是 Git 仓库根目录")  # 拒绝隐式父仓库。
        head = await self._git("-C", str(self._source), "rev-parse", "HEAD")  # 查询源仓库当前提交。
        resolved = await self._git("-C", str(self._source), "rev-parse", "--verify", f"{base_commit}^{{commit}}")  # 解析任务基础提交。
        status = await self._git("-C", str(self._source), "status", "--porcelain=v1", "--untracked-files=all")  # 查询源仓库修改。
        if head.strip() != resolved.strip():  # 要求源仓库已经停在基础提交。
            raise CandidateWorkspaceError("候选源 HEAD 与 base_commit 不一致")  # 阻止候选从错误提交分叉。
        if status.strip():  # 要求源仓库完全干净。
            raise CandidateWorkspaceError("候选源必须是干净 Git 工作区")  # 防止复制用户未提交修改。

    async def _create_one(self, candidate_id: str, patch: str, digest: str, *, base_commit: str) -> CandidateWorkspace:  # 创建一个候选副本。
        path = (self._root / candidate_id).resolve()  # 计算候选的绝对目标路径。
        if not path.is_relative_to(self._root):  # 额外确认候选 ID 没有越界能力。
            raise CandidateWorkspaceError("候选路径越出隔离根")  # 拒绝路径穿越。
        if path.exists():  # 不覆盖已有候选目录或用户文件。
            raise CandidateWorkspaceError(f"候选工作区已存在: {candidate_id}")  # 要求每轮使用全新隔离根。
        try:  # 在候选副本中检出精确基础提交。
            await self._git("clone", "--local", "--no-hardlinks", "--no-checkout", str(self._source), str(path))  # 使用独立 Git 对象副本避免共享可变工作区。
            await self._git("-C", str(path), "checkout", "--detach", base_commit)  # 让每个候选从相同提交开始。
            status = await self._git("-C", str(path), "status", "--porcelain=v1", "--untracked-files=all")  # 验证克隆后没有额外修改。
            if status.strip():  # 克隆后的工作区理论上必须干净。
                raise CandidateWorkspaceError(f"候选 {candidate_id} 克隆后不干净")  # 阻止不确定状态进入 Runtime。
        except BaseException:  # 创建后续步骤失败时清理当前目录。
            await asyncio.to_thread(shutil.rmtree, path, True)  # 在线程中删除目录避免阻塞事件循环。
            raise  # 保留原始错误。
        return CandidateWorkspace(candidate_id, path, patch, digest)  # 返回独立候选工作区描述。

    async def cleanup(self) -> None:  # 删除当前管理器创建的候选目录。
        paths = tuple(item.path for item in self._workspaces)  # 复制路径列表避免清理过程中修改迭代对象。
        self._workspaces.clear()  # 先清空内部状态避免重复清理。
        for path in paths:  # 逐个处理候选目录。
            if path.exists() and path.is_relative_to(self._root):  # 只删除本管理器隔离根内的路径。
                await asyncio.to_thread(shutil.rmtree, path, True)  # 使用线程删除大量 Git 文件。

    async def _git(self, *command: str) -> str:  # 执行不经过 Shell 的 Git 命令。
        process = await asyncio.create_subprocess_exec(  # 启动参数数组形式的 Git 子进程。
            "git",  # 固定使用 Git 可执行文件。
            *command,  # 传入调用方已经拆分的参数。
            stdout=asyncio.subprocess.PIPE,  # 捕获标准输出用于校验。
            stderr=asyncio.subprocess.PIPE,  # 捕获错误输出用于诊断。
        )  # 完成子进程创建。
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)  # 给克隆和提交操作施加硬超时。
        if process.returncode != 0:  # 检查 Git 是否成功结束。
            detail = stderr.decode("utf-8", errors="replace").strip()  # 解码有限错误信息。
            raise CandidateWorkspaceError(f"Git 命令失败：{detail}")  # 将失败提升为候选工作区错误。
        return stdout.decode("utf-8", errors="replace")  # 返回解码后的标准输出。
