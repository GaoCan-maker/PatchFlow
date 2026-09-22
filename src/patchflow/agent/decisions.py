"""第五周每个模型阶段的严格结构化输出协议。"""  # 无效回复不能进入 Runtime 或 Evidence Graph。

from __future__ import annotations  # 延迟解析 Pydantic 返回类型。

from enum import StrEnum  # 使用稳定字符串枚举限制反思路由。

from pydantic import BaseModel, ConfigDict, Field  # 校验模型生成的 JSON 字段。

from patchflow.memory.evidence import HypothesisStatus  # 复用图中的假设生命周期。


class StrictDecision(BaseModel):  # 为全部阶段共用严格模型边界。
    model_config = ConfigDict(extra="forbid", strict=True)  # 拒绝未知字段和隐式类型转换。


class IssueUnderstanding(StrictDecision):  # UNDERSTAND 阶段的可审计问题分解。
    summary: str = Field(min_length=5, max_length=500)  # 保存不丢约束的问题概要。
    expected: str = Field(min_length=3, max_length=500)  # 描述需求中的预期行为。
    actual: str = Field(min_length=3, max_length=500)  # 描述现状或明确写出尚未观测。
    constraints: list[str] = Field(default_factory=list, max_length=20)  # 列出 Issue 原文约束。
    unknowns: list[str] = Field(default_factory=list, max_length=20)  # 列出需要验证但尚未确认的未知项。
    reproduction_plan: str = Field(min_length=3, max_length=500)  # 说明将运行什么公开复现检查。


class RepairPlan(StrictDecision):  # PLAN 阶段给出有限、可证伪的单候选方案。
    hypothesis: str = Field(min_length=5, max_length=500)  # 描述待验证根因而非已确认事实。
    target_file: str = Field(min_length=1, max_length=1_000)  # 指向已索引的仓库文件。
    target_symbol: str = Field(min_length=1, max_length=500)  # 指向计划修改的 AST 符号。
    expected_change: str = Field(min_length=5, max_length=500)  # 说明为什么该修改应修复问题。
    regression_risk: str = Field(min_length=3, max_length=500)  # 说明可能破坏的行为。
    verification: str = Field(min_length=3, max_length=500)  # 说明将如何判断候选成功。
    abandon_if: str = Field(min_length=3, max_length=500)  # 给出明确放弃条件。


class PatchProposal(StrictDecision):  # GENERATE_CANDIDATES 阶段只接受标准补丁文本。
    patch: str = Field(min_length=20, max_length=500_000)  # 限制单次候选补丁大小。


class FailureKind(StrEnum):  # 反思应把失败分类到可执行原因。
    LOCALIZATION = "localization_error"  # 根因位置可能不对。
    PATCH = "patch_error"  # 修改本身错误或补丁无法应用。
    VERIFICATION = "insufficient_verification"  # 测试信号不足或未覆盖期望。
    ENVIRONMENT = "environment_error"  # 环境与依赖使验证不可解释。


class ReflectionAction(StrEnum):  # 限制下一阶段只能走状态机合法路由。
    RELOCALIZE = "relocalize"  # 重新结合失败反馈进行定位。
    REPLAN = "replan"  # 在现有定位结果上换根因或方案。
    REPAIR = "repair"  # 保留假设并生成不同补丁。
    STOP = "stop"  # 证据不足或预算原因停止。


class ReflectionDecision(StrictDecision):  # REFLECT 阶段输出基于测试证据的结构化决策。
    disconfirmed_expectation: str = Field(min_length=3, max_length=500)  # 指出哪项预期被失败推翻。
    new_fact: str = Field(min_length=3, max_length=500)  # 描述新证据，但仍保留模型推断来源。
    hypothesis_status: HypothesisStatus  # 更新待验证、部分支持、反驳或放弃状态。
    failure_kind: FailureKind  # 区分定位、补丁、验证与环境问题。
    next_action: ReflectionAction  # 选择重定位、重新计划、继续修补或终止。
    reason: str = Field(min_length=5, max_length=500)  # 解释本次路由的证据依据。
    repeated_attempt: bool  # 模型显式声明是否发现重复动作，系统仍会独立校验补丁摘要。
