"""真实模型适配器的离线协议与 Agent 闭环测试。"""  # 不发送网络请求或消耗 API 额度。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 在同步 pytest 中运行异步模型协议。
import json  # 构造与解析标准函数调用参数。
import sys  # 为本地仓库测试选择当前 Python。
from pathlib import Path  # 标注临时目录与运行目录。

import pytest  # 使用参数化验证异常响应。

from patchflow.agent.linear_react import LinearReactAgent  # 验证适配器与原有 Agent 可组合。
from patchflow.application.run_initializer import initialize_run  # 创建真实轨迹和运行清单。
from patchflow.config.models import AppConfig, ModelConfig  # 验证模型配置与密钥隔离。
from patchflow.domain.enums import EventType, RunStatus  # 检查运行终态和轨迹事件。
from patchflow.domain.task import Budget  # 验证未知成本下的预算拒绝。
from patchflow.domain.tools import ToolCall  # 构造原始函数调用历史。
from patchflow.model.errors import ModelOutputError, ModelProviderError  # 验证稳定错误分类。
from patchflow.model.openai_chat import (  # 导入适配器与 SDK 异常包装边界。
    OpenAIChatModel,  # 测试公开的模型适配器。
    _SDKTransport,  # 测试 SDK 异常归一化逻辑。
)  # 结束适配器导入。
from patchflow.model.protocol import ModelMessage, ModelRequest  # 构造公开模型输入。
from patchflow.runtime.local import LocalRuntime  # 在可信临时仓库执行真实工具。
from patchflow.tools import (  # 获取 Pydantic 工具模式。
    ApplyPatchTool,  # 验证可写工具定义。
    ReadCodeTool,  # 验证只读工具定义。
    RunTestsTool,  # 验证测试工具定义。
    SearchTextTool,  # 验证搜索工具定义。
)  # 结束工具导入。
from tests.runtime_helpers import (  # 使用已验证的仓库测试夹具。
    create_temporary_git_repository,  # 创建隔离仓库。
    valid_patch,  # 构造可应用候选补丁。
)  # 结束测试夹具导入。


class ScriptedTransport:  # 定义不需要 SDK 或网络的传输替身。
    def __init__(self, responses: list[dict[str, object]]) -> None:  # 接收预设的服务响应。
        self.responses = list(responses)  # 复制脚本，避免测试修改调用方列表。
        self.payloads: list[dict[str, object]] = []  # 保存每次实际请求供断言。

    async def create(self, payload: dict[str, object]) -> dict[str, object]:  # 模拟一次异步非流式调用。
        self.payloads.append(payload)  # 记录公开请求参数和消息。
        return self.responses.pop(0)  # 按顺序返回一个 JSON 兼容回复。


class FailingTransport:  # 定义报告网络故障的离线传输。
    async def create(self, payload: dict[str, object]) -> dict[str, object]:  # 模拟远程请求失败。
        raise ModelProviderError("api_connection", retryable=True)  # 提供不包含服务正文的稳定分类。


def _completion(*, content: str | None = None, name: str | None = None, arguments: dict[str, object] | None = None, call_id: str = "call-1", usage: dict[str, object] | None = None) -> dict[str, object]:  # 构造最小标准服务响应。
    calls = [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}] if name else None  # 可选生成原生函数调用。
    message = {"content": content, "tool_calls": calls, "refusal": None}  # 模拟模型候选消息。
    counts = usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 10}}  # 默认返回可核算用量。
    return {"choices": [{"finish_reason": "tool_calls" if name else "stop", "message": message}], "usage": counts}  # 返回单候选非流式对象。


def _request(*, history: tuple[ModelMessage, ...] | None = None) -> ModelRequest:  # 创建只有公开字段的模型请求。
    messages = history if history is not None else (ModelMessage("system", "修复这个 Python 仓库"),)  # 设置默认 Issue 可见历史。
    return ModelRequest("safe-task", "修复这个 Python 仓库", messages, (ReadCodeTool().spec,))  # 公布一个带模式的只读工具。


def test_tool_schema_and_function_call_round_trip() -> None:  # 验证工具参数模式与原始调用 ID。
    transport = ScriptedTransport([_completion(name="read_code", arguments={"path": "app.py"}, call_id="tool-42")])  # 预设合法函数调用。
    config = ModelConfig(provider="openai_chat", model="test-model", input_price_usd_per_million=2, output_price_usd_per_million=8)  # 配置可审计价格。
    model = OpenAIChatModel(config, transport=transport)  # 使用离线传输而不要求密钥。
    result = asyncio.run(model.complete(_request()))  # 执行一次真实适配器解析。
    assert result.tool_call == ToolCall(call_id="tool-42", tool_name="read_code", arguments={"path": "app.py"})  # 保留 provider 调用 ID。
    assert result.usage.input_tokens == 100 and result.usage.output_tokens == 20  # 读取 provider 原始 token 数。
    assert result.usage.cached_input_tokens == 10  # 记录缓存输入 token。
    assert result.usage.cost_usd == pytest.approx(0.00036)  # 根据明确配置的单价估算成本。
    payload = transport.payloads[0]  # 检查真正发送给模型的消息结构。
    assert payload["parallel_tool_calls"] is False  # 与线性单动作协议保持一致。
    assert payload["max_completion_tokens"] == config.max_output_tokens  # 应用配置的输出上限。
    assert payload["tools"][0]["function"]["parameters"]["additionalProperties"] is False  # 参数模式来自严格 Pydantic 模型。
    assert "api_key" not in json.dumps(payload)  # 请求体不能携带密钥字段。


def test_tool_result_history_keeps_original_call_id() -> None:  # 验证下一轮请求符合原生函数调用协议。
    call = ToolCall(call_id="first-call", tool_name="read_code", arguments={"path": "app.py"})  # 构造上一轮工具调用。
    history = (ModelMessage("user", "请修复问题"), ModelMessage("assistant", "", tool_call=call), ModelMessage("tool", "读取成功", tool_call_id="first-call"))  # 构造完整调用-反馈对。
    transport = ScriptedTransport([_completion(content="需要继续检查。")])  # 预设后续纯文本决策。
    result = asyncio.run(OpenAIChatModel(ModelConfig(provider="openai_chat", model="test-model"), transport=transport).complete(_request(history=history)))  # 执行下一轮请求。
    messages = transport.payloads[0]["messages"]  # 读取适配器构造的消息列表。
    assert messages[-2]["tool_calls"][0]["id"] == "first-call"  # 助手消息携带原始调用 ID。
    assert messages[-1]["tool_call_id"] == "first-call"  # 工具结果引用相同 ID。
    assert json.loads(messages[-2]["tool_calls"][0]["function"]["arguments"]) == {"path": "app.py"}  # 参数保持结构化。
    assert result.final_answer == "需要继续检查。"  # 最终文本被正确解析。
    assert result.usage.cost_usd is None  # 未配置价格时成本不能伪装成零。


@pytest.mark.parametrize("response", [  # 对不同无效服务输出运行同一拒绝断言。
    {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},  # 没有候选时不能凭空生成动作。
    {"choices": [{"message": {"content": "完成"}}]},  # 缺失 token 用量不能绕过预算。
    _completion(name="read_code", arguments={"path": "app.py"}, call_id=""),  # 空调用 ID 无法关联反馈。
    _completion(name="unlisted_tool", arguments={}),  # 不允许执行未公开函数。
    _completion(content="   "),  # 只有空白的结束文本无效。
])  # 结束异常响应样本列表。
def test_invalid_responses_are_rejected(response: dict[str, object]) -> None:  # 确认解析器不猜测缺失数据。
    model = OpenAIChatModel(ModelConfig(provider="openai_chat", model="test-model"), transport=ScriptedTransport([response]))  # 装配异常响应。
    with pytest.raises(ModelOutputError):  # 每类错误均应产生稳定异常。
        asyncio.run(model.complete(_request()))  # 禁止错误响应进入工具层。


def test_credentials_and_custom_url_are_guarded(monkeypatch: pytest.MonkeyPatch) -> None:  # 验证密钥和地址边界。
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # 去除环境中可能存在的真实密钥。
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):  # 未注入传输时要求官方密钥。
        OpenAIChatModel(ModelConfig(provider="openai_chat", model="test-model"))  # 不触发 SDK 构造或网络请求。
    with pytest.raises(ValueError, match="HTTPS"):  # 拒绝远程明文 HTTP 地址。
        ModelConfig(provider="openai_compatible", model="test-model", base_url="http://example.com/v1")  # 防止密钥经明文传输。
    config = ModelConfig(provider="openai_compatible", model="test-model", base_url="http://localhost:8000/v1")  # 本地自托管开发允许 HTTP。
    assert config.base_url == "http://localhost:8000/v1"  # 确认安全本地地址保留。


def test_optional_sdk_client_constructs_without_network() -> None:  # 验证可选依赖与真实客户端构造路径。
    pytest.importorskip("openai")  # 未安装可选依赖的基础环境可跳过此项。
    config = ModelConfig(provider="openai_chat", model="offline-test-model", api_key="offline-not-a-real-key")  # 使用明确无效的本地测试密钥。
    model = OpenAIChatModel(config)  # 只构造官方异步客户端，不执行 complete 或网络调用。
    assert model is not None  # 确认 SDK 接口和配置能够装配。
    assert "offline-not-a-real-key" not in config.model_dump_json()  # 配置快照不会泄漏明文密钥。


@pytest.mark.parametrize("kind,retryable", [("api_timeout", True), ("authentication", False)])  # 验证暂时故障与永久故障分类不同。
def test_installed_sdk_errors_are_classified_offline(kind: str, retryable: bool) -> None:  # 不发送网络请求地触发真实 SDK 异常类型。
    openai = pytest.importorskip("openai")  # 基础依赖环境允许跳过此项。
    httpx2 = pytest.importorskip("httpx2")  # SDK v3 的请求对象类型来自其 HTTP 依赖。
    request = httpx2.Request("POST", "https://example.invalid/v1/chat/completions")  # 构造不会发送的测试请求。
    response = httpx2.Response(401, request=request)  # 构造不会发送的鉴权失败响应。
    error = openai.APITimeoutError(request) if kind == "api_timeout" else openai.AuthenticationError("invalid", response=response, body=None)  # 生成真实 SDK 异常实例。

    class RaisingCompletions:  # 定义在调用时抛出预设异常的内存客户端端点。
        async def create(self, **payload: object) -> object:  # 模拟异步 Chat Completions 接口。
            raise error  # 交给适配器处理，而非执行 HTTP。

    class FakeChat:  # 构造 SDK 客户端的 chat 层级。
        completions = RaisingCompletions()  # 提供可 await 的 completions 端点。

    class FakeClient:  # 构造 SDK 客户端的最外层对象。
        chat = FakeChat()  # 提供 chat.completions.create 路径。

    transport = _SDKTransport(ModelConfig(provider="openai_chat", model="offline-test"), "fake-key")  # 构造真实 SDK 包装器但不调用网络。
    transport._client = FakeClient()  # 仅在测试中替换其底层客户端。
    with pytest.raises(ModelProviderError) as captured:  # 断言异常转换为稳定内部类型。
        asyncio.run(transport.create({"model": "offline-test", "messages": []}))  # 执行完全离线的异常路径。
    assert captured.value.kind == kind and captured.value.retryable is retryable  # 确认类型和重试标记符合预期。


def test_adapter_runs_agent_with_real_repository_and_fake_transport(tmp_path: Path) -> None:  # 验证真 Agent 与真工具可使用新适配器。
    repository = create_temporary_git_repository(tmp_path)  # 创建安全的临时 Git 仓库。
    config = AppConfig(model=ModelConfig(provider="openai_chat", model="test-model"))  # 只声明模型身份，不使用真实密钥。
    context = initialize_run(repository.task, config, runs_root=tmp_path / "runs")  # 初始化轨迹和清单。
    script = [  # 让传输依次模拟搜索、读取、修补、测试和结束。
        _completion(name="search_text", arguments={"query": "def greet"}, call_id="search"),  # 搜索目标函数。
        _completion(name="read_code", arguments={"path": "app.py"}, call_id="read"),  # 读取目标代码。
        _completion(name="apply_patch", arguments={"patch": valid_patch()}, call_id="patch"),  # 应用可行补丁。
        _completion(name="run_tests", arguments={"command": [sys.executable, "-m", "pytest", "-q"]}, call_id="tests"),  # 验证最新修改。
        _completion(content="补丁已通过公开测试。"),  # 请求生成最终补丁。
    ]  # 结束五步传输脚本。
    transport = ScriptedTransport(script)  # 创建不会进行 API 调用的传输。
    model = OpenAIChatModel(config.model, transport=transport)  # 装配真实模型解析逻辑。
    tools = (SearchTextTool(), ReadCodeTool(), ApplyPatchTool(), RunTestsTool())  # 限定 Agent 可见工具。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 限定所有代码操作在可信临时仓库。
    outcome = asyncio.run(LinearReactAgent(model, tools).run(repository.task, context, runtime))  # 执行完整线性闭环。
    assert outcome.status is RunStatus.SUCCEEDED  # 真实工具测试通过并产生非空补丁。
    assert "cleaned_name" in context.layout.final_patch_path.read_text(encoding="utf-8")  # 校验最终 patch 落盘。
    assert transport.payloads[-1]["messages"][-1]["tool_call_id"] == "tests"  # 结束前模型收到测试结果关联 ID。
    assert context.event_store.load_all()[-1].event_type is EventType.RUN_COMPLETED  # 检查终态事件可回放。


def test_model_errors_and_unknown_cost_have_distinct_stop_reasons(tmp_path: Path) -> None:  # 区分 API 故障与成本预算错误。
    repository = create_temporary_git_repository(tmp_path)  # 创建两个运行共用的干净临时仓库。
    config = AppConfig(model=ModelConfig(provider="openai_chat", model="test-model"))  # 保持成本价格未知。
    failed_context = initialize_run(repository.task, config, runs_root=tmp_path / "runs")  # 创建 API 故障运行。
    failing_model = OpenAIChatModel(config.model, transport=FailingTransport())  # 装配可分类的网络故障。
    failed = asyncio.run(LinearReactAgent(failing_model, ()).run(repository.task, failed_context, LocalRuntime(repository.repository, repository.isolation_root)))  # 执行一次失败请求。
    assert failed.status is RunStatus.INFRASTRUCTURE_ERROR and failed.stop_reason == "model_api_connection"  # 网络故障不记为修复失败。
    priced_task = repository.task.model_copy(update={"budget": Budget(max_cost_usd=1)})  # 设置必须能够核算的成本上限。
    budget_context = initialize_run(priced_task, config, runs_root=tmp_path / "runs")  # 创建第二个独立运行。
    unpriced_model = OpenAIChatModel(config.model, transport=ScriptedTransport([_completion(content="完成")]))  # 模拟没有价格的服务回复。
    stopped = asyncio.run(LinearReactAgent(unpriced_model, ()).run(priced_task, budget_context, LocalRuntime(repository.repository, repository.isolation_root)))  # 执行成本预算检查。
    assert stopped.status is RunStatus.FAILED and stopped.stop_reason == "unpriced_model_usage"  # 未知成本不可冒充零花费。
