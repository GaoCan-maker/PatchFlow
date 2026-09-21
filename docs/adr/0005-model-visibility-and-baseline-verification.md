# ADR-0005：模型可见输入与 Baseline 验证边界

状态：接受。日期：2026-09-21。

## 背景

`TaskSpec` 同时携带 Agent 可见的 Issue 与仅评测层可解析的 `evaluation_ref`。把整个任务对象传给模型适配器会造成隐藏测试元数据泄漏，使评测失真。另一方面，线性 Agent 如果只依赖模型说“已完成”，无法保证补丁存在或当前版本通过公开测试。

## 决策

- `ModelRequest` 只提供公开任务 ID、Issue 文本、线性历史和显式允许的工具声明；评测引用不进入模型协议。
- `ModelResponse` 每次只能表达一个工具调用或一个最终说明，且附带 provider 报告的 token 与成本用量。
- Linear ReAct 对未知工具与参数错误返回结构化反馈，并把模型及工具交互写入因果关联的 JSONL 事件流。
- 写工具调用使先前测试通过状态失效；只有最新修改后 `run_tests` 成功、Runtime 导出非空完整 diff 时才输出成功 patch。
- 公开测试通过仅是 Baseline 的验收门槛，不等同隐藏测试正确性；评测结果由独立 Evaluation Harness 给出。

## 后果与后续

此边界降低了评测泄漏和模型自报成功的风险，但没有解决测试覆盖不足、恶意测试、长上下文和真实 provider 失败模式。后续需引入有界 ContextBuilder、Verifier Pyramid 和真实模型适配器；不得把 FakeModel 测试的通过率当作真实 Agent 的修复率。
