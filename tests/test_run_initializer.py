"""运行初始化与 artifact 测试。"""

from __future__ import annotations

import json

from patchflow.application.run_initializer import initialize_run
from patchflow.config.models import AppConfig, ModelConfig
from patchflow.domain.enums import EventType, RunStatus
from patchflow.storage.manifest_store import ManifestStore


def test_initialize_run_creates_standard_artifacts(tmp_path, sample_task) -> None:
    """空运行也必须具备可审计的清单、快照和首个事件。"""

    config = AppConfig(
        model=ModelConfig(
            provider="fake",
            model="deterministic-test-model",
            api_key="must-not-be-written",
        )
    )

    context = initialize_run(
        sample_task,
        config,
        runs_root=tmp_path,
        code_version="test-sha",
    )

    assert context.manifest.status is RunStatus.PENDING
    assert context.layout.manifest_path.exists()
    assert context.layout.task_path.exists()
    assert context.layout.config_path.exists()
    assert context.layout.trajectory_path.exists()
    assert (context.layout.run_dir / "candidates").is_dir()

    config_snapshot = context.layout.config_path.read_text(encoding="utf-8")
    assert "must-not-be-written" not in config_snapshot

    loaded_manifest = ManifestStore(context.layout.manifest_path).load()
    assert loaded_manifest == context.manifest

    events = context.event_store.load_all()
    assert len(events) == 1
    assert events[0].event_type is EventType.RUN_CREATED

    serialized_task = json.loads(context.layout.task_path.read_text(encoding="utf-8"))
    assert serialized_task["task_id"] == sample_task.task_id


def test_initialize_run_uses_distinct_run_ids(tmp_path, sample_task) -> None:
    """同一任务重复运行也不能覆盖前一次 artifact。"""

    first = initialize_run(sample_task, AppConfig(), runs_root=tmp_path)
    second = initialize_run(sample_task, AppConfig(), runs_root=tmp_path)

    assert first.manifest.run_id != second.manifest.run_id
    assert first.layout.run_dir.exists()
    assert second.layout.run_dir.exists()

