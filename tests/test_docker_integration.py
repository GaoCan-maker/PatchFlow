"""需要显式开启的真实 DockerRuntime 集成验收。"""  # 说明默认单测不依赖 Docker daemon。

from __future__ import annotations  # 启用延迟类型标注。

import asyncio  # 在同步 pytest 中运行异步 Runtime。
import os  # 读取显式 Docker 测试开关。
from pathlib import Path  # 接收 pytest 临时目录。

import pytest  # 使用跳过和临时目录夹具。

from patchflow.runtime.docker import DockerRuntime  # 导入真实 Docker 后端。
from patchflow.runtime.errors import PathViolationError  # 验证容器内符号链接策略拒绝。
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
            tests = await runtime.execute(("python", "-m", "pytest", "-q"), timeout_seconds=60.0)  # 执行容器内真实测试。
            assert tests.succeeded and "1 passed" in tests.stdout  # 验证回归测试通过。
            assert "cleaned_name = name.strip()" in await runtime.get_diff()  # 验证导出正确候选 diff。
            await runtime.reset()  # 在容器副本中回到基础提交。
            assert await runtime.get_diff() == ""  # 验证回滚后没有已跟踪修改。
            assert "cleaned_name" not in (repository.repository / "app.py").read_text(encoding="utf-8")  # 验证宿主源文件未变。
        finally:  # 在所有退出路径执行清理。
            await runtime.close()  # 销毁任务容器和私有副本。

    asyncio.run(scenario())  # 运行完整真实 Docker 集成场景。
