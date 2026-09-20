"""运行清单模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from patchflow.domain.enums import RunStatus


def utc_now() -> datetime:
    """生成带时区的 UTC 时间。"""

    return datetime.now(timezone.utc)


class RunManifest(BaseModel):
    """描述一次运行如何产生，用于复现和审计。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = Field(default=1, ge=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    status: RunStatus = RunStatus.PENDING
    code_version: str = Field(default="unknown", min_length=1)
    agent_name: str = Field(default="unconfigured", min_length=1)
    model_name: str = Field(default="unconfigured", min_length=1)
    runtime_name: str = Field(default="unconfigured", min_length=1)
    config_digest: str = Field(min_length=1)
    task_digest: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    final_patch_path: str | None = None
    internal_report_path: str | None = None
    evaluation_report_path: str | None = None
    stop_reason: str | None = None

