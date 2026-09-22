"""可序列化且严格校验的应用配置。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit  # 解析自定义模型服务地址，避免意外明文传输密钥。

from pydantic import (  # 增加服务地址字段校验能力。
    BaseModel,  # 定义配置模型基类。
    ConfigDict,  # 配置严格字段行为。
    Field,  # 声明数值和文本约束。
    SecretStr,  # 避免密钥出现在配置快照。
    field_validator,  # 校验自定义服务 URL。
)  # 结束配置模型依赖导入。


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
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)  # 默认不发送温度参数，以兼容不支持该字段的模型。
    max_output_tokens: int = Field(default=4_096, ge=1)
    api_key: SecretStr | None = Field(default=None, exclude=True)
    base_url: str | None = None  # 可选自定义 Chat Completions 兼容服务地址。
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600.0)  # 限制单次远程请求时间。
    input_price_usd_per_million: float | None = Field(default=None, ge=0)  # 可选输入 token 单价，用于可审计成本估算。
    output_price_usd_per_million: float | None = Field(default=None, ge=0)  # 可选输出 token 单价，用于可审计成本估算。

    @field_validator("base_url")  # 在创建 SDK 客户端之前验证自定义服务地址。
    @classmethod  # 不需要访问配置实例即可验证单个字段。
    def validate_base_url(cls, value: str | None) -> str | None:  # 返回受限的原始 URL。
        if value is None:  # 官方 OpenAI 服务使用 SDK 默认地址。
            return None  # 不改变默认配置。
        parsed = urlsplit(value)  # 使用标准结构化解析器拆分 URL。
        local_host = parsed.hostname in {"localhost", "127.0.0.1", "::1"}  # 识别允许明文连接的本机服务。
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local_host):  # 远程地址必须使用 TLS。
            raise ValueError("自定义模型服务地址必须使用 HTTPS；仅本机允许 HTTP")  # 拒绝远程明文传输密钥。
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:  # 禁止嵌入凭据与查询参数。
            raise ValueError("自定义模型服务地址格式无效")  # 避免把密钥或追踪字段带入地址。
        return value  # 交由 SDK 处理路径前缀。


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
