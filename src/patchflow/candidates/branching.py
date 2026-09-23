"""有限宽度候选搜索、并发验证和确定性选择。"""  # 说明本模块把候选工作区与验证器连接起来。

from __future__ import annotations  # 允许延迟解析类型标注。

import asyncio  # 管理候选验证并发和取消。
import json  # 保存机器可读候选报告。
from collections.abc import Callable  # 标注 Runtime 工厂函数。
from dataclasses import dataclass  # 保存候选结果和排序结论。
from pathlib import Path  # 处理候选隔离根和报告目录。

from patchflow.candidates.workspaces import (  # 导入候选工作区创建与摘要函数。
    CandidateWorkspace,  # 标注一个独立候选目录。
    CandidateWorkspaceManager,  # 创建同一基础提交的候选副本。
)  # 完成工作区依赖导入。
from patchflow.domain.runtime import Runtime  # 限定候选只能通过 Runtime 执行。
from patchflow.domain.task import TaskSpec  # 读取任务基础提交和仓库位置。
from patchflow.verification.pyramid import (  # 导入验证金字塔及其结果。
    CandidateVerification,  # 保存一个候选的逐层验证结果。
    VerificationPlan,  # 描述候选需要执行的命令。
    VerificationPyramid,  # 逐层验证候选。
)  # 完成验证依赖导入。

RuntimeFactory = Callable[[Path, Path], Runtime]  # 定义根据候选路径创建 Runtime 的工厂协议。


@dataclass(frozen=True, slots=True)
class BranchingConfig:  # 保存第六周候选搜索的硬上限。
    max_candidates: int = 4  # 限制单轮最多创建的候选数量。
    max_concurrency: int = 2  # 限制同时运行的候选验证数量。
    command_timeout_seconds: float = 300.0  # 限制单个验证命令耗时。
    keep_workspaces: bool = False  # 默认验证结束后删除候选源码副本。

    def __post_init__(self) -> None:  # 对不可变配置执行运行时校验。
        if self.max_candidates < 1:  # 检查候选数量下限。
            raise ValueError("max_candidates 必须大于零")  # 拒绝空搜索宽度。
        if self.max_concurrency < 1:  # 检查并发数下限。
            raise ValueError("max_concurrency 必须大于零")  # 拒绝无法执行的调度配置。
        if self.command_timeout_seconds <= 0:  # 检查命令超时有效性。
            raise ValueError("command_timeout_seconds 必须大于零")  # 拒绝无限命令。


@dataclass(frozen=True, slots=True)
class CandidateBranchResult:  # 保存一个候选分支的完整结果。
    candidate_id: str  # 保存候选身份。
    patch_digest: str  # 保存补丁摘要。
    workspace: Path  # 保存候选工作区路径以支持调试和报告。
    verification: CandidateVerification | None  # 保存验证报告或启动前空值。
    error: str | None = None  # 保存不泄露密钥的基础设施错误类型。
    verified_patch: str | None = None  # 保存工作区清理前导出的真实 Git diff。

    @property  # 提供排序器需要的通过状态。
    def passed(self) -> bool:  # 返回候选是否通过确定性验证。
        return self.verification is not None and self.verification.passed and self.error is None and bool(self.verified_patch)  # 只有完整报告和真实 diff 才算成功。

    def summary(self) -> dict[str, object]:  # 返回可写入 JSON 的候选摘要。
        verification = self.verification  # 保存局部引用减少重复属性访问。
        return {  # 返回不包含完整 stdout 的有限报告。
            "candidate_id": self.candidate_id,  # 保存候选身份。
            "patch_digest": self.patch_digest,  # 保存去重摘要。
            "workspace": str(self.workspace),  # 保存调试路径。
            "passed": self.passed,  # 保存最终候选结论。
            "error": self.error,  # 保存基础设施错误分类。
            "hard_failure": verification.hard_failure if verification else True,  # 保存硬拒绝标记。
            "failed_level": verification.failed_level.value if verification and verification.failed_level else None,  # 保存失败层级。
            "changed_files": list(verification.changed_files) if verification else [],  # 保存实际修改文件。
            "steps": [  # 保存每个验证命令的短结果。
                {  # 构造一个验证步骤摘要。
                    "level": step.level.value,  # 保存验证层级。
                    "command": list(step.command),  # 保存实际参数数组。
                    "passed": step.passed,  # 保存命令结果。
                    "elapsed_seconds": step.elapsed_seconds,  # 保存命令耗时。
                    "return_code": step.return_code,  # 保存退出码。
                    "reason": step.reason,  # 保存稳定原因。
                }  # 完成验证步骤摘要。
                for step in verification.steps  # 遍历当前候选的验证步骤。
            ] if verification else [],  # 没有验证报告时输出空步骤。
        }  # 完成候选 JSON 摘要。


@dataclass(frozen=True, slots=True)
class CandidateSelection:  # 保存确定性候选选择结论。
    selected_candidate_id: str | None  # 保存选中候选或无合格候选。
    reason: str  # 保存选择或拒绝原因。
    ranked_candidate_ids: tuple[str, ...]  # 保存所有候选的确定性排序。


class CandidateBranchingEngine:  # 实现第六周有限宽度候选搜索。
    def __init__(self, *, config: BranchingConfig | None = None, pyramid: VerificationPyramid | None = None) -> None:  # 注入搜索上限和验证器。
        self._config = config or BranchingConfig()  # 使用保守默认候选配置。
        self._pyramid = pyramid or VerificationPyramid(command_timeout_seconds=self._config.command_timeout_seconds)  # 创建同样受限的验证器。

    async def run(  # 创建、并发验证和选择候选。
        self,  # 接收当前引擎实例。
        task: TaskSpec,  # 接收基础任务定义。
        patches: tuple[str, ...] | list[str],  # 接收同一轮模型生成的候选补丁。
        *,  # 强制调度和报告参数使用关键字。
        isolation_root: Path,  # 接收候选工作区父目录。
        runtime_factory: RuntimeFactory,  # 接收候选 Runtime 创建函数。
        plan: VerificationPlan | None = None,  # 接收可选的验证命令计划。
        report_path: Path | None = None,  # 接收可选候选报告路径。
    ) -> tuple[tuple[CandidateBranchResult, ...], CandidateSelection]:  # 返回候选结果和选择结论。
        verification_plan = plan or VerificationPlan.from_task(task)  # 没有显式计划时使用任务公开命令。
        manager = CandidateWorkspaceManager(Path(task.repo_spec.location), isolation_root, max_candidates=self._config.max_candidates)  # 为本轮候选创建独立副本管理器。
        workspaces = await manager.create_many(tuple(patches), base_commit=task.base_commit)  # 先去重再从同一提交创建副本。
        semaphore = asyncio.Semaphore(self._config.max_concurrency)  # 限制同时占用 Runtime 和测试资源的候选数。

        async def evaluate(workspace: CandidateWorkspace) -> CandidateBranchResult:  # 定义单候选验证任务。
            async with semaphore:  # 获取候选级并发槽。
                candidate_task = workspace.task_for(task)  # 将 Runtime 绑定到当前候选仓库。
                runtime: Runtime | None = None  # 在工厂失败时仍能生成候选级错误报告。
                try:  # 确保每个候选的 Runtime 都会释放。
                    runtime = runtime_factory(workspace.path, isolation_root)  # 为当前候选创建独立 Runtime。
                    await runtime.start(candidate_task)  # 检查候选 HEAD、路径策略和初始干净状态。
                    verification = await self._pyramid.verify(  # 在当前候选工作区执行验证金字塔。
                        runtime,  # 传递候选专属 Runtime。
                        candidate_id=workspace.candidate_id,  # 绑定候选身份。
                        patch=workspace.patch,  # 传递当前候选补丁。
                        patch_digest=workspace.patch_digest,  # 传递去重摘要。
                        plan=verification_plan,  # 传递层级命令计划。
                    )  # 完成当前候选验证。
                    exported = await runtime.get_diff() if verification.passed else None  # 在关闭工作区之前导出真实补丁。
                    return CandidateBranchResult(workspace.candidate_id, workspace.patch_digest, workspace.path, verification, verified_patch=exported)  # 返回确定性验证报告和真实 diff。
                except Exception as error:  # 把单候选环境错误隔离在当前候选。
                    return CandidateBranchResult(workspace.candidate_id, workspace.patch_digest, workspace.path, None, type(error).__name__)  # 其他候选仍可继续验证。
                finally:  # 无论验证成功或失败都关闭 Runtime。
                    try:  # 将清理异常也限制在当前候选。
                        if runtime is not None:  # 仅关闭已经创建的 Runtime。
                            await runtime.close()  # 释放进程、容器和文件句柄。
                    except Exception:  # 清理失败不应阻塞其他候选报告。
                        pass  # 候选工作区最终仍由管理器统一清理。

        try:  # 确保所有候选报告写完后再清理工作区。
            results = tuple(await asyncio.gather(*(evaluate(item) for item in workspaces)))  # 并发验证所有候选并保持输入顺序。
            selection = self.select(results)  # 使用硬约束优先的确定性规则选择候选。
            if report_path is not None:  # 调用方需要报告时才写磁盘。
                report_path.parent.mkdir(parents=True, exist_ok=True)  # 创建报告目录。
                report_path.write_text(json.dumps({"selection": {"selected_candidate_id": selection.selected_candidate_id, "reason": selection.reason, "ranked_candidate_ids": list(selection.ranked_candidate_ids)}, "candidates": [item.summary() for item in results]}, ensure_ascii=False, indent=2), encoding="utf-8")  # 保存有限 JSON 报告。
            return results, selection  # 返回完整候选结果和选择结论。
        finally:  # 默认清理源码副本防止磁盘和状态泄漏。
            if not self._config.keep_workspaces:  # 调试模式可以保留候选目录。
                await manager.cleanup()  # 删除所有本轮候选工作区。

    def select(self, results: tuple[CandidateBranchResult, ...]) -> CandidateSelection:  # 根据确定性验证结果排序并选择候选。
        ranked = tuple(sorted(results, key=self._sort_key))  # 先对所有候选生成稳定排序。
        ranked_ids = tuple(item.candidate_id for item in ranked)  # 只向调用方暴露候选身份顺序。
        valid = [item for item in ranked if item.passed]  # 硬过滤所有未完整通过的候选。
        if not valid:  # 没有候选通过充分验证时必须明确失败。
            return CandidateSelection(None, "no_candidate_passed_required_verification", ranked_ids)  # 不选择最不差候选。
        return CandidateSelection(valid[0].candidate_id, "deterministic_verification_rank", ranked_ids)  # 选择排序最优的确定性通过候选。

    def _sort_key(self, result: CandidateBranchResult) -> tuple[int, int, float, str]:  # 构造稳定的候选排序键。
        if not result.passed:  # 失败候选必须排在通过候选之后。
            return (1, 0, float("inf"), result.candidate_id)  # 失败状态不能被风险分数洗白。
        verification = result.verification  # 成功候选一定存在验证报告。
        assert verification is not None  # 用断言帮助类型检查器理解前置条件。
        return (0, len(verification.changed_files), verification.total_elapsed_seconds, result.candidate_id)  # 优先少改文件、低成本且 ID 稳定的候选。
