# PatchFlow 开发进度

> 作用：记录每一步做了什么、为什么这样做、如何验证、还剩什么问题，以及下一步的明确范围。  
> 更新规则：每次开始开发前先阅读本文，完成后更新完成记录、验证记录、已知限制和下一步。

## 1. 总体状态

| 项目 | 当前状态 |
|---|---|
| 当前里程碑 | 第 2 周：Runtime 与工具闭环 |
| 当前步骤 | Step 2A：LocalRuntime 与核心结构化工具（已完成） |
| 项目阶段 | 本地可信仓库闭环可运行，尚未达到未知代码安全执行条件 |
| 下一步骤 | Step 2B：DockerRuntime 最小安全版本 |
| 最后更新 | 2026-09-20 |

## 2. 本轮目标

在不接入真实模型、不执行未知仓库的前提下，把 Step 1 的 `Runtime` 与 `Tool` 协议落成可测试实现：

- 用真实路径解析和任务路径策略限制文件访问。
- 在显式隔离根的严格子目录中运行可信临时 Git 仓库。
- 统一命令成功、失败、启动失败、超时和输出截断结果。
- 用统一 diff 完成“修改 -> 测试 -> diff -> 回滚”闭环。
- 为 Agent 后续调用提供严格参数校验和结构化 Observation。

## 3. 已完成记录

### Step 0：项目总体设计

- 完成 `PatchFlow_项目设计文档.md`。
- 明确 Agent Harness、Runtime、Evaluation Harness 和 SWE-bench 的职责边界。
- 确定 baseline、消融实验、指标与十周开发路线。

### Step 1：工程与领域骨架

- 建立 `src` 工程布局、依赖约束和测试框架。
- 定义 TaskSpec、AgentState、Event、Tool、Runtime、RunManifest 等核心契约。
- 实现 JSONL 事件存储、manifest 存储和 run 初始化。
- 记录 ADR-0001：核心 Agent Harness 不直接依赖重型 Agent 框架。

### Step 2A：LocalRuntime 与核心工具闭环

状态：已完成并通过 WSL 实测。

#### 路径与工作区安全

- 新增 `WorkspacePathResolver`。
- 拒绝绝对路径、`..`、空路径、NUL 字符和工作区外真实路径。
- 在解析符号链接后再次检查工作区边界，阻止 symlink escape。
- 支持 `allowed_paths`、`denied_paths` 和 `read_only_paths`。
- `workspace` 必须是 `isolation_root` 的严格子目录；隔离根不能是文件系统根。
- Runtime 启动前验证仓库根、HEAD、base commit 和完全干净的 Git 状态。
- 启动阶段不自动 reset，避免误删调用方已有修改。

#### 命令执行

- 新增异步 `LocalRuntime.execute()`，命令只接受参数元组，不经过 Shell。
- 固定命令工作目录，不允许调用方指定任意 cwd。
- 子进程仅继承环境变量允许列表，未知变量和 API key 默认不传入。
- 捕获 stdout、stderr、退出码、耗时、超时和终止原因。
- 使用头尾保留策略截断长输出，并显式标记原始长度。
- 控制面 Git 命令使用独立输出预算，避免 Agent 的小输出上限截断仓库路径或提交 SHA。
- POSIX/WSL 下为命令创建独立会话，超时时先终止进程组，再在宽限期后强制结束。

#### 补丁与回滚

- `apply_patch` 同时校验统一 diff 的旧路径和新路径，覆盖修改、新增、删除和重命名边界。
- 补丁先执行 `git apply --check`，检查通过后才正式应用。
- 路径违规、只读文件和不可应用补丁都返回结构化拒绝结果。
- `get_diff` 导出相对于启动 base commit 的标准 Git diff。
- `reset` 只在已完成隔离检查的工作区执行 `git reset --hard` 与 `git clean -fd`。

#### 结构化工具

- `search_text`：使用 `git grep` 搜索已跟踪文本，区分无匹配与命令失败。
- `read_code`：按一开始计数的行范围读取代码，返回稳定行号并限制字符数。
- `apply_patch`：应用经过 Runtime 安全检查的统一 diff。
- `git_diff`：返回当前候选相对于 base commit 的 diff。
- `run_tests`：运行参数数组形式的测试命令。
- `run_command`：保留给高权限一般命令，权限级别与测试工具分离。
- 所有工具参数使用 Pydantic `extra="forbid"` 严格校验。
- 统一返回 `ToolResult`，包含调用 ID、成功状态、结构化数据、摘要、错误类型、耗时和截断状态。

#### 工程决策

- 新增 ADR-0002，明确 LocalRuntime 是开发后端而非不可信代码沙箱。
- Ruff 忽略 `E501` 与 `RUF001/2/3`：逐行中文注释会自然产生长行和全角标点，这些规则只影响排版。
- 当前阶段没有新增运行时依赖，也不需要模型 API key。

## 4. 验证记录

验证环境：

- 操作系统：WSL 2，Ubuntu 22.04。
- Conda 环境：`patchflow`。
- Python：3.11.16。
- Pydantic：2.13.5。
- pytest：8.4.2。
- Ruff：0.16.8。

自动验证结果：

- [x] 全量 pytest：`32 passed in 2.06s`。
- [x] 新增 Runtime、工具和测试文件通过 Ruff：`All checks passed!`。
- [x] 命令成功、非零退出、可执行文件不存在、长输出和硬超时均有测试。
- [x] 有效补丁可以应用、测试、导出 diff 并回滚。
- [x] 不可应用补丁与只读路径补丁不留下部分修改。
- [x] 绝对路径、父目录跳转、`.git`、只读路径和符号链接逃逸被拒绝。
- [x] 脏仓库和 HEAD/base commit 不一致时拒绝启动，原有文件不会被清理。
- [x] 集成测试走通 `search -> read -> patch -> pytest -> diff -> reset`。

测试过程中发现并修复：

- 首轮结果为 `30 passed, 2 failed`。
- 一个失败来自测试文案断言，已修正为实际错误文案。
- 另一个失败暴露真实设计问题：较小输出预算会截断内部 `git rev-parse --show-toplevel` 结果。现在控制面 Git 输出与 Agent Observation 输出使用独立预算。

静态检查说明：

- 本步骤新增代码全部通过 Ruff。
- 全仓库 Ruff 仍会报告 Step 1 代码中的 `UP017`，即建议把 `timezone.utc` 改成 Python 3.11 的 `datetime.UTC`；这是无行为影响的既有风格债务，未在本步骤顺手修改。

## 5. 当前安全边界与限制

- LocalRuntime 不是 Docker 沙箱，共享宿主机内核、网络和当前 Linux 用户权限。
- LocalRuntime 只允许用于可信代码和专用临时仓库，不能直接运行未知 GitHub 仓库。
- 当前没有 CPU、内存、PID、磁盘和网络级资源限制。
- Windows 原生环境只能可靠终止直接子进程；进程组终止能力以 Linux/WSL 为目标平台。
- `git clean -fd` 不删除 ignored 文件，因此缓存可能保留；后续候选隔离将用独立 worktree 或容器处理。
- patch 路径暂不支持 Git 的带引号转义文件名。
- `search_text` 当前只搜索 Git 已跟踪文件；`repo_map` 和 `find_symbol` 尚未实现。
- 尚未接入日志事件、Agent 状态机、模型适配器、上下文管理、候选分支和验证金字塔。
- 尚未接入 SWE-bench Inference/Evaluation Harness。

## 6. 下一步计划

下一步进入 Step 2B：DockerRuntime 最小安全版本。

计划顺序：

1. 检查 WSL 中 Docker Engine / Docker Desktop 集成是否可用，并记录版本与资源条件。
2. 定义 `DockerRuntimeConfig`：镜像、CPU、内存、PID、网络、只读挂载、临时目录和超时。
3. 实现最小 DockerRuntime，复用现有 `Runtime` 协议和 `CommandResult`。
4. 默认禁用网络，不挂载用户主目录、SSH、云凭据和 Docker socket。
5. 让任务工作区以受控方式进入容器，并支持 base commit、补丁、diff 和 reset。
6. 将 LocalRuntime 工具集测试参数化，使同一组契约测试可以验证两个 Runtime。
7. 增加容器超时、资源限制、环境变量泄漏和任务间隔离测试。
8. 更新 ADR、README 和本文档。

进入 Step 3 的门槛：DockerRuntime 最小版本及其安全测试完成后，再实现 deterministic fake model 驱动的 Baseline Agent。真实模型 API 会在模型适配器阶段才需要配置。

## 7. 变更纪律

- 每次只推进一个明确步骤。
- 每个步骤都包含实现、测试、进度更新和下一步。
- 新增重要依赖前先记录原因。
- 影响安全、数据格式或实验公平性的选择必须新增 ADR。
- 不把 LocalRuntime 描述成安全沙箱。
- 不把基础设施失败计入 Agent 能力失败。
- 不在未确认的用户工作区执行破坏性 reset 或 clean。
