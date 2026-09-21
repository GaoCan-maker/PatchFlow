"""LocalRuntime 生命周期、命令执行与补丁语义测试。"""  # 说明本文件验证本地运行时的安全边界。

from __future__ import annotations  # 启用延迟解析类型标注。

import asyncio  # 导入同步 pytest 中运行异步接口的能力。
import subprocess  # 导入构造脏工作区所需的短 Git 命令能力。
import sys  # 导入当前 Python 解释器路径以稳定运行子进程。
from pathlib import Path  # 导入跨平台临时路径类型。

import pytest  # 导入异常断言和临时目录夹具。

from patchflow.runtime.errors import (  # 导入需要精确断言的安全异常。
    DirtyWorkspaceError,  # 导入脏工作区拒绝异常。
    RuntimeNotStartedError,  # 导入非法生命周期异常。
    WorkspaceSafetyError,  # 导入隔离目录配置异常。
)  # 结束异常导入列表。
from patchflow.runtime.local import LocalRuntime  # 导入待测试本地运行时。
from tests.runtime_helpers import (  # 导入共享临时仓库夹具。
    create_temporary_git_repository,  # 导入临时 Git 仓库构造函数。
    valid_patch,  # 导入可应用到测试仓库的标准补丁。
)  # 结束共享测试夹具导入列表。


def test_runtime_executes_success_failure_timeout_and_truncation(tmp_path: Path) -> None:  # 验证四种命令结果。
    repository = create_temporary_git_repository(tmp_path)  # 创建完全隔离且干净的临时 Git 仓库。
    runtime = LocalRuntime(  # 创建具有较小输出预算的本地运行时。
        repository.repository,  # 指定任务仓库目录。
        repository.isolation_root,  # 指定允许破坏性清理的测试隔离根。
        max_output_chars=80,  # 使用小上限以便稳定触发输出截断。
        termination_grace_seconds=0.1,  # 缩短测试中的进程终止宽限时间。
    )  # 完成本地运行时构造。

    async def scenario() -> None:  # 定义在单个事件循环中执行的测试场景。
        with pytest.raises(RuntimeNotStartedError):  # 验证启动前不能执行命令。
            await runtime.execute((sys.executable, "-c", "print('no')"), timeout_seconds=1.0)  # 尝试非法执行。
        await runtime.start(repository.task)  # 通过 Git 和路径安全检查启动运行时。
        success = await runtime.execute(  # 执行正常退出并同时产生两种输出的命令。
            (sys.executable, "-c", "import sys; print('ok'); print('note', file=sys.stderr)"),  # 使用参数数组传递脚本。
            timeout_seconds=2.0,  # 设置足够完成的硬超时。
        )  # 完成成功命令执行。
        assert success.succeeded is True  # 验证零退出码被识别为成功。
        assert success.return_code == 0  # 验证记录真实退出码。
        assert success.stdout.strip() == "ok"  # 验证标准输出被完整捕获。
        assert success.stderr.strip() == "note"  # 验证标准错误被完整捕获。
        failure = await runtime.execute(  # 执行显式非零退出的命令。
            (sys.executable, "-c", "import sys; print('bad', file=sys.stderr); raise SystemExit(7)"),  # 生成诊断并退出七。
            timeout_seconds=2.0,  # 设置足够完成的硬超时。
        )  # 完成失败命令执行。
        assert failure.succeeded is False  # 验证非零退出不被误判为成功。
        assert failure.return_code == 7  # 验证保留业务退出码。
        assert "bad" in failure.stderr  # 验证失败诊断可反馈给 Agent。
        missing = await runtime.execute(("patchflow-command-that-does-not-exist",), timeout_seconds=1.0)  # 执行不存在命令。
        assert missing.return_code is None  # 验证未启动进程没有伪造退出码。
        assert missing.termination_reason == "process_start_failed"  # 验证提供稳定失败分类。
        large_output = await runtime.execute(  # 执行超过字符预算的输出命令。
            (sys.executable, "-c", "print('A' * 300)"),  # 输出足够长的确定性文本。
            timeout_seconds=2.0,  # 设置足够完成的硬超时。
        )  # 完成长输出命令执行。
        assert large_output.output_truncated is True  # 验证记录截断事实。
        assert "输出已截断" in large_output.stdout  # 验证输出中包含清晰截断标记。
        timeout = await runtime.execute(  # 执行超过硬时限的休眠命令。
            (sys.executable, "-c", "import time; print('started', flush=True); time.sleep(5)"),  # 先输出再长时间休眠。
            timeout_seconds=0.2,  # 使用很短时限触发进程组终止。
        )  # 完成超时命令执行。
        assert timeout.timed_out is True  # 验证明确记录超时状态。
        assert timeout.termination_reason == "timeout"  # 验证提供稳定超时分类。
        assert "started" in timeout.stdout  # 验证保留超时发生前的标准输出。
        assert timeout.elapsed_seconds < 3.0  # 验证命令没有等待完整五秒。
        await runtime.close()  # 关闭运行时并清理生命周期状态。
        with pytest.raises(RuntimeNotStartedError):  # 验证关闭后不能继续读取文件。
            await runtime.read_file("app.py")  # 尝试在关闭状态访问仓库。

    asyncio.run(scenario())  # 在独立事件循环中运行异步场景。


def test_runtime_applies_patch_generates_diff_and_resets(tmp_path: Path) -> None:  # 验证候选补丁完整生命周期。
    repository = create_temporary_git_repository(tmp_path)  # 创建干净临时仓库。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 创建受控本地运行时。

    async def scenario() -> None:  # 定义补丁应用测试场景。
        await runtime.start(repository.task)  # 启动并固定基础提交。
        initial_content = await runtime.read_file("app.py", start_line=1, end_line=2)  # 读取原始代码片段。
        assert "name.strip" not in initial_content  # 验证修改前尚未实现需求。
        applied = await runtime.apply_patch(valid_patch())  # 应用通过路径策略的有效补丁。
        assert applied.applied is True  # 验证有效补丁成功应用。
        assert applied.changed_files == ("app.py",)  # 验证返回规范化修改文件列表。
        modified_content = await runtime.read_file("app.py")  # 读取补丁后的完整代码。
        assert "cleaned_name = name.strip()" in modified_content  # 验证目标代码确实进入工作区。
        created = await runtime.execute(  # 通过 Runtime 在临时仓库中新建一个模块。
            (sys.executable, "-c", "from pathlib import Path; Path('new_module.py').write_text('VALUE = 1\\n')"),  # 写入可识别的新增文件。
            timeout_seconds=2.0,  # 限制新文件创建耗时。
        )  # 完成容器无关的新增文件命令。
        assert created.succeeded  # 验证新增文件已经写入工作区。
        diff = await runtime.get_diff()  # 导出相对于基础提交的候选差异。
        assert "diff --git a/app.py b/app.py" in diff  # 验证 diff 包含目标文件头。
        assert "+    cleaned_name = name.strip()" in diff  # 验证 diff 包含新增逻辑。
        assert "diff --git a/new_module.py b/new_module.py" in diff  # 验证新增文件也进入最终 patch。
        await runtime.reset()  # 回滚所有候选修改。
        restored_content = await runtime.read_file("app.py")  # 重新读取被回滚文件。
        assert restored_content == initial_content  # 验证内容精确恢复到基础提交。
        assert not (repository.repository / "new_module.py").exists()  # 验证新增文件也被回滚。
        assert await runtime.get_diff() == ""  # 验证回滚后不存在残留差异。
        reapplied = await runtime.apply_patch(diff)  # 检查导出的组合补丁能重新应用。
        assert reapplied.applied  # 验证组合补丁格式对 Git 有效。
        assert (repository.repository / "new_module.py").read_text(encoding="utf-8") == "VALUE = 1\n"  # 验证新文件内容恢复。
        await runtime.reset()  # 为本测试清理重新应用的候选修改。
        await runtime.close()  # 关闭本次运行时。

    asyncio.run(scenario())  # 运行异步补丁场景。


def test_runtime_rejects_invalid_and_read_only_patches_without_changes(tmp_path: Path) -> None:  # 验证失败原子性。
    repository = create_temporary_git_repository(tmp_path)  # 创建干净临时仓库。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 创建受控本地运行时。
    read_only_patch = (  # 构造试图修改只读 README 的统一 diff。
        "diff --git a/README.md b/README.md\n"  # 声明只读目标文件。
        "--- a/README.md\n"  # 声明旧文件路径。
        "+++ b/README.md\n"  # 声明新文件路径。
        "@@ -1 +1 @@\n"  # 声明单行替换范围。
        "-# Temporary Repository\n"  # 删除原说明标题。
        "+# Changed\n"  # 添加不允许的说明标题。
    )  # 完成只读补丁文本。
    malformed_patch = valid_patch().replace("def greet", "def missing", 1)  # 构造上下文无法匹配的补丁。

    async def scenario() -> None:  # 定义补丁拒绝场景。
        await runtime.start(repository.task)  # 启动运行时。
        read_only_result = await runtime.apply_patch(read_only_patch)  # 尝试修改只读文件。
        assert read_only_result.applied is False  # 验证策略层拒绝补丁。
        assert "只允许读取" in (read_only_result.rejection_reason or "")  # 验证拒绝原因可用于 Agent 反思。
        malformed_result = await runtime.apply_patch(malformed_patch)  # 尝试应用无法匹配的补丁。
        assert malformed_result.applied is False  # 验证 Git 预检查拒绝补丁。
        assert malformed_result.rejection_reason == "git apply --check 未通过"  # 验证稳定高层错误原因。
        assert await runtime.get_diff() == ""  # 验证两次失败均未留下部分修改。
        await runtime.close()  # 关闭运行时。

    asyncio.run(scenario())  # 运行异步拒绝场景。


def test_runtime_rejects_dirty_repository_on_start(tmp_path: Path) -> None:  # 验证不会覆盖用户已有修改。
    repository = create_temporary_git_repository(tmp_path)  # 创建初始干净仓库。
    (repository.repository / "untracked.txt").write_text("do not delete\n", encoding="utf-8")  # 制造未跟踪用户文件。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 创建本地运行时。
    with pytest.raises(DirtyWorkspaceError):  # 验证启动阶段精确拒绝脏工作区。
        asyncio.run(runtime.start(repository.task))  # 尝试启动可能执行 reset 的运行时。
    assert (repository.repository / "untracked.txt").read_text(encoding="utf-8") == "do not delete\n"  # 验证文件未被清理。


def test_runtime_rejects_unsafe_workspace_scope(tmp_path: Path) -> None:  # 验证破坏性操作作用域必须显式隔离。
    repository = create_temporary_git_repository(tmp_path)  # 创建隔离根及其严格子仓库。
    with pytest.raises(WorkspaceSafetyError):  # 验证工作区不能与隔离根相同。
        LocalRuntime(repository.repository, repository.repository)  # 尝试移除严格子目录边界。
    outside_root = tmp_path / "different-root"  # 创建不包含仓库的另一目录路径。
    outside_root.mkdir()  # 创建另一目录供真实路径解析。
    with pytest.raises(WorkspaceSafetyError):  # 验证工作区不能位于隔离根之外。
        LocalRuntime(repository.repository, outside_root)  # 尝试配置错误隔离根。


def test_runtime_rejects_head_that_differs_from_base_commit(tmp_path: Path) -> None:  # 验证不会隐式切换提交。
    repository = create_temporary_git_repository(tmp_path)  # 创建基础仓库和任务。
    (repository.repository / "second.txt").write_text("second\n", encoding="utf-8")  # 创建第二次提交文件。
    subprocess.run(("git", "add", "second.txt"), cwd=repository.repository, check=True)  # 暂存第二次提交文件。
    subprocess.run(("git", "commit", "-m", "Create second commit"), cwd=repository.repository, check=True)  # 创建新 HEAD。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 创建仍指向旧 base_commit 的运行时。
    with pytest.raises(WorkspaceSafetyError, match="HEAD"):  # 验证启动拒绝不一致提交。
        asyncio.run(runtime.start(repository.task))  # 尝试启动旧任务定义。
