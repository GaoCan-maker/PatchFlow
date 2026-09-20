"""标准运行目录布局。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArtifactLayout:
    """集中定义一次运行的目录和标准文件名。"""

    runs_root: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.runs_root / self.run_id

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def task_path(self) -> Path:
        return self.run_dir / "task.json"

    @property
    def config_path(self) -> Path:
        return self.run_dir / "config.snapshot.json"

    @property
    def trajectory_path(self) -> Path:
        return self.run_dir / "trajectory.jsonl"

    @property
    def final_patch_path(self) -> Path:
        return self.run_dir / "final.patch"

    def initialize(self) -> None:
        """创建固定目录；已存在时保持幂等。"""

        self.run_dir.mkdir(parents=True, exist_ok=False)
        for directory in (
            "contexts",
            "model_calls",
            "tool_calls",
            "candidates",
            "logs",
        ):
            (self.run_dir / directory).mkdir()

