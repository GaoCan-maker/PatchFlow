# PatchFlow

PatchFlow 是一个验证驱动的仓库级 Code Agent。系统计划接收代码仓库、基础提交和 Issue 描述，在隔离环境中完成问题理解、故障复现、代码定位、候选补丁生成、测试验证和最终补丁输出。

当前仓库已经完成领域骨架、LocalRuntime 与 DockerRuntime 工具闭环、三个第三周基线、Hybrid Localization、显式状态机、Evidence Graph、候选分支、验证金字塔、SWE-bench 轻量适配层，以及可选的 OpenAI Chat Completions 模型适配器。尚未完成真实 API 能力实验、官方 SWE-bench gold/五题端到端验收和正式大规模统计。

## 当前能力

- 使用严格校验的 `TaskSpec` 描述本地、MicroSWE 和 SWE-bench 任务。
- 使用 `Budget` 与 `BudgetUsage` 描述预算上限和实际消耗。
- 使用追加式 `AgentEvent` 记录可回放事件。
- 使用 `AgentState` 表示可恢复的 Agent 当前状态。
- 提供异步 `LocalRuntime`，支持命令执行、超时、stdout/stderr 捕获、确定性截断、补丁、diff 和回滚。
- 提供 DockerRuntime：只读挂载源仓库，在容器 tmpfs 中复制出独立可写副本；默认禁网、非 root、限制 CPU/内存/PID，命令超时销毁容器。
- 两种 Runtime 的 `get_diff` 均包含受 Git 跟踪的改动和未忽略的新文件；Docker CLI 输出使用有界缓冲，避免大量输出占满宿主机内存。
- 使用严格子目录、Git 基础提交、干净工作区、路径策略和符号链接解析约束本地执行范围。
- 提供 `search_text`、`read_code`、`apply_patch`、`git_diff`、`run_tests` 和 `run_command` 结构化工具。
- 使用 Pydantic 严格校验工具参数，并将参数错误、权限拒绝、非零退出和超时转换成结构化观察。
- 为每次运行创建唯一 run ID、manifest、轨迹和 artifact 目录。
- 使用可脚本化 FakeModel 驱动线性 Agent；工具反馈、预算消耗和最终决定写入可回放轨迹。
- `OneShotAgent` 固定预算读取已跟踪 Python 文件，只向模型发送一次请求，不在生成阶段运行测试；`BashOnlyAgent` 只暴露受控通用命令并强制 DockerRuntime；`LinearReactAgent` 使用细粒度工具和测试反馈。
- `ContextBuilder` 保留最新完整工具调用/反馈对，压缩较旧轨迹并记录 `CONTEXT_COMPACTED`；字节估计不是模型精确 token 计数。
- `BatchRunner` 为每个任务/策略创建独立 Agent 容器和独立评测容器，重新应用补丁并运行全部公开测试；报告区分 Agent 状态、补丁可应用性、公开测试成功率与基础设施错误。
- 使用 Pydantic 工具参数模式生成原生函数调用定义；OpenAIChatModel 严格校验单个工具调用并保留 `tool_call_id`。
- 模型适配器支持官方 OpenAI 地址和自定义 HTTPS 兼容地址；密钥只从宿主环境或排除序列化的 SecretStr 读取。
- 只有最新修改经过公开测试且最终 diff 非空时才保存 `final.patch`；评测私有引用不传给模型。
- 提供基础单元测试和确定性的 fake 对象测试入口。

## 环境要求

- Python 3.11 或更高版本。
- 推荐在独立虚拟环境中开发。

## 开发安装

在项目根目录执行：

```powershell
python -m pip install -e ".[dev]"
```

运行测试：

```powershell
python -m pytest
```

运行静态检查：

```powershell
python -m ruff check .
```

## DockerRuntime 验收

在 Windows 上启动 Docker Desktop，打开 `Settings > Resources > WSL Integration`，为 `Ubuntu-22.04` 开启集成并应用。随后在 WSL 项目根目录执行：

```bash
docker version
docker build -t patchflow-runtime:py311 .
PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_docker_integration.py
PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_linear_agent.py
```

镜像构建需要联网下载基础镜像和 Python 依赖；任务运行默认 `--network none`。`.dockerignore` 只允许包代码与必要元数据进入构建上下文。WSL2 + Docker Desktop 环境已通过真实容器集成测试，包括工具闭环、超时清理、任务隔离和大输出截断；每次更改后仍应重新运行测试。

## 可选模型适配

基础开发和测试不需要 API 密钥。若要使用适配器，在 WSL 的 `patchflow` 环境中安装可选依赖：`python -m pip install -e ".[dev,model-openai]"`。当前环境已安装并离线验证 SDK 3.16.2；测试只使用传输替身，不发出付费请求。

- 官方 OpenAI 服务：将 `model.provider` 设为 `openai_chat`、`model.model` 设为你有权限使用的模型 ID，并在宿主 WSL 环境设置 `OPENAI_API_KEY`。不需要把密钥写进仓库。
- 支持 Chat Completions 的其他服务：将 `model.provider` 设为 `openai_compatible`，显式设置 `model.base_url`，并在宿主设置 `PATCHFLOW_MODEL_API_KEY`。远程地址必须是 HTTPS；本机开发地址可使用 HTTP。具体模型与功能兼容性需实测，不能由接口名称推定。
- 成本预算只有在同时配置输入和输出 token 单价时才可启用；否则适配器将成本标为未知，遇到 `max_cost_usd` 会停止。单价需要按实际提供商和模型更新。

模型密钥仅供宿主 Agent 调用 API，不传入 DockerRuntime。真实模型 CLI 已提供，但必须显式加 `--allow-api-spend`，本项目尚未用真实账号或模型完成联调。将仓库代码发送给任何模型服务前，请先确认仓库数据的授权与服务条款。

### AGICTO API 接入（WSL）

AGICTO 是第三方 OpenAI 兼容服务，本项目无需增加专用 SDK：使用现有 `openai_compatible` 适配器和 `https://api.agicto.cn/v1` 即可。AGICTO 文档给出的环境变量示例是 `AGICTO_API_KEY`，但 **PatchFlow 当前代码读取的是 `PATCHFLOW_MODEL_API_KEY`**；不要误设为 `OPENAI_API_KEY`，也不要把真实密钥写进代码、任务 JSON、README 或聊天消息。

先在项目根目录和 `patchflow` Conda 环境安装可选 SDK，然后在同一个 WSL 终端无回显地输入密钥：

```bash
cd "/mnt/c/Users/73621/Desktop/code agent"
conda activate patchflow
python -m pip install -e '.[dev,model-openai]'
read -r -s -p 'AGICTO API Key: ' PATCHFLOW_MODEL_API_KEY
printf '\n'
export PATCHFLOW_MODEL_API_KEY
test -n "$PATCHFLOW_MODEL_API_KEY" && echo '密钥已设置于当前 WSL 终端'
```

先准备全为合成代码的两任务数据集并离线校验；`prepare-smoke` 要求目标目录不存在或为空：

```bash
python -m patchflow.cli prepare-smoke "$HOME/patchflow-smoke-agicto"
python -m patchflow.cli validate "$HOME/patchflow-smoke-agicto/tasks.json"
docker image inspect patchflow-runtime:py311
```

只有你确认愿意发送烟测代码、产生 API 费用后，再运行首次真实请求。`gpt-4.1-mini` 只是模型广场中的示例 ID，使用前请核对你账户可用的模型及函数调用支持：

```bash
python -m patchflow.cli run-batch "$HOME/patchflow-smoke-agicto/tasks.json" \
  --provider openai_compatible \
  --base-url https://api.agicto.cn/v1 \
  --model-id gpt-4.1-mini \
  --strategy one_shot \
  --runs-root "$HOME/patchflow-runs-agicto" \
  --report "$HOME/patchflow-report-agicto.json" \
  --allow-api-spend
python -m json.tool "$HOME/patchflow-report-agicto.json"
```

这份烟测含两个任务，One-shot 每任务最多调用模型一次；它只验证文本补丁输出，不验证函数调用。确认后再单独试 `--strategy linear_react`，以验证原生工具调用和多轮反馈；后者可能每任务调用多次。AGICTO 文档说明函数调用能力依赖具体模型，OpenAI 兼容格式也不保证所有可选参数或返回用量完全一致。若服务拒绝 `max_completion_tokens`、`parallel_tool_calls`，或不返回输入/输出 token 用量，当前严格适配器会失败，需要针对该模型做兼容验证后再修改。**当前 CLI 的 `--allow-api-spend` 不是金额上限，未配置模型单价时 JSON 报告中的 `cost_usd: null` 不代表免费。** 请在 AGICTO 后台检查可用的密钥额度或限额设置并关注使用日志。

## 第三周批量烟测

在 WSL 的 `patchflow` 环境和项目根目录运行以下命令；数据集路径应指向一个不存在或空的目录：

```bash
python -m patchflow.cli prepare-smoke /tmp/patchflow-smoke
python -m patchflow.cli validate /tmp/patchflow-smoke/tasks.json
PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_week3_baselines.py
```

前两条命令完全离线，不需要 API key。两任务烟测只用于验证实验流水线，不足以比较策略能力，也不是设计文档计划的 30-50 任务正式 MicroSWE 集。新增测试还会验证任务修复前失败、假模型三策略的六条路径、补丁独立复测，以及错误补丁被拒绝。

真实模型批量调用示例；此命令**可能产生费用并发送仓库代码**，必须由你主动提供密钥和付费开关：

```bash
export OPENAI_API_KEY='你的密钥'
python -m patchflow.cli run-batch /tmp/patchflow-smoke/tasks.json --provider openai_chat --model-id '你确认可用的模型ID' --runs-root /tmp/patchflow-runs --report /tmp/patchflow-report.json --allow-api-spend
```

兼容服务需改用 `--provider openai_compatible --base-url https://...`，并设置 `PATCHFLOW_MODEL_API_KEY`。需要先自行确认模型是否支持 Chat Completions 原生函数调用、输出 token 参数和代码发送策略。报告只统计公开测试；未配置模型单价时 `cost_usd` 为 `null`，不要把它当零成本。Bash-only 的通用命令具有高风险，即使容器默认禁网、非 root、只读源仓库，也只应在经审查的测试机器运行未知任务。

## 文档入口

- `PatchFlow_项目设计文档.md`：完整需求、架构与实验设计。
- `DEVELOPMENT_PROGRESS.md`：每轮完成内容、验证结果和下一步计划。
- `docs/adr/`：重大架构决策记录。

## 当前边界

- 当前版本已提供真实模型适配器，但现有自动测试不调用真实 LLM 或 SWE-bench，因此运行测试不需要 API key。
- 真实 API 请求尚未验收；SDK 自动重试已禁用，避免不受 Agent 预算约束的隐藏请求。命令行真实批量运行需要用户显式确认花费。
- FakeModel 的测试通过只证明编排和安全边界符合预期，不代表模型有真实修复能力或隐藏测试通过。
- `LocalRuntime` 共享宿主机内核、网络和当前用户权限，只适合可信代码与开发测试。
- 不要把自己的工作仓库直接交给 `LocalRuntime`；应把任务复制到专用隔离根的子目录，并确保启动前 Git 工作区完全干净。
- DockerRuntime 已完成基础集成验收，但它不是通用安全边界的证明；正式执行未知仓库、SWE-bench 或模型生成的高风险命令前仍需按部署环境进行风险审查。

## 第五周主策略

已新增独立的 `PatchFlowAgent`：显式阶段状态机、可溯源 Evidence Graph、有界分区上下文及失败后的结构化反思。它不修改第三周三条 baseline。离线 FakeModel 和真实 Docker 集成测试均不需要 API key；真实单任务入口 `python -m patchflow.agent_cli` 必须显式提供 `--allow-api-spend`，并自行设置模型服务密钥。运行方法、代码阅读顺序和单候选限制见 `docs/week5_state_machine.md`。

## 第六周候选分支

已新增并接入候选分支与验证金字塔模块：候选从同一 `base_commit` 创建独立 Git 副本，按规范化补丁摘要去重，在受限并发下运行 V0 到 V5 验证层级，并使用硬约束优先的确定性规则选择候选。`max_candidates_per_round=1` 保留第五周单候选路径，设置为 2 到 4 时主策略会生成多个结构化补丁并验证真实 diff；第六周模块的运行方式和阅读顺序见 `docs/week6_candidate_branching.md`。

## 第七周 SWE-bench Adapter

已新增 SWE-bench 原始记录到安全 `TaskSpec` 的转换、官方三字段 prediction JSONL 导出、`swebench.harness.run_evaluation` 参数数组调用、进程组超时、模型密钥移除和多版本结果解析。推理任务写盘前会递归拒绝 gold patch、测试补丁、`FAIL_TO_PASS` 与 `PASS_TO_PASS`；官方报告中的 unresolved、评测错误、空补丁和结果缺失分别统计。离线测试不安装官方包、不下载数据集也不启动 Docker；真实 gold 与五题 Agent 预测验收需要独立官方环境和显式 `--allow-harness-run`。安装、命令、阅读顺序和当前限制见 `docs/week7_swebench.md`。

## 阶段 7.5 实验接线

阶段 7.5 增加固定子集仓库准备、SWE-bench 官方实例镜像推理、严格 JSON 模型探针、`patch_generated` benchmark 终态、运行工件到 prediction 的自动收集，以及 `patchflow`/`one_shot` 两种必要实验策略。SWE-bench 推理不读取隐藏测试，只有官方 Harness 可以给出 resolved 结论；`deepseek-v3.2` 等模型 ID 通过 CLI 的 `--model-id` 传入，不写入源码或密钥变量。完整操作顺序和精简实验矩阵见 `docs/week7_5_experiment_readiness.md`。
