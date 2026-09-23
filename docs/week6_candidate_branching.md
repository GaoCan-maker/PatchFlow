# 第六周：候选分支与验证金字塔

## 本周目标

第五周的 `PatchFlowAgent` 每轮只在一个可变工作区中生成和验证一个补丁。第六周增加有限宽度候选搜索，使多个候选从同一个 `base_commit` 独立创建、并发验证、确定性排序和清理。

本周实现的核心链路是：

```text
多个 patch 文本
    ↓
规范化摘要去重
    ↓
同一 base_commit 创建独立 Git 副本
    ↓
候选级并发调度
    ↓
V0 补丁应用
    ↓
V1 语法
    ↓
V2 静态检查
    ↓
V3 最小复现
    ↓
V4 目标测试
    ↓
V5 回归测试
    ↓
硬约束过滤与确定性选择
```

## 文件职责

### `src/patchflow/candidates/workspaces.py`

`CandidateWorkspaceManager` 在创建候选前检查源仓库：

- 源仓库必须是 Git 根目录。
- `HEAD` 必须等于任务的 `base_commit`。
- 源仓库必须干净。
- 候选目录必须位于显式 `isolation_root` 内。
- 每个候选通过 `git clone --local --no-hardlinks` 创建独立副本。
- 每个副本再次 detached checkout 到相同基础提交。
- 候选补丁在规范化后通过 SHA-256 去重。

候选之间只共享不可变的源仓库对象，不共享可变工作区。候选验证结束后默认清理源码副本；调试时可以通过 `keep_workspaces` 保留它们。

### `src/patchflow/verification/pyramid.py`

`VerificationPyramid` 按成本从低到高执行：

| 层级 | 检查 | 默认行为 |
|---|---|---|
| V0 | 补丁路径和 `git apply` | 必须通过 |
| V1 | 修改 Python 文件的 `py_compile` | 必须通过 |
| V2 | 可选静态检查命令 | 配置后必须通过 |
| V3 | 可选最小复现测试 | 配置后必须通过 |
| V4 | `TaskSpec.public_commands` 目标测试 | 默认必须通过 |
| V5 | 可选回归测试 | 配置后必须通过 |

任何命令超时、启动失败或非零退出都会立即淘汰当前候选。验证完成后重新读取 `Runtime.get_diff()`；如果测试修改了候选工作区，候选也会被硬拒绝。

### `src/patchflow/candidates/branching.py`

`CandidateBranchingEngine` 完成候选生命周期：

1. 读取一轮候选补丁。
2. 去除空补丁和重复补丁。
3. 创建候选副本。
4. 用信号量限制同时验证的候选数。
5. 为每个候选创建独立 Runtime。
6. 收集候选级验证报告。
7. 先硬过滤失败候选，再按修改文件数、验证耗时和候选 ID 稳定排序。
8. 没有候选通过时返回 `selected_candidate_id=None`。

选择器不会使用“最不差候选”冒充成功，也不会允许模型判断覆盖明确的测试失败。

## Runtime 工厂

候选引擎不直接绑定 LocalRuntime 或 DockerRuntime，而是接收：

```text
runtime_factory(candidate_path, isolation_root) -> Runtime
```

这样可以在本地测试使用 `LocalRuntime`，在真实任务中为每个候选创建独立 `DockerRuntime`。Runtime 的 `start()` 会再次检查候选仓库位于正确的基础提交，补丁和测试执行都必须经过同一个候选 Runtime。

## 当前边界

- 第六周提供独立的候选分支编排器和验证器，默认第五周单候选入口保持兼容。
- 当前候选是有限宽度的一层搜索，不实现无限树搜索或跨轮候选父子关系。
- 当前 V2、V3、V5 由调用方提供命令；系统不会猜测静态工具或隐藏测试。
- 当前选择器使用确定性规则，不使用 LLM Judge 覆盖失败测试。
- `git clone --local --no-hardlinks` 适用于本地 Git 仓库；远程仓库和 SWE-bench 专用镜像由后续 Adapter 负责。
- 候选级并发必须结合 CPU、内存、Docker 容器和测试数据库容量设置，默认并发为 2。

## 离线测试

在 WSL 的 `patchflow` 环境、项目根目录执行：

```bash
python -m pytest -q tests/test_week6_branching.py
PATCHFLOW_RUN_DOCKER_TESTS=1 python -m pytest -q tests/test_week6_docker_integration.py
python -m ruff check src/patchflow/candidates src/patchflow/verification tests/test_week6_branching.py
```

第一条测试使用临时 Git 仓库和 `LocalRuntime`；第二条测试显式创建两个 Docker Runtime，默认跳过。两者都不创建模型客户端、不读取 API Key，也不产生付费调用。

## 代码阅读顺序

1. `src/patchflow/candidates/workspaces.py`：先理解候选如何从同一基础提交复制出来。
2. `src/patchflow/verification/pyramid.py`：再理解单候选如何逐层验证。
3. `src/patchflow/candidates/branching.py`：最后理解并发调度和确定性选择。
4. `tests/test_week6_branching.py`：用一个语法失败候选和一个成功候选对照阅读。
5. 第五周的 `src/patchflow/agent/patchflow.py`：重点阅读 `_drive_branches`，理解模型生成的多个 patch 如何接入本周引擎，再回看 `_generate` 和 `_verify` 的单候选兼容路径。
6. `tests/test_patchflow_agent.py` 中的 `test_sixth_week_branching_selects_verified_candidate_from_independent_workspaces`：查看从 Issue 理解、计划、双候选生成到最终真实 diff 的完整接入测试。
