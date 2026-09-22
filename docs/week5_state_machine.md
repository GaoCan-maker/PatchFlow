# 第五周：显式状态机、Evidence Graph 与结构化反思

## 本周交付

第五周新增 `PatchFlowAgent`，与第三周 `OneShotAgent`、`LinearReactAgent` 和 `BashOnlyAgent` 并列，不修改三条 baseline 的模型上下文或工具集合。它在一个 Runtime 工作区内逐阶段执行：

`INITIALIZE → UNDERSTAND → REPRODUCE → LOCALIZE → PLAN → GENERATE_CANDIDATES → VERIFY_CANDIDATES → SELECT_AND_FINALIZE`。候选失败时进入 `REFLECT`，可回到 `LOCALIZE`、`PLAN` 或 `GENERATE_CANDIDATES`，也可有证据地停止。所有迁移使用既有 `AgentState.transition_to()`；完整任务受墙钟、模型次数、token、工具动作次数、命令时间和可选成本上限约束。

当前每轮只生成和验证一个补丁。失败后调用 `Runtime.reset()` 恢复基础提交，记录旧候选及失败事实，再按反思决定下一步。第六周才实现独立候选工作区、并发分支和验证金字塔，因此本周不声称具备多候选搜索。

## 每阶段职责

| 阶段 | 主要输入 | 产物与限制 |
|---|---|---|
| INITIALIZE | TaskSpec、Runtime | 检查干净基础提交，建立第四周 `RepoIndex`，创建 Issue Fact |
| UNDERSTAND | Issue | 模型返回严格 JSON：摘要、预期、当前状态、原文约束、未知项、复现计划；输出仍标记为模型推断 |
| REPRODUCE | `public_commands` | 通过 Runtime 执行公开命令，记录 Test/Failure 和退出码；命令无法启动属于基础设施错误 |
| LOCALIZE | Issue、实际测试输出 | 使用第四周 `localize()` 排出文件和符号 Top-K，并保存来源为静态分析的节点及代码片段 |
| PLAN | 定位、失败、旧反证 | 模型返回根因、文件/符号、预期改动、回归风险、验证与放弃条件；目标必须位于本轮 Top-K |
| GENERATE_CANDIDATES | 当前计划 | 模型返回 `patch` JSON 字段；Runtime 原子应用，实际修改文件必须与单文件计划一致；相同补丁摘要不能重试 |
| VERIFY_CANDIDATES | 已应用候选 | 执行所有公开命令并绑定候选 ID；记录每个验证结果；比较测试前后的 Git diff，拒绝测试副作用污染 |
| REFLECT | 最近实际失败 | 模型返回被否定预期、新事实候选、失败类别、假设状态、下一动作和理由；系统校验强状态必须有真实验证支持 |
| SELECT_AND_FINALIZE | 全部公开测试通过 | 从 Runtime 导出真实 diff，保存最终补丁；只声称公开内部验证通过，不声称 SWE-bench resolved |

模型输出不符合 Pydantic 阶段协议时直接失败，不能执行自由文本补丁。新策略不会让模型自行调用任意 Shell 命令；第五周的执行动作只来自任务公开测试命令和经过 Runtime 检查的补丁。第四周 `repo_map`/`find_symbol` 工具仍可在后续更开放的策略中注入，但本轮通过确定性的仓库索引投影给模型，不改变基线工具权限。

## Evidence Graph 与上下文

`src/patchflow/memory/evidence.py` 保存 Issue Fact、File、Symbol、Test、Failure、Stack Frame 类型定义、Hypothesis、Plan Step、Patch Attempt、Verification Result 和 Decision，以及支持、反驳、定义、修改、验证等关系。节点同时保存 `origin` 和 `source_ref`。Issue 原文为 `issue`，AST 定位为 `derived`，模型内容为 `model`，实际测试和补丁结果为 `runtime`/`verification`。模型说“测试失败”不会自动成为执行事实；确认/反驳假设必须引用方向一致的实际验证节点，候选验证关系必须匹配候选 ID。

完整事件轨迹写入 `<run_dir>/trajectory.jsonl`，当前结构化工作记忆原子保存到 `<run_dir>/evidence_graph.json`。`src/patchflow/memory/context.py` 将固定 Issue、当前状态、代码证据、当前失败、已尝试补丁和近期证据投影为有界请求，记录选中/丢弃节点及分区字节数。最近失败、当前修改文件、主假设和被反驳方案是不可静默丢弃的核心区；装不下时明确停止，而不是把关键反证裁掉。该字节估算只是请求容量的保守近似，真正 token 和费用仍按 provider 返回用量执行硬预算。

## 离线验收与真实入口

在 WSL `patchflow` 环境、项目根目录运行：

```bash
python -m pytest -q tests/test_evidence_memory.py tests/test_patchflow_agent.py tests/test_agent_cli.py
PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_patchflow_docker_integration.py
```

以上测试使用 FakeModel，不需要 API key，不调用付费服务。真实单任务入口需要你自己在 WSL 中设置 `PATCHFLOW_MODEL_API_KEY`，确认服务端模型 ID、函数/文本输出兼容性、价格和代码发送策略后，再显式执行下列命令。不要把密钥发到聊天或写入仓库：

```bash
python -m patchflow.agent_cli /tmp/patchflow-smoke/tasks.json --task-id micro-greet-strip --provider openai_compatible --base-url https://api.agicto.cn/v1 --model-id '你已确认可用的模型ID' --runs-root /tmp/patchflow-main-runs --allow-api-spend
```

没有 `--allow-api-spend` 时，入口在读取密钥、创建运行目录或启动容器前退出。本周未替你发送任何真实模型请求，不保证 AGICTO 上任何特定模型能稳定遵循严格 JSON 阶段协议。真实运行会向服务发送 Issue、有限代码片段和失败证据，并可能计费。

## 限制和阅读顺序

目前只支持单文件计划和每轮单候选；公开测试通过是内部验证，不代表隐藏测试通过。静态索引基于基础提交，失败后的重新定位使用新测试反馈，但第六周前不对补丁工作区做增量索引。临时 Docker 启动可能受本机 daemon 资源波动影响，基础设施错误与修复失败分开记录。第三周 baseline 仍保持原样，后续实验才能公平比较结构化记忆带来的收益。

推荐阅读顺序：

1. `src/patchflow/agent/decisions.py`：四类模型阶段输出。
2. `src/patchflow/memory/evidence.py`：来源、节点、边、假设状态和一致性。
3. `src/patchflow/memory/context.py`：六分区、预算和选择记录。
4. `src/patchflow/agent/patchflow.py`：从 `_initialize` 到 `_drive`，最后看 `run` 生命周期。
5. `src/patchflow/agent_cli.py`：Docker 隔离与显式付费门槛。
6. `tests/test_patchflow_agent.py` 和 `tests/test_evidence_memory.py`：成功、失败回跳、反证、预算和污染边界；最后看 Docker 集成测试。
