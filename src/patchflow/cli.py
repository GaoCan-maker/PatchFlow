"""显式选择数据集、模型和付费权限的批量运行入口。"""  # 默认操作不触发远程 API。

from __future__ import annotations  # 延迟解析类型标注。

import argparse  # 解析清晰的命令行子命令。
import asyncio  # 驱动异步 Agent 和评测器。
import json  # 读取结构化任务清单并打印报告。
from pathlib import Path  # 处理用户输入的文件路径。

from patchflow.config.models import AppConfig, ModelConfig, RuntimeConfig  # 构造可快照的运行配置。
from patchflow.domain.task import TaskSpec  # 严格校验每个任务。
from patchflow.evaluation.batch import STRATEGIES, BatchRunner  # 复用三策略统一评测。
from patchflow.evaluation.smoke import prepare_smoke_dataset  # 创建离线小型开发任务集。
from patchflow.model.openai_chat import OpenAIChatModel  # 按需构造真实模型客户端。


def _tasks(path: Path) -> tuple[TaskSpec, ...]:  # 从 JSON 文件读取一批领域任务。
    raw = json.loads(path.read_text(encoding="utf-8"))  # 使用标准解析器而非字符串处理。
    if not isinstance(raw, list):  # TaskSpec 批次必须是 JSON 数组。
        raise ValueError("任务文件必须是 TaskSpec 对象数组")  # 给出明确输入格式错误。
    return tuple(TaskSpec.model_validate(item) for item in raw)  # 逐项严格校验任务与路径策略。


def main(argv: list[str] | None = None) -> int:  # 提供可在测试中注入 argv 的主入口。
    parser = argparse.ArgumentParser(prog="patchflow")  # 创建命令行解析器。
    commands = parser.add_subparsers(dest="command", required=True)  # 要求显式选择操作。
    prepare = commands.add_parser("prepare-smoke", help="创建两任务离线 MicroSWE 烟测集")  # 定义无网络数据集命令。
    prepare.add_argument("root", type=Path)  # 接收目标空目录。
    validate = commands.add_parser("validate", help="校验任务 JSON，不调用模型")  # 定义默认安全校验命令。
    validate.add_argument("tasks", type=Path)  # 接收任务清单路径。
    run = commands.add_parser("run-batch", help="运行三策略 Docker 批量评测")  # 定义真实模型实验命令。
    run.add_argument("tasks", type=Path)  # 接收相同任务清单。
    run.add_argument("--provider", choices=("openai_chat", "openai_compatible"), required=True)  # 明确选择服务类型。
    run.add_argument("--model-id", required=True)  # 明确选择模型 ID。
    run.add_argument("--base-url")  # 兼容服务必须显式提供 URL。
    run.add_argument("--runs-root", type=Path, required=True)  # 指定 artifacts 根目录。
    run.add_argument("--report", type=Path, required=True)  # 指定报告输出路径。
    run.add_argument("--strategy", choices=STRATEGIES, action="append")  # 可选运行基线子集。
    run.add_argument("--allow-api-spend", action="store_true")  # 显式确认模型调用可能收费。
    arguments = parser.parse_args(argv)  # 解析全部参数并执行基础格式检查。
    if arguments.command == "prepare-smoke":  # 创建不调用模型的烟测数据集。
        tasks = prepare_smoke_dataset(arguments.root)  # 在空目录中生成两个独立仓库。
        print(f"已创建 {len(tasks)} 个任务：{arguments.root / 'tasks.json'}")  # 告知用户清单位置。
        return 0  # 返回成功退出码。
    tasks = _tasks(arguments.tasks)  # 验证数据集，无论是否将运行模型。
    if not tasks:  # 空批次没有评测意义。
        parser.error("任务清单不能为空")  # 返回标准 argparse 错误。
    if arguments.command == "validate":  # 校验命令不能走到模型客户端构造。
        print(f"已校验 {len(tasks)} 个任务；未调用模型。")  # 明确不产生 API 费用。
        return 0  # 返回成功退出码。
    if not arguments.allow_api_spend:  # 付费开关是批量调用的硬门槛。
        parser.error("真实模型运行需要 --allow-api-spend；仅校验请使用 validate")  # 避免无意收费。
    model_config = ModelConfig(provider=arguments.provider, model=arguments.model_id, base_url=arguments.base_url)  # 校验服务地址和模型 ID。
    OpenAIChatModel(model_config)  # 在创建运行 artifact 前预检 SDK 与宿主密钥，但不调用 API。
    config = AppConfig(model=model_config, runtime=RuntimeConfig(kind="docker"))  # 批量运行只允许 Docker 隔离。
    def model_factory(strategy: str, task: TaskSpec) -> OpenAIChatModel:  # 为每次尝试构造独立模型适配器。
        return OpenAIChatModel(model_config)  # 从宿主环境读取密钥，绝不传入容器。
    runner = BatchRunner(config, model_factory, runs_root=arguments.runs_root)  # 装配统一批量评测器。
    report = asyncio.run(runner.run(tasks, strategies=tuple(arguments.strategy or STRATEGIES), report_path=arguments.report))  # 运行真实模型实验。
    print(json.dumps(report.summary, ensure_ascii=False, indent=2))  # 打印统一策略聚合指标。
    return 0  # 返回成功退出码。


if __name__ == "__main__":  # 允许 python -m patchflow.cli 直接调用。
    raise SystemExit(main())  # 将 main 的退出码交给操作系统。
