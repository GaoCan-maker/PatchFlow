"""第四周文件级与符号级定位评测及证据消融。"""  # 金标仅由评测端读取，不进入 Agent 上下文。

from __future__ import annotations  # 延迟解析返回类型。

import json  # 输出可复现的机器可读报告。
from collections.abc import Callable  # 为金标排名谓词声明可调用类型。
from dataclasses import asdict, dataclass, replace  # 记录样本结果并生成消融配置。
from pathlib import Path  # 管理报告与可选基础提交索引缓存。

from patchflow.domain.task import TaskSpec  # 读取任务仓库与公开问题描述。
from patchflow.localization import (  # 复用同一定位实现。
    IndexSettings,  # 配置索引资源上限。
    LocalizationSettings,  # 配置通道开关与 Top-K。
    RankedCandidate,  # 标注可解释排名候选。
    build_repo_index,  # 建立基础提交索引。
    localize,  # 计算完整配置与消融结果。
)  # 完成定位模块导入。
from patchflow.runtime.docker import DockerRuntime  # 在容器内读取可能不可信的任务仓库。

CHANNELS = ("issue", "search", "traceback", "test_relation", "symbol")  # 固定首版消融通道顺序。


@dataclass(frozen=True, slots=True)  # 明确区分文件和函数两种金标。
class LocalizationGold:  # 评测端私有的定位答案。
    file: str  # 目标工作区相对路径。
    symbol: str  # 目标限定函数或方法名。
    traceback: str = ""  # 可选的修复前公开测试栈帧。
    failing_tests: tuple[str, ...] = ()  # 可选修复前失败测试名称。


@dataclass(frozen=True, slots=True)  # 保存单任务、单通道配置的原始排名。
class LocalizationCase:  # 供审计 Top-K 命中与具体证据。
    task_id: str  # 保存任务标识。
    variant: str  # 标识完整配置或某个被移除的通道。
    gold_file: str  # 保存目标文件路径。
    gold_symbol: str  # 保存目标函数限定名。
    file_rank: int | None  # 保存目标文件的一开始计数排名。
    symbol_rank: int | None  # 保存目标函数的一开始计数排名。
    candidates: dict[str, object]  # 保存实际 Top-K 和特征证据供复盘。


def _rank(items: tuple[RankedCandidate, ...], predicate: Callable[[RankedCandidate], bool]) -> int | None:  # 计算金标在候选列表中的名次。
    for position, item in enumerate(items, 1):  # 使用一开始计数位置。
        if predicate(item):  # 调用本地评测谓词检查金标。
            return position  # 返回命中的真实排名。
    return None  # Top-K 范围内未召回金标。


def _summary(cases: list[LocalizationCase], variant: str) -> dict[str, float | int]:  # 聚合独立任务的定位指标。
    selected = [case for case in cases if case.variant == variant]  # 只统计指定消融配置。
    size = len(selected)  # 每个配置共享相同的任务分母。
    return {"tasks": size, "file_top1": sum(case.file_rank == 1 for case in selected) / size, "file_top3": sum(case.file_rank is not None and case.file_rank <= 3 for case in selected) / size, "file_top5": sum(case.file_rank is not None and case.file_rank <= 5 for case in selected) / size, "symbol_top1": sum(case.symbol_rank == 1 for case in selected) / size, "symbol_top3": sum(case.symbol_rank is not None and case.symbol_rank <= 3 for case in selected) / size, "symbol_top10": sum(case.symbol_rank is not None and case.symbol_rank <= 10 for case in selected) / size, "file_mrr": sum(1 / case.file_rank for case in selected if case.file_rank is not None) / size, "symbol_mrr": sum(1 / case.symbol_rank for case in selected if case.symbol_rank is not None) / size}  # 未命中样本对 MRR 贡献为零。


async def evaluate_localization(tasks: tuple[TaskSpec, ...], gold: dict[str, LocalizationGold], *, report_path: Path | None = None, cache_dir: Path | None = None, index_settings: IndexSettings | None = None) -> dict[str, object]:  # 在相同仓库基础提交上运行完整配置及五组消融。
    index_settings = index_settings or IndexSettings()  # 为本次评测选择独立的不可变默认限制。
    if not tasks or len({task.task_id for task in tasks}) != len(tasks):  # 检查任务批次有效性。
        raise ValueError("定位评测需要非空且 task_id 唯一的任务")  # 防止重复样本改变分母。
    if set(gold) != {task.task_id for task in tasks}:  # 金标必须与任务一一对应。
        raise ValueError("定位金标必须与任务 ID 完全一致")  # 避免无标签样本被静默跳过。
    cases: list[LocalizationCase] = []  # 保存所有任务与配置的明细。
    variants = ("full", *(f"without_{channel}" for channel in CHANNELS))  # 每个通道恰好做一次留一消融。
    for task in tasks:  # 逐任务使用独立容器。
        runtime = DockerRuntime(Path(task.repo_spec.location))  # 绝不在宿主机直接运行陌生仓库。
        try:  # 无论索引或排序是否失败都销毁任务容器。
            await runtime.start(task)  # 验证干净基础提交并启动隔离环境。
            index = await build_repo_index(runtime, task, settings=index_settings, cache_dir=cache_dir)  # 建立或加载基础提交索引。
            label = gold[task.task_id]  # 仅在评测端读取金标和公开失败证据。
            for variant in variants:  # 对同一份索引计算完整配置与五组消融。
                settings = LocalizationSettings(top_k_files=5, top_k_symbols=10)  # 固定 Top-K 保证各组可比。
                if variant != "full":  # 消融组只关闭对应一个通道。
                    settings = replace(settings, **{variant.removeprefix("without_"): False})  # 不改变其他权重和候选集。
                result = localize(index, task.problem_statement, traceback=label.traceback, failing_tests=label.failing_tests, settings=settings)  # 产生结构化排名与证据。
                file_rank = _rank(result.files, lambda candidate, target=label.file: candidate.path == target)  # 绑定当前任务目标文件并计算排名。
                symbol_rank = _rank(result.symbols, lambda candidate, target=label.file, name=label.symbol: candidate.path == target and candidate.symbol == name)  # 绑定当前任务目标函数并计算排名。
                candidates = {"files": [asdict(item) for item in result.files], "symbols": [asdict(item) for item in result.symbols], "skipped": list(index.skipped)}  # 保留完整解释和索引缺口。
                cases.append(LocalizationCase(task.task_id, variant, label.file, label.symbol, file_rank, symbol_rank, candidates))  # 记录本样本结果。
        finally:  # 始终关闭 DockerRuntime。
            await runtime.close()  # 销毁隔离容器与临时工作区。
    report: dict[str, object] = {"dataset_size": len(tasks), "summary": {variant: _summary(cases, variant) for variant in variants}, "cases": [asdict(case) for case in cases]}  # 形成统一机器可读报告。
    if report_path is not None:  # 仅在用户指定路径时写出报告。
        report_path.parent.mkdir(parents=True, exist_ok=True)  # 创建报告目录。
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 保存含原始证据的 JSON。
    return report  # 将报告返回给 CLI 或测试。
