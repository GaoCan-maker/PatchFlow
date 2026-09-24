"""第七周官方 Harness 命令、授权和结果解析测试。"""  # 全部测试离线运行且不要求安装 swebench。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 在同步 pytest 中检查异步授权门槛。
import json  # 构造不同官方版本风格的报告夹具。
from pathlib import Path  # 管理临时日志目录和 prediction 文件。

import pytest  # 断言缺失、矛盾与未授权运行被拒绝。

from patchflow.swebench.harness import (  # 导入第七周官方编排接口。
    SweBenchHarnessConfig,  # 构造固定评测配置。
    SweBenchInstanceStatus,  # 检查逐实例归一化状态。
    build_harness_command,  # 检查官方命令参数数组。
    parse_harness_results,  # 解析模拟官方报告。
    run_harness,  # 检查真实调用许可门槛。
)  # 完成 Harness 接口导入。


def _config(tmp_path: Path) -> SweBenchHarnessConfig:  # 构造两个实例的最小受控配置。
    return SweBenchHarnessConfig(  # 返回严格不可变配置。
        dataset_name="princeton-nlp/SWE-bench_Lite",  # 使用官方公开轻量数据集名。
        split="test",  # 使用官方测试分片。
        run_id="week7-unit",  # 使用稳定测试 run ID。
        workdir=tmp_path,  # 把所有可能输出限制在临时目录。
        instance_ids=("demo__repo-1", "demo__repo-2"),  # 固定两实例分母。
        python_executable="/opt/swebench/bin/python",  # 模拟独立官方环境解释器。
        max_workers=2,  # 测试并发参数传递。
        timeout_seconds=900,  # 测试单实例超时参数传递。
    )  # 完成配置构造。


def test_build_harness_command_uses_official_module_and_explicit_instance_ids(tmp_path: Path) -> None:  # 验证命令无 Shell 且参数完整。
    config = _config(tmp_path)  # 创建测试配置。
    predictions = tmp_path / "predictions.jsonl"  # 构造带空格安全性的路径对象。
    command = build_harness_command(config, predictions)  # 只构造命令而不运行。
    assert command[:3] == ("/opt/swebench/bin/python", "-m", "swebench.harness.run_evaluation")  # 必须复用官方模块入口。
    assert command[command.index("--dataset_name") + 1] == "princeton-nlp/SWE-bench_Lite"  # 数据集参数必须准确。
    assert command[command.index("--predictions_path") + 1] == str(predictions)  # 路径必须保持一个参数而非 Shell 拼接。
    assert command[command.index("--instance_ids") + 1 :] == config.instance_ids  # 实例集合必须显式固定。
    assert "--max_workers" in command and "2" in command  # 并发限制必须进入官方命令。
    assert "--timeout" in command and "900" in command  # 单实例超时必须进入官方命令。


def test_parse_harness_results_supports_instance_reports_and_summary_lists(tmp_path: Path) -> None:  # 验证兼容常见官方报告结构。
    results_root = tmp_path / "logs" / "run_evaluation" / "week7-unit"  # 模拟 SWE-bench 5.0.2 官方 run 目录。
    first = results_root / "model" / "demo__repo-1" / "report.json"  # 模拟逐实例 report.json。
    first.parent.mkdir(parents=True)  # 创建嵌套官方目录。
    first.write_text(json.dumps({"demo__repo-1": {"patch_successfully_applied": True, "resolved": True}}), encoding="utf-8")  # 写入官方 resolved 结构。
    summary = results_root / "results.json"  # 模拟汇总结果文件。
    summary.write_text(json.dumps({"unresolved_ids": ["demo__repo-2"]}), encoding="utf-8")  # 写入另一实例失败列表。
    report = parse_harness_results(results_root, run_id="week7-unit", expected_instance_ids=("demo__repo-1", "demo__repo-2"))  # 解析完整运行。
    assert report.expected_instances == 2  # 固定分母必须保留。
    assert report.resolved == 1  # 成功数量应来自官方 resolved=true。
    assert report.unresolved == 1  # 未解决数量应来自官方汇总列表。
    assert report.errors == 0 and report.missing == 0  # 完整报告不应出现基础设施分类。
    statuses = {item.instance_id: item.status for item in report.instances}  # 构造便于断言的状态映射。
    assert statuses["demo__repo-1"] is SweBenchInstanceStatus.RESOLVED  # 检查成功实例。
    assert statuses["demo__repo-2"] is SweBenchInstanceStatus.UNRESOLVED  # 检查失败实例。
    first_result = next(item for item in report.instances if item.instance_id == "demo__repo-1")  # 获取补丁诊断完整实例。
    assert first_result.patch_applied is True  # 官方 patch 应用字段应被保留。


def test_parse_harness_results_keeps_missing_separate_from_unresolved(tmp_path: Path) -> None:  # 验证基础设施缺失不污染能力失败。
    results_root = tmp_path / "run"  # 创建最小结果根。
    results_root.mkdir()  # 确保目录存在。
    (results_root / "results.json").write_text(json.dumps({"resolved_ids": ["demo__repo-1"]}), encoding="utf-8")  # 只报告一个预期实例。
    report = parse_harness_results(results_root, run_id="missing-case", expected_instance_ids=("demo__repo-1", "demo__repo-2"))  # 解析含缺失实例的运行。
    assert report.resolved == 1  # 已报告成功仍应保留。
    assert report.unresolved == 0  # 缺失实例不能被默认为未解决。
    assert report.missing == 1  # 缺失应单独统计为基础设施不完整。
    assert report.instances[1].status is SweBenchInstanceStatus.MISSING  # 逐实例状态必须同样明确。


def test_parse_harness_results_rejects_contradictory_or_foreign_instances(tmp_path: Path) -> None:  # 验证错误目录不会被静默聚合。
    contradictory = tmp_path / "contradictory"  # 创建矛盾报告目录。
    contradictory.mkdir()  # 创建真实目录。
    (contradictory / "a.json").write_text(json.dumps({"resolved_ids": ["demo__repo-1"]}), encoding="utf-8")  # 第一份报告宣称成功。
    (contradictory / "b.json").write_text(json.dumps({"unresolved_ids": ["demo__repo-1"]}), encoding="utf-8")  # 第二份报告宣称失败。
    with pytest.raises(ValueError, match="矛盾状态"):  # 解析器不能自行选择结论。
        parse_harness_results(contradictory, run_id="bad", expected_instance_ids=("demo__repo-1",))  # 解析矛盾运行。
    foreign = tmp_path / "foreign"  # 创建混入其他 run 的目录。
    foreign.mkdir()  # 创建真实目录。
    (foreign / "results.json").write_text(json.dumps({"resolved_ids": ["other__repo-9"]}), encoding="utf-8")  # 写入非预期实例。
    with pytest.raises(ValueError, match="非预期实例"):  # 解析器应保护固定分母。
        parse_harness_results(foreign, run_id="foreign", expected_instance_ids=("demo__repo-1",))  # 解析污染目录。


def test_run_harness_requires_explicit_permission_before_process_start(tmp_path: Path) -> None:  # 验证默认路径绝不启动 Docker 或官方包。
    config = _config(tmp_path)  # 创建有效工作目录和配置。
    with pytest.raises(PermissionError, match="allow_execution=True"):  # 未授权应在读取 prediction 或创建进程前失败。
        asyncio.run(run_harness(config, tmp_path / "missing.jsonl"))  # 故意传入不存在文件证明权限检查最先执行。
