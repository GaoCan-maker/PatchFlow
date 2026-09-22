"""同任务、同预算的三策略公开测试批量评测。"""  # 不读取私有 evaluation_ref 或隐藏测试。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 生成机器可读统一报告。
import shlex  # 安全解析公开测试命令参数。
from collections.abc import Callable  # 标注每次运行独立构建模型的工厂。
from dataclasses import asdict, dataclass  # 定义可序列化的评测结果。
from pathlib import Path  # 管理报告与源仓库路径。

from patchflow.agent.bash_only import BashOnlyAgent  # 注册仅命令工具基线。
from patchflow.agent.linear_react import LinearReactAgent  # 注册细粒度工具 ReAct 基线。
from patchflow.agent.one_shot import OneShotAgent  # 注册无反馈单轮基线。
from patchflow.application.run_initializer import initialize_run  # 为每次尝试创建独立轨迹与清单。
from patchflow.config.models import AgentConfig, AppConfig  # 保存每种策略的真实配置快照。
from patchflow.domain.enums import RunStatus  # 区分任务失败与基础设施失败。
from patchflow.domain.task import TaskSpec  # 接收共享的任务说明。
from patchflow.model.protocol import Model  # 使用 provider 无关模型接口。
from patchflow.runtime.docker import DockerRuntime, DockerRuntimeConfig  # 强制独立容器评测。
from patchflow.tools.command import RunCommandTool, RunTestsTool  # 构造线性基线工具集合。
from patchflow.tools.repository import (  # 构造细粒度仓库工具集合。
    ApplyPatchTool,  # 允许应用标准补丁。
    GitDiffTool,  # 允许查看候选差异。
    ReadCodeTool,  # 允许按行读取代码。
    SearchTextTool,  # 允许搜索已跟踪文本。
)  # 结束仓库工具导入。

STRATEGIES = ("one_shot", "linear_react", "bash_only")  # 固定对照组名称和报告顺序。


@dataclass(frozen=True, slots=True)  # 单次运行结果写入统一报告。
class BatchCase:  # 保存可跨策略对比的指标。
    task_id: str  # 标识被评测任务。
    strategy: str  # 标识 Agent 策略。
    run_id: str  # 链接到完整事件轨迹和补丁。
    agent_status: str  # 保存 Agent 自身终态。
    stop_reason: str  # 保存机器可读停止原因。
    patch_applied: bool  # 表示补丁在全新容器能否应用。
    public_passed: bool  # 表示全新容器的全部公开命令是否成功。
    evaluation_error: str | None  # 单独记录评测基础设施异常。
    model_calls: int  # 保存模型调用量。
    tool_calls: int  # 保存工具调用量。
    input_tokens: int  # 保存模型报告的输入 token。
    output_tokens: int  # 保存模型报告的输出 token。
    cost_usd: float | None  # 未定价时保持空值而非假装零成本。
    wall_clock_seconds: float  # 保存 Agent 总运行时间。


@dataclass(frozen=True, slots=True)  # 把整体报告作为不可变返回值。
class BatchReport:  # 保存每组任务的结果与聚合统计。
    dataset_size: int  # 记录唯一任务数。
    cases: tuple[BatchCase, ...]  # 保留每个任务每种策略的原始观测。
    summary: dict[str, dict[str, float | int | None]]  # 保存每种策略的汇总指标。


class BatchRunner:  # 串行运行各策略以减少容器和 API 资源峰值。
    def __init__(self, config: AppConfig, model_factory: Callable[[str, TaskSpec], Model], *, runs_root: Path, docker_config: DockerRuntimeConfig | None = None) -> None:  # 注入统一配置与模型工厂。
        if config.runtime.kind != "docker":  # 三组真实模型实验必须使用同等级隔离。
            raise ValueError("批量评测必须使用 DockerRuntime")  # 阻止未知代码在宿主机执行。
        self._config = config  # 保存公开模型与 Runtime 配置。
        self._factory = model_factory  # 每次实验调用工厂获取全新模型实例。
        self._runs_root = runs_root  # 保存 artifacts 目录。
        self._docker_config = docker_config  # 保存相同的容器资源限制。

    def _agent(self, strategy: str, model: Model) -> LinearReactAgent:  # 为不同基线装配同一协议。
        if strategy == "one_shot":  # 单轮策略不公布任何工具。
            return OneShotAgent(model)  # 返回固定快照的补丁生成器。
        if strategy == "bash_only":  # 通用命令策略只接触受控 shell。
            return BashOnlyAgent(model)  # 返回强制 Docker 的命令 Agent。
        if strategy == "linear_react":  # 线性策略使用已有细粒度仓库工具。
            return LinearReactAgent(model, (SearchTextTool(), ReadCodeTool(), ApplyPatchTool(), GitDiffTool(), RunTestsTool(), RunCommandTool()))  # 返回可获得测试反馈的循环 Agent。
        raise ValueError(f"未知评测策略：{strategy}")  # 防止配置拼写错误落入默认策略。

    async def _evaluate(self, task: TaskSpec, patch: str | None) -> tuple[bool, bool, str | None]:  # 在全新容器独立复现补丁。
        if patch is None:  # 未提交补丁的 Agent 无需启动评测容器。
            return False, False, None  # 记录不可应用且未通过。
        if not task.public_commands:  # 没有公开命令时不能声称通过公开测试。
            return False, False, "missing_public_commands"  # 数据集准备错误单独记录。
        runtime = DockerRuntime(Path(task.repo_spec.location), self._docker_config)  # 新建与生成阶段完全隔离的工作区。
        evaluation: tuple[bool, bool, str | None] = (False, False, None)  # 先初始化可被关闭失败覆盖的最终结论。
        try:  # 无论应用或测试结果如何都释放容器。
            await runtime.start(task)  # 再次检查基础提交和干净仓库。
            applied = await runtime.apply_patch(patch)  # 在全新基础提交上应用 Agent 补丁。
            if not applied.applied:  # 拒绝应用不成功的候选。
                evaluation = (False, False, applied.rejection_reason)  # 报告具体应用失败原因。
            else:  # 仅对可应用补丁执行公开测试。
                evaluation = (True, True, None)  # 先假设所有公开命令都会成功。
                for public_command in task.public_commands:  # 要求所有公开测试命令通过。
                    result = await runtime.execute(tuple(shlex.split(public_command)), timeout_seconds=min(300.0, task.budget.max_command_seconds))  # 不经 Shell 拼接执行测试。
                    if not result.succeeded:  # 非零退出或超时均为测试失败。
                        evaluation = (True, False, "public_test_failed")  # 明确补丁可应用但测试不通过。
                        break  # 任一公开命令失败即可停止本次评测。
        except Exception as error:  # 单个环境错误不应终止整批任务。
            evaluation = (False, False, f"evaluation_{type(error).__name__}")  # 只报告稳定异常类型。
        finally:  # 始终尝试销毁本次评测容器。
            try:  # 容器清理失败也应归类为评测基础设施错误。
                await runtime.close()  # 释放 Docker 工作区。
            except Exception as error:  # 防止清理异常中断整个任务批次。
                evaluation = (False, False, f"evaluation_close_{type(error).__name__}")  # 不把未成功清理的结果报告为通过。
        return evaluation  # 返回本次独立公开评测的完整结论。

    async def run(self, tasks: tuple[TaskSpec, ...], *, strategies: tuple[str, ...] = STRATEGIES, report_path: Path | None = None) -> BatchReport:  # 顺序执行统一任务矩阵。
        if not tasks or len({task.task_id for task in tasks}) != len(tasks):  # 批次必须非空且任务标识唯一。
            raise ValueError("任务批次不能为空或包含重复 task_id")  # 防止错误分母和覆盖报告。
        if not strategies or len(set(strategies)) != len(strategies) or any(name not in STRATEGIES for name in strategies):  # 明确验证策略集合。
            raise ValueError("策略列表必须为不重复的已知策略")  # 拒绝模糊实验配置。
        cases: list[BatchCase] = []  # 收集各策略各任务的逐例结果。
        for task in tasks:  # 相同任务顺序供三种策略复用。
            for strategy in strategies:  # 每个策略得到独立容器与模型对象。
                config = self._config.model_copy(update={"agent": AgentConfig(strategy=strategy)})  # 快照准确记录本次策略。
                context = initialize_run(task, config, runs_root=self._runs_root)  # 创建新的 run_id 和轨迹文件。
                runtime = DockerRuntime(Path(task.repo_spec.location), self._docker_config)  # 为 Agent 创建独立容器。
                agent = self._agent(strategy, self._factory(strategy, task))  # 获取策略专属的新模型实例。
                outcome = await agent.run(task, context, runtime)  # 执行 Agent 并生成可选补丁。
                applied, passed, error = await self._evaluate(task, outcome.patch)  # 独立评测永不复用 Agent 工作区。
                usage = context.state.usage  # 读取最终预算计数。
                cost = usage.cost_usd if usage.model_calls == 0 or (self._config.model.input_price_usd_per_million is not None and self._config.model.output_price_usd_per_million is not None) else None  # 未知价格保留空值。
                cases.append(BatchCase(task.task_id, strategy, context.state.run_id, outcome.status.value, outcome.stop_reason, applied, passed, error, usage.model_calls, usage.tool_calls, usage.input_tokens, usage.output_tokens, cost, usage.wall_clock_seconds))  # 保存完整逐例指标。
        summary: dict[str, dict[str, float | int | None]] = {}  # 构造策略维度的聚合结果。
        for strategy in strategies:  # 每个策略使用相同任务分母。
            selected = [case for case in cases if case.strategy == strategy]  # 获取该策略所有任务。
            successes = sum(case.public_passed for case in selected)  # 只根据独立公开测试计数成功。
            summary[strategy] = {"tasks": len(selected), "public_passes": successes, "public_pass_rate": successes / len(selected), "infra_errors": sum(case.agent_status == RunStatus.INFRASTRUCTURE_ERROR.value or (case.evaluation_error is not None and case.evaluation_error.startswith("evaluation_")) for case in selected), "avg_model_calls": sum(case.model_calls for case in selected) / len(selected), "avg_tool_calls": sum(case.tool_calls for case in selected) / len(selected), "avg_input_tokens": sum(case.input_tokens for case in selected) / len(selected), "avg_output_tokens": sum(case.output_tokens for case in selected) / len(selected), "avg_wall_clock_seconds": sum(case.wall_clock_seconds for case in selected) / len(selected), "total_cost_usd": sum(case.cost_usd or 0 for case in selected) if all(case.cost_usd is not None for case in selected) else None}  # 生成不掩盖基础设施失败的统一指标。
        report = BatchReport(len(tasks), tuple(cases), summary)  # 保存不可变批次报告。
        if report_path is not None:  # 调用方可指定机器可读输出文件。
            report_path.parent.mkdir(parents=True, exist_ok=True)  # 创建报告目录。
            report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 写入 JSON 报告。
        return report  # 返回同一份报告供程序化分析。
