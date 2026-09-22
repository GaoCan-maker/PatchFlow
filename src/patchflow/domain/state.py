"""Agent 当前状态和预算消耗。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from patchflow.domain.enums import AgentPhase, RunStatus
from patchflow.domain.task import Budget


class InvalidStateTransitionError(ValueError):
    """状态机发生不允许的阶段跳转。"""


@dataclass(slots=True)
class BudgetUsage:
    """记录一次运行中已经消耗的资源。"""

    agent_steps: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    command_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    cost_usd: float = 0.0

    def exceeded_items(self, budget: Budget) -> tuple[str, ...]:
        """返回已经达到或超过硬限制的预算名称。"""

        exceeded: list[str] = []
        comparisons = (
            ("agent_steps", self.agent_steps, budget.max_agent_steps),
            ("model_calls", self.model_calls, budget.max_model_calls),
            ("input_tokens", self.input_tokens, budget.max_input_tokens),
            ("output_tokens", self.output_tokens, budget.max_output_tokens),
            ("tool_calls", self.tool_calls, budget.max_tool_calls),
            ("command_seconds", self.command_seconds, budget.max_command_seconds),
            ("wall_clock_seconds", self.wall_clock_seconds, budget.max_wall_clock_seconds),
        )
        for name, used, limit in comparisons:
            if used >= limit:
                exceeded.append(name)
        if budget.max_cost_usd is not None and self.cost_usd >= budget.max_cost_usd:
            exceeded.append("cost_usd")
        return tuple(exceeded)


# 这里只约束高层流程；每个阶段内部的细粒度行为将在 Agent 模块中实现。
_ALLOWED_PHASE_TRANSITIONS: dict[AgentPhase, frozenset[AgentPhase]] = {
    AgentPhase.CREATED: frozenset({AgentPhase.INITIALIZE, AgentPhase.LINEAR_REACT, AgentPhase.ONE_SHOT, AgentPhase.CANCELLED}),  # 三种基线共享初始入口。
    AgentPhase.ONE_SHOT: frozenset({AgentPhase.COMPLETED, AgentPhase.FAILED, AgentPhase.CANCELLED}),  # 单轮基线只能提交或失败。
    AgentPhase.LINEAR_REACT: frozenset({AgentPhase.COMPLETED, AgentPhase.FAILED, AgentPhase.CANCELLED}),  # 基线只保留一个显式阶段。
    AgentPhase.INITIALIZE: frozenset(
        {AgentPhase.UNDERSTAND, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.UNDERSTAND: frozenset(
        {AgentPhase.REPRODUCE, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.REPRODUCE: frozenset(
        {AgentPhase.LOCALIZE, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.LOCALIZE: frozenset(
        {AgentPhase.PLAN, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.PLAN: frozenset(
        {AgentPhase.GENERATE_CANDIDATES, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.GENERATE_CANDIDATES: frozenset(
        {AgentPhase.VERIFY_CANDIDATES, AgentPhase.REFLECT, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.VERIFY_CANDIDATES: frozenset(
        {
            AgentPhase.SELECT_AND_FINALIZE,
            AgentPhase.REFLECT,
            AgentPhase.FAILED,
            AgentPhase.CANCELLED,
        }
    ),
    AgentPhase.REFLECT: frozenset(
        {
            AgentPhase.LOCALIZE,
            AgentPhase.PLAN,
            AgentPhase.GENERATE_CANDIDATES,
            AgentPhase.FAILED,
            AgentPhase.CANCELLED,
        }
    ),
    AgentPhase.SELECT_AND_FINALIZE: frozenset(
        {AgentPhase.COMPLETED, AgentPhase.FAILED, AgentPhase.CANCELLED}
    ),
    AgentPhase.COMPLETED: frozenset(),
    AgentPhase.FAILED: frozenset(),
    AgentPhase.CANCELLED: frozenset(),
}


@dataclass(slots=True)
class AgentState:
    """由事件折叠得到的可恢复当前状态。"""

    run_id: str
    task_id: str
    phase: AgentPhase = AgentPhase.CREATED
    status: RunStatus = RunStatus.PENDING
    plan: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    main_hypothesis: str | None = None
    alternative_hypotheses: list[str] = field(default_factory=list)
    visited_files: set[str] = field(default_factory=set)
    visited_symbols: set[str] = field(default_factory=set)
    executed_tests: set[str] = field(default_factory=set)
    candidate_ids: list[str] = field(default_factory=list)
    selected_candidate_id: str | None = None
    last_failure: str | None = None
    evidence_graph_version: int = 0
    stop_reason: str | None = None
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, next_phase: AgentPhase) -> None:
        """执行经过校验的高层阶段迁移。"""

        allowed = _ALLOWED_PHASE_TRANSITIONS[self.phase]
        if next_phase not in allowed:
            raise InvalidStateTransitionError(
                f"不允许从阶段 {self.phase.value} 跳转到 {next_phase.value}"
            )
        self.phase = next_phase
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        """判断状态机是否已经进入终止阶段。"""

        return self.phase in {
            AgentPhase.COMPLETED,
            AgentPhase.FAILED,
            AgentPhase.CANCELLED,
        }
