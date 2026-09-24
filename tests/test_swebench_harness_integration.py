"""需要显式开启的官方 SWE-bench gold 环境集成测试。"""  # 默认 CI 不下载数据集或启动大型镜像。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 运行异步官方 Harness 适配器。
import os  # 读取显式集成测试开关和实例配置。
from pathlib import Path  # 接收官方 Harness 工作目录。

import pytest  # 在环境未准备时安全跳过重型测试。

from patchflow.swebench.harness import (  # 导入真实官方调用接口。
    HarnessProcessStatus,  # 检查官方进程是否完整完成。
    SweBenchHarnessConfig,  # 构造单实例低并发评测配置。
    run_harness,  # 调用显式授权的官方模块进程。
)  # 完成真实 Harness 接口导入。

pytestmark = pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_SWEBENCH_TESTS") != "1", reason="需要显式开启真实 SWE-bench Harness")  # 默认跳过高资源官方评测。


def test_official_gold_patch_environment() -> None:  # 用一个显式实例验证官方数据、镜像和 Docker 环境。
    instance_id = os.environ.get("PATCHFLOW_SWEBENCH_INSTANCE_ID")  # 读取用户选定的小型官方实例。
    workdir = os.environ.get("PATCHFLOW_SWEBENCH_WORKDIR")  # 读取允许官方写日志的工作目录。
    python_executable = os.environ.get("PATCHFLOW_SWEBENCH_PYTHON", "python")  # 读取安装了 swebench 的解释器。
    if not instance_id or not workdir:  # 开启测试后仍要求完整显式资源配置。
        pytest.fail("请设置 PATCHFLOW_SWEBENCH_INSTANCE_ID 和 PATCHFLOW_SWEBENCH_WORKDIR")  # 给出可执行配置提示。
    config = SweBenchHarnessConfig(  # 构造单实例低并发 gold 验证。
        dataset_name=os.environ.get("PATCHFLOW_SWEBENCH_DATASET", "princeton-nlp/SWE-bench_Lite"),  # 允许覆盖官方数据集名称。
        split=os.environ.get("PATCHFLOW_SWEBENCH_SPLIT", "test"),  # 允许覆盖数据分片。
        run_id=os.environ.get("PATCHFLOW_SWEBENCH_RUN_ID", "patchflow-week7-gold"),  # 为官方日志指定稳定 run ID。
        workdir=Path(workdir),  # 使用用户准备的可写目录。
        instance_ids=(instance_id,),  # 只验证一个实例以控制资源。
        python_executable=python_executable,  # 使用独立官方环境。
        max_workers=1,  # gold 烟测固定串行。
        process_timeout_seconds=float(os.environ.get("PATCHFLOW_SWEBENCH_PROCESS_TIMEOUT", "7200")),  # 允许慢速首次镜像准备。
    )  # 完成真实配置。
    execution = asyncio.run(run_harness(config, "gold", allow_execution=True))  # 显式授权官方 gold 评测。
    assert execution.status is HarnessProcessStatus.COMPLETED, execution.stderr  # 环境验证必须完整生成结果。
    assert execution.report is not None and execution.report.resolved == 1  # 官方 gold patch 应成功解决选定实例。
