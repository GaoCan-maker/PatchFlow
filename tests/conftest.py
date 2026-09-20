"""测试共享夹具。"""

from __future__ import annotations

import pytest

from patchflow.domain.enums import RepositoryKind, TaskSource
from patchflow.domain.task import RepositorySpec, TaskSpec


@pytest.fixture
def sample_task() -> TaskSpec:
    """返回不依赖真实仓库的最小合法任务。"""

    return TaskSpec(
        task_id="local-demo-001",
        source=TaskSource.LOCAL,
        repo_spec=RepositorySpec(
            kind=RepositoryKind.LOCAL,
            location="C:/temporary/demo-repo",
        ),
        base_commit="abc123",
        problem_statement="修复配置解析器无法处理空列表的问题，并保持默认值行为。",
        public_commands=("python -m pytest",),
        tags=frozenset({"parser", "single-file"}),
    )

