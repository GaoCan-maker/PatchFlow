"""第五周主策略 CLI 的显式付费保护测试。"""  # 默认路径不能构造客户端或启动容器。

from __future__ import annotations  # 延迟解析临时目录类型。

from pathlib import Path  # 管理测试清单和运行目录。

import pytest  # 断言 argparse 阻止未授权付费调用。

from patchflow.agent_cli import main  # 调用真实主策略命令入口。
from patchflow.evaluation.smoke import prepare_smoke_dataset  # 生成结构合法的离线任务清单。


def test_main_agent_cli_requires_explicit_api_spend(tmp_path: Path) -> None:  # 验证没有许可时不读取 API key 或建 artifact。
    prepare_smoke_dataset(tmp_path / "smoke")  # 创建两任务 JSON 清单但不运行模型。
    tasks = tmp_path / "smoke" / "tasks.json"  # 指向有效任务清单。
    runs_root = tmp_path / "runs"  # 指定不应被创建的运行目录。
    with pytest.raises(SystemExit) as caught:  # argparse 应在任何网络或容器操作前退出。
        main([str(tasks), "--task-id", "micro-greet-strip", "--provider", "openai_compatible", "--model-id", "example-model", "--base-url", "https://api.agicto.cn/v1", "--runs-root", str(runs_root)])  # 故意不提供付费确认旗标。
    assert caught.value.code == 2  # 参数错误应返回标准非零退出码。
    assert not runs_root.exists()  # 未授权时不应创建运行工件。
