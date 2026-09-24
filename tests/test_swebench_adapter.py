"""第七周 SWE-bench 数据转换、隔离和 prediction 导出测试。"""  # 测试不下载数据集也不启动 Docker。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 检查真实写盘 JSON 和 JSONL 字段。
from pathlib import Path  # 管理 pytest 临时输出路径。

import pytest  # 断言泄漏、重复和未授权操作被拒绝。
from pydantic import ValidationError  # 区分严格 prediction 模式错误。

from patchflow.domain.enums import RepositoryKind, TaskSource  # 检查转换后的领域枚举。
from patchflow.swebench.models import (  # 导入第七周纯数据接口。
    InferenceBundle,  # 构造带元数据的安全推理任务。
    SweBenchPrediction,  # 构造官方三字段预测。
    SweBenchRecord,  # 解析包含金标的原始记录。
    adapt_record_to_task,  # 转换公开 TaskSpec。
    assert_inference_payload_safe,  # 直接验证递归泄漏检查。
    extract_evaluation_record,  # 检查私有答案单独保存。
    prediction_from_task,  # 将 Agent patch 转为官方预测。
    read_predictions,  # 读取并复核标准 JSONL。
    write_inference_bundle,  # 写出推理安全 bundle。
    write_predictions,  # 写出官方 prediction 文件。
    write_task_specs,  # 写出现有 Agent CLI 兼容数组。
)  # 完成适配器接口导入。


def _record() -> SweBenchRecord:  # 构造同时含公开题目和评测私有答案的代表样本。
    return SweBenchRecord.model_validate(  # 使用官方字段命名模拟 Hugging Face 记录。
        {  # 开始样本对象。
            "instance_id": "demo__project-123",  # 提供合法官方实例 ID。
            "repo": "demo/project",  # 提供公开 GitHub 仓库标识。
            "base_commit": "abc123",  # 提供修复前基础提交。
            "problem_statement": "Fix the parser when an empty token is received.",  # 提供 Agent 可见 Issue。
            "version": "1.2",  # 提供环境诊断版本。
            "environment_setup_commit": "env456",  # 提供评测环境提交。
            "patch": "diff --git a/parser.py b/parser.py\n+fixed\n",  # 提供绝不能泄漏的 gold patch。
            "test_patch": "diff --git a/tests.py b/tests.py\n+hidden assertion\n",  # 提供绝不能泄漏的测试补丁。
            "FAIL_TO_PASS": '["tests.py::test_empty"]',  # 模拟官方 JSON 字符串列表。
            "PASS_TO_PASS": ["tests.py::test_regular"],  # 模拟官方数组列表。
            "text": "ignored future dataset field",  # 验证数据集额外字段不会进入领域对象。
        }  # 结束样本对象。
    )  # 返回严格记录。


def test_record_is_split_into_safe_task_and_private_evaluation_record(tmp_path: Path) -> None:  # 验证两阶段数据边界。
    record = _record()  # 创建含完整答案的原始记录。
    repository = tmp_path / "prepared-repository"  # 构造不要求真实存在的本地仓库位置。
    task = adapt_record_to_task(  # 转换为 Agent 可见任务。
        record,  # 传入完整官方记录。
        dataset_name="princeton-nlp/SWE-bench_Lite",  # 记录官方数据集来源。
        split="test",  # 记录分片。
        repository_location=str(repository),  # 映射到调用方准备的基础仓库。
        repository_kind=RepositoryKind.LOCAL,  # 显式声明本地定位。
    )  # 完成公开任务转换。
    private = extract_evaluation_record(record)  # 单独构造 Evaluation Phase 记录。
    assert task.task_id == record.instance_id  # 官方 ID 必须稳定贯穿推理与评测。
    assert task.source is TaskSource.SWE_BENCH  # 任务来源必须准确标记。
    assert task.repo_spec.kind is RepositoryKind.LOCAL  # 仓库覆盖必须被保留。
    assert task.public_commands == ()  # 隐藏测试列表不能伪装为公开测试命令。
    serialized = json.dumps(task.model_dump(mode="json"), ensure_ascii=False).lower()  # 序列化真实 Agent 输入。
    assert "hidden assertion" not in serialized  # 测试补丁内容不能泄漏。
    assert "+fixed" not in serialized  # gold patch 内容不能泄漏。
    assert "fail_to_pass" not in serialized  # 目标测试字段不能泄漏。
    assert "pass_to_pass" not in serialized  # 回归测试字段不能泄漏。
    assert private.gold_patch == record.patch  # 评测侧仍应完整保留 gold patch。
    assert private.test_patch == record.test_patch  # 评测侧仍应完整保留测试补丁。
    assert private.fail_to_pass == ("tests.py::test_empty",)  # 字符串列表必须正确解析。
    assert private.pass_to_pass == ("tests.py::test_regular",)  # 数组列表必须正确解析。


def test_inference_writers_reject_nested_private_fields(tmp_path: Path) -> None:  # 验证泄漏检查覆盖嵌套字典和不同大小写。
    with pytest.raises(ValueError, match="FAIL_TO_PASS"):  # 预期报告具体泄漏路径。
        assert_inference_payload_safe({"context": [{"FAIL_TO_PASS": ["secret"]}]})  # 把答案藏在列表内也必须失败。
    task = adapt_record_to_task(_record(), dataset_name="dataset", split="test")  # 构造安全远程 Git 任务。
    bundle_path = tmp_path / "inference" / "bundle.json"  # 指定带元数据输出。
    tasks_path = tmp_path / "inference" / "tasks.json"  # 指定现有 CLI 兼容输出。
    write_inference_bundle(InferenceBundle(dataset_name="dataset", split="test", tasks=(task,)), bundle_path)  # 写出安全 bundle。
    write_task_specs((task,), tasks_path)  # 写出安全任务数组。
    bundle_payload = json.loads(bundle_path.read_text(encoding="utf-8"))  # 读取真实 bundle 文件。
    tasks_payload = json.loads(tasks_path.read_text(encoding="utf-8"))  # 读取真实任务数组文件。
    assert bundle_payload["tasks"][0]["task_id"] == task.task_id  # bundle 应保留正确任务。
    assert tasks_payload[0]["task_id"] == task.task_id  # CLI 数组应保留正确任务。
    assert "patch" not in json.dumps(bundle_payload).lower()  # 文件中不能出现评测补丁字段。


def test_prediction_export_has_exact_official_fields_and_round_trips(tmp_path: Path) -> None:  # 验证官方 JSONL 契约。
    task = adapt_record_to_task(_record(), dataset_name="dataset", split="test")  # 构造 SWE-bench TaskSpec。
    prediction = prediction_from_task(task, model_name_or_path="patchflow-main/model-small", model_patch="diff --git a/a.py b/a.py\n+x\n")  # 从最终补丁构造预测。
    output = tmp_path / "predictions.jsonl"  # 指定官方预测文件。
    write_predictions((prediction,), output)  # 原子写出标准 JSONL。
    raw = json.loads(output.read_text(encoding="utf-8").strip())  # 解析唯一一行。
    assert set(raw) == {"instance_id", "model_name_or_path", "model_patch"}  # 不允许轨迹、答案或配置混入。
    assert read_predictions(output) == (prediction,)  # 严格读取必须无损往返。


def test_prediction_contract_rejects_extra_fields_empty_patch_and_duplicates(tmp_path: Path) -> None:  # 验证错误输入不会进入昂贵 Harness。
    with pytest.raises(ValidationError):  # Pydantic 应拒绝任何额外字段。
        SweBenchPrediction.model_validate({"instance_id": "demo__project-1", "model_name_or_path": "method", "model_patch": "diff", "gold_patch": "secret"})  # 故意尝试把金标写入 prediction。
    with pytest.raises(ValidationError):  # 空补丁不应产生模糊官方任务。
        SweBenchPrediction(instance_id="demo__project-1", model_name_or_path="method", model_patch="   ")  # 构造只有空白的补丁。
    prediction = SweBenchPrediction(instance_id="demo__project-1", model_name_or_path="method", model_patch="diff")  # 构造合法预测。
    with pytest.raises(ValueError, match="重复"):  # 写盘前应检查跨行重复实例。
        write_predictions((prediction, prediction), tmp_path / "duplicates.jsonl")  # 同一实例提交两次。
