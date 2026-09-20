"""AgentState 与预算使用量测试。"""

from __future__ import annotations

import pytest

from patchflow.domain.enums import AgentPhase
from patchflow.domain.state import AgentState, BudgetUsage, InvalidStateTransitionError
from patchflow.domain.task import Budget


def test_agent_state_accepts_legal_phase_transition() -> None:
    """创建状态可以进入初始化阶段。"""

    state = AgentState(run_id="run-1", task_id="task-1")
    state.transition_to(AgentPhase.INITIALIZE)

    assert state.phase is AgentPhase.INITIALIZE
    assert not state.is_terminal


def test_agent_state_rejects_illegal_phase_transition() -> None:
    """不能从创建状态跳过全部流程直接完成。"""

    state = AgentState(run_id="run-1", task_id="task-1")

    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentPhase.COMPLETED)


def test_terminal_state_cannot_transition_again() -> None:
    """终止状态不允许重新进入工作阶段。"""

    state = AgentState(run_id="run-1", task_id="task-1", phase=AgentPhase.FAILED)

    assert state.is_terminal
    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentPhase.LOCALIZE)


def test_budget_usage_reports_reached_limits() -> None:
    """预算达到硬上限时必须明确返回对应资源名。"""

    budget = Budget(max_agent_steps=2, max_model_calls=3, max_cost_usd=1.0)
    usage = BudgetUsage(agent_steps=2, model_calls=2, cost_usd=1.0)

    assert usage.exceeded_items(budget) == ("agent_steps", "cost_usd")

