"""RunManifest 的原子存储。"""

from __future__ import annotations

import os
from pathlib import Path

from patchflow.domain.run import RunManifest


class ManifestStore:
    """保存和加载运行清单。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, manifest: RunManifest) -> None:
        """先写临时文件再替换，避免中途退出留下半个 JSON。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary_path.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, self._path)

    def load(self) -> RunManifest:
        """加载并重新校验运行清单。"""

        return RunManifest.model_validate_json(self._path.read_text(encoding="utf-8"))

