"""第四周仓库索引、证据排名、缓存和消融的离线测试。"""  # 使用可信的 pytest 临时仓库而不调用模型。

from __future__ import annotations  # 延迟解析测试辅助类型。

import asyncio  # 在普通 pytest 函数中执行异步 Runtime。
import json  # 检查索引缓存采用标准 JSON。
from dataclasses import replace  # 构造不同的 Issue 和路径策略测试任务。
from pathlib import Path  # 读取临时目录夹具。

import pytest  # 使用异常断言与临时目录。

from patchflow.localization import (  # 导入待测试定位 API。
    IndexSettings,  # 配置索引资源上限。
    LocalizationSettings,  # 配置通道和 Top-K。
    _parse_file,  # 直接验证 AST 解析与语法错误降级边界。
    build_repo_index,  # 建立临时仓库索引。
    localize,  # 运行可解释排名。
)  # 完成待测接口导入。
from patchflow.runtime.local import LocalRuntime  # 只在可信临时仓库中使用本地运行时。
from tests.runtime_helpers import create_temporary_git_repository  # 复用已审计的 Git 测试仓库夹具。


def test_index_builds_ast_map_and_reuses_base_cache(tmp_path: Path) -> None:  # 验证仓库地图与基础提交缓存。
    fixture = create_temporary_git_repository(tmp_path)  # 创建包含 app.py 与 test_app.py 的干净仓库。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 限定本地运行时只访问隔离测试目录。

    async def scenario() -> None:  # 将启动、读取与关闭保持在同一事件循环。
        await runtime.start(fixture.task)  # 验证仓库基础提交。
        try:  # 测试失败时也必须关闭运行时。
            first = await build_repo_index(runtime, fixture.task, cache_dir=tmp_path / "cache")  # 首次构建并写入缓存。
            second = await build_repo_index(runtime, fixture.task, cache_dir=tmp_path / "cache")  # 使用相同配置命中缓存。
            assert first == second  # 验证 JSON 重建结果与首次索引完全一致。
            assert {item.path for item in first.files} == {"app.py", "test_app.py"}  # 只索引已跟踪 Python 文件。
            assert any(symbol.name == "greet" and symbol.line == 1 for item in first.files for symbol in item.symbols)  # 核对 AST 函数位置。
            assert any(item["path"] == "app.py" for item in first.repo_map())  # 仓库地图包含目标源码。
            raw = json.loads(next((tmp_path / "cache").glob("*.json")).read_text(encoding="utf-8"))  # 使用 JSON 解析缓存文件。
            assert raw["adapter"] == "python-ast-v1"  # 验证解析版本包含在缓存中。
        finally:  # 无论断言是否成立都释放运行时。
            await runtime.close()  # 关闭可信本地运行时。

    asyncio.run(scenario())  # 执行完整异步索引场景。


def test_hybrid_localization_explains_file_and_symbol_ranking(tmp_path: Path) -> None:  # 验证多通道证据与 Top-K。
    fixture = create_temporary_git_repository(tmp_path)  # 准备具有 greet 函数和测试导入的仓库。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 仅连接隔离测试仓库。

    async def scenario() -> None:  # 包装异步索引过程。
        await runtime.start(fixture.task)  # 确保工作区干净。
        try:  # 对同一份索引比较完整配置与消融。
            index = await build_repo_index(runtime, fixture.task)  # 从真实 Runtime 建立 AST 索引。
            trace = 'Traceback (most recent call last):\n  File "/workspace/app.py", line 2, in greet\nAssertionError'  # 构造指向真实函数范围的失败栈帧。
            full = localize(index, fixture.task.problem_statement, traceback=trace, failing_tests=("test_app.py::test_greet",))  # 综合问题、源码、栈帧和测试关系。
            assert full.files[0].path == "app.py"  # 目标文件应排在首位。
            assert full.symbols[0].symbol == "greet"  # 目标函数应排在首位。
            assert full.files[0].features["traceback"] == 1.0  # 保存原始栈帧特征。
            assert full.files[0].contributions["traceback"] == 4.0  # 保存加权后的栈帧贡献。
            assert full.files[0].evidence["traceback_frames"] == [("/workspace/app.py", 2)]  # 保留实际命中栈帧。
            assert full.files[0].evidence["related_tests"] == ["test_app.py"]  # 测试导入关联应指向公开测试。
            no_trace = localize(index, fixture.task.problem_statement, traceback=trace, failing_tests=("test_greet",), settings=LocalizationSettings(traceback=False))  # 移除栈帧通道。
            assert no_trace.files[0].contributions["traceback"] == 0.0  # 消融组不得继续获得栈帧分数。
            assert no_trace.files[0].score < full.files[0].score  # 同一候选的总分应随证据移除而下降。
        finally:  # 确保测试资源释放。
            await runtime.close()  # 关闭本地运行时。

    asyncio.run(scenario())  # 执行完整定位场景。


def test_index_rejects_dirty_base_and_explicit_limits(tmp_path: Path) -> None:  # 验证候选污染和资源限制。
    fixture = create_temporary_git_repository(tmp_path)  # 创建干净的可信测试仓库。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 创建受隔离根限制的运行时。

    async def scenario() -> None:  # 在同一运行时中检查所有拒绝条件。
        await runtime.start(fixture.task)  # 启动并验证基础提交。
        try:  # 异常后仍关闭运行时。
            with pytest.raises(ValueError, match="超过索引上限"):  # 两个 Python 文件超过单文件上限。
                await build_repo_index(runtime, fixture.task, settings=IndexSettings(max_files=1))  # 请求不允许静默截断的索引。
            (fixture.repository / "app.py").write_text("def changed():\n    pass\n", encoding="utf-8")  # 在可信临时仓库中制造候选修改。
            with pytest.raises(ValueError, match="干净仓库"):  # 脏工作区不得使用基础提交缓存。
                await build_repo_index(runtime, fixture.task)  # 尝试在补丁后重新建基础索引。
        finally:  # 即使拒绝成功也释放资源。
            await runtime.close()  # 关闭运行时但不重置测试仓库。

    asyncio.run(scenario())  # 执行安全边界测试。


def test_localization_topk_and_empty_evidence_are_deterministic(tmp_path: Path) -> None:  # 检查无证据时的稳定排序。
    fixture = create_temporary_git_repository(tmp_path)  # 创建固定两个 Python 文件的仓库。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 使用隔离运行时读取源码。

    async def scenario() -> None:  # 组合异步索引和同步排名。
        await runtime.start(fixture.task)  # 启动基础工作区。
        try:  # 确保执行结束时释放资源。
            index = await build_repo_index(runtime, fixture.task)  # 获取可复现的路径和符号集合。
            result = localize(index, "完全无关的描述", settings=LocalizationSettings(top_k_files=1, top_k_symbols=1))  # 禁用实际匹配且限制每级输出一个。
            assert len(result.files) == 1 and len(result.symbols) == 1  # 两个 Top-K 上限都应生效。
            assert result.files[0].path == "app.py"  # 零分同分时按路径排序。
            assert result.files[0].score == 0.0  # 无证据候选不得伪造信心。
            with pytest.raises(ValueError, match="Top-K"):  # 拒绝不合法候选容量。
                localize(index, "问题描述", settings=replace(LocalizationSettings(), top_k_files=0))  # 将文件上限设为零。
        finally:  # 测试正常或异常时都关闭运行时。
            await runtime.close()  # 释放隔离资源。

    asyncio.run(scenario())  # 执行稳定性测试。


def test_ast_methods_calls_and_syntax_error_fallback() -> None:  # 验证 AST 不执行代码并正确标记无法解析的文件。
    source = "class Greeter:\n    def greet(self):\n        return helper()\n"  # 准备带方法与调用的简短源码。
    parsed = _parse_file("package/greeter.py", source)  # 只解析内存文本而不导入模块。
    assert [(symbol.name, symbol.kind, symbol.line, symbol.end_line) for symbol in parsed.symbols] == [("Greeter", "class", 1, 3), ("Greeter.greet", "method", 2, 3)]  # 验证限定名和行范围。
    assert parsed.calls == ("helper",)  # 验证静态调用名称被记录。
    broken = _parse_file("broken.py", "def invalid(:\n")  # 解析一个确实有语法错误的文件。
    assert broken.parse_error is True and broken.symbols == ()  # 错误文件保留文本而不伪造符号。
    assert broken.lines == ("def invalid(:",)  # 文件级文本仍可以参与搜索。


def test_git_listing_truncation_is_not_a_partial_index(tmp_path: Path) -> None:  # 验证超小输出预算不会生成误导性半索引。
    fixture = create_temporary_git_repository(tmp_path)  # 创建两个已跟踪 Python 文件。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root, max_output_chars=3)  # 故意把普通命令观察预算压到三个字符。

    async def scenario() -> None:  # 包装运行时生命周期。
        await runtime.start(fixture.task)  # 控制面仍使用独立预算，因此可以正常启动。
        try:  # 测试失败时也关闭运行时。
            with pytest.raises(ValueError, match="枚举失败或输出被截断"):  # 截断的文件列表不能被当成完整索引。
                await build_repo_index(runtime, fixture.task)  # 尝试在受限观察预算下枚举文件。
        finally:  # 无论异常是否符合预期都释放运行时。
            await runtime.close()  # 结束本地运行时。

    asyncio.run(scenario())  # 执行截断安全测试。
