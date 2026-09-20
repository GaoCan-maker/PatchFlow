# PatchFlow

PatchFlow 是一个验证驱动的仓库级 Code Agent。系统计划接收代码仓库、基础提交和 Issue 描述，在隔离环境中完成问题理解、故障复现、代码定位、候选补丁生成、测试验证和最终补丁输出。

当前仓库处于第 1 阶段：项目规格与领域骨架。此阶段不接入真实模型，也不执行未知仓库命令，目标是先稳定后续模块共同依赖的数据契约、事件协议和运行产物格式。

## 当前能力

- 使用严格校验的 `TaskSpec` 描述本地、MicroSWE 和 SWE-bench 任务。
- 使用 `Budget` 与 `BudgetUsage` 描述预算上限和实际消耗。
- 使用追加式 `AgentEvent` 记录可回放事件。
- 使用 `AgentState` 表示可恢复的 Agent 当前状态。
- 定义 `Tool` 和 `Runtime` 协议，但暂不提供实际命令执行器。
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

当前版本不会调用 LLM、Docker 或 SWE-bench，也不会在用户仓库中执行命令。Runtime 和 Tool 目前只是协议；它们的安全实现属于下一阶段。

