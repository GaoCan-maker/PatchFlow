"""创建一次空运行所需的标准资源。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from patchflow.config.models import AppConfig
from patchflow.domain.enums import EventActor, EventType
from patchflow.domain.events import AgentEvent
from patchflow.domain.run import RunManifest
from patchflow.domain.state import AgentState
from patchflow.domain.task import TaskSpec
from patchflow.storage.artifacts import ArtifactLayout
from patchflow.storage.event_store import JsonlEventStore
from patchflow.storage.manifest_store import ManifestStore
from patchflow.utils.hashing import stable_digest
from patchflow.utils.ids import create_run_id


@dataclass(frozen=True, slots=True)
class RunContext:
    """初始化完成后交给后续 Agent 编排器的资源集合。"""

    manifest: RunManifest
    state: AgentState
    layout: ArtifactLayout
    event_store: JsonlEventStore


def initialize_run(
    task: TaskSpec,
    config: AppConfig,
    *,
    runs_root: Path | None = None,
    code_version: str = "unknown",
) -> RunContext:
    """创建运行目录、快照、manifest、初始状态和首个事件。"""

    root = runs_root if runs_root is not None else config.storage.runs_root
    run_id = create_run_id(task.task_id)
    layout = ArtifactLayout(runs_root=root, run_id=run_id)
    layout.initialize()

    # SecretStr 字段已被配置模型排除，配置快照不会写入 API key。
    config_json = config.model_dump_json(indent=2)
    task_json = task.model_dump_json(indent=2)
    layout.config_path.write_text(config_json, encoding="utf-8")
    layout.task_path.write_text(task_json, encoding="utf-8")

    manifest = RunManifest(
        run_id=run_id,
        task_id=task.task_id,
        code_version=code_version,
        agent_name=config.agent.strategy,
        model_name=f"{config.model.provider}/{config.model.model}",
        runtime_name=config.runtime.kind,
        config_digest=stable_digest(config_json),
        task_digest=stable_digest(task_json),
    )
    ManifestStore(layout.manifest_path).save(manifest)

    state = AgentState(run_id=run_id, task_id=task.task_id)
    event_store = JsonlEventStore(layout.trajectory_path)
    event_store.append(
        AgentEvent(
            run_id=run_id,
            task_id=task.task_id,
            event_type=EventType.RUN_CREATED,
            actor=EventActor.SYSTEM,
            payload={
                "code_version": code_version,
                "config_digest": manifest.config_digest,
                "task_digest": manifest.task_digest,
            },
        )
    )

    return RunContext(
        manifest=manifest,
        state=state,
        layout=layout,
        event_store=event_store,
    )

