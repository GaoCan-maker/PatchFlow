# PatchFlow 开发进度

> 作用：记录每一步做了什么、为什么这样做、如何验证、还剩什么问题，以及下一步的明确范围。  
> 更新规则：每次开始开发前先更新“当前工作”，完成后更新“完成记录”和“下一步”。

## 1. 总体状态

| 项目 | 当前状态 |
|---|---|
| 当前里程碑 | 第 1 周：项目规格与领域骨架 |
| 当前步骤 | Step 1：初始化工程并建立核心领域契约（已完成） |
| 项目阶段 | 等待进入 Step 2 |
| 下一里程碑 | 第 2 周：LocalRuntime 与核心工具闭环 |
| 最后更新 | 2026-09-20 |

## 2. 当前工作

本轮目标是在不接入真实模型、不执行未知命令的前提下，建立后续所有模块共享的稳定边界：

- Python 工程骨架和依赖约束。
- TaskSpec、预算、路径策略和仓库描述。
- AgentState、阶段、运行状态和预算使用量。
- AgentEvent 与 JSONL 事件存储。
- Tool、Runtime、命令结果和补丁结果协议。
- RunManifest、run ID 和 artifact 目录约定。
- 配置模型和基础测试框架。
- ADR 模板和首个架构决策。

## 3. 已完成记录

### Step 0：完成项目总体设计

- 已完成 `PatchFlow_项目设计文档.md`。
- 已明确项目目标、非目标、架构、SWE-bench 接入方式、实验方案和十周路线。
- 已确定 Inference Phase 与 Evaluation Phase 必须隔离。

### Step 1：初始化工程并建立领域骨架

状态：已完成并通过测试。

已完成：

- 建立 `src` 布局和 `pyproject.toml`。
- 定义任务、预算、路径、事件、状态、工具和 Runtime 核心类型。
- 建立追加式 JSONL 事件存储和 manifest 存储。
- 建立 run 初始化流程，能够生成空运行的标准 artifact。
- 建立 pytest 基础测试。
- 建立 ADR 模板，并记录“不直接依赖重型 Agent 框架”的首个决策。

设计约束：

- Pydantic 用于配置和外部输入校验。
- dataclass 用于内部执行结果和轻量可变状态。
- 领域层不依赖模型 SDK、Docker、CLI 或 SWE-bench。
- Runtime 和 Tool 在本阶段只定义协议，不提供高风险实现。

## 4. 验证记录

本轮应完成以下验证：

- [x] 所有 pytest 测试通过。
- [x] `TaskSpec` 能拒绝空问题、非法路径和空命令。
- [x] Agent 状态能执行合法迁移并拒绝非法迁移。
- [x] JSONL EventStore 能按顺序写入并读取事件。
- [x] run 初始化能创建 manifest、trajectory 和标准目录。
- [x] manifest 可以保存并重新加载。
- [x] 全部 Python 源文件通过 AST 语法解析。
- [ ] Ruff 静态检查通过；当前 Anaconda 环境尚未安装 Ruff，待安装开发依赖后执行。

验证环境：

- Python：3.13.5（Anaconda）。
- Pydantic：2.10.3。
- pytest：8.3.4。

验证结果：

- pytest 共收集并通过 15 个测试，结果为 `15 passed in 0.15s`。
- AST 解析检查覆盖 `src` 和 `tests` 下 25 个 Python 文件，全部通过。
- 首次测试受到 Codex 沙箱临时目录权限影响；改用项目内临时目录后完整通过。该问题属于执行环境限制，不是测试失败。
- Ruff 未运行，原因是当前解释器中不存在 `ruff` 模块；`pyproject.toml` 已将 Ruff 声明为开发依赖。

## 5. 当前已知限制

- 尚未实现 LocalRuntime 和 DockerRuntime。
- 尚未实现文件、搜索、patch、测试与 Shell 工具。
- 尚未接入真实模型。
- 尚未实现完整 Agent 状态机；当前只提供阶段与合法迁移基础。
- 尚未实现 Evidence Graph、候选补丁和验证金字塔。
- 尚未接入 SWE-bench。

这些限制符合第 1 周范围，不属于本轮缺陷。

## 6. 下一步计划

下一步进入 Step 2：LocalRuntime 与核心工具闭环。

计划顺序：

1. 实现安全路径解析器，处理绝对路径、`..` 和符号链接逃逸。
2. 实现 LocalRuntime，只允许在显式临时工作区中运行。
3. 实现命令执行结果、单次 timeout、stdout/stderr 捕获与截断。
4. 实现 `search_text`、`read_code`、`apply_patch`、`git_diff` 和 `run_tests`。
5. 为正常、失败、超时、非法路径和回滚建立测试。
6. 完成本地临时 Git 仓库中的“读取 -> 修改 -> 测试 -> 导出 diff”闭环。
7. 更新本文档，并记录 Runtime 安全边界 ADR。

本阶段暂不实现 DockerRuntime；先让 Runtime 抽象和工具语义在可控临时目录中稳定，再替换底层执行环境。

## 7. 变更纪律

- 每次只推进一个明确步骤。
- 每个步骤都必须包含实现、测试、进度更新和下一步。
- 新增重要依赖前先记录原因。
- 影响安全、数据格式或实验公平性的选择必须新增 ADR。
- 不用未验证的功能勾选项目总体验收项。
- 不把基础设施失败伪装成 Agent 能力失败。
