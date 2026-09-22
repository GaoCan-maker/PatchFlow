"""把第四周基础提交索引暴露为受校验的只读 Agent 工具。"""  # 工具不重新读取宿主仓库，也不改变第三周基线。

from __future__ import annotations  # 延迟解析工具和索引类型。

import json  # 按真实结构化观察的序列化大小执行输出预算。

from pydantic import Field  # 对页码、条数和查询文本施加严格限制。

from patchflow.domain.enums import PermissionLevel  # 标记两个工具均为只读。
from patchflow.domain.runtime import Runtime  # 保持与现有工具协议一致。
from patchflow.domain.tools import ToolSpec  # 向 Agent 公布参数模式和权限。
from patchflow.localization import RepoIndex  # 读取已构建的基础提交仓库地图。
from patchflow.tools.base import (  # 复用统一参数校验与结果包装。
    ToolArguments,  # 提供严格拒绝未知字段的参数基类。
    ToolOutcome,  # 包装具体工具返回的结构化结果。
    ValidatedTool,  # 复用统一参数校验与错误转换。
)  # 完成工具基类导入。


class RepoMapArguments(ToolArguments):  # 定义可分页的仓库地图请求。
    offset: int = Field(default=0, ge=0)  # 从零开始指定第一项文件位置。
    limit: int = Field(default=10, ge=1, le=10)  # 每次最多返回十个文件以控制上下文。


class FindSymbolArguments(ToolArguments):  # 定义基础提交符号查询参数。
    query: str = Field(min_length=1, max_length=200)  # 限制单次符号查询长度。
    limit: int = Field(default=20, ge=1, le=20)  # 最多返回二十个符号位置。


class RepoMapTool(ValidatedTool[RepoMapArguments]):  # 提供分页仓库结构只读视图。
    def __init__(self, index: RepoIndex) -> None:  # 注入由 Runtime 在干净基础提交生成的索引。
        self._index = index  # 保存不可变基础提交地图。
        super().__init__(ToolSpec(name="repo_map", description="分页查看基础提交的 Python 文件、符号和导入关系。", permission=PermissionLevel.READ_ONLY, read_only=True, default_timeout_seconds=10.0, max_output_chars=20_000), RepoMapArguments)  # 公开参数模式与只读权限。

    async def _run(self, arguments: RepoMapArguments, runtime: Runtime) -> ToolOutcome:  # 从预建索引取得页面。
        del runtime  # 索引已经由 Runtime 安全构建，查询不重新触碰工作区。
        selected = self._index.files[arguments.offset : arguments.offset + arguments.limit]  # 只切出受限文件窗口。
        entries = [{"path": item.path, "symbols": [{"name": symbol.name, "kind": symbol.kind, "line": symbol.line} for symbol in item.symbols[:20]], "omitted_symbols": max(0, len(item.symbols) - 20), "imports": list(item.imports[:20]), "parse_error": item.parse_error} for item in selected]  # 限制单文件展开并声明省略量。
        skipped = list(self._index.skipped[:20])  # 最多展示二十个被跳过的路径。
        data = {"files": entries, "offset": arguments.offset, "total_files": len(self._index.files), "has_more": False, "skipped": skipped, "omitted_skipped": len(self._index.skipped) - len(skipped)}  # 预先构造完整观察包络。
        while entries and len(json.dumps(data, ensure_ascii=False)) > self.spec.max_output_chars:  # 将真实结构化 JSON 控制在工具输出预算内。
            entries.pop()  # 从页面尾部逐项缩小，但保留剩余结果的顺序。
        if selected and not entries:  # 单个文件条目本身也可能异常巨大。
            return ToolOutcome(False, summary="单个仓库地图条目超过输出上限。", error_type="output_limit")  # 明确失败，不返回损坏的半条目。
        if len(json.dumps(data, ensure_ascii=False)) > self.spec.max_output_chars:  # 被跳过文件列表也可能单独超过预算。
            return ToolOutcome(False, summary="仓库地图元数据超过输出上限。", error_type="output_limit")  # 避免不受控观察进入模型上下文。
        has_more = arguments.offset + len(entries) < len(self._index.files)  # 根据实际保留条数而非原始页大小计算下一页。
        data["has_more"] = has_more  # 告知 Agent 是否应继续分页。
        return ToolOutcome(True, data, f"返回 {len(entries)} 个基础提交文件。", truncated=has_more or bool(self._index.skipped) or any(item["omitted_symbols"] for item in entries))  # 保留分页和索引缺口信号。


class FindSymbolTool(ValidatedTool[FindSymbolArguments]):  # 提供 AST 符号位置检索。
    def __init__(self, index: RepoIndex) -> None:  # 注入不可变基础提交索引。
        self._index = index  # 保存已通过 Runtime 策略的符号集合。
        super().__init__(ToolSpec(name="find_symbol", description="按名称查找基础提交中的 Python 类、函数或方法。", permission=PermissionLevel.READ_ONLY, read_only=True, default_timeout_seconds=10.0, max_output_chars=20_000), FindSymbolArguments)  # 声明严格的只读符号工具。

    async def _run(self, arguments: FindSymbolArguments, runtime: Runtime) -> ToolOutcome:  # 根据符号名返回位置。
        del runtime  # 查询仅使用已经建立的 AST 索引。
        query = arguments.query.casefold()  # 进行不区分大小写的子串匹配。
        matches = [{"path": item.path, "name": symbol.name, "kind": symbol.kind, "line": symbol.line, "end_line": symbol.end_line} for item in self._index.files for symbol in item.symbols if query in symbol.name.casefold()]  # 保留准确路径与定义行范围。
        matches.sort(key=lambda item: (item["name"].casefold() != query, item["path"], item["line"]))  # 精确命中优先，其余按路径和行号稳定排序。
        limited = matches[: arguments.limit]  # 限制进入 Agent 上下文的符号条数。
        data = {"matches": limited, "query": arguments.query, "total_matches": len(matches)}  # 构造真实工具观察负载。
        while limited and len(json.dumps(data, ensure_ascii=False)) > self.spec.max_output_chars:  # 按序列化后的实际字符数控制输出。
            limited.pop()  # 缩小返回结果而不改变精确命中优先顺序。
        if matches and not limited:  # 单个符号路径或名称也可能超出上限。
            return ToolOutcome(False, summary="单个符号条目超过输出上限。", error_type="output_limit")  # 明确拒绝无法安全表示的观察。
        return ToolOutcome(True, data, f"找到 {len(matches)} 个基础提交符号。", truncated=len(matches) > len(limited))  # 显式标记省略结果。
