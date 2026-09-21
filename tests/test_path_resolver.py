"""安全工作区路径解析器测试。"""  # 说明本文件聚焦路径边界。

from __future__ import annotations  # 启用延迟解析类型标注。

from pathlib import Path  # 导入测试路径类型。

import pytest  # 导入异常断言和临时目录夹具。

from patchflow.runtime.errors import PathViolationError  # 导入路径策略异常。
from patchflow.runtime.paths import WorkspacePathResolver  # 导入待测试路径解析器。
from tests.runtime_helpers import create_temporary_git_repository  # 导入临时仓库工厂。


def test_resolver_accepts_allowed_file(tmp_path: Path) -> None:  # 测试正常文件解析。
    fixture = create_temporary_git_repository(tmp_path)  # 创建隔离 Git 仓库。
    resolver = WorkspacePathResolver(fixture.repository, fixture.task.path_policy)  # 创建任务路径解析器。

    resolved = resolver.resolve("app.py")  # 解析允许访问的已跟踪文件。

    assert resolved == (fixture.repository / "app.py").resolve()  # 验证结果是工作区内真实路径。


@pytest.mark.parametrize("unsafe_path", ("../outside.txt", "/etc/passwd", "C:/Windows/system.ini"))  # 枚举跨平台逃逸形式。
def test_resolver_rejects_lexical_escape(tmp_path: Path, unsafe_path: str) -> None:  # 测试词法目录逃逸。
    fixture = create_temporary_git_repository(tmp_path)  # 创建隔离 Git 仓库。
    resolver = WorkspacePathResolver(fixture.repository, fixture.task.path_policy)  # 创建任务路径解析器。

    with pytest.raises(PathViolationError):  # 期待所有不安全路径被拒绝。
        resolver.resolve(unsafe_path, must_exist=False)  # 尝试解析工作区外目标。


def test_resolver_rejects_denied_git_metadata(tmp_path: Path) -> None:  # 测试拒绝列表保护 Git 元数据。
    fixture = create_temporary_git_repository(tmp_path)  # 创建隔离 Git 仓库。
    resolver = WorkspacePathResolver(fixture.repository, fixture.task.path_policy)  # 创建任务路径解析器。

    with pytest.raises(PathViolationError):  # 期待访问 .git 被拒绝。
        resolver.resolve(".git/HEAD")  # 尝试读取敏感 Git 元数据。


def test_resolver_rejects_write_to_read_only_path(tmp_path: Path) -> None:  # 测试只读路径写保护。
    fixture = create_temporary_git_repository(tmp_path)  # 创建隔离 Git 仓库。
    resolver = WorkspacePathResolver(fixture.repository, fixture.task.path_policy)  # 创建任务路径解析器。

    assert resolver.resolve("README.md", write=False).is_file()  # 验证只读文件仍允许读取。
    with pytest.raises(PathViolationError):  # 期待写入同一文件被拒绝。
        resolver.resolve("README.md", write=True)  # 尝试以写模式解析只读文件。


def test_resolver_rejects_symlink_escape(tmp_path: Path) -> None:  # 测试真实路径层面的符号链接逃逸。
    fixture = create_temporary_git_repository(tmp_path)  # 创建隔离 Git 仓库。
    outside = tmp_path / "outside.txt"  # 在仓库外创建目标文件路径。
    outside.write_text("secret", encoding="utf-8")  # 写入用于验证的外部内容。
    link = fixture.repository / "escape-link"  # 在仓库内创建符号链接路径。
    link.symlink_to(outside)  # 让仓库内路径指向工作区外文件。
    resolver = WorkspacePathResolver(fixture.repository, fixture.task.path_policy)  # 创建任务路径解析器。

    with pytest.raises(PathViolationError):  # 期待解析真实路径后发现越界。
        resolver.resolve("escape-link")  # 尝试通过仓库内符号链接读取外部文件。

