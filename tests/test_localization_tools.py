"""第四周 repo_map 与 find_symbol 工具的严格契约测试。"""  # 只使用可信临时仓库与 LocalRuntime。

from __future__ import annotations  # 延迟解析测试路径类型。

import asyncio  # 驱动异步 Runtime 和工具调用。
from pathlib import Path  # 接收 pytest 的隔离目录。

from patchflow.domain.tools import ToolCall  # 构造正式的结构化工具调用。
from patchflow.localization import (  # 在测试中构造正常与异常大小的仓库地图。
    IndexedFile,  # 保存单文件 AST 视图。
    IndexedSymbol,  # 构造超长符号名边界。
    RepoIndex,  # 组织预构建的基础提交索引。
    build_repo_index,  # 在干净基础提交构建真实仓库地图。
)  # 完成索引类型导入。
from patchflow.runtime.local import LocalRuntime  # 使用现有可信隔离目录后端。
from patchflow.tools.localization import FindSymbolTool, RepoMapTool  # 导入两个新增只读工具。
from tests.runtime_helpers import create_temporary_git_repository  # 生成已跟踪源码与测试文件。


def test_repository_map_and_symbol_tools_are_paginated_and_validated(tmp_path: Path) -> None:  # 验证工具分页、查询与参数拒绝。
    fixture = create_temporary_git_repository(tmp_path)  # 创建包含 greet 函数的 Git 基础提交。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 只允许访问测试隔离根。

    async def scenario() -> None:  # 在同一生命周期内建立索引并运行工具。
        await runtime.start(fixture.task)  # 验证基础提交干净。
        try:  # 任一断言失败仍释放运行时。
            index = await build_repo_index(runtime, fixture.task)  # 获取真正经过 Runtime 路径策略的索引。
            map_tool = RepoMapTool(index)  # 构造分页仓库地图工具。
            symbol_tool = FindSymbolTool(index)  # 构造 AST 符号查询工具。
            page = await map_tool.execute(ToolCall(call_id="map-1", tool_name="repo_map", arguments={"limit": 1}), runtime)  # 请求第一页一个文件。
            assert page.success and page.data["files"][0]["path"] == "app.py"  # 验证结构化路径结果。
            assert page.data["has_more"] is True and page.truncated is True  # 未返回完整地图时必须告知分页。
            second = await map_tool.execute(ToolCall(call_id="map-2", tool_name="repo_map", arguments={"offset": 1, "limit": 1}), runtime)  # 请求第二页。
            assert second.data["files"][0]["path"] == "test_app.py"  # 验证稳定路径顺序。
            found = await symbol_tool.execute(ToolCall(call_id="symbol-1", tool_name="find_symbol", arguments={"query": "greet"}), runtime)  # 查询函数名。
            assert found.success and found.data["matches"][0]["path"] == "app.py"  # 精确符号优先返回源文件。
            invalid = await symbol_tool.execute(ToolCall(call_id="symbol-2", tool_name="find_symbol", arguments={"query": "greet", "limit": 100}), runtime)  # 尝试超出工具输出上限。
            assert invalid.success is False and invalid.error_type == "invalid_arguments"  # Pydantic 必须在工具主体前拒绝参数。
        finally:  # 保证测试资源清理。
            await runtime.close()  # 关闭本地运行时。

    asyncio.run(scenario())  # 执行完整工具边界测试。


def test_localization_tools_reject_single_oversize_entry(tmp_path: Path) -> None:  # 验证单条结果超限也不会泄入观察。
    fixture = create_temporary_git_repository(tmp_path)  # 构造可传入工具协议的可信运行时仓库。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 工具只查询内存索引，不在此测试中启动运行时。
    huge = "huge" + "x" * 25_000  # 制造超过工具二万字符预算的单个定义名。
    symbol = IndexedSymbol(huge, "function", 1, 1)  # 构造 AST 定义位置。
    index = RepoIndex("test-key", (IndexedFile("app.py", (), (symbol,), (), ()),), ())  # 只含一个异常符号的人工索引。

    async def scenario() -> None:  # 检查两个工具的共同输出预算边界。
        mapped = await RepoMapTool(index).execute(ToolCall(call_id="large-map", tool_name="repo_map", arguments={}), runtime)  # 请求异常大仓库地图条目。
        found = await FindSymbolTool(index).execute(ToolCall(call_id="large-symbol", tool_name="find_symbol", arguments={"query": "huge"}), runtime)  # 请求异常大符号条目。
        assert mapped.success is False and mapped.error_type == "output_limit"  # 地图工具拒绝单条超限观察。
        assert found.success is False and found.error_type == "output_limit"  # 符号工具同样拒绝超限观察。

    asyncio.run(scenario())  # 执行异步工具结果检查。
