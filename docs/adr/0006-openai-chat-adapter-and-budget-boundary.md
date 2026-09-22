# ADR-0006：Chat Completions 适配器与模型预算边界

状态：接受。日期：2026-09-21。

## 背景

Step 3A 的 FakeModel 证明了 Agent 编排，但模型历史仅用普通文本描述工具调用，不能直接交给原生函数调用接口。工具声明也没有参数模式。真实模型接入必须保持核心 Agent 与厂商 SDK 解耦，并避免密钥、隐藏评测信息、额外重试和未知成本污染实验。

## 决策

- 在现有模型协议上保留工具调用 ID，并用工具参数 Pydantic 模型生成唯一来源的 JSON Schema。
- 首个真实适配器使用官方异步 SDK 的 Chat Completions 函数调用接口，原因是它也可服务显式配置的兼容端点；这是兼容性选择，不表示所有端点或模型已实测。OpenAI 对新建 OpenAI 专用项目推荐 Responses API，后续可另加 Responses 适配器，不改变核心 Agent。
- SDK 放在 `model-openai` 可选依赖中；只有显式构造真实传输时才读取宿主 API 密钥。DockerRuntime 不继承密钥。
- 请求固定单候选、禁止并发工具调用；回复中的函数调用、参数 JSON、消息角色和 token 用量都要严格校验。无法安全解释时停止，不猜测。
- SDK 自动重试禁用。任何未来应用层重试须产生独立事件并计入模型调用、时间、token 和成本预算。
- 无明确单价时成本保持未知；任务设有成本预算时拒绝继续执行。单价由用户按实际模型填写，当前估算未区分缓存折扣，属于保守近似。
- 远程兼容端点仅允许 HTTPS，本机开发地址可使用 HTTP。真实调用前需确认仓库数据发送许可。

## 验收与限制

已用离线传输替身测试工具协议、错误分类和真实临时仓库闭环；已安装并离线构造官方 SDK 客户端。尚未调用真实模型 API，因此没有验证账户权限、模型参数兼容性、实际费用或修复成功率。下一步先实现有界上下文和安全入口，再由用户明确选择 provider 做小额联调。

参考：[OpenAI Chat Completions API](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)、[OpenAI 错误分类](https://developers.openai.com/api/docs/guides/error-codes)、[OpenAI SDK 快速开始](https://developers.openai.com/api/docs/quickstart)。
