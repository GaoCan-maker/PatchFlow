"""显式开启的 Docker 定位评测端到端验收。"""  # 不发送模型请求也不读取任何 API key。

from __future__ import annotations  # 延迟解析测试类型。

import asyncio  # 驱动两个 DockerRuntime 的异步生命周期。
import json  # 验证评测报告确实写为结构化 JSON。
import os  # 读取现有 Docker 集成测试开关。
from pathlib import Path  # 管理隔离的 MicroSWE 数据集目录。

import pytest  # 提供跳过条件和临时目录夹具。

from patchflow.evaluation.localization import (  # 调用真实定位评测器。
    LocalizationGold,  # 声明评测端私有金标。
    evaluate_localization,  # 运行完整配置及消融。
)  # 完成评测接口导入。
from patchflow.evaluation.smoke import prepare_smoke_dataset  # 复用第三周公开的两任务数据集。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 默认单测不依赖 daemon。
def test_docker_localization_report_and_ablations(tmp_path: Path) -> None:  # 验证两个任务的完整配置与五组消融。
    tasks = prepare_smoke_dataset(tmp_path / "smoke")  # 生成两个具有真实 Git 基础提交的公开缺陷。
    gold = {"micro-greet-strip": LocalizationGold("app.py", "greet", failing_tests=("test_trim",)), "micro-parse-empty": LocalizationGold("app.py", "parse_items", failing_tests=("test_empty",))}  # 金标和修复前公开失败测试留在评测端。
    report_path = tmp_path / "localization_report.json"  # 指定隔离目录中的报告文件。
    report = asyncio.run(evaluate_localization(tasks, gold, report_path=report_path, cache_dir=tmp_path / "index_cache"))  # 在默认隔离 Docker 容器中执行评测。
    assert report["dataset_size"] == 2  # 任务分母必须与输入一致。
    assert set(report["summary"]) == {"full", "without_issue", "without_search", "without_traceback", "without_test_relation", "without_symbol"}  # 验证每个通道都有对应消融。
    assert len(report["cases"]) == 12  # 两个任务乘六种配置应产生十二份独立排名。
    assert report["summary"]["full"]["file_top1"] == 1.0  # 两个公开缺陷的目标文件都应排在首位。
    assert report["summary"]["full"]["symbol_top1"] == 1.0  # 两个目标函数都应排在首位。
    assert json.loads(report_path.read_text(encoding="utf-8"))["dataset_size"] == 2  # 磁盘报告与内存报告同源。
