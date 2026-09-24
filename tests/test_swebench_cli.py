"""第七周 SWE-bench CLI 的离线行为和危险操作门槛测试。"""  # 测试不安装官方包也不启动 Docker。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 构造原始数据集和检查 CLI 输出文件。
from pathlib import Path  # 管理 pytest 临时输入输出路径。

import pytest  # 断言 argparse 对未授权真实运行返回标准错误。

from patchflow.swebench.models import read_predictions  # 复核 CLI 导出的官方 JSONL。
from patchflow.swebench_cli import main  # 直接调用真实命令行主函数。


def _dataset_record(instance_id: str = "demo__repo-1") -> dict[str, object]:  # 构造含公开字段和私有答案的最小记录。
    return {  # 返回可直接 JSON 序列化的官方风格对象。
        "instance_id": instance_id,  # 提供稳定实例 ID。
        "repo": "demo/repo",  # 提供公开仓库标识。
        "base_commit": "abc123",  # 提供基础提交。
        "problem_statement": "Fix behavior for an empty parser token.",  # 提供公开 Issue。
        "patch": "diff --git a/a.py b/a.py\n+gold\n",  # 提供绝不能进入 tasks.json 的 gold patch。
        "test_patch": "diff --git a/test_a.py b/test_a.py\n+hidden\n",  # 提供隐藏测试补丁。
        "FAIL_TO_PASS": ["test_a.py::test_empty"],  # 提供目标测试答案。
        "PASS_TO_PASS": ["test_a.py::test_regular"],  # 提供回归测试答案。
    }  # 完成样本对象。


def test_convert_cli_writes_safe_tasks_and_separate_private_records(tmp_path: Path) -> None:  # 验证 CLI 落盘边界而非仅验证函数返回。
    source = tmp_path / "subset.json"  # 指定原始数据集文件。
    source.write_text(json.dumps([_dataset_record()]), encoding="utf-8")  # 写入一条包含答案的记录。
    tasks = tmp_path / "inference" / "tasks.json"  # 指定 Agent 任务输出。
    bundle = tmp_path / "inference" / "bundle.json"  # 指定带元数据安全 bundle。
    private = tmp_path / "evaluation-private" / "records.json"  # 指定显式私有答案输出。
    exit_code = main(  # 调用真实 convert 子命令。
        [  # 开始参数数组。
            "convert",  # 选择离线转换。
            str(source),  # 传入原始数据文件。
            "--dataset-name",  # 指定数据集参数名。
            "princeton-nlp/SWE-bench_Lite",  # 指定官方公开 Lite 数据集。
            "--split",  # 指定分片参数名。
            "test",  # 指定 test 分片。
            "--tasks-output",  # 指定任务输出参数名。
            str(tasks),  # 传入任务输出路径。
            "--bundle-output",  # 指定 bundle 输出参数名。
            str(bundle),  # 传入 bundle 输出路径。
            "--private-evaluation-output",  # 显式请求私有评测文件。
            str(private),  # 传入与推理目录分开的路径。
        ]  # 完成参数数组。
    )  # 完成 CLI 调用。
    assert exit_code == 0  # 离线转换应成功退出。
    task_payload = json.loads(tasks.read_text(encoding="utf-8"))  # 读取真实 Agent 输入文件。
    private_payload = json.loads(private.read_text(encoding="utf-8"))  # 读取真实评测侧文件。
    serialized_task = json.dumps(task_payload, ensure_ascii=False).lower()  # 生成便于泄漏断言的文本。
    assert "gold" not in serialized_task and "hidden" not in serialized_task  # 答案内容不能进入 Agent 文件。
    assert "fail_to_pass" not in serialized_task and "pass_to_pass" not in serialized_task  # 答案键不能进入 Agent 文件。
    assert private_payload[0]["gold_patch"].endswith("+gold\n")  # 私有文件应完整保留 gold patch。
    assert bundle.exists()  # 可审计 bundle 应被成功写出。


def test_export_cli_enforces_five_prediction_acceptance_floor(tmp_path: Path) -> None:  # 验证五题验收不会误用不足数量的文件。
    raw = tmp_path / "raw.json"  # 指定简化 Agent 补丁汇总输入。
    raw.write_text(  # 写入五条不含模型名的候选预测。
        json.dumps([{"instance_id": f"demo__repo-{index}", "model_patch": f"diff --git a/a.py b/a.py\n+fix-{index}\n"} for index in range(5)]),  # 生成五个唯一实例。
        encoding="utf-8",  # 使用固定 UTF-8。
    )  # 完成输入文件写入。
    output = tmp_path / "predictions.jsonl"  # 指定官方 JSONL 输出。
    exit_code = main(["export", str(raw), str(output), "--model-name-or-path", "patchflow-main/test-model", "--minimum-predictions", "5"])  # 调用真实五题导出。
    assert exit_code == 0  # 满足数量和模式时应成功。
    predictions = read_predictions(output)  # 使用同一严格读取器复核结果。
    assert len(predictions) == 5  # 输出必须恰好包含五条预测。
    assert {item.model_name_or_path for item in predictions} == {"patchflow-main/test-model"}  # 统一方法标识必须补全。


def test_run_harness_cli_requires_explicit_permission(tmp_path: Path) -> None:  # 验证真实运行默认不会创建官方进程或日志。
    predictions = tmp_path / "predictions.jsonl"  # 指定一条合法 prediction 文件。
    predictions.write_text(json.dumps({"instance_id": "demo__repo-1", "model_name_or_path": "method", "model_patch": "diff"}) + "\n", encoding="utf-8")  # 写入严格三字段 JSONL。
    report = tmp_path / "report.json"  # 指定未授权时不应创建的报告。
    with pytest.raises(SystemExit) as caught:  # argparse 应将缺少许可视为参数错误。
        main(  # 调用真实 run-harness 子命令但不提供许可开关。
            [  # 开始命令参数数组。
                "run-harness",  # 选择真实评测命令。
                "--run-id",  # 指定运行标识参数名。
                "no-permission",  # 提供稳定运行标识。
                "--workdir",  # 指定工作目录参数名。
                str(tmp_path),  # 使用存在的临时目录。
                "--predictions",  # 指定 prediction 参数名。
                str(predictions),  # 传入合法 prediction 文件。
                "--report-output",  # 指定报告输出参数名。
                str(report),  # 传入不应创建的报告路径。
            ]  # 完成参数数组。
        )  # 完成未授权 CLI 调用。
    assert caught.value.code == 2  # 未授权应返回 argparse 标准退出码二。
    assert not report.exists()  # 未授权路径不得写报告或伪装完成。
    assert not (tmp_path / "logs").exists()  # 未授权路径不得启动官方 Harness 创建日志。
