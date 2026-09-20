"""跨模块共享的枚举。"""

from enum import StrEnum


class TaskSource(StrEnum):
    """任务数据的来源。"""

    LOCAL = "local"
    MICRO_SWE = "micro_swe"
    SWE_BENCH = "swe_bench"


class RepositoryKind(StrEnum):
    """仓库定位方式。"""

    LOCAL = "local"
    GIT = "git"
    CONTAINER_IMAGE = "container_image"


class AgentPhase(StrEnum):
    """自主修复状态机的高层阶段。"""

    CREATED = "created"
    INITIALIZE = "initialize"
    UNDERSTAND = "understand"
    REPRODUCE = "reproduce"
    LOCALIZE = "localize"
    PLAN = "plan"
    GENERATE_CANDIDATES = "generate_candidates"
    VERIFY_CANDIDATES = "verify_candidates"
    REFLECT = "reflect"
    SELECT_AND_FINALIZE = "select_and_finalize"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    """一次任务运行的生命周期状态。"""

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    PATCH_GENERATED = "patch_generated"
    EVALUATING = "evaluating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CANCELLED = "cancelled"


class EventActor(StrEnum):
    """事件的直接产生者。"""

    SYSTEM = "system"
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"
    RUNTIME = "runtime"
    VERIFIER = "verifier"
    USER = "user"
    EVALUATOR = "evaluator"


class EventType(StrEnum):
    """首个版本中需要稳定支持的事件类型。"""

    RUN_CREATED = "run_created"
    RUN_STATUS_CHANGED = "run_status_changed"
    PHASE_CHANGED = "phase_changed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    BUDGET_UPDATED = "budget_updated"
    EVIDENCE_UPDATED = "evidence_updated"
    CANDIDATE_CREATED = "candidate_created"
    PATCH_APPLIED = "patch_applied"
    VERIFICATION_COMPLETED = "verification_completed"
    CONTEXT_COMPACTED = "context_compacted"
    DECISION_RECORDED = "decision_recorded"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class PermissionLevel(StrEnum):
    """工具权限等级，用于后续命令策略和人工确认。"""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXECUTE = "execute"
    HIGH_RISK_EXECUTE = "high_risk_execute"

