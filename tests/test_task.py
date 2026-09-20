"""TaskSpec、预算和路径策略测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchflow.domain.enums import RepositoryKind, TaskSource
from patchflow.domain.task import PathPolicy, RepositorySpec, TaskSpec


def test_task_spec_accepts_valid_local_task() -> None:
    """合法本地任务应完成规范化并保持不可变。"""

    task = TaskSpec(
        task_id="demo-1",
        source=TaskSource.LOCAL,
        repo_spec=RepositorySpec(kind=RepositoryKind.LOCAL, location="  C:/repo  "),
        base_commit="  abc123  ",
        problem_statement="  修复解析空配置时出现的 ValueError 异常。  ",
        public_commands=("  python -m pytest  ",),
        tags=frozenset({"Parser"}),
    )

    assert task.repo_spec.location == "C:/repo"
    assert task.base_commit == "abc123"
    assert task.public_commands == ("python -m pytest",)
    assert task.tags == frozenset({"parser"})


@pytest.mark.parametrize(
    "path",
    ["../secret", "src/../../secret", "/etc/passwd", "C:/Users/secret"],
)
def test_path_policy_rejects_escape_paths(path: str) -> None:
    """路径策略不能声明工作区外路径。"""

    with pytest.raises(ValidationError):
        PathPolicy(allowed_paths=(path,))


def test_task_spec_rejects_blank_command() -> None:
    """公开命令集合中的空字符串应被拒绝。"""

    with pytest.raises(ValidationError):
        TaskSpec(
            task_id="demo-2",
            source=TaskSource.LOCAL,
            repo_spec=RepositorySpec(kind=RepositoryKind.LOCAL, location="C:/repo"),
            base_commit="abc123",
            problem_statement="修复一个可以稳定复现的配置解析错误。",
            public_commands=(" ",),
        )


def test_local_task_requires_local_repository() -> None:
    """本地任务不能意外引用远程 Git 仓库。"""

    with pytest.raises(ValidationError):
        TaskSpec(
            task_id="demo-3",
            source=TaskSource.LOCAL,
            repo_spec=RepositorySpec(
                kind=RepositoryKind.GIT,
                location="https://example.invalid/repo.git",
            ),
            base_commit="abc123",
            problem_statement="修复一个可以稳定复现的配置解析错误。",
        )

