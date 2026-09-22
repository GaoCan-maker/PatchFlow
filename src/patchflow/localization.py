"""第四周的离线仓库地图、Python AST 索引和可解释混合定位。"""  # 本模块不调用模型，也不执行待修复代码。

from __future__ import annotations  # 延迟解析类型标注以支持下方类之间的引用。

import ast  # 使用标准库 AST 获取真实的 Python 定义和导入关系。
import hashlib  # 为基础提交索引计算稳定的缓存键。
import json  # 用非可执行的 JSON 保存索引缓存。
import os  # 使用原子替换提交完整缓存文件。
import re  # 从 Issue 和 traceback 提取词项与路径。
import uuid  # 为并发写入生成互不冲突的临时文件名。
from dataclasses import asdict, dataclass  # 保存不可变索引与定位证据。
from pathlib import Path, PurePosixPath  # 管理缓存路径和仓库相对路径。

from patchflow.domain.runtime import Runtime  # 所有仓库读取均经过现有隔离协议。
from patchflow.domain.task import TaskSpec  # 用基础提交和仓库位置标识索引。
from patchflow.runtime.errors import PatchFlowRuntimeError  # 区分不允许读取的仓库文件。

_ADAPTER_VERSION = "python-ast-v1"  # 修改 AST 解析语义时必须更新缓存版本。
_WORDS = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[\u4e00-\u9fff]{2,}")  # 识别英文代码词与中文短语。
_TRACE = re.compile(r'File ["\']([^"\']+\.py)["\'], line (\d+)')  # 只提取 Python traceback 的文件与行号。


@dataclass(frozen=True, slots=True)  # 配置不可变以保证缓存键和运行行为一致。
class IndexSettings:  # 限制索引工作的时间与内存开销。
    max_files: int = 400  # 限制需要读取的 Python 文件数量。
    max_lines_per_file: int = 600  # 跳过超过该行数的大型文件。
    max_chars_per_file: int = 18_000  # 留出 Docker Runtime 输出预算余量。
    command_timeout_seconds: float = 20.0  # 限制 Git 枚举和状态检查的耗时。


@dataclass(frozen=True, slots=True)  # 保存一个可追溯到源码位置的定义。
class IndexedSymbol:  # 描述函数、类或方法的静态定义。
    name: str  # 保存完整限定名，例如 Greeter.greet。
    kind: str  # 区分 class、function 与 method。
    line: int  # 保存一开始计数的定义起始行。
    end_line: int  # 保存定义结束行，供 traceback 映射。


@dataclass(frozen=True, slots=True)  # 保存一个已跟踪 Python 文件的最小可搜索视图。
class IndexedFile:  # 将路径、AST 与局部搜索所需内容关联。
    path: str  # 保存正斜杠工作区相对路径。
    lines: tuple[str, ...]  # 保存受大小限制的基础提交文本行。
    symbols: tuple[IndexedSymbol, ...]  # 保存 AST 得到的全部定义。
    imports: tuple[str, ...]  # 保存静态 import 的模块名。
    calls: tuple[str, ...]  # 保存静态调用的末级名称，不能当作完整调用图。
    parse_error: bool = False  # 标记语法无法解析但文本仍可搜索。


@dataclass(frozen=True, slots=True)  # 一份索引只归属于一个基础提交。
class RepoIndex:  # 保存可供仓库地图和定位器共同使用的索引。
    cache_key: str  # 包含仓库标识、基础提交、适配器版本及配置。
    files: tuple[IndexedFile, ...]  # 保存按路径排序的已索引文件。
    skipped: tuple[str, ...]  # 保存超限或访问受限的文件以解释召回缺口。

    def repo_map(self) -> tuple[dict[str, object], ...]:  # 输出不包含整份源码的仓库地图。
        return tuple({"path": item.path, "symbols": [{"name": symbol.name, "kind": symbol.kind, "line": symbol.line} for symbol in item.symbols], "imports": list(item.imports), "parse_error": item.parse_error} for item in self.files)  # 返回可序列化的路径与符号概要。


def _symbols_from_tree(tree: ast.AST) -> tuple[IndexedSymbol, ...]:  # 从 AST 获取具有准确行范围的定义。
    collected: list[IndexedSymbol] = []  # 按源码遍历顺序累积符号。

    def visit(body: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:  # 递归遍历类和函数体以构造限定名。
        for node in body:  # 只在语句层识别定义，避免把引用当作定义。
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):  # 识别类、同步函数和异步函数。
                name = ".".join((*parents, node.name))  # 用包含关系构造稳定限定名。
                kind = "class" if isinstance(node, ast.ClassDef) else "method" if parents else "function"  # 区分顶层函数与嵌套成员。
                collected.append(IndexedSymbol(name, kind, node.lineno, node.end_lineno or node.lineno))  # 保存实际源码行范围。
                visit(node.body, (*parents, node.name))  # 继续索引嵌套定义。

    visit(getattr(tree, "body", []))  # 从模块体开始扫描定义。
    return tuple(collected)  # 返回不可变符号序列。


def _parse_file(path: str, content: str) -> IndexedFile:  # 构造可用于搜索和关联的单文件视图。
    lines = tuple(content.splitlines())  # 保留源码的行序用于位置证据。
    try:  # 语法错误文件仍保留文件级文本搜索能力。
        tree = ast.parse(content, filename=path)  # 不执行仓库代码，只解析语法树。
    except SyntaxError:  # 文件不兼容当前解释器语法时采用降级索引。
        return IndexedFile(path, lines, (), (), (), True)  # 明确标记符号信息缺失。
    imports: set[str] = set()  # 去重仓库内测试关联需要的模块名。
    calls: set[str] = set()  # 去重静态调用名以构造弱符号证据。
    for node in ast.walk(tree):  # 扫描整棵树中的导入与调用节点。
        if isinstance(node, ast.Import):  # 处理 import pkg.module 形式。
            imports.update(alias.name for alias in node.names)  # 保留完整被导入模块名。
        elif isinstance(node, ast.ImportFrom) and node.module is not None:  # 处理 from pkg.module import x 形式。
            imports.add(node.module)  # 保存导入来源模块。
            imports.update(alias.name for alias in node.names)  # 同时保存导入符号供关系匹配。
        elif isinstance(node, ast.Call):  # 识别潜在调用点但不声称解析运行时绑定。
            function = node.func  # 读取被调用表达式。
            if isinstance(function, ast.Name):  # 处理 greet(...) 调用。
                calls.add(function.id)  # 保存简单函数名。
            elif isinstance(function, ast.Attribute):  # 处理 object.greet(...) 调用。
                calls.add(function.attr)  # 保存末级属性名。
    return IndexedFile(path, lines, _symbols_from_tree(tree), tuple(sorted(imports)), tuple(sorted(calls)))  # 返回可复现的静态索引。


async def build_repo_index(runtime: Runtime, task: TaskSpec, *, settings: IndexSettings | None = None, cache_dir: Path | None = None) -> RepoIndex:  # 在已启动且干净的 Runtime 中建立索引。
    settings = settings or IndexSettings()  # 每次调用独立选择不可变默认配置。
    if settings.max_files < 1 or settings.max_lines_per_file < 1 or settings.max_chars_per_file < 1 or settings.command_timeout_seconds <= 0:  # 拒绝无效资源限制。
        raise ValueError("索引限制必须全部大于零")  # 防止空索引被误认为有效结果。
    identity = {"repository": task.repo_spec.location, "base_commit": task.base_commit, "adapter": _ADAPTER_VERSION, "settings": asdict(settings)}  # 明确缓存键的四个组成部分。
    cache_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()  # 生成稳定且不暴露路径的文件名。
    status = await runtime.execute(("git", "status", "--porcelain", "-z"), timeout_seconds=settings.command_timeout_seconds)  # 检查基础索引不会读取被候选修改的工作区。
    if not status.succeeded or status.output_truncated or status.stdout:  # 脏工作区或不完整状态均不能使用基础提交缓存。
        raise ValueError("建立基础索引要求干净仓库和完整 Git 状态")  # 防止候选间缓存污染。
    cache_path = cache_dir / f"{cache_key}.json" if cache_dir is not None else None  # 仅在调用方显式给出目录时保存源码缓存。
    if cache_path is not None and cache_path.is_file():  # 尝试读取已有基础提交索引。
        try:  # 损坏或旧格式缓存不应阻止重新构建。
            raw = json.loads(cache_path.read_text(encoding="utf-8"))  # 使用 JSON 而非执行性 pickle。
            if raw.get("cache_key") == cache_key and raw.get("adapter") == _ADAPTER_VERSION:  # 核对身份与解析版本。
                files = tuple(IndexedFile(item["path"], tuple(item["lines"]), tuple(IndexedSymbol(**symbol) for symbol in item["symbols"]), tuple(item["imports"]), tuple(item["calls"]), item["parse_error"]) for item in raw["files"])  # 从受控字段重建不可变对象。
                return RepoIndex(cache_key, files, tuple(raw["skipped"]))  # 命中缓存时不重新读取代码。
        except (OSError, ValueError, KeyError, TypeError):  # 缓存无效时忽略并重新索引。
            pass  # 下一步按真实仓库重建完整索引。
    listing = await runtime.execute(("git", "ls-files", "-z", "--", "*.py"), timeout_seconds=settings.command_timeout_seconds)  # 枚举已跟踪 Python 文件而非扫描宿主目录。
    if not listing.succeeded or listing.output_truncated:  # 截断结果会产生静默召回偏差。
        raise ValueError("Git 文件枚举失败或输出被截断")  # 要求调用方提高 Runtime 输出上限或缩小仓库。
    paths = sorted(path for path in listing.stdout.split("\0") if path)  # 按路径排序以保证结果可复现。
    if len(paths) > settings.max_files:  # 大仓库需要显式提高预算或做分片索引。
        raise ValueError(f"Python 文件数 {len(paths)} 超过索引上限 {settings.max_files}")  # 不偷偷只取前 N 个文件。
    indexed: list[IndexedFile] = []  # 收集通过路径策略和大小限制的文件。
    skipped: list[str] = []  # 记录跳过文件供评测解释召回失败。
    for path in paths:  # 逐个从 Runtime 读取仓库内容。
        try:  # 路径策略、符号链接或 Docker 输出限制可能拒绝文件。
            content = await runtime.read_file(path, end_line=settings.max_lines_per_file + 1)  # 多读一行以检测超过行数限制。
        except (PatchFlowRuntimeError, OSError, UnicodeError, ValueError):  # 只跳过可预期的文件读取失败。
            skipped.append(path)  # 不将不可读文件伪装成空文件。
            continue  # 继续索引其他可读文件。
        if len(content) > settings.max_chars_per_file or len(content.splitlines()) > settings.max_lines_per_file:  # 检查文本大小和行数两道限制。
            skipped.append(path)  # 在仓库地图中声明未覆盖的文件。
            continue  # 不对不完整源码运行 AST。
        indexed.append(_parse_file(path, content))  # 将完整、可读源码转成静态索引。
    index = RepoIndex(cache_key, tuple(indexed), tuple(skipped))  # 固定本次索引结果。
    if cache_path is not None:  # 仅在用户显式指定缓存目录时落盘源码。
        cache_path.parent.mkdir(parents=True, exist_ok=True)  # 创建缓存父目录。
        temporary = cache_path.with_name(f"{cache_path.name}.{uuid.uuid4().hex}.tmp")  # 生成同目录原子提交临时文件。
        payload = {"cache_key": cache_key, "adapter": _ADAPTER_VERSION, "files": [asdict(item) for item in index.files], "skipped": list(index.skipped)}  # 保存完整且可验证的 JSON 结构。
        try:  # 即使写盘失败也应清理未提交临时文件。
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")  # 写入完成前不替换正式缓存。
            os.replace(temporary, cache_path)  # 以原子替换发布完整缓存。
        finally:  # 清理因写入或替换异常留下的临时文件。
            temporary.unlink(missing_ok=True)  # 删除仅由本次调用创建的临时文件。
    return index  # 返回与磁盘缓存相同的基础提交索引。


@dataclass(frozen=True, slots=True)  # 配置每个证据通道是否参加排名。
class LocalizationSettings:  # 允许离线定位消融而不改动定位器代码。
    issue: bool = True  # 启用问题描述词项匹配。
    search: bool = True  # 启用源码文本命中。
    traceback: bool = True  # 启用运行栈帧位置。
    test_relation: bool = True  # 启用测试导入到源文件的关联。
    symbol: bool = True  # 启用符号名与调用关系。
    top_k_files: int = 5  # 输出前五个文件候选。
    top_k_symbols: int = 10  # 输出前十个函数或方法候选。


@dataclass(frozen=True, slots=True)  # 保存一个排序结果的完整可解释分数。
class RankedCandidate:  # 文件和符号均使用相同证据格式。
    path: str  # 保存候选仓库相对路径。
    symbol: str | None  # 文件级候选为空，符号级候选填写限定名。
    line: int | None  # 符号级候选填写定义起始行。
    score: float  # 保存所有已启用特征的贡献之和。
    features: dict[str, float]  # 保存未经权重放大的原始特征值。
    contributions: dict[str, float]  # 保存每个通道对最终分数的贡献。
    evidence: dict[str, object]  # 保存命中词、栈帧或测试关系的可审计信息。


@dataclass(frozen=True, slots=True)  # 同时返回两个粒度的 Top-K 结果。
class LocalizationResult:  # 供 CLI、工具与评测复用的定位输出。
    files: tuple[RankedCandidate, ...]  # 保存文件级排序。
    symbols: tuple[RankedCandidate, ...]  # 保存符号级排序。


def _tokens(text: str) -> set[str]:  # 规范化自然语言、路径和蛇形命名词项。
    return {part.lower() for token in _WORDS.findall(text) for part in (token, *token.split("_")) if len(part) > 2}  # 同时保留完整标识符和可检索的下划线片段。


def localize(index: RepoIndex, issue: str, *, traceback: str = "", failing_tests: tuple[str, ...] = (), settings: LocalizationSettings | None = None) -> LocalizationResult:  # 融合静态与动态定位证据。
    settings = settings or LocalizationSettings()  # 为本次排名选择不可变默认配置。
    if settings.top_k_files < 1 or settings.top_k_symbols < 1:  # 拒绝没有输出容量的配置。
        raise ValueError("Top-K 必须大于零")  # 保证评测指标有定义。
    words = _tokens(issue)  # 从 Issue 提取查询词。
    failure_words = _tokens(" ".join(failing_tests))  # 从失败测试名称提取词项。
    frames = [(path.replace("\\", "/"), int(line)) for path, line in _TRACE.findall(traceback)]  # 解析所有 traceback 栈帧。
    weights = {"issue": 2.0, "search": 1.0, "traceback": 4.0, "test_relation": 2.0, "symbol": 2.0}  # 明确各通道固定首版权重。
    enabled = {name: getattr(settings, name) for name in weights}  # 保留每个消融开关的实际状态。
    files: list[RankedCandidate] = []  # 累积文件级候选。
    symbols: list[RankedCandidate] = []  # 累积函数与方法级候选。
    modules = {item.path: item.path.removesuffix(".py").replace("/", ".") for item in index.files}  # 将仓库路径映射到可能的 Python 模块名。
    test_files = [item for item in index.files if PurePosixPath(item.path).name.startswith("test_") or "/tests/" in f"/{item.path}"]  # 标识公开测试文件。
    for item in index.files:  # 逐个计算文件级证据。
        path_words = _tokens(item.path.replace("/", " ").replace("_", " "))  # 提取路径中的问题词。
        name_words = set().union(*(_tokens(symbol.name.replace("_", " ")) for symbol in item.symbols)) if item.symbols else set()  # 提取本文件符号名称。
        issue_hits = sorted(words & (path_words | name_words))  # 保存可审计的命名命中词。
        source_hits = [(number, sorted(words & _tokens(line.replace("_", " ")))) for number, line in enumerate(item.lines, 1) if words & _tokens(line.replace("_", " "))]  # 保存 Issue 查询在源码中的命中行。
        matched_frames = [(path, line) for path, line in frames if path == item.path or path.endswith("/" + item.path)]  # 用完整相对路径匹配 traceback。
        related_tests = [test.path for test in test_files if failing_tests and test.path != item.path and (modules[item.path] in test.imports or PurePosixPath(item.path).stem in test.imports) and any(name.rsplit("::", 1)[-1] in {symbol.name for symbol in test.symbols} or _tokens(name) & (_tokens(test.path) | set(test.calls)) for name in failing_tests)]  # 用真实失败测试函数定义或调用名验证测试关系。
        call_hits = sorted(words & set(item.calls))  # 保存与 Issue 同名的静态调用。
        raw = {"issue": len(issue_hits) / max(1, len(words)), "search": min(1.0, len(source_hits) / 3), "traceback": 1.0 if matched_frames else 0.0, "test_relation": 1.0 if related_tests else 0.0, "symbol": min(1.0, len(call_hits) / max(1, len(words)))}  # 将每个特征归一到零到一。
        if failure_words and _tokens(item.path.replace("_", " ")) & failure_words:  # 失败测试名称直接包含文件词时补充测试关系。
            raw["test_relation"] = max(raw["test_relation"], 0.5)  # 弱证据不等同于真实导入关系。
        contributions = {name: round(value * weights[name], 4) if enabled[name] else 0.0 for name, value in raw.items()}  # 显式保留消融后每项贡献。
        test_prior = -1.0 if item in test_files and item.path not in issue else 0.0  # 普通修复任务降低测试文件先验，但 Issue 明确点名时保留。
        raw["test_file_prior"] = 1.0 if test_prior else 0.0  # 把负向先验也作为原始特征保存。
        contributions["test_file_prior"] = test_prior  # 确保最终分数等于贡献之和。
        evidence = {"issue_terms": issue_hits, "search_lines": [line for line, _ in source_hits[:10]], "traceback_frames": matched_frames, "related_tests": related_tests, "call_terms": call_hits}  # 保存原始位置和命中依据。
        score = round(sum(contributions.values()), 4)  # 计算文件总分。
        files.append(RankedCandidate(item.path, None, None, score, raw, contributions, evidence))  # 保留零分候选以便召回评测。
        for symbol in item.symbols:  # 在文件先验之上计算每个符号的直接证据。
            symbol_words = _tokens(symbol.name.replace("_", " "))  # 分词限定符号名。
            direct_hits = sorted(words & symbol_words)  # 保存问题描述直接提到的符号词。
            frame_hits = [(path, line) for path, line in matched_frames if symbol.line <= line <= symbol.end_line]  # 定位落入函数体的栈帧。
            symbol_source_hits = [line for line, _ in source_hits if symbol.line <= line <= symbol.end_line]  # 限制源码搜索证据到当前符号范围。
            symbol_raw = {"issue": len(direct_hits) / max(1, len(words)), "search": min(1.0, len(symbol_source_hits) / 3), "traceback": 1.0 if frame_hits else 0.0, "test_relation": raw["test_relation"], "symbol": 1.0 if direct_hits else 0.0}  # 计算符号自己的直接证据。
            symbol_contributions = {name: round(value * weights[name], 4) if enabled[name] else 0.0 for name, value in symbol_raw.items()}  # 保存符号级各通道贡献。
            symbol_score = round(sum(symbol_contributions.values()) + score * 0.25, 4)  # 文件分数只作为较弱的符号先验。
            symbol_evidence = {"issue_terms": direct_hits, "search_lines": symbol_source_hits[:10], "traceback_frames": frame_hits, "related_tests": related_tests}  # 保留符号的精确证据。
            symbols.append(RankedCandidate(item.path, symbol.name, symbol.line, symbol_score, symbol_raw, symbol_contributions, symbol_evidence))  # 加入符号候选。
    files.sort(key=lambda candidate: (-candidate.score, candidate.path))  # 分数相同时用路径保持稳定顺序。
    symbols.sort(key=lambda candidate: (-candidate.score, candidate.path, candidate.line or 0))  # 符号级也保持完全可复现。
    return LocalizationResult(tuple(files[:settings.top_k_files]), tuple(symbols[:settings.top_k_symbols]))  # 返回配置指定的两个粒度 Top-K。
