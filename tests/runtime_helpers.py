"""Step 2 Runtime 与工具测试共享的临时 Git 仓库。"""  # 说明本文件只服务测试夹具。

from __future__ import annotations  # 启用延迟解析类型标注。

import subprocess  # 导入无需 Shell 的 Git 仓库初始化能力。
from dataclasses import dataclass  # 导入轻量测试夹具数据类。
from pathlib import Path  # 导入跨平台测试路径对象。

from patchflow.domain.enums import RepositoryKind, TaskSource  # 导入任务来源和仓库类型。
from patchflow.domain.task import PathPolicy, RepositorySpec, TaskSpec  # 导入任务领域模型。


@dataclass(frozen=True, slots=True)  # 使用不可变紧凑对象保存临时仓库信息。
class TemporaryGitRepository:  # 定义测试共享仓库夹具。
    """包含隔离根、仓库路径、基础提交和 TaskSpec。"""  # 说明夹具提供的完整上下文。

    isolation_root: Path  # 保存允许 Runtime 重置的测试隔离根。
    repository: Path  # 保存具体 Git 仓库目录。
    base_commit: str  # 保存初始提交完整 SHA。
    task: TaskSpec  # 保存与临时仓库一致的任务定义。


def _run_git(repository: Path, *arguments: str) -> str:  # 定义测试仓库 Git 命令辅助函数。
    completed = subprocess.run(  # 使用参数数组同步运行短 Git 管理命令。
        ("git", *arguments),  # 构造不经过 Shell 的 Git 参数数组。
        cwd=repository,  # 固定命令工作目录为临时仓库。
        check=True,  # 让仓库初始化错误立即使测试失败。
        capture_output=True,  # 捕获输出避免污染 pytest 界面。
        text=True,  # 直接按文本形式接收 Git 输出。
    )  # 完成 Git 命令执行。
    return completed.stdout.strip()  # 返回去除末尾换行的标准输出。


def create_temporary_git_repository(tmp_path: Path) -> TemporaryGitRepository:  # 创建每个测试独享的 Git 仓库。
    isolation_root = tmp_path / "isolated"  # 在 pytest 临时目录下创建显式隔离根。
    repository = isolation_root / "repository"  # 把实际仓库放在隔离根严格子目录。
    repository.mkdir(parents=True)  # 一次性创建隔离根和仓库目录。
    _run_git(repository, "init", "-b", "main")  # 初始化具有稳定 main 分支名的仓库。
    _run_git(repository, "config", "user.email", "patchflow-tests@example.invalid")  # 配置本地测试提交邮箱。
    _run_git(repository, "config", "user.name", "PatchFlow Tests")  # 配置本地测试提交用户名。
    _run_git(repository, "config", "core.autocrlf", "false")  # 禁用换行自动转换以稳定 patch。
    app_content = (  # 构造待搜索和修改的简单产品代码。
        "def greet(name: str) -> str:  # 定义示例问候函数。\n"  # 写入带中文注释的函数签名。
        "    return f\"Hello, {name}!\"  # 返回包含输入姓名的问候语。\n"  # 写入带中文注释的当前实现。
    )  # 完成产品代码文本。
    test_content = (  # 构造可由 pytest 执行的公开回归测试。
        "from app import greet  # 导入待测试函数。\n"  # 写入带中文注释的导入语句。
        "\n"  # 保留符合 Python 风格的空行。
        "def test_greet() -> None:  # 定义问候函数回归测试。\n"  # 写入带中文注释的测试签名。
        "    assert greet(\"Ada\") == \"Hello, Ada!\"  # 验证基础行为保持不变。\n"  # 写入带中文注释的断言。
    )  # 完成测试代码文本。
    (repository / "app.py").write_text(app_content, encoding="utf-8")  # 写入产品代码文件。
    (repository / "test_app.py").write_text(test_content, encoding="utf-8")  # 写入公开测试文件。
    (repository / "README.md").write_text("# Temporary Repository\n", encoding="utf-8")  # 写入搜索辅助文档。
    (repository / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n", encoding="utf-8")  # 忽略测试工具产生的缓存目录。
    _run_git(repository, "add", "app.py", "test_app.py", "README.md", ".gitignore")  # 暂存初始仓库文件。
    _run_git(repository, "commit", "-m", "Create temporary test repository")  # 创建任务基础提交。
    base_commit = _run_git(repository, "rev-parse", "HEAD")  # 读取完整基础提交 SHA。
    task = TaskSpec(  # 创建与真实临时仓库一致的任务定义。
        task_id="runtime-demo-001",  # 设置稳定测试任务 ID。
        source=TaskSource.LOCAL,  # 声明本地任务来源。
        repo_spec=RepositorySpec(  # 描述本地 Git 仓库位置。
            kind=RepositoryKind.LOCAL,  # 声明仓库由本地路径提供。
            location=str(repository),  # 保存临时仓库绝对路径。
        ),  # 完成仓库描述。
        base_commit=base_commit,  # 固定任务基础提交。
        problem_statement="让 greet 在生成问候语前移除姓名两端的空白。",  # 提供可理解的修复需求。
        public_commands=("python -m pytest -q",),  # 保存公开测试命令提示。
        path_policy=PathPolicy(  # 设置任务文件访问策略。
            allowed_paths=(".",),  # 允许访问仓库工作树。
            denied_paths=(".git",),  # 禁止 Agent 直接读取或修改 Git 元数据。
            read_only_paths=("README.md",),  # 把说明文档设置为只读以测试策略。
        ),  # 完成路径策略。
        tags=frozenset({"runtime", "python"}),  # 添加实验分层标签。
    )  # 完成任务定义。
    return TemporaryGitRepository(  # 返回完整临时仓库夹具。
        isolation_root=isolation_root,  # 返回隔离根。
        repository=repository,  # 返回仓库路径。
        base_commit=base_commit,  # 返回基础提交。
        task=task,  # 返回任务定义。
    )  # 完成夹具构造。


def valid_patch() -> str:  # 定义可以应用到临时仓库的候选补丁。
    return (  # 返回标准 Git 统一 diff。
        "diff --git a/app.py b/app.py\n"  # 声明修改文件。
        "index cfac195..8d7c14d 100644\n"  # 提供可选索引元数据。
        "--- a/app.py\n"  # 声明旧文件路径。
        "+++ b/app.py\n"  # 声明新文件路径。
        "@@ -1,2 +1,3 @@\n"  # 声明旧新行范围。
        " def greet(name: str) -> str:  # 定义示例问候函数。\n"  # 保留函数签名上下文。
        "+    cleaned_name = name.strip()  # 去除姓名两端空白。\n"  # 添加需求要求的规范化逻辑。
        "-    return f\"Hello, {name}!\"  # 返回包含输入姓名的问候语。\n"  # 删除直接使用原始输入的实现。
        "+    return f\"Hello, {cleaned_name}!\"  # 使用规范化姓名生成问候语。\n"  # 添加新返回实现。
    )  # 完成标准补丁文本。
