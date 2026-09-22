"""基于官方异步 SDK 的 Chat Completions 函数调用适配器。"""  # 该实现不改变 Agent 的模型协议。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 序列化函数调用参数并解析模型生成的 JSON。
import os  # 只从宿主进程环境读取模型 API 密钥。
from typing import Any, Protocol  # 定义可在测试中替换的异步传输接口。

from pydantic import BaseModel, ConfigDict, Field, ValidationError  # 校验外部服务响应结构。

from patchflow.config.models import ModelConfig  # 复用经过验证的模型配置。
from patchflow.domain.tools import ToolCall  # 复用严格的工具调用领域模型。
from patchflow.model.errors import (  # 复用 provider 无关的错误类型。
    ModelOutputError,  # 标记不能安全执行的回复。
    ModelProviderError,  # 标记网络或服务故障。
)  # 结束模型错误导入。
from patchflow.model.protocol import ModelRequest, ModelResponse, ModelUsage  # 实现既有模型协议。


class CompletionTransport(Protocol):  # 将 SDK 网络调用与响应解析分离。
    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:  # 返回一个 JSON 兼容的非流式回复。
        """执行一次 Chat Completions 请求。"""  # 允许测试使用内存传输替身。


class _SDKTransport:  # 将官方 SDK 的异常与对象转换成稳定内部格式。
    def __init__(self, config: ModelConfig, api_key: str) -> None:  # 创建不自动重试的异步客户端。
        try:  # 将真实 SDK 限定为可选依赖。
            import openai  # 导入已安装的官方 OpenAI Python SDK。
        except ImportError as error:  # 开发环境可能只安装基础依赖。
            raise RuntimeError('请先安装可选依赖：python -m pip install -e ".[model-openai]"') from error  # 给出明确安装方法。
        self._sdk = openai  # 保存 SDK 异常类型以便精确分类。
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=config.base_url, timeout=config.request_timeout_seconds, max_retries=0)  # 禁用不计入 Agent 预算的隐藏重试。

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:  # 执行一次真实异步模型请求。
        try:  # 仅捕获已知 SDK 网络与状态异常。
            response = await self._client.chat.completions.create(**payload)  # 使用官方函数调用接口。
        except self._sdk.APITimeoutError as error:  # 识别远程请求超时。
            raise ModelProviderError("api_timeout", retryable=True) from error  # 保留类型并隐藏服务错误原文。
        except self._sdk.APIConnectionError as error:  # 识别 DNS、TLS 等连接错误。
            raise ModelProviderError("api_connection", retryable=True) from error  # 标记未来可重试。
        except self._sdk.RateLimitError as error:  # 识别请求速率限制。
            raise ModelProviderError("rate_limit", retryable=True) from error  # 不在此处偷偷重试。
        except self._sdk.InternalServerError as error:  # 识别服务端临时故障。
            raise ModelProviderError("server_error", retryable=True) from error  # 交由上层决定是否重试。
        except self._sdk.AuthenticationError as error:  # 识别失效的 API 密钥。
            raise ModelProviderError("authentication", retryable=False) from error  # 禁止无意义重试。
        except self._sdk.PermissionDeniedError as error:  # 识别模型或项目访问权限不足。
            raise ModelProviderError("permission_denied", retryable=False) from error  # 要求用户处理权限。
        except self._sdk.BadRequestError as error:  # 识别模型不支持参数或请求格式错误。
            raise ModelProviderError("bad_request", retryable=False) from error  # 避免重复发送无效请求。
        except self._sdk.APIStatusError as error:  # 保守处理其他已知 HTTP 状态错误。
            raise ModelProviderError("api_status_error", retryable=False) from error  # 不泄漏原始错误正文。
        return response.model_dump(mode="json")  # 转换为可严格校验的普通 JSON 对象。


class _Function(BaseModel):  # 定义函数调用的最小有效结构。
    model_config = ConfigDict(extra="ignore", strict=True)  # 忽略 provider 扩展字段并拒绝隐式类型转换。
    name: str = Field(min_length=1)  # 函数名称必须非空。
    arguments: str  # 参数应是 JSON 字符串而非任意 Python 对象。


class _FunctionCall(BaseModel):  # 定义函数调用 ID 与函数体。
    model_config = ConfigDict(extra="ignore", strict=True)  # 保持外部响应的严格类型。
    id: str = Field(min_length=1)  # ID 用于关联下一轮工具结果。
    type: str  # 只接受 function 类型，其他类型在解析时拒绝。
    function: _Function | None = None  # 非函数工具可能没有该字段。


class _Message(BaseModel):  # 定义模型候选消息的最小可消费字段。
    model_config = ConfigDict(extra="ignore", strict=True)  # 允许 SDK 保留额外元数据。
    content: str | None = None  # 纯文本结束说明可能在此字段中。
    refusal: str | None = None  # 安全拒绝需要单独分类。
    tool_calls: list[_FunctionCall] | None = None  # 函数调用可能为空或不存在。


class _Choice(BaseModel):  # 定义一次候选输出。
    model_config = ConfigDict(extra="ignore", strict=True)  # 排除不影响决策的服务字段。
    finish_reason: str | None = None  # 识别长度耗尽和内容过滤。
    message: _Message  # 保存回复消息内容。


class _CacheDetails(BaseModel):  # 定义可选输入缓存计数。
    model_config = ConfigDict(extra="ignore", strict=True)  # 忽略未来增加的缓存细节。
    cached_tokens: int | None = Field(default=None, ge=0)  # 服务可能不报告缓存数量。


class _Usage(BaseModel):  # 定义预算控制所需的 provider 用量。
    model_config = ConfigDict(extra="ignore", strict=True)  # 只接收整数 token 数。
    prompt_tokens: int = Field(ge=0)  # 输入 token 数必须已报告。
    completion_tokens: int = Field(ge=0)  # 输出 token 数必须已报告。
    prompt_tokens_details: _CacheDetails | None = None  # 缓存明细为可选字段。


class _Completion(BaseModel):  # 定义完整非流式模型回复。
    model_config = ConfigDict(extra="ignore", strict=True)  # 不依赖 provider 非核心字段。
    choices: list[_Choice]  # 必须有且只有一个候选。
    usage: _Usage  # 缺失用量时不能继续做预算统计。


class OpenAIChatModel:  # 实现现有异步 Model 协议的真实服务适配器。
    def __init__(self, config: ModelConfig, *, transport: CompletionTransport | None = None) -> None:  # 注入配置或测试传输。
        if config.provider not in {"openai_chat", "openai_compatible"}:  # 防止把其他 provider 配置误交给此适配器。
            raise ValueError("此适配器仅接受 openai_chat 或 openai_compatible 配置")  # 尽早暴露装配错误。
        if config.model == "unconfigured":  # 真实调用必须显式选择模型 ID。
            raise ValueError("必须配置真实模型 ID")  # 不猜测模型或自动产生费用。
        if config.provider == "openai_compatible" and config.base_url is None:  # 兼容服务必须明确地址。
            raise ValueError("兼容服务必须提供 base_url")  # 避免误把第三方密钥发给官方地址。
        priced_input = config.input_price_usd_per_million is not None  # 检查输入单价是否设置。
        priced_output = config.output_price_usd_per_million is not None  # 检查输出单价是否设置。
        if priced_input != priced_output:  # 成本估算必须拥有成对价格。
            raise ValueError("输入与输出单价必须同时设置或同时留空")  # 禁止不完整成本统计。
        self._config = config  # 保留无明文序列化能力的配置对象。
        if transport is not None:  # 测试传输不需要密钥或 SDK。
            self._transport = transport  # 使用显式注入的离线传输。
        else:  # 真实远程调用只在显式构造时建立客户端。
            configured_key = config.api_key.get_secret_value() if config.api_key is not None else None  # 优先读取不进入快照的 SecretStr。
            variable = "PATCHFLOW_MODEL_API_KEY" if config.provider == "openai_compatible" else "OPENAI_API_KEY"  # 为不同服务选择明确环境变量。
            api_key = configured_key or os.environ.get(variable)  # 只在宿主进程查找模型密钥。
            if not api_key:  # 未设置密钥时绝不尝试网络请求。
                raise ValueError(f"缺少模型 API 密钥：请设置 {variable}")  # 不在错误中打印密钥内容。
            self._transport = _SDKTransport(config, api_key)  # 创建异步 SDK 传输。

    async def complete(self, request: ModelRequest) -> ModelResponse:  # 生成一次工具调用或最终文本。
        payload = self._build_payload(request)  # 从公开任务数据构造函数调用请求。
        raw = await self._transport.create(payload)  # 发起恰好一次请求；不进行隐藏重试。
        try:  # 严格解析完整服务回复。
            completion = _Completion.model_validate(raw)  # 验证候选、消息和用量字段。
        except ValidationError as error:  # 缺字段或类型错误属于无效模型响应。
            raise ModelOutputError("模型回复缺少必需结构或用量") from error  # 不把未经验证的回复交给 Agent。
        if len(completion.choices) != 1:  # 保证适配器只消费一个候选动作。
            raise ModelOutputError("模型必须返回且只返回一个候选")  # 避免暗中忽略额外动作。
        choice = completion.choices[0]  # 获取唯一候选。
        if choice.finish_reason in {"length", "content_filter"} or choice.message.refusal:  # 区分不完整输出和拒绝。
            raise ModelOutputError(f"模型未生成可执行动作：{choice.finish_reason or 'refusal'}")  # 不把部分内容当补丁。
        cache_details = completion.usage.prompt_tokens_details  # 读取可能存在的缓存输入明细。
        cached_tokens = cache_details.cached_tokens if cache_details and cache_details.cached_tokens is not None else 0  # 缺失时按零记录。
        input_price = self._config.input_price_usd_per_million  # 获取可选输入单价。
        output_price = self._config.output_price_usd_per_million  # 获取可选输出单价。
        estimated_cost = (completion.usage.prompt_tokens * input_price + completion.usage.completion_tokens * output_price) / 1_000_000 if input_price is not None and output_price is not None else None  # 未配置价格时明确保持未知。
        usage = ModelUsage(completion.usage.prompt_tokens, completion.usage.completion_tokens, estimated_cost, cached_tokens)  # 返回完整可审计用量。
        calls = choice.message.tool_calls or []  # 将缺失工具字段视为空列表。
        if len(calls) > 1:  # 即使服务忽略禁用并发的设置也不能丢弃调用。
            raise ModelOutputError("线性基线每轮只允许一个工具调用")  # 拒绝不可安全执行的多调用回复。
        if calls:  # 解析唯一结构化函数调用。
            call = calls[0]  # 取得原始 provider 调用 ID 和函数。
            if call.type != "function" or call.function is None:  # 当前协议不执行内置或自定义非函数工具。
                raise ModelOutputError("只支持 function 类型的工具调用")  # 避免错误路由。
            allowed = {tool.name for tool in request.tools}  # 只接受 Agent 实际公布的工具名称。
            if call.function.name not in allowed:  # 提供商返回未知工具时不执行任何代码。
                raise ModelOutputError("模型调用了未公布的工具")  # 将其归为无效结构化输出。
            try:  # 函数参数必须是 JSON 对象。
                arguments = json.loads(call.function.arguments)  # 使用标准 JSON 解析器处理参数。
            except json.JSONDecodeError as error:  # 模型可能产生截断或不完整 JSON。
                raise ModelOutputError("工具参数不是合法 JSON") from error  # 保持错误类型稳定。
            if not isinstance(arguments, dict):  # 数组或标量不能映射到工具参数模型。
                raise ModelOutputError("工具参数必须是 JSON 对象")  # 不猜测或自动包裹参数。
            try:  # 再由领域调用模型校验 JSON 兼容值。
                tool_call = ToolCall.model_validate({"call_id": call.id, "tool_name": call.function.name, "arguments": arguments})  # 保留原始函数调用 ID。
            except ValidationError as error:  # 拒绝过度嵌套或无效 JSON 形态。
                raise ModelOutputError("工具调用不符合领域协议") from error  # 避免不安全执行。
            return ModelResponse(tool_call=tool_call, usage=usage)  # 返回单个可分发动作。
        if not choice.message.content or not choice.message.content.strip():  # 无工具且无结束文本是无效回复。
            raise ModelOutputError("模型回复没有工具调用或结束说明")  # 明确终止而非空转。
        return ModelResponse(final_answer=choice.message.content, usage=usage)  # 返回最终说明供 Agent 验证补丁。

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:  # 构造 Chat Completions 请求体。
        messages: list[dict[str, Any]] = [{"role": "system", "content": "你是仓库级代码修复助手。遵守任务中的策略约束；若提供工具，每轮最多调用一个。"}]  # 具体结束条件由 Agent 策略决定。
        for message in request.history:  # 保留线性 Agent 的原始消息顺序。
            if message.role == "assistant" and message.tool_call is not None:  # 重建原生助手函数调用消息。
                call = message.tool_call  # 读取稳定的原始调用 ID 与参数。
                messages.append({"role": "assistant", "content": message.content or None, "tool_calls": [{"id": call.call_id, "type": "function", "function": {"name": call.tool_name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}}]})  # 让下一轮工具回复能正确配对。
            elif message.role == "tool" and message.tool_call_id is not None:  # 重建工具反馈消息。
                messages.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content})  # 保留原始结果关联 ID。
            elif message.role in {"system", "user", "assistant"} and message.tool_call is None and message.tool_call_id is None:  # 处理普通文本消息。
                messages.append({"role": message.role, "content": message.content})  # 不改变原文内容。
            else:  # 拒绝不完整或未知角色的历史消息。
                raise ValueError("模型历史消息缺少工具关联 ID 或角色无效")  # 防止发送不合法 API 请求。
        payload: dict[str, Any] = {"model": self._config.model, "messages": messages, "max_completion_tokens": self._config.max_output_tokens, "n": 1}  # 固定单候选和输出上限。
        if self._config.temperature is not None:  # 某些模型不支持温度参数。
            payload["temperature"] = self._config.temperature  # 仅在用户显式配置时发送。
        if self._config.provider == "openai_chat":  # 官方服务支持显式禁止响应存储。
            payload["store"] = False  # 避免无意保存代码仓库内容。
        if request.tools:  # 没有工具时不发送空工具定义。
            if any(not tool.parameters_schema for tool in request.tools):  # 所有原生工具必须带严格参数模式。
                raise ValueError("工具缺少参数 JSON Schema")  # 阻止模型收到不完整定义。
            payload["tools"] = [{"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters_schema}} for tool in request.tools]  # 公布 Pydantic 生成的参数模式。
            payload["tool_choice"] = "auto"  # 允许模型在调用工具和给出结论之间选择。
            payload["parallel_tool_calls"] = False  # 与单动作 ModelResponse 协议保持一致。
        return payload  # 返回不含 API 密钥和评测私有引用的请求体。
