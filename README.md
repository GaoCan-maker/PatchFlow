# PatchFlow

PatchFlow 是一个验证驱动的仓库级 Code Agent。系统计划接收代码仓库、基础提交和 Issue 描述，在隔离环境中完成问题理解、故障复现、代码定位、候选补丁生成、测试验证和最终补丁输出。

当前仓库已经完成领域骨架、LocalRuntime 与 DockerRuntime 工具闭环，以及 FakeModel 驱动的最小 Linear ReAct Agent。尚未接入真实模型 provider 和完整 PatchFlow 策略。

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

## 文档入口

- `PatchFlow_项目设计文档.md`：完整需求、架构与实验设计。
- `DEVELOPMENT_PROGRESS.md`：每轮完成内容、验证结果和下一步计划。
- `docs/adr/`：重大架构决策记录。

## 当前边界

- 当前版本只提供 FakeModel，不调用真实 LLM 或 SWE-bench，因此不需要配置模型 API key。
- FakeModel 的测试通过只证明编排和安全边界符合预期，不代表模型有真实修复能力或隐藏测试通过。
- `LocalRuntime` 共享宿主机内核、网络和当前用户权限，只适合可信代码与开发测试。
- 不要把自己的工作仓库直接交给 `LocalRuntime`；应把任务复制到专用隔离根的子目录，并确保启动前 Git 工作区完全干净。
- DockerRuntime 已完成基础集成验收，但它不是通用安全边界的证明；正式执行未知仓库、SWE-bench 或模型生成的高风险命令前仍需按部署环境进行风险审查。
