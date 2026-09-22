"""只运行仓库定位与留一消融的离线命令行入口。"""  # 与可能收费的 Agent 批量运行入口分离。

from __future__ import annotations  # 延迟解析入口函数类型。

import argparse  # 解析任务、金标和报告路径。
import asyncio  # 驱动 DockerRuntime 的异步索引过程。
import json  # 解析结构化任务与金标文件。
from pathlib import Path  # 表示输入和输出文件位置。

from patchflow.domain.task import TaskSpec  # 严格验证任务清单。
from patchflow.evaluation.localization import (  # 调用离线评测器。
    LocalizationGold,  # 验证每个任务的定位金标。
    evaluate_localization,  # 运行完整配置与留一消融。
)  # 完成评测模块导入。


def main(argv: list[str] | None = None) -> int:  # 支持脚本执行和测试直接调用。
    parser = argparse.ArgumentParser(prog="python -m patchflow.localization_cli")  # 创建独立且不触发模型调用的 CLI。
    parser.add_argument("tasks", type=Path)  # 接收 TaskSpec JSON 数组。
    parser.add_argument("gold", type=Path)  # 接收 task_id 到文件与符号金标的映射。
    parser.add_argument("--report", type=Path, required=True)  # 要求显式指定评测报告文件。
    parser.add_argument("--cache-dir", type=Path)  # 可选把受限源码索引缓存在用户指定目录。
    args = parser.parse_args(argv)  # 完成命令行参数解析。
    raw_tasks = json.loads(args.tasks.read_text(encoding="utf-8"))  # 读取任务清单 JSON。
    raw_gold = json.loads(args.gold.read_text(encoding="utf-8"))  # 读取与 Agent 输入分离的评测金标。
    if not isinstance(raw_tasks, list) or not isinstance(raw_gold, dict):  # 验证两个顶层 JSON 结构。
        parser.error("任务必须是数组，金标必须是 task_id 映射")  # 拒绝含糊输入格式。
    tasks = tuple(TaskSpec.model_validate(item) for item in raw_tasks)  # 逐个校验任务字段和路径策略。
    gold = {task_id: LocalizationGold(**data) for task_id, data in raw_gold.items()}  # 将金标转换为不可变评测对象。
    report = asyncio.run(evaluate_localization(tasks, gold, report_path=args.report, cache_dir=args.cache_dir))  # 在 Docker 中完成无模型评测。
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))  # 只在终端输出简短指标汇总。
    return 0  # 以成功退出码结束离线评测。


if __name__ == "__main__":  # 允许 python -m patchflow.localization_cli 调用。
    raise SystemExit(main())  # 将主函数结果交给进程退出状态。
