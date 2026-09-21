"""内置工具从搜索到回滚的集成测试。"""  # 说明本文件验证 Agent 可见工具契约。

from __future__ import annotations  # 启用延迟解析类型标注。

import asyncio  # 导入同步 pytest 中运行异步工具链的能力。
import os  # 导入 Docker 集成测试显式开关。
import sys  # 导入当前 Python 解释器以运行临时仓库测试。
from pathlib import Path  # 导入 pytest 临时路径类型。

import pytest  # 导入参数化和条件跳过能力。

from patchflow.domain.runtime import Runtime  # 导入两个后端共享的运行时协议。
from patchflow.domain.tools import ToolCall  # 导入结构化工具调用模型。
from patchflow.runtime.docker import DockerRuntime  # 导入真实容器运行时。
from patchflow.runtime.local import LocalRuntime  # 导入工具所依赖的真实本地运行时。
from patchflow.tools import (  # 导入本阶段提供的全部内置工具。
    ApplyPatchTool,  # 导入补丁应用工具。
    GitDiffTool,  # 导入 Git diff 工具。
    ReadCodeTool,  # 导入代码读取工具。
    RunCommandTool,  # 导入一般命令工具。
    RunTestsTool,  # 导入测试执行工具。
    SearchTextTool,  # 导入文本搜索工具。
)  # 结束工具导入列表。
from tests.runtime_helpers import (  # 导入共享仓库和补丁夹具。
    create_temporary_git_repository,  # 导入临时 Git 仓库构造函数。
    valid_patch,  # 导入可应用到测试仓库的标准补丁。
)  # 结束共享测试夹具导入列表。


def _call(call_id: str, tool_name: str, arguments: dict[str, object]) -> ToolCall:  # 简化测试中的工具调用构造。
    return ToolCall.model_validate(  # 使用正式 Pydantic 边界创建调用对象。
        {"call_id": call_id, "tool_name": tool_name, "arguments": arguments}  # 传入调用元数据和参数。
    )  # 返回经过领域模型校验的工具调用。


@pytest.mark.parametrize("backend", ("local", "docker"))  # 同一工具契约在两种 Runtime 上运行。
def test_repository_tools_complete_read_patch_test_diff_and_reset_cycle(tmp_path: Path, backend: str) -> None:  # 验证核心闭环。
    repository = create_temporary_git_repository(tmp_path)  # 创建受控临时 Git 仓库。
    if backend == "docker" and os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1":  # 默认不启动真实容器。
        pytest.skip("需要显式开启 Docker 集成测试")  # 避免普通单测依赖 daemon。
    runtime: Runtime = (  # 使用领域协议持有两种具体后端。
        DockerRuntime(repository.repository)  # Docker 后端只读挂载源仓库。
        if backend == "docker"  # 根据参数选择容器后端。
        else LocalRuntime(repository.repository, repository.isolation_root)  # 否则使用本地隔离目录后端。
    )  # 完成运行时选择。
    python_command = "python" if backend == "docker" else sys.executable  # 使用各执行环境自己的解释器。
    search_tool = SearchTextTool()  # 创建文本搜索工具。
    read_tool = ReadCodeTool()  # 创建代码读取工具。
    patch_tool = ApplyPatchTool()  # 创建补丁应用工具。
    tests_tool = RunTestsTool()  # 创建测试执行工具。
    diff_tool = GitDiffTool()  # 创建差异查看工具。

    async def scenario() -> None:  # 定义完整工具链异步场景。
        await runtime.start(repository.task)  # 启动真实运行时并固定基础提交。
        search = await search_tool.execute(  # 在已跟踪文件中定位目标函数。
            _call("call-search", "search_text", {"query": "def greet"}),  # 构造固定字符串搜索调用。
            runtime,  # 传入唯一允许访问仓库的运行时。
        )  # 完成搜索工具调用。
        assert search.success is True  # 验证搜索成功。
        assert search.data["matches"][0]["path"] == "app.py"  # 验证搜索结果定位到产品代码。
        read = await read_tool.execute(  # 按搜索结果读取目标文件。
            _call("call-read", "read_code", {"path": "app.py", "start_line": 1, "end_line": 2}),  # 指定精确范围。
            runtime,  # 传入真实运行时。
        )  # 完成读取工具调用。
        assert read.success is True  # 验证读取成功。
        assert "     1 | def greet" in read.data["content"]  # 验证返回稳定原始行号。
        patch = await patch_tool.execute(  # 通过 Agent 可见工具应用候选修改。
            _call("call-patch", "apply_patch", {"patch": valid_patch()}),  # 提供标准统一 diff。
            runtime,  # 传入负责路径校验和原子应用的运行时。
        )  # 完成补丁工具调用。
        assert patch.success is True  # 验证补丁工具报告成功。
        assert patch.data["changed_files"] == ["app.py"]  # 验证结构化返回修改文件。
        tests = await tests_tool.execute(  # 在候选工作区执行真实 pytest。
            _call(  # 构造参数数组形式的测试调用。
                "call-tests",  # 设置测试调用 ID。
                "run_tests",  # 指定测试工具名称。
                {"command": [python_command, "-m", "pytest", "-q"], "timeout_seconds": 30.0},  # 设置命令和硬超时。
            ),  # 完成测试调用构造。
            runtime,  # 传入真实运行时。
        )  # 完成测试执行。
        assert tests.success is True  # 验证临时仓库回归测试通过。
        assert "1 passed" in tests.data["stdout"]  # 验证保留 pytest 关键结果。
        diff = await diff_tool.execute(_call("call-diff", "git_diff", {}), runtime)  # 获取最终候选差异。
        assert diff.success is True  # 验证 diff 工具执行成功。
        assert "cleaned_name = name.strip()" in diff.data["diff"]  # 验证 diff 包含目标修复。
        await runtime.reset()  # 使用 Runtime 回到任务基础状态。
        clean_diff = await diff_tool.execute(_call("call-clean-diff", "git_diff", {}), runtime)  # 再次获取差异。
        assert clean_diff.data["diff"] == ""  # 验证回滚后工具观察为空 diff。
        await runtime.close()  # 关闭工具链使用的运行时。

    asyncio.run(scenario())  # 运行完整异步工具链。


def test_tools_return_structured_errors_for_invalid_calls_and_commands(tmp_path: Path) -> None:  # 验证可反思错误。
    repository = create_temporary_git_repository(tmp_path)  # 创建独立临时 Git 仓库。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 创建真实本地运行时。
    read_tool = ReadCodeTool()  # 创建读取工具用于参数和路径错误测试。
    command_tool = RunCommandTool()  # 创建一般命令工具用于非零退出测试。

    async def scenario() -> None:  # 定义结构化错误异步场景。
        await runtime.start(repository.task)  # 启动真实运行时。
        mismatch = await read_tool.execute(  # 故意把错误工具名交给读取工具实例。
            _call("call-mismatch", "search_text", {"path": "app.py"}),  # 构造名称不匹配调用。
            runtime,  # 传入运行时但预期不会访问它。
        )  # 完成名称错误调用。
        assert mismatch.success is False  # 验证调用被拒绝。
        assert mismatch.error_type == "tool_name_mismatch"  # 验证稳定错误分类。
        invalid = await read_tool.execute(  # 提供反向行号和未知参数。
            _call("call-invalid", "read_code", {"path": "app.py", "start_line": 5, "end_line": 2}),  # 构造非法范围。
            runtime,  # 传入运行时但预期不会读取文件。
        )  # 完成参数错误调用。
        assert invalid.success is False  # 验证 Pydantic 拒绝非法参数。
        assert invalid.error_type == "invalid_arguments"  # 验证参数错误可机器识别。
        denied = await read_tool.execute(  # 尝试访问策略明确拒绝的 Git 元数据。
            _call("call-denied", "read_code", {"path": ".git/config"}),  # 构造拒绝路径读取调用。
            runtime,  # 传入执行路径策略的真实运行时。
        )  # 完成拒绝路径调用。
        assert denied.success is False  # 验证读取未发生。
        assert denied.error_type == "PathViolationError"  # 验证安全异常被转换为结构化结果。
        command_failure = await command_tool.execute(  # 执行具有确定退出码的失败命令。
            _call(  # 构造一般命令调用。
                "call-command-failure",  # 设置调用 ID。
                "run_command",  # 指定一般命令工具。
                {"command": [sys.executable, "-c", "import sys; print('failed'); raise SystemExit(9)"]},  # 设置失败脚本。
            ),  # 完成命令调用构造。
            runtime,  # 传入真实运行时。
        )  # 完成失败命令执行。
        assert command_failure.success is False  # 验证非零退出映射为工具失败。
        assert command_failure.error_type == "nonzero_exit"  # 验证提供稳定失败分类。
        assert command_failure.data["return_code"] == 9  # 验证保留真实退出码。
        assert "failed" in command_failure.data["stdout"]  # 验证保留失败前标准输出。
        await runtime.close()  # 关闭运行时。

    asyncio.run(scenario())  # 运行结构化错误场景。
