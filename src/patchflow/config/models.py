"""可序列化且严格校验的应用配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StrictConfigModel(BaseModel):
    """拒绝未知字段，避免配置拼写错误被静默忽略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentConfig(StrictConfigModel):
    """Agent 策略的首批稳定配置。"""

    strategy: str = Field(default="linear_react", min_length=1)
    max_candidates_per_round: int = Field(default=1, ge=1, le=8)
    max_reflection_rounds: int = Field(default=2, ge=0, le=10)
    stop_on_verified_candidate: bool = True


class ModelConfig(StrictConfigModel):
    """模型配置；密钥使用 SecretStr，序列化时不会暴露明文。"""

    provider: str = Field(default="unconfigured", min_length=1)
    model: str = Field(default="unconfigured", min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4_096, ge=1)
    api_key: SecretStr | None = Field(default=None, exclude=True)


class RuntimeConfig(StrictConfigModel):
    """Runtime 安全和资源限制配置。"""

    kind: str = Field(default="local", pattern=r"^(local|docker|swe_bench)$")
    command_timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_chars: int = Field(default=20_000, ge=1)
    network_enabled: bool = False
    cpu_limit: float | None = Field(default=None, gt=0)
    memory_limit_mb: int | None = Field(default=None, ge=128)
    pid_limit: int | None = Field(default=None, ge=16)


class StorageConfig(StrictConfigModel):
    """运行 artifact 的持久化位置。"""

    runs_root: Path = Path("runs")
    keep_failed_workspaces: bool = True


class AppConfig(StrictConfigModel):
    """一次运行需要快照保存的完整应用配置。"""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

