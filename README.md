# PatchFlow

PatchFlow 是一个验证驱动的仓库级 Code Agent。系统计划接收代码仓库、基础提交和 Issue 描述，在隔离环境中完成问题理解、故障复现、代码定位、候选补丁生成、测试验证和最终补丁输出。

当前仓库已经完成领域骨架和 LocalRuntime 工具闭环。现阶段不接入真实模型；命令执行仅用于已复制到显式隔离目录中的可信开发仓库，不能把 LocalRuntime 当作未知代码沙箱。

## 当前能力

- 使用严格校验的 `TaskSpec` 描述本地、MicroSWE 和 SWE-bench 任务。
- 使用 `Budget` 与 `BudgetUsage` 描述预算上限和实际消耗。
- 使用追加式 `AgentEvent` 记录可回放事件。
- 使用 `AgentState` 表示可恢复的 Agent 当前状态。
- 提供异步 `LocalRuntime`，支持命令执行、超时、stdout/stderr 捕获、确定性截断、补丁、diff 和回滚。
- 使用严格子目录、Git 基础提交、干净工作区、路径策略和符号链接解析约束本地执行范围。
- 提供 `search_text`、`read_code`、`apply_patch`、`git_diff`、`run_tests` 和 `run_command` 结构化工具。
- 使用 Pydantic 严格校验工具参数，并将参数错误、权限拒绝、非零退出和超时转换成结构化观察。
- 为每次运行创建唯一 run ID、manifest、轨迹和 artifact 目录。
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

## 文档入口

- `PatchFlow_项目设计文档.md`：完整需求、架构与实验设计。
- `DEVELOPMENT_PROGRESS.md`：每轮完成内容、验证结果和下一步计划。
- `docs/adr/`：重大架构决策记录。

## 当前边界

- 当前版本不会调用 LLM、Docker 或 SWE-bench，因此不需要配置模型 API key。
- `LocalRuntime` 共享宿主机内核、网络和当前用户权限，只适合可信代码与开发测试。
- 不要把自己的工作仓库直接交给 `LocalRuntime`；应把任务复制到专用隔离根的子目录，并确保启动前 Git 工作区完全干净。
- 正式执行未知仓库、SWE-bench 或模型生成的高风险命令前，必须先完成 DockerRuntime 的网络、资源、挂载和凭据隔离。
