"""需要显式开启的真实 DockerRuntime 集成验收。"""  # 说明默认单测不依赖 Docker daemon。

from __future__ import annotations  # 启用延迟类型标注。

import asyncio  # 在同步 pytest 中运行异步 Runtime。
import os  # 读取显式 Docker 测试开关。
from pathlib import Path  # 接收 pytest 临时目录。

import pytest  # 使用跳过和临时目录夹具。

from patchflow.runtime.docker import DockerRuntime  # 导入真实 Docker 后端。
from patchflow.runtime.errors import (  # 验证路径策略和超时后生命周期。
    PathViolationError,  # 验证符号链接绕过被拒绝。
    RuntimeNotStartedError,  # 验证超时销毁后的生命周期保护。
)  # 结束运行时异常导入列表。
from tests.runtime_helpers import (  # 导入临时任务仓库和有效补丁。
    create_temporary_git_repository,  # 创建真实 Git 基础提交。
    valid_patch,  # 生成可应用的统一 diff。
)  # 结束测试夹具导入列表。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 默认跳过真实容器。
def test_real_docker_runtime_complete_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # 验证容器闭环。
    repository = create_temporary_git_repository(tmp_path)  # 创建与用户仓库隔离的测试仓库。
    monkeypatch.setenv("PATCHFLOW_TEST_SECRET", "must-not-enter-container")  # 设置不允许传入容器的哨兵环境变量。
    runtime = DockerRuntime(repository.repository)  # 创建默认禁网、非 root、限资源容器后端。

    async def scenario() -> None:  # 组织真实容器生命周期。
        await runtime.start(repository.task)  # 启动容器并复制干净仓库到私有 tmpfs。
        try:  # 即使中途断言失败也必须销毁容器。
            content = await runtime.read_file("app.py", start_line=1, end_line=2)  # 在容器中读取源码。
            assert "def greet" in content  # 验证容器内仓库副本可读。
            environment = await runtime.execute(  # 查询容器内是否暴露宿主测试密钥。
                ("python", "-c", "import os; print(os.environ.get('PATCHFLOW_TEST_SECRET', 'absent'))"),  # 读取指定环境变量。
                timeout_seconds=10.0,  # 为短命令设置硬超时。
            )  # 完成环境变量检查。
            assert environment.succeeded and environment.stdout.strip() == "absent"  # 验证密钥未传入容器。
            source_write = await runtime.execute(  # 尝试修改只读挂载的输入仓库。
                ("python", "-c", "open('/source/app.py', 'a').write('forbidden')"),  # 对输入路径尝试追加文本。
                timeout_seconds=10.0,  # 限制权限检查命令耗时。
            )  # 完成只读挂载检查。
            assert not source_write.succeeded  # 验证容器无法修改宿主源仓库。
            linked = await runtime.execute(("ln", "-s", ".git/config", "hidden_link"), timeout_seconds=10.0)  # 创建指向拒绝目录的符号链接。
            assert linked.succeeded  # 验证链接确实存在于容器私有副本。
            with pytest.raises(PathViolationError):  # 验证容器内真实路径再次执行拒绝策略。
                await runtime.read_file("hidden_link")  # 尝试借助链接读取 Git 配置。
            patch = await runtime.apply_patch(valid_patch())  # 在容器私有副本应用候选补丁。
            assert patch.applied  # 验证补丁成功应用。
            created = await runtime.execute(  # 在容器私有工作区新增一个未跟踪模块。
                ("python", "-c", "from pathlib import Path; Path('new_module.py').write_text('VALUE = 1\\n')"),  # 写入新增文件。
                timeout_seconds=10.0,  # 限制文件创建命令耗时。
            )  # 完成新文件创建。
            assert created.succeeded  # 验证容器内新文件创建成功。
            tests = await runtime.execute(("python", "-m", "pytest", "-q"), timeout_seconds=60.0)  # 执行容器内真实测试。
            assert tests.succeeded and "1 passed" in tests.stdout  # 验证回归测试通过。
            diff = await runtime.get_diff()  # 导出同时包含修改与新增文件的候选补丁。
            assert "cleaned_name = name.strip()" in diff  # 验证已有代码修改进入补丁。
            assert "diff --git a/new_module.py b/new_module.py" in diff  # 验证新增文件进入最终补丁。
            large = await runtime.execute(  # 生成超过 Agent 观察预算的大输出。
                ("python", "-c", "import sys; print('X' * 200000); print('END', file=sys.stderr)"),  # 同时产生大量 stdout 和短 stderr。
                timeout_seconds=10.0,  # 确保测试不会无限占用容器。
            )  # 完成受限输出测试。
            assert large.succeeded and large.output_truncated  # 验证大输出成功且明确标记截断。
            assert len(large.stdout) <= 20_000 and "END" in large.stderr  # 验证头尾预算和 stderr 保留。
            await runtime.reset()  # 在容器副本中回到基础提交。
            assert await runtime.get_diff() == ""  # 验证回滚后没有已跟踪修改。
            assert not (repository.repository / "new_module.py").exists()  # 验证新增文件没有写入宿主源仓库。
            assert "cleaned_name" not in (repository.repository / "app.py").read_text(encoding="utf-8")  # 验证宿主源文件未变。
        finally:  # 在所有退出路径执行清理。
            await runtime.close()  # 销毁任务容器和私有副本。

    asyncio.run(scenario())  # 运行完整真实 Docker 集成场景。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 默认跳过真实容器。
def test_real_docker_runtime_timeout_removes_container(tmp_path: Path) -> None:  # 验证命令超时的销毁语义。
    repository = create_temporary_git_repository(tmp_path)  # 创建不会触碰用户仓库的临时任务。
    runtime = DockerRuntime(repository.repository)  # 创建独立容器运行时。

    async def scenario() -> None:  # 定义超时与清理测试流程。
        await runtime.start(repository.task)  # 启动真实任务容器。
        try:  # 确保断言失败时仍调用清理接口。
            result = await runtime.execute(  # 在容器内执行明显超出时限的命令。
                ("python", "-c", "import time; time.sleep(5)"),  # 休眠五秒以触发硬超时。
                timeout_seconds=0.2,  # 设置短硬时限。
            )  # 等待 Runtime 终止命令并销毁容器。
            assert result.timed_out and result.termination_reason == "timeout"  # 验证结构化超时结果。
            with pytest.raises(RuntimeNotStartedError):  # 验证容器已经失效。
                await runtime.execute(("true",), timeout_seconds=1.0)  # 尝试复用已销毁的容器。
        finally:  # 保留重复关闭的幂等清理。
            await runtime.close()  # 在所有退出路径确认无容器残留。

    asyncio.run(scenario())  # 执行真实超时集成测试。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 默认跳过真实容器。
def test_real_docker_runtime_tasks_have_independent_workspaces(tmp_path: Path) -> None:  # 验证任务间状态隔离。
    repository = create_temporary_git_repository(tmp_path)  # 创建单一只读源仓库。
    first = DockerRuntime(repository.repository)  # 创建第一个任务容器。
    second = DockerRuntime(repository.repository)  # 创建第二个任务容器。

    async def scenario() -> None:  # 定义两个容器共享输入但不共享修改的场景。
        try:  # 确保任一启动或断言失败时两个容器都关闭。
            await first.start(repository.task)  # 从基础提交创建第一个私有副本。
            await second.start(repository.task)  # 从同一基础提交创建第二个私有副本。
            patched = await first.apply_patch(valid_patch())  # 只在第一个容器修改代码。
            assert patched.applied  # 验证第一个候选补丁成功。
            first_code = await first.read_file("app.py")  # 读取第一个容器的文件。
            second_code = await second.read_file("app.py")  # 读取第二个容器的文件。
            assert "cleaned_name" in first_code  # 验证第一个工作区看到自己的修改。
            assert "cleaned_name" not in second_code  # 验证第二个工作区保持原样。
            assert await second.get_diff() == ""  # 验证第二个容器没有候选差异。
        finally:  # 无论结果如何都释放两个独立容器。
            await first.close()  # 销毁第一个任务容器。
            await second.close()  # 销毁第二个任务容器。

    asyncio.run(scenario())  # 执行真实任务间隔离测试。
