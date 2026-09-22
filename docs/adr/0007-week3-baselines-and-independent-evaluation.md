# ADR-0007：第三周基线与独立公开测试评测

状态：已采用；仅完成 FakeModel 烟测，未进行真实模型能力实验。

## 背景

项目设计文档第 3 周要求 One-shot、Linear ReAct、Bash-only 在同一批 MicroSWE 任务上运行并输出统一报告。仅比较 Agent 自报成功会被测试命令选择、过期测试结果或模型幻觉污染。真实模型运行可能产生费用并泄露仓库代码，因此不能在自动测试中默认执行。

## 决策

1. 三策略复用 `TaskSpec`、`Budget`、`Model`、`Runtime`、运行清单和轨迹。每个任务/策略组合创建全新模型对象和 DockerRuntime。
2. One-shot 使用固定字节预算、按已跟踪 Python 路径排序的前 120 行代码快照；单次模型请求没有工具定义，生成阶段不运行测试。补丁应用成功只标记 `PATCH_GENERATED`，不等于通过评测。
3. Linear ReAct 保留细粒度工具反馈；Bash-only 只公布 `run_command`，且拒绝 LocalRuntime。Bash-only 只有精确匹配任务公开测试 argv 的成功命令可将当前候选标记为已验证。
4. 长历史按完整函数调用/反馈对压缩；固定 Issue 和最近反馈不可被静默截断。估算的是请求字节，不承诺与 provider token 窗口精确一致。
5. 评测器在第二个全新 DockerRuntime 上重新应用最终补丁，执行全部公开命令；报告同时保存 Agent 状态、补丁可应用性、公开测试结果、基础设施错误、调用次数、token 与可计价成本。
6. CLI 的 `prepare-smoke`、`validate` 完全离线；`run-batch` 除模型 ID 和密钥外，还要求显式 `--allow-api-spend`。自动测试只用 FakeModel。

## 已知限制

- 两任务烟测只检验管线，不可据此判断哪种策略效果更好；30-50 任务正式集、隐藏测试与 SWE-bench 属于后续工作。
- One-shot 的确定性文件选择可能遗漏较大仓库中的目标文件；仓库索引和混合定位属于第 4 周。
- Bash-only 可在容器内执行高风险任意命令，当前 Docker 配置降低风险但不是通用安全证明。评测器不信任 Agent 容器的测试输出。
- 未配置准确单价时报告成本为未知；总 token 与字节上下文预算也不是同一计量。
