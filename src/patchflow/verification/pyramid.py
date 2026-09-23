"""按从低成本到高成本顺序验证候选补丁。"""  # 说明本模块实现验证金字塔。

from __future__ import annotations  # 允许延迟解析类型标注。

import time  # 记录每个验证层级的耗时。
from dataclasses import dataclass  # 保存不可变计划和报告。
from enum import StrEnum  # 使用可序列化的字符串枚举。

from patchflow.domain.runtime import CommandResult, Runtime  # 依赖统一 Runtime 协议。
from patchflow.domain.task import TaskSpec  # 从任务中读取公开测试命令。


class VerificationLevel(StrEnum):  # 定义由低到高的验证层级。
    V0_PATCH = "v0_patch"  # 检查补丁格式、路径和应用结果。
    V1_SYNTAX = "v1_syntax"  # 检查修改后的 Python 文件是否可解析。
    V2_STATIC = "v2_static"  # 执行可选的静态检查命令。
    V3_REPRODUCTION = "v3_reproduction"  # 执行最小复现命令。
    V4_TARGET = "v4_target"  # 执行与问题相关的目标测试。
    V5_REGRESSION = "v5_regression"  # 执行更大范围回归命令。


@dataclass(frozen=True, slots=True)
class VerificationPlan:  # 保存一个候选需要经过的命令层级。
    python_executable: str = "python"  # 保存 V1 语法检查使用的解释器路径或命令名。
    allowed_files: frozenset[str] | None = None  # 可选限制候选只能修改计划中的文件。
    static_commands: tuple[str, ...] = ()  # 保存可选 lint、类型或静态分析命令。
    reproduction_commands: tuple[str, ...] = ()  # 保存可选最小复现命令。
    target_commands: tuple[str, ...] = ()  # 保存 Issue 指定或定位得到的目标测试。
    regression_commands: tuple[str, ...] = ()  # 保存更大范围回归测试。
    require_target: bool = True  # 默认要求至少有一个目标验证命令。

    @classmethod  # 提供从统一任务生成默认计划的工厂方法。
    def from_task(cls, task: TaskSpec, *, python_executable: str = "python", allowed_files: frozenset[str] | None = None, reproduction_commands: tuple[str, ...] = (), regression_commands: tuple[str, ...] = ()) -> VerificationPlan:  # 将公开命令放入目标测试层。
        return cls(  # 构造不猜测额外静态检查的保守计划。
            python_executable=python_executable,  # 使用调用方明确选择的解释器。
            allowed_files=allowed_files,  # 传递候选实际可修改的文件集合。
            reproduction_commands=reproduction_commands,  # 使用调用方明确提供的最小复现命令。
            target_commands=task.public_commands,  # 把任务公开命令作为默认目标测试。
            regression_commands=regression_commands,  # 使用调用方明确提供的大范围回归命令。
            require_target=True,  # 没有公开命令时仍要求目标验证并拒绝虚构成功。
        )  # 返回验证计划。


@dataclass(frozen=True, slots=True)
class VerificationStep:  # 保存单个层级或命令的执行结果。
    level: VerificationLevel  # 保存验证层级。
    command: tuple[str, ...]  # 保存不经 Shell 的实际参数数组。
    passed: bool  # 保存该命令是否通过。
    elapsed_seconds: float  # 保存命令实际耗时。
    return_code: int | None  # 保存进程退出码。
    reason: str  # 保存可读且稳定的结果原因。
    result: CommandResult | None = None  # 可选保留完整 Runtime 观察供上层审计。


@dataclass(frozen=True, slots=True)
class CandidateVerification:  # 保存单个候选的全部验证信息。
    candidate_id: str  # 保存候选标识，防止结果串线。
    patch_digest: str  # 保存候选补丁摘要。
    applied: bool  # 保存 V0 补丁是否应用成功。
    passed: bool  # 保存候选是否通过所有必需层级。
    hard_failure: bool  # 保存是否触发不可接受的硬拒绝条件。
    failed_level: VerificationLevel | None  # 保存首个失败层级。
    changed_files: tuple[str, ...]  # 保存补丁实际修改文件。
    steps: tuple[VerificationStep, ...] = ()  # 保存按执行顺序排列的层级结果。
    total_elapsed_seconds: float = 0.0  # 保存候选验证总耗时。
    workspace_clean_after_verification: bool = True  # 保存测试是否污染候选工作区。


class VerificationPyramid:  # 在一个独立 Runtime 中验证一个候选。
    def __init__(self, *, command_timeout_seconds: float = 300.0, max_steps: int = 64) -> None:  # 配置单命令超时和层级数量。
        if command_timeout_seconds <= 0:  # 检查命令超时必须为正数。
            raise ValueError("command_timeout_seconds 必须大于零")  # 拒绝无效验证配置。
        if max_steps < 1:  # 检查单候选命令数量上限。
            raise ValueError("max_steps 必须大于零")  # 防止无限验证。
        self._command_timeout_seconds = command_timeout_seconds  # 保存单命令超时。
        self._max_steps = max_steps  # 保存验证动作上限。

    async def verify(self, runtime: Runtime, *, candidate_id: str, patch: str, patch_digest: str, plan: VerificationPlan) -> CandidateVerification:  # 依次执行候选验证。
        started = time.perf_counter()  # 记录候选验证开始时间。
        applied_result = await runtime.apply_patch(patch)  # 首先执行 V0 补丁路径和可应用性检查。
        v0 = VerificationStep(  # 把补丁应用结果统一包装为验证步骤。
            level=VerificationLevel.V0_PATCH,  # 标记当前为补丁应用层。
            command=("git", "apply"),  # 用抽象控制命令表示 Runtime 内部补丁动作。
            passed=applied_result.applied,  # 只有 Runtime 报告成功应用才通过。
            elapsed_seconds=0.0,  # PatchResult 当前不单独暴露耗时。
            return_code=0 if applied_result.applied else 1,  # 映射为稳定的二值退出码。
            reason="patch_applied" if applied_result.applied else (applied_result.rejection_reason or "patch_rejected"),  # 保存拒绝原因。
            result=None,  # 补丁动作没有 CommandResult 对象。
        )  # 完成 V0 步骤。
        if not applied_result.applied:  # 补丁无法应用时立即硬拒绝。
            return CandidateVerification(candidate_id, patch_digest, False, False, True, VerificationLevel.V0_PATCH, applied_result.changed_files, (v0,), time.perf_counter() - started, True)  # 不执行后续命令。
        if plan.allowed_files is not None and set(applied_result.changed_files) != set(plan.allowed_files):  # 检查实际修改范围与计划一致。
            rejected = VerificationStep(VerificationLevel.V0_PATCH, ("git", "diff", "--name-only"), False, 0.0, 1, "unexpected_modified_files")  # 保存范围违规原因。
            return CandidateVerification(candidate_id, patch_digest, True, False, True, VerificationLevel.V0_PATCH, applied_result.changed_files, (v0, rejected), time.perf_counter() - started, True)  # 直接淘汰越界候选。
        expected_diff = await runtime.get_diff()  # 保存应用补丁后的真实差异基线。
        steps: list[VerificationStep] = [v0]  # 按顺序保存已经完成的验证步骤。
        changed_files = applied_result.changed_files  # 保存 Runtime 校验过的修改文件。
        syntax_commands = self._syntax_commands(changed_files, plan.python_executable)  # 为 Python 修改文件构造低成本语法检查。
        command_groups = (  # 定义验证金字塔的执行顺序。
            (VerificationLevel.V1_SYNTAX, syntax_commands),  # 先执行语法解析。
            (VerificationLevel.V2_STATIC, tuple(self._split_commands(plan.static_commands))),  # 再执行可选静态检查。
            (VerificationLevel.V3_REPRODUCTION, tuple(self._split_commands(plan.reproduction_commands))),  # 再执行最小复现。
            (VerificationLevel.V4_TARGET, tuple(self._split_commands(plan.target_commands))),  # 再执行目标测试。
            (VerificationLevel.V5_REGRESSION, tuple(self._split_commands(plan.regression_commands))),  # 最后执行更大回归。
        )  # 完成验证层级定义。
        executed = 0  # 统计已经启动的验证命令数量。
        for level, commands in command_groups:  # 按低成本到高成本顺序处理每一层。
            if level is VerificationLevel.V4_TARGET and plan.require_target and not commands:  # 缺少目标测试时不能报告通过。
                return self._failed_report(candidate_id, patch_digest, changed_files, steps, started, level, "missing_target_commands")  # 返回明确的硬失败。
            for command in commands:  # 逐个执行当前层级的命令。
                if executed >= self._max_steps:  # 检查验证动作硬上限。
                    return self._failed_report(candidate_id, patch_digest, changed_files, steps, started, level, "verification_steps")  # 防止无限命令。
                executed += 1  # 记录一次即将发生的 Runtime 动作。
                result = await runtime.execute(command, timeout_seconds=self._command_timeout_seconds)  # 在独立候选工作区执行命令。
                passed = result.succeeded  # 超时、非零退出和启动失败都不通过。
                steps.append(self._step(level, command, result, passed))  # 将观察绑定到当前候选报告。
                if not passed:  # 任何语法、目标或回归失败都淘汰当前候选。
                    return self._failed_report(candidate_id, patch_digest, changed_files, steps, started, level, self._failure_reason(result))  # 立即早停节省资源。
        final_diff = await runtime.get_diff()  # 在所有测试结束后重新读取实际工作区差异。
        clean = final_diff == expected_diff  # 差异变化说明测试过程污染了候选工作区。
        if not clean:  # 测试副作用是硬拒绝条件。
            return CandidateVerification(candidate_id, patch_digest, True, False, True, VerificationLevel.V4_TARGET, changed_files, tuple(steps), time.perf_counter() - started, False)  # 拒绝被污染的候选。
        return CandidateVerification(candidate_id, patch_digest, True, True, False, None, changed_files, tuple(steps), time.perf_counter() - started, True)  # 返回全部验证通过的候选。

    def _syntax_commands(self, changed_files: tuple[str, ...], python_executable: str) -> tuple[tuple[str, ...], ...]:  # 为修改的 Python 文件构造语法命令。
        return tuple((python_executable, "-m", "py_compile", path) for path in changed_files if path.endswith(".py"))  # 非 Python 文件不执行 Python 语法检查。

    def _split_commands(self, commands: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:  # 将公开命令转换为参数数组。
        import shlex  # 局部导入避免验证模块导入时产生无关副作用。
        parsed: list[tuple[str, ...]] = []  # 保存已经解析的命令数组。
        for command in commands:  # 按任务顺序解析命令。
            parts = tuple(shlex.split(command))  # 使用标准参数解析而不构造 Shell 字符串。
            if parts:  # 忽略空命令但不让它启动进程。
                parsed.append(parts)  # 保存非空命令。
        return tuple(parsed)  # 返回不可变命令组。

    def _step(self, level: VerificationLevel, command: tuple[str, ...], result: CommandResult, passed: bool) -> VerificationStep:  # 将 Runtime 结果转换为报告步骤。
        return VerificationStep(level, command, passed, result.elapsed_seconds, result.return_code, "passed" if passed else self._failure_reason(result), result)  # 返回可审计步骤。

    def _failure_reason(self, result: CommandResult) -> str:  # 将命令失败归类为稳定原因。
        if result.timed_out:  # 优先识别超时。
            return "command_timeout"  # 返回超时分类。
        if result.return_code is None:  # 识别进程无法启动。
            return "command_start_failed"  # 返回基础设施分类。
        return "command_failed"  # 其他非零退出归为命令失败。

    def _failed_report(self, candidate_id: str, digest: str, files: tuple[str, ...], steps: list[VerificationStep], started: float, level: VerificationLevel, reason: str) -> CandidateVerification:  # 构造统一硬失败报告。
        return CandidateVerification(candidate_id, digest, level is not VerificationLevel.V0_PATCH, False, True, level, files, tuple(steps), time.perf_counter() - started, True)  # 保存首个失败层级和原因所在步骤。
