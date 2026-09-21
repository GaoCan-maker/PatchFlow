# ADR-0002：LocalRuntime 仅用于可信隔离工作区

- 状态：已接受
- 日期：2026-09-20
- 决策者：项目维护者

## 背景

PatchFlow 需要一个快速、可调试的 Runtime 来开发工具协议和 Agent loop。直接等待 DockerRuntime 会推迟接口验证，但直接在用户仓库中运行模型生成命令又可能破坏文件、泄漏凭据或留下子进程。

本阶段需要明确回答两个问题：LocalRuntime 可以安全承诺什么，以及哪些风险必须留给 DockerRuntime 处理。

## 候选方案

### 方案 A：只实现 DockerRuntime

隔离语义更接近最终系统，但镜像、挂载、网络和资源限制会同时引入较多基础设施变量，不利于先稳定 Runtime 与 Tool 契约。

### 方案 B：LocalRuntime 直接运行任意用户仓库

开发最方便，但 Python 级路径检查无法限制进程读取主目录、访问网络、消耗资源或调用宿主程序，安全承诺不成立。

### 方案 C：LocalRuntime 限定可信临时仓库，同时继续实现 DockerRuntime

LocalRuntime 用于快速单元测试、集成测试和调试；未知仓库、SWE-bench 与正式 Agent 执行必须使用 DockerRuntime。

## 决策

采用方案 C，并建立以下不可绕过的约束：

- `workspace` 必须是显式 `isolation_root` 的严格子目录。
- Runtime 启动时验证仓库根、base commit、HEAD 和完全干净的 Git 状态。
- 启动时不自动 reset，避免清除调用方未保存修改。
- 文件访问经过真实路径解析、路径策略和符号链接逃逸检查。
- 命令只接受参数数组，不启用 Shell，不允许调用方覆盖 cwd。
- 子进程环境变量采用允许列表，默认不传递 API key 等未知变量。
- POSIX 下超时终止整个进程组；输出必须捕获、限长并标记截断。
- patch 在应用前校验旧路径和新路径，并执行 `git apply --check`。
- `reset` 与 `clean` 只能在已经通过隔离校验并成功启动的工作区中执行。

这些约束降低误操作风险，但不构成针对恶意代码的安全沙箱。

## 后果

积极后果：

- Runtime 和结构化工具可以在没有 Docker 与模型 API 的情况下快速测试。
- Agent loop、baseline 和工具契约可以先获得稳定反馈。
- 脏仓库、路径逃逸、补丁失败和超时行为都有确定性测试。

消极后果与限制：

- 任务进程仍共享宿主机内核、当前用户权限和网络。
- 当前无法强制 CPU、内存、PID、磁盘或网络上限。
- Windows 原生环境的进程树终止能力弱于 Linux/WSL。
- 未知仓库和模型生成命令在 DockerRuntime 完成前不得正式执行。

## 重新评估条件

当 DockerRuntime 具备稳定的开发体验、可复用镜像缓存和完整契约测试后，重新评估 LocalRuntime 是否只保留为测试后端，或从面向用户的配置中完全隐藏。
