# PatchFlow 开发进度

> 作用：记录每一步做了什么、为什么这样做、如何验证、还剩什么问题，以及下一步的明确范围。  
> 更新规则：每次开始开发前先阅读本文，完成后更新完成记录、验证记录、已知限制和下一步。

## 1. 总体状态

| 项目 | 当前状态 |
|---|---|
| 当前里程碑 | 第 3 周 Baseline Agent 开发中 |
| 当前步骤 | Step 3A：模型协议、FakeModel 与最小 Linear ReAct（已完成） |
| 项目阶段 | FakeModel 驱动的 Agent 闭环已在 LocalRuntime 和 DockerRuntime 通过测试 |
| 下一步骤 | Step 3B：真实 provider 适配、上下文限制与剩余 baseline |
| 最后更新 | 2026-09-21 |

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

### Step 2B：DockerRuntime 最小安全版本

状态：最小 Docker 后端已通过真实容器集成测试；以下环境探测记录保留当时状态，最新验收见 Step 2C。

已完成：

- 新增 `DockerRuntimeConfig`，使用 Pydantic 限定镜像、CPU、内存、PID、tmpfs 和输出预算。
- 新增 `DockerRuntime`，实现现有 Runtime 协议；模型与工具层无需知道 Docker CLI 细节。
- 只读挂载本地源仓库到 `/source`，以非 root 用户复制到 `/work/repo` 私有 tmpfs；命令、Git、补丁、测试均在容器内执行。
- Docker 启动固定使用 `--network none`、`--read-only`、`--cap-drop ALL`、`no-new-privileges`、CPU/内存/PID 上限及受限 tmpfs。
- 容器只继承显式设置的 HOME 和 PYTHONDONTWRITEBYTECODE，不透传模型 API key；宿主 Docker CLI 也使用最小环境变量列表。
- 读取文件在宿主和容器中双重检查真实路径与路径策略，防止容器内新增符号链接绕过 `.git` 等拒绝规则。
- 所有容器命令使用参数数组和硬超时；任意容器命令超时后销毁容器，避免残留后台进程。
- 新增最小 Python/Git/pytest Dockerfile 和 `.dockerignore` 构建上下文白名单。
- 新增无 daemon 单测和显式开关控制的真实 Docker 集成测试。
- 新增 ADR-0003，记录容器复制、只读挂载、超时销毁与剩余风险。

实现时的环境探测结果（历史记录，现已解决）：

- `Ubuntu-22.04` 当前能找到 Docker Desktop Windows 路径下的 `docker` 入口，但运行时提示本发行版没有启用 WSL Integration。
- 因此本轮未构建镜像、未启动容器，也未执行真实 Docker 集成测试。
- 当时不需要注册模型 API；随后已启用 WSL Integration 并构建镜像，见 Step 2C 验证。

验证结果：

- WSL `patchflow` 环境全量 pytest：`35 passed, 1 skipped in 2.17s`，跳过项为真实容器集成测试。
- 本轮新增 Python 文件通过 Ruff：`All checks passed!`。
- 单测验证安全启动参数、只读挂载、非 root 用户、资源限制、补丁预检、协议兼容与超时清理的编排逻辑。

当时未完成或待实测（后续状态见 Step 2C）：

- 真实 Docker 启动、只读挂载权限、容器内 Git/pytest、环境变量隔离与符号链接策略测试。
- 当前 Git diff 与 Step 2A 一样仅包含已跟踪文件修改；新增文件如何纳入最终 patch 需在正式 Agent 输出前解决。
- Docker CLI 当前使用 `communicate()` 捕获完整输出后截断，极端大量输出仍可能占用宿主内存；需要流式上限。
- 当前没有独立 Docker 磁盘配额与高级 seccomp/AppArmor 策略，也没有未知恶意仓库的红队验收。

### Step 2C：补丁完整性、流式输出与真实容器验收

状态：已完成；本阶段无需模型 API。

已完成：

- 在 WSL2 + Docker Desktop 中复跑用户报告的集成测试，结果 `1 passed in 2.37s`。
- LocalRuntime 与 DockerRuntime 的 `get_diff()` 现在合并已跟踪文件修改，以及 `git ls-files --others --exclude-standard -z` 枚举的未跟踪新文件。
- 新文件通过 `git diff --no-index --binary` 与空设备比较生成标准可应用 patch；不修改 Git 索引。
- Git 列表、单文件差异或合并补丁超出控制面预算时显式失败，不返回截断的“最终 patch”。
- 新增 `OutputAccumulator`，Docker CLI stdout/stderr 在异步读取时仅保存有限头尾内容，并通过增量 UTF-8 解码保留跨块字符。
- 新增真实容器测试：大输出截断、命令超时后的容器销毁、两个任务容器的工作区隔离。
- 将工具闭环测试参数化，确认同一组结构化工具可在 LocalRuntime 和 DockerRuntime 上工作。
- 临时 Python 仓库测试夹具增加 `.gitignore`，避免 pytest 缓存被误认为候选源码文件。
- 新增 ADR-0004，记录补丁完整性与流式输出决策。

验证结果：

- WSL `patchflow` 环境在 `PATCHFLOW_RUN_DOCKER_TESTS=1` 下全量 pytest：`41 passed in 9.30s`。
- 真实 Docker 集成测试单独运行：`3 passed in 5.47s`。
- 修改的 Runtime 与测试文件 Ruff：`All checks passed!`。
- LocalRuntime 测试验证“已跟踪修改 + 新文件”组合 patch 经 reset 后可重新应用。
- Docker 测试验证新文件进入 diff、20 万字符输出受限、超时销毁容器、两个任务互不污染。

仍需注意：

- 被 `.gitignore` 忽略的文件不会进入最终 patch，这是当前明确约定。
- Git 带引号转义的特殊路径尚不受 patch 路径解析器支持；超大补丁会显式失败。
- LocalRuntime 的底层子进程仍在完整读取后截断，尚未复用 Docker 的流式输出收集器；它仍只用于可信仓库。
- Docker 容器有默认资源限制，但不是绝对安全沙箱；尚未完成恶意仓库红队测试与高级 seccomp/AppArmor 策略。

### Step 3A：最小 Linear ReAct Agent

状态：已完成；本阶段仍不需要模型 API key。

已完成：

- 新增 `ModelRequest`、`ModelResponse`、`ModelUsage` 和异步 `Model` 协议；每次回复必须是单个工具调用或结束说明。
- 模型请求仅包含公开任务 ID、Issue 文本、线性历史和已注册工具声明；不传递 `TaskSpec.evaluation_ref` 等评测私有字段。
- 新增脚本式 `FakeModel`，按预设回复产生确定性工具轨迹，并保存请求供测试检查。
- 新增 `LinearReactAgent`：复用已有 Runtime、Tool、RunContext、AgentState、RunManifest 和 JSONL 事件存储。
- 搜索、读取、补丁、测试与结束在同一线性历史中循环；未知工具、重复调用 ID 和参数错误作为可反馈观察处理。
- 限制模型调用、Agent 步数、工具次数、token、命令耗时、成本和全局墙钟时间；超限停止并保留机器可读原因。
- 成功必须由最新工作区公开测试通过且 Runtime 导出非空完整 patch 支撑；写工具调用使旧测试反馈失效。
- Runtime 启动、模型回复、工具调用/结果、预算和终态写入因果关联的 AgentEvent，最终 manifest 与 patch 落盘。
- LocalRuntime 在上层取消命令时主动终止进程树，防止全局墙钟超时留下后台进程。
- 新增 ADR-0005，记录模型公开输入边界和 Baseline 验证规则。

验证结果：

- FakeModel 端到端专项测试覆盖 LocalRuntime 和真实 DockerRuntime 成功路径，以及失败测试、过期测试、未知工具、工具预算、token 预算、模型超时、正在执行的本地命令超时、评测私有字段隔离。
- `PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_linear_agent.py`：`9 passed in 4.38s`。
- `PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q`：最终复跑 `50 passed in 13.01s`，包含真实 Docker Agent 路径。
- 本轮新增模型、Agent 与测试文件 Ruff：`All checks passed!`；已有 Step 1/2 代码的历史 Ruff 风格债务未在本轮批量改写。

当前边界：

- 真实模型 provider adapter 尚未实现；FakeModel 只用于确定性正确性测试，不代表模型真实修复能力。
- Linear ReAct 是无分支、无 Evidence Graph 的基线；完整状态机、反思、候选搜索和 SWE-bench 评测尚未接入。
- 当前成功条件是公开测试通过，不等于隐藏测试或语义正确性通过；后续需接 Verifier Pyramid。
- 结构化工具反馈目前放入线性历史，长轨迹上下文还没有压缩或 provider 级 token 预估。

## 4. 验证记录

验证环境：

- 操作系统：WSL 2，Ubuntu 22.04。
- Conda 环境：`patchflow`。
- Python：3.11.16。
- Pydantic：2.13.5。
- pytest：8.4.2。
- Ruff：0.16.8。

Step 2A 当时的自动验证结果（后续全量结果见 Step 2C）：

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
- LocalRuntime 没有 CPU、内存、PID、磁盘和网络级资源限制；DockerRuntime 的默认限制已在真实 daemon 启动并通过小型任务测试。
- Windows 原生环境只能可靠终止直接子进程；进程组终止能力以 Linux/WSL 为目标平台。
- `git clean -fd` 不删除 ignored 文件，因此缓存可能保留；后续候选隔离将用独立 worktree 或容器处理。
- patch 路径暂不支持 Git 的带引号转义文件名。
- `search_text` 当前只搜索 Git 已跟踪文件；`repo_map` 和 `find_symbol` 尚未实现。
- 尚未接入日志事件、Agent 状态机、模型适配器、上下文管理、候选分支和验证金字塔。
- 尚未接入 SWE-bench Inference/Evaluation Harness。

## 6. 下一步计划

下一步进入 Step 3B：真实 provider 适配、上下文限制与剩余 baseline。

计划顺序：

1. 为至少一个真实模型提供商实现适配器，并明确密钥加载、网络失败、重试和用量上报；此时才需要注册或配置模型 API。
2. 将模型上下文构建从完整线性历史改成有上限、可审计的输入，避免长轨迹超出上下文窗口。
3. 实现 One-shot Patch 与 Bash-only baseline，复用同一任务、Runtime、预算和事件协议。
4. 为模型调用超时、服务错误、无效结构化输出、超量工具结果补充确定性测试。
5. 设计公开测试与更严格 Verifier 的区分，防止把公开测试通过误写成任务最终正确。

Step 3A 的 FakeModel 验收已完成。进入 Step 3B 前无需额外环境；届时接真实 provider 才需用户选择并提供相应 API 凭据。

## 7. 变更纪律

- 每次只推进一个明确步骤。
- 每个步骤都包含实现、测试、进度更新和下一步。
- 新增重要依赖前先记录原因。
- 影响安全、数据格式或实验公平性的选择必须新增 ADR。
- 不把 LocalRuntime 描述成安全沙箱。
- 不把基础设施失败计入 Agent 能力失败。
- 不在未确认的用户工作区执行破坏性 reset 或 clean。
