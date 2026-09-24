"""SWE-bench 转换、prediction 导出与官方评测命令行入口。"""  # 所有昂贵操作都要求显式子命令和许可开关。

from __future__ import annotations  # 延迟解析类型标注。

import argparse  # 构造无隐式网络操作的子命令接口。
import asyncio  # 驱动异步官方 Harness 子进程。
import json  # 读取数据集、仓库映射和候选预测。
import shlex  # 仅用于把参数数组打印成可复制的诊断命令。
from pathlib import Path  # 管理输入输出和官方工作目录。
from typing import Any  # 接收 JSON 解码后的未验证对象。

from patchflow.domain.enums import RepositoryKind  # 解析仓库映射中的定位方式。
from patchflow.swebench.harness import (  # 导入官方 Harness 编排接口。
    SweBenchHarnessConfig,  # 构造严格官方评测配置。
    build_harness_command,  # 在不执行时预览真实命令。
    parse_harness_results,  # 离线解析已有官方结果。
    run_harness,  # 在显式许可后启动官方进程。
)  # 完成 Harness 接口导入。
from patchflow.swebench.inference import (  # 准备仓库并收集正式运行补丁。
    RepositoryPreparationError,  # 分类公开仓库克隆和检出失败。
    collect_run_predictions,  # 从标准运行工件生成官方 prediction。
    prepare_repositories,  # 为固定实例准备干净基础仓库。
)  # 完成 benchmark 推理辅助接口导入。
from patchflow.swebench.models import (  # 导入数据转换、隔离和 prediction 接口。
    InferenceBundle,  # 保存带数据集元信息的推理 bundle。
    SweBenchPrediction,  # 校验官方三字段预测。
    SweBenchRecord,  # 校验原始数据集记录。
    adapt_record_to_task,  # 转换安全 TaskSpec。
    extract_evaluation_record,  # 提取评测侧私有答案。
    read_predictions,  # 验证正式 JSONL 并派生实例分母。
    write_inference_bundle,  # 写出带元数据安全 bundle。
    write_predictions,  # 写出官方 JSONL。
    write_private_evaluation_records,  # 写出显式隔离私有记录。
    write_task_specs,  # 写出现有 PatchFlow CLI 可读取的任务数组。
)  # 完成数据接口导入。


def _load_json_records(path: Path) -> tuple[dict[str, Any], ...]:  # 同时支持 JSON 数组和 Hugging Face 常用 JSONL。
    text = path.read_text(encoding="utf-8")  # 一次读取用户显式指定的数据文件。
    if not text.strip():  # 空数据集不能产生稳定评测任务。
        raise ValueError(f"输入文件为空：{path}")  # 提前给出清晰错误。
    if text.lstrip().startswith("["):  # JSON 数组以左方括号开始。
        raw = json.loads(text)  # 使用标准解析器加载完整数组。
        if not isinstance(raw, list):  # 防御意外根对象。
            raise ValueError("JSON 数据集根必须是数组")  # 拒绝模糊格式。
        records = tuple(raw)  # 固定原始顺序供可复现实验使用。
    else:  # 其余输入按每行一个 JSON 对象处理。
        records = tuple(json.loads(line) for line in text.splitlines() if line.strip())  # 忽略纯空白分隔行。
    if not records or any(not isinstance(item, dict) for item in records):  # 每条数据都必须是对象。
        raise ValueError("数据集必须包含至少一个 JSON 对象")  # 拒绝标量或空集合。
    return records  # 返回待 Pydantic 严格解析的对象元组。


def _load_repository_map(path: Path | None) -> dict[str, tuple[RepositoryKind, str]]:  # 读取实例到已准备仓库的可选映射。
    if path is None:  # 未提供映射时由转换器使用官方 GitHub 地址。
        return {}  # 返回空映射表示远程 Git 定位。
    raw = json.loads(path.read_text(encoding="utf-8"))  # 解析显式仓库映射文件。
    if not isinstance(raw, dict):  # 映射根必须由 instance_id 索引。
        raise ValueError("repository-map 必须是 JSON 对象")  # 拒绝无法关联实例的数组。
    mapping: dict[str, tuple[RepositoryKind, str]] = {}  # 初始化规范化映射。
    for instance_id, value in raw.items():  # 遍历每个实例仓库配置。
        if isinstance(value, str):  # 字符串简写表示已经准备好的本地仓库。
            mapping[str(instance_id)] = (RepositoryKind.LOCAL, value)  # 保存本地定位方式和原始路径。
            continue  # 处理下一个映射项。
        if not isinstance(value, dict):  # 完整写法必须是对象。
            raise ValueError(f"repository-map[{instance_id}] 必须是路径字符串或对象")  # 给出具体错误实例。
        kind = RepositoryKind(value.get("kind", RepositoryKind.LOCAL.value))  # 解析 local、git 或 container_image。
        location = value.get("location")  # 读取仓库路径、URL 或镜像名。
        if not isinstance(location, str) or not location.strip():  # 仓库位置不能缺失或为空。
            raise ValueError(f"repository-map[{instance_id}].location 不能为空")  # 在 TaskSpec 构造前失败。
        mapping[str(instance_id)] = (kind, location.strip())  # 保存规范仓库配置。
    return mapping  # 返回所有实例映射。


def _instance_ids(arguments: argparse.Namespace, predictions_path: str) -> tuple[str, ...]:  # 统一获得官方运行的明确实例分母。
    explicit = tuple(arguments.instance_id or ())  # 读取可重复提供的命令行实例 ID。
    if predictions_path == "gold":  # gold 特殊值没有 prediction 文件可用于派生 ID。
        if not explicit:  # 禁止无边界地运行整个数据集 gold。
            raise ValueError("gold 评测必须至少提供一个 --instance-id")  # 控制资源并明确分母。
        return explicit  # 返回用户固定的 gold 实例集合。
    predictions = read_predictions(Path(predictions_path))  # 对自定义预测执行完整格式校验。
    derived = tuple(item.instance_id for item in predictions)  # 从官方 JSONL 派生实例集合。
    if explicit and set(explicit) != set(derived):  # 显式 ID 与文件必须完全一致。
        raise ValueError("--instance-id 与 prediction 文件中的实例不一致")  # 防止错误评测分母。
    return explicit or derived  # 优先保留用户顺序，否则使用文件顺序。


def _harness_config(arguments: argparse.Namespace, predictions_path: str) -> SweBenchHarnessConfig:  # 从共享 CLI 参数构造严格配置。
    return SweBenchHarnessConfig(  # 让 Pydantic 统一验证范围和重复实例。
        dataset_name=arguments.dataset_name,  # 传入官方数据集名称。
        split=arguments.split,  # 传入数据分片。
        run_id=arguments.run_id,  # 传入唯一运行标识。
        workdir=arguments.workdir,  # 传入官方日志工作目录。
        instance_ids=_instance_ids(arguments, predictions_path),  # 传入固定实例集合。
        python_executable=arguments.python_executable,  # 传入安装了 swebench 的解释器。
        max_workers=arguments.max_workers,  # 传入受限并发数。
        timeout_seconds=arguments.timeout_seconds,  # 传入单实例超时。
        process_timeout_seconds=arguments.process_timeout_seconds,  # 传入整批硬超时。
    )  # 完成配置构造。


def _add_harness_arguments(parser: argparse.ArgumentParser) -> None:  # 为预览和真实运行复用同一组官方参数。
    parser.add_argument("--dataset-name", default="princeton-nlp/SWE-bench_Lite")  # 默认使用官方文档中的轻量公开数据集。
    parser.add_argument("--split", default="test")  # 默认评测官方 test 分片。
    parser.add_argument("--run-id", required=True)  # 要求每次实验显式唯一标识。
    parser.add_argument("--workdir", type=Path, required=True)  # 要求显式指定官方日志工作目录。
    parser.add_argument("--predictions", required=True, help="prediction JSONL 路径或官方特殊值 gold")  # 接收评测输入。
    parser.add_argument("--instance-id", action="append")  # 可重复固定实例；自定义预测时可从文件派生。
    parser.add_argument("--python-executable", default="python")  # 允许使用独立 swebench Conda 环境解释器。
    parser.add_argument("--max-workers", type=int, default=1)  # 默认串行以控制 Docker 资源。
    parser.add_argument("--timeout-seconds", type=int, default=1_800)  # 设置官方单实例超时。
    parser.add_argument("--process-timeout-seconds", type=float, default=21_600.0)  # 设置外层整批硬超时。


def _write_json(path: Path, payload: Any) -> None:  # 保存 CLI 生成的非关键汇总报告。
    path.parent.mkdir(parents=True, exist_ok=True)  # 创建输出父目录。
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")  # 以稳定 UTF-8 JSON 写盘。


def main(argv: list[str] | None = None) -> int:  # 提供可由测试直接注入参数的主入口。
    parser = argparse.ArgumentParser(prog="patchflow-swebench")  # 创建第七周专用命令行解析器。
    commands = parser.add_subparsers(dest="command", required=True)  # 要求用户明确选择无副作用或昂贵操作。
    convert = commands.add_parser("convert", help="把 SWE-bench JSON/JSONL 转为安全 TaskSpec")  # 定义任务转换命令。
    convert.add_argument("input", type=Path)  # 接收包含官方记录的本地数据文件。
    convert.add_argument("--dataset-name", required=True)  # 记录真实数据集名称。
    convert.add_argument("--split", required=True)  # 记录真实数据分片。
    convert.add_argument("--repository-map", type=Path)  # 可选映射到已经准备好的本地仓库或镜像。
    convert.add_argument("--tasks-output", type=Path, required=True)  # 输出现有 PatchFlow CLI 可读取的任务数组。
    convert.add_argument("--bundle-output", type=Path)  # 可选输出带数据集元信息的安全 bundle。
    convert.add_argument("--private-evaluation-output", type=Path)  # 可选显式输出评测私有答案文件。
    prepare = commands.add_parser("prepare", help="克隆固定 SWE-bench 子集并生成本地 TaskSpec")  # 定义显式联网仓库准备命令。
    prepare.add_argument("input", type=Path)  # 接收本地保存的官方 JSON 或 JSONL 数据集记录。
    prepare.add_argument("--instance-id", action="append", required=True)  # 要求逐个固定实验实例，禁止隐式克隆整个数据集。
    prepare.add_argument("--repositories-root", type=Path, required=True)  # 指定每个实例独立仓库的父目录。
    prepare.add_argument("--tasks-output", type=Path, required=True)  # 写出 Agent CLI 可直接读取的本地任务数组。
    prepare.add_argument("--dataset-name", default="princeton-nlp/SWE-bench_Lite")  # 记录实验使用的官方数据集名称。
    prepare.add_argument("--split", default="test")  # 记录实验数据分片。
    prepare.add_argument("--git-timeout-seconds", type=float, default=900.0)  # 限制每条克隆或检出命令的墙钟时间。
    collect = commands.add_parser("collect-runs", help="从独立 runs root 收集官方 prediction JSONL")  # 定义不调用模型的预测汇总命令。
    collect.add_argument("runs_root", type=Path)  # 接收一个实验配置独占的 PatchFlow 运行根目录。
    collect.add_argument("output", type=Path)  # 指定官方三字段 JSONL 输出路径。
    collect.add_argument("--instance-id", action="append", required=True)  # 固定并保留本次实验的实例顺序和分母。
    collect.add_argument("--model-name-or-path", required=True)  # 保存方法、模型和配置的稳定实验标识。
    export = commands.add_parser("export", help="严格校验并导出官方 prediction JSONL")  # 定义预测导出命令。
    export.add_argument("input", type=Path)  # 接收 JSON 数组或 JSONL 预测对象。
    export.add_argument("output", type=Path)  # 指定标准 JSONL 输出路径。
    export.add_argument("--model-name-or-path")  # 输入缺少模型标识时使用统一值。
    export.add_argument("--minimum-predictions", type=int, default=1)  # 正式五题验收可设置为五。
    preview = commands.add_parser("harness-command", help="只打印官方 Harness 命令，不执行")  # 定义安全预览命令。
    _add_harness_arguments(preview)  # 注册共享官方参数。
    run = commands.add_parser("run-harness", help="显式运行官方 Evaluation Harness")  # 定义真实 Docker 评测命令。
    _add_harness_arguments(run)  # 注册共享官方参数。
    run.add_argument("--allow-harness-run", action="store_true")  # 真实运行的硬许可开关。
    run.add_argument("--report-output", type=Path, required=True)  # 保存 PatchFlow 规范执行报告。
    parse = commands.add_parser("parse-results", help="离线解析已有官方 Harness 结果")  # 定义无 Docker 报告解析命令。
    parse.add_argument("results_root", type=Path)  # 接收具体 run 的官方结果根目录。
    parse.add_argument("--run-id", required=True)  # 接收关联运行标识。
    parse.add_argument("--instance-id", action="append", required=True)  # 接收固定预期实例集合。
    parse.add_argument("--report-output", type=Path, required=True)  # 保存规范逐实例报告。
    arguments = parser.parse_args(argv)  # 执行 argparse 基础格式校验。
    try:  # 把领域输入错误转换为简洁标准 CLI 错误。
        if arguments.command == "convert":  # 执行完全离线的数据转换。
            raw_records = _load_json_records(arguments.input)  # 加载 JSON 数组或 JSONL。
            records = tuple(SweBenchRecord.model_validate(item) for item in raw_records)  # 严格解析官方字段。
            repository_map = _load_repository_map(arguments.repository_map)  # 加载可选已准备仓库映射。
            tasks = []  # 初始化安全 TaskSpec 列表。
            for record in records:  # 按数据集原始顺序转换每个实例。
                mapped = repository_map.get(record.instance_id)  # 查询当前实例的仓库定位覆盖。
                task = adapt_record_to_task(  # 构造不含任何答案字段的任务。
                    record,  # 传入严格官方记录。
                    dataset_name=arguments.dataset_name,  # 传入真实数据集名称。
                    split=arguments.split,  # 传入真实分片。
                    repository_location=mapped[1] if mapped else None,  # 传入可选仓库位置。
                    repository_kind=mapped[0] if mapped else None,  # 传入可选仓库类型。
                )  # 完成当前实例转换。
                tasks.append(task)  # 保存公开任务。
            write_task_specs(tasks, arguments.tasks_output)  # 写出现有 Agent CLI 可直接读取的安全数组。
            if arguments.bundle_output is not None:  # 调用方需要元数据时额外写 bundle。
                bundle = InferenceBundle(dataset_name=arguments.dataset_name, split=arguments.split, tasks=tuple(tasks))  # 构造严格安全 bundle。
                write_inference_bundle(bundle, arguments.bundle_output)  # 原子写出元数据和任务。
            if arguments.private_evaluation_output is not None:  # 私有答案仅在显式路径下写出。
                private_records = tuple(extract_evaluation_record(record) for record in records)  # 提取与 TaskSpec 分离的评测记录。
                write_private_evaluation_records(private_records, arguments.private_evaluation_output)  # 原子写出私有文件。
            print(f"已转换 {len(tasks)} 个 SWE-bench 任务；未调用模型或官方 Harness。")  # 告知操作结果和副作用边界。
            return 0  # 返回成功退出码。
        if arguments.command == "prepare":  # 执行显式联网的固定子集仓库准备。
            raw_records = _load_json_records(arguments.input)  # 加载用户已经下载到本地的数据集记录。
            records = tuple(SweBenchRecord.model_validate(item) for item in raw_records)  # 严格解析公开与隔离字段边界。
            tasks = prepare_repositories(records, instance_ids=tuple(arguments.instance_id), output_root=arguments.repositories_root, dataset_name=arguments.dataset_name, split=arguments.split, timeout_seconds=arguments.git_timeout_seconds)  # 克隆并检出每个实例的精确基础提交。
            write_task_specs(tasks, arguments.tasks_output)  # 只写出不含 gold 和隐藏测试的安全任务数组。
            print(f"已准备 {len(tasks)} 个干净实例仓库并写出任务：{arguments.tasks_output}")  # 告知真实网络和文件副作用。
            return 0  # 仓库与任务全部成功后返回零。
        if arguments.command == "collect-runs":  # 从已经完成的 Agent 运行生成官方预测文件。
            predictions = collect_run_predictions(arguments.runs_root, instance_ids=tuple(arguments.instance_id), model_name_or_path=arguments.model_name_or_path)  # 严格检查终态、任务来源、补丁和固定分母。
            write_predictions(predictions, arguments.output)  # 原子写出官方三字段 JSONL。
            print(f"已从运行工件收集 {len(predictions)} 条 prediction：{arguments.output}")  # 告知实际输出数量和路径。
            return 0  # 完整收集后返回成功。
        if arguments.command == "export":  # 严格生成官方三字段 JSONL。
            raw_predictions = _load_json_records(arguments.input)  # 加载候选预测数组或 JSONL。
            predictions = []  # 初始化规范 prediction 列表。
            for raw_prediction in raw_predictions:  # 逐条补全可选统一模型名并校验。
                candidate = dict(raw_prediction)  # 复制输入避免修改解析结果。
                if "model_name_or_path" not in candidate and arguments.model_name_or_path:  # 输入可只保存实例与 patch。
                    candidate["model_name_or_path"] = arguments.model_name_or_path  # 补充统一实验标识。
                predictions.append(SweBenchPrediction.model_validate(candidate))  # 拒绝多余字段、空补丁和无效 ID。
            if arguments.minimum_predictions < 1 or len(predictions) < arguments.minimum_predictions:  # 检查验收要求的最小预测数。
                raise ValueError(f"prediction 数量 {len(predictions)} 小于要求的 {arguments.minimum_predictions}")  # 阻止误把小样本当成正式验收。
            write_predictions(predictions, arguments.output)  # 原子写出官方 JSONL。
            print(f"已导出 {len(predictions)} 条 prediction：{arguments.output}")  # 告知用户真实输出数量。
            return 0  # 返回成功退出码。
        if arguments.command in {"harness-command", "run-harness"}:  # 预览和真实运行共享严格配置。
            config = _harness_config(arguments, arguments.predictions)  # 构造固定实例分母和资源配置。
            command = build_harness_command(config, arguments.predictions)  # 构造官方参数数组。
            if arguments.command == "harness-command":  # 安全预览不能启动任何子进程。
                print(shlex.join(command))  # 输出仅供审计和复制的转义命令。
                return 0  # 返回成功退出码。
            if not arguments.allow_harness_run:  # 真实评测必须显式确认 Docker 资源消耗。
                parser.error("真实官方评测需要 --allow-harness-run；预览请使用 harness-command")  # 在启动进程前拒绝。
            execution = asyncio.run(run_harness(config, arguments.predictions, allow_execution=True))  # 调用官方独立评测进程。
            _write_json(arguments.report_output, execution.model_dump(mode="json"))  # 保存 PatchFlow 进程级和实例级报告。
            print(execution.model_dump_json(indent=2))  # 在终端显示同一份结构化结论。
            return 0 if execution.status.value == "completed" else 1  # 只有完整官方结果返回成功。
        report = parse_harness_results(arguments.results_root, run_id=arguments.run_id, expected_instance_ids=tuple(arguments.instance_id))  # 离线解析已有官方日志。
        _write_json(arguments.report_output, report.model_dump(mode="json"))  # 保存规范逐实例报告。
        print(report.model_dump_json(indent=2))  # 在终端展示聚合计数。
        return 0 if report.missing == 0 else 1  # 缺失实例时返回非零提醒调用方。
    except (OSError, ValueError, PermissionError, RepositoryPreparationError) as error:  # 捕获用户输入、文件、仓库准备和显式权限错误。
        parser.error(str(error))  # 使用 argparse 统一打印错误并返回退出码二。
    return 2  # 为静态类型分析保留不可达兜底返回。


if __name__ == "__main__":  # 支持 python -m patchflow.swebench_cli 调用。
    raise SystemExit(main())  # 将主函数退出码传给操作系统。
