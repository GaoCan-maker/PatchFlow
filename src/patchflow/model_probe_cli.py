"""用一次极小请求检查 OpenAI 兼容模型能否满足 PatchFlow 的严格 JSON 协议。"""  # 该入口不会访问仓库、Docker 或 SWE-bench 隐藏数据。

from __future__ import annotations  # 延迟解析类型标注。

import argparse  # 要求用户显式提供服务、模型和付费许可。
import asyncio  # 驱动异步模型适配器。
import json  # 验证模型是否返回没有 Markdown 包裹的标准 JSON。

from patchflow.config.models import ModelConfig  # 复用正式服务地址、超时和输出预算校验。
from patchflow.model.errors import (  # 将协议失败与服务失败分开报告。
    ModelOutputError,  # 表示返回内容不满足严格模型协议。
    ModelProviderError,  # 表示认证、网络或远程服务失败。
)  # 完成模型错误类型导入。
from patchflow.model.openai_chat import OpenAIChatModel  # 使用实验阶段相同的真实模型适配器。
from patchflow.model.protocol import (  # 构造不包含工具或仓库代码的最小请求。
    ModelMessage,  # 保存探针中的单条用户指令。
    ModelRequest,  # 封装一次 provider 无关的最小请求。
)  # 完成模型协议导入。


async def _probe(config: ModelConfig) -> dict[str, object]:  # 发起恰好一次最小兼容性请求。
    model = OpenAIChatModel(config)  # 只在显式调用时从环境变量读取 API key。
    instruction = '只返回一个 JSON 对象，不要 Markdown 代码块或解释：{"probe":"ok"}'  # 模拟 PatchFlow 阶段严格 JSON 输出要求。
    request = ModelRequest(task_id="model-probe", problem_statement="检查模型 JSON 兼容性。", history=(ModelMessage(role="user", content=instruction),), tools=())  # 不发送任何仓库内容或函数工具。
    response = await model.complete(request)  # 通过与正式实验完全相同的 Chat Completions 适配器请求一次。
    if response.tool_call is not None or response.final_answer is None:  # 探针只允许纯文本 JSON 回复。
        raise ModelOutputError("探针期望纯 JSON 文本，但模型返回了工具调用")  # 明确模型协议不兼容。
    try:  # 使用标准 JSON 解析器拒绝 Markdown 围栏和额外说明。
        payload = json.loads(response.final_answer)  # 解析模型原始最终文本。
    except json.JSONDecodeError as error:  # 主策略同样无法消费非 JSON 阶段回复。
        raise ModelOutputError("模型没有返回可直接解析的严格 JSON") from error  # 给出可行动兼容性结论。
    if payload != {"probe": "ok"}:  # 字段或值漂移说明模型没有遵守最小输出约束。
        raise ModelOutputError("模型 JSON 内容不符合探针协议")  # 不把近似输出误报为通过。
    return {"compatible": True, "model_id": config.model, "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens, "cached_input_tokens": response.usage.cached_input_tokens}  # 只返回非敏感兼容性与用量信息。


def main(argv: list[str] | None = None) -> int:  # 提供可测试且默认不消费 API 的命令入口。
    parser = argparse.ArgumentParser(prog="python -m patchflow.model_probe_cli")  # 创建独立低成本探针解析器。
    parser.add_argument("--provider", choices=("openai_chat", "openai_compatible"), required=True)  # 选择正式实验将使用的适配器类型。
    parser.add_argument("--model-id", required=True)  # 接收服务商控制台中的精确模型 ID。
    parser.add_argument("--base-url")  # 第三方兼容服务必须提供 HTTPS Base URL。
    parser.add_argument("--timeout-seconds", type=float, default=60.0)  # 限制这一小次远程请求的墙钟时间。
    parser.add_argument("--allow-api-spend", action="store_true")  # 防止导入、测试或误操作产生费用。
    arguments = parser.parse_args(argv)  # 解析用户显式输入。
    if not arguments.allow_api_spend:  # 未确认费用时绝不构造真实客户端。
        parser.error("模型兼容性探针会产生一次 API 请求，必须设置 --allow-api-spend")  # 返回 argparse 标准错误。
    try:  # 把配置和远程失败转换为不泄漏密钥的短结果。
        config = ModelConfig(provider=arguments.provider, model=arguments.model_id, base_url=arguments.base_url, request_timeout_seconds=arguments.timeout_seconds, max_output_tokens=64)  # 使用极小输出预算控制探针成本。
        result = asyncio.run(_probe(config))  # 执行恰好一次真实模型请求。
    except (ValueError, RuntimeError, ModelOutputError, ModelProviderError) as error:  # 捕获配置、可选依赖、协议或服务分类错误。
        print(json.dumps({"compatible": False, "error": str(error)}, ensure_ascii=False, indent=2))  # 不打印密钥或原始服务响应。
        return 1  # 让脚本和实验自动化能检测不兼容。
    print(json.dumps(result, ensure_ascii=False, indent=2))  # 输出可记录的模型 ID 与 token 用量。
    return 0  # 严格 JSON 探针通过时返回成功。


if __name__ == "__main__":  # 支持 python -m patchflow.model_probe_cli 调用。
    raise SystemExit(main())  # 将主函数退出码交给操作系统。
