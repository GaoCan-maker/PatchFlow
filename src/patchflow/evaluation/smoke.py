"""创建可重复的两任务 MicroSWE 开发烟测集。"""  # 仅供 Week 3 流水线验收，不代表正式基准。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 写出 TaskSpec 批量输入。
import subprocess  # 用 Git CLI 创建固定基础提交。
from pathlib import Path  # 管理用户指定的独立仓库目录。

from patchflow.domain.enums import RepositoryKind, TaskSource  # 填写任务来源与仓库定位。
from patchflow.domain.task import RepositorySpec, TaskSpec  # 构造正式领域任务。

_CASES = (  # 定义两个不依赖网络的小型 Python 缺陷。
    ("micro-greet-strip", "让 greet 返回的问候语去掉姓名两端的空白，同时保留正常姓名行为。", "def greet(name: str) -> str:  # 生成问候语。\n    return f'Hello, {name}!'  # 当前未规范化输入。\n", "from app import greet  # 导入待测试函数。\n\ndef test_trim() -> None:  # 覆盖带空白的姓名。\n    assert greet(' Ada ') == 'Hello, Ada!'  # 检查规范化输出。\n\ndef test_plain() -> None:  # 覆盖正常姓名。\n    assert greet('Ada') == 'Hello, Ada!'  # 检查原有行为。\n"),  # 第一项是字符串边界缺陷。
    ("micro-parse-empty", "让 parse_items 忽略逗号分隔输入中的空白项，同时保留正常项顺序。", "def parse_items(raw: str) -> list[str]:  # 解析逗号分隔输入。\n    return [part.strip() for part in raw.split(',')]  # 当前保留空白项。\n", "from app import parse_items  # 导入解析函数。\n\ndef test_empty() -> None:  # 覆盖空白字段。\n    assert parse_items('a, ,b,') == ['a', 'b']  # 检查空项被忽略。\n\ndef test_order() -> None:  # 覆盖正常顺序。\n    assert parse_items('b,a') == ['b', 'a']  # 检查项目顺序不变。\n"),  # 第二项是列表过滤缺陷。
)  # 完成固定任务定义。


def _git(repository: Path, *arguments: str) -> str:  # 运行仅用于生成数据集的 Git 管理命令。
    result = subprocess.run(("git", *arguments), cwd=repository, capture_output=True, text=True, check=True)  # 不通过 Shell 拼接参数。
    return result.stdout.strip()  # 返回标准输出供读取提交 SHA。


def prepare_smoke_dataset(root: Path) -> tuple[TaskSpec, ...]:  # 在用户指定的空目录中创建两份干净仓库。
    if root.exists() and any(root.iterdir()):  # 绝不覆盖用户已有目录内容。
        raise ValueError("烟测数据集目录必须为空")  # 要求调用方明确选择新的位置。
    root.mkdir(parents=True, exist_ok=True)  # 创建数据集根目录。
    tasks: list[TaskSpec] = []  # 收集共享的正式任务定义。
    for task_id, issue, app_source, test_source in _CASES:  # 为每个缺陷创建独立基础提交。
        repository = root / task_id  # 为单任务选择子目录。
        repository.mkdir()  # 建立不共享工作树的仓库。
        _git(repository, "init", "-b", "main")  # 创建稳定分支名的 Git 仓库。
        _git(repository, "config", "user.name", "PatchFlow Smoke")  # 只配置当前测试仓库提交者。
        _git(repository, "config", "user.email", "patchflow-smoke@example.invalid")  # 使用无效域名避免真实邮件。
        _git(repository, "config", "core.autocrlf", "false")  # 防止跨平台换行变化影响补丁。
        (repository / "app.py").write_text(app_source, encoding="utf-8")  # 写入待修复源码。
        (repository / "test_app.py").write_text(test_source, encoding="utf-8")  # 写入修复前失败的公开回归测试。
        (repository / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n", encoding="utf-8")  # 忽略公开测试缓存。
        _git(repository, "add", "app.py", "test_app.py", ".gitignore")  # 暂存固定初始文件。
        _git(repository, "commit", "-m", "Add MicroSWE smoke task")  # 创建任务基础提交。
        base_commit = _git(repository, "rev-parse", "HEAD")  # 固定评测起点。
        tasks.append(TaskSpec(task_id=task_id, source=TaskSource.MICRO_SWE, repo_spec=RepositorySpec(kind=RepositoryKind.LOCAL, location=str(repository.resolve())), base_commit=base_commit, problem_statement=issue, public_commands=("python -m pytest -q",), tags=frozenset({"micro_swe", "smoke", "python"})))  # 构造不含私有答案的任务。
    (root / "tasks.json").write_text(json.dumps([json.loads(task.model_dump_json()) for task in tasks], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 保存可直接用于 CLI 的任务清单。
    return tuple(tasks)  # 返回内存中的同一批任务。
