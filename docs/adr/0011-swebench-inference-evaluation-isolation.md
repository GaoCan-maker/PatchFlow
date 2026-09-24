# ADR 0011：SWE-bench 推理与官方评测隔离

## 状态

已接受，适用于第七周实现。

## 背景

SWE-bench 数据记录同时包含 Issue、基础提交、gold patch、测试补丁和目标测试列表。如果直接把原始记录作为 Agent 输入，模型可能读到答案或隐藏测试。另一方面，自行实现最终测试选择与 `resolved` 判定会造成结果与官方口径不一致。

## 决策

1. 原始 `SweBenchRecord` 只存在于转换边界，不作为 Agent 请求对象。
2. Inference Phase 只持有不含 gold、test patch、FAIL_TO_PASS 和 PASS_TO_PASS 的 `TaskSpec`。
3. 写出推理任务前执行递归禁用字段检查；发现泄漏时失败，不做静默删除。
4. prediction 严格限制为 `instance_id`、`model_name_or_path` 和 `model_patch` 三个字段。
5. 最终评分调用官方 `swebench.harness.run_evaluation`，PatchFlow 不重新实现 `resolved`。
6. 官方 Harness 使用独立 Python 环境和进程，在 Agent 停止后运行；常见模型 API 密钥不传给评测子进程。
7. 解析结果时将 unresolved、empty patch、evaluation error、missing result 和进程故障分开统计。
8. 真实 Harness 默认禁止执行，必须由 CLI 的 `--allow-harness-run` 或 API 的 `allow_execution=True` 显式授权。

## 备选方案

### 在 PatchFlow Runtime 内直接运行隐藏测试

实现看似统一，但 Agent Runtime 与最终评分共享工作区和数据，容易产生泄漏，也会复制官方环境构建和判定逻辑，因此拒绝。

### 将整个官方数据记录序列化为 TaskSpec 扩展字段

可以减少转换代码，但隐藏字段会进入 run artifact、模型上下文或调试日志，无法形成可信隔离，因此拒绝。

### 把 `swebench` 加入 PatchFlow 核心依赖

安装方便，但官方包、数据集和 Docker 依赖较重，容易与 Agent 环境产生版本冲突。当前使用外部解释器调用官方模块，保持适配层轻量。

### 将报告缺失算作 unresolved

聚合简单，但会把镜像、磁盘、超时和报告格式问题错误归因于 Agent 能力，因此拒绝。

## 后果

正面结果：Agent 输入可审计，prediction 格式与官方契约一致，最终结论由官方 Harness 给出，基础设施故障不会污染 pass rate。

代价：需要维护两个 Python 环境；任务仓库或推理镜像仍需单独准备；正式验收依赖 Docker、数据集下载、镜像空间和真实 Agent prediction。

## 重新评估条件

当官方提供稳定的进程内 Python API、报告格式发生不兼容变化，或 PatchFlow 改用官方远程评测服务时，重新评估模块入口、日志定位和外部环境边界；无论实现如何变化，gold 与隐藏测试不得进入同一次 Agent 推理。
