"""代码搜索、读取、补丁和 diff 工具。"""  # 说明本文件提供仓库级基础 ACI。

from __future__ import annotations  # 启用延迟解析类型标注。

from typing import Self  # 导入 Python 3.11 提供的 Self 类型。

from pydantic import Field, model_validator  # 导入字段约束和跨字段校验器。

from patchflow.domain.enums import PermissionLevel  # 导入工具权限等级。
from patchflow.domain.runtime import Runtime  # 导入运行时协议。
from patchflow.domain.tools import ToolSpec  # 导入工具声明模型。
from patchflow.runtime.output import truncate_text  # 导入统一文本截断逻辑。
from patchflow.tools.base import ToolArguments, ToolOutcome, ValidatedTool  # 导入工具基础设施。


class SearchTextArguments(ToolArguments):  # 定义仓库文本搜索参数。
    """`git grep` 搜索所需的受控参数。"""  # 说明搜索范围固定为已跟踪仓库文件。

    query: str = Field(min_length=1, max_length=500)  # 限制搜索表达式长度。
    fixed_strings: bool = True  # 默认把查询作为普通字符串而非正则。
    max_results: int = Field(default=50, ge=1, le=200)  # 限制反馈给 Agent 的匹配条数。


class ReadCodeArguments(ToolArguments):  # 定义代码片段读取参数。
    """按工作区相对路径和一开始计数行号读取代码。"""  # 说明读取参数语义。

    path: str = Field(min_length=1, max_length=1_000)  # 接收受 Runtime 检查的相对路径。
    start_line: int = Field(default=1, ge=1)  # 设置包含式起始行。
    end_line: int | None = Field(default=None, ge=1)  # 设置可选包含式结束行。
    max_chars: int = Field(default=12_000, ge=200, le=50_000)  # 限制单次读取进入上下文的字符数。

    @model_validator(mode="after")  # 在单字段校验后检查行范围关系。
    def validate_line_range(self) -> Self:  # 定义结束行不早于起始行的规则。
        if self.end_line is not None and self.end_line < self.start_line:  # 检查反向范围。
            raise ValueError("end_line 不能小于 start_line")  # 拒绝无法解释的行范围。
        return self  # 返回通过跨字段检查的不可变参数对象。


class ApplyPatchArguments(ToolArguments):  # 定义统一 diff 应用参数。
    """只接受非空统一 diff 文本。"""  # 说明补丁格式由 Runtime 和 Git 继续验证。

    patch: str = Field(min_length=1, max_length=500_000)  # 限制单次补丁大小以控制资源消耗。


class GitDiffArguments(ToolArguments):  # 定义无业务参数的 diff 请求。
    """保留空模型以拒绝任何未知参数。"""  # 说明空模型仍提供严格校验价值。


class SearchTextTool(ValidatedTool[SearchTextArguments]):  # 定义仓库已跟踪文本搜索工具。
    """使用 `git grep` 搜索仓库并返回结构化位置。"""  # 说明搜索后端和输出。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="search_text",  # 设置模型调用名称。
                description="在 Git 已跟踪文件中搜索文本或正则表达式。",  # 描述工具用途。
                permission=PermissionLevel.READ_ONLY,  # 声明只读权限。
                read_only=True,  # 标记不会修改工作区。
                default_timeout_seconds=20.0,  # 设置默认搜索超时。
                max_output_chars=20_000,  # 设置最大观察文本长度。
            ),  # 完成工具声明。
            SearchTextArguments,  # 绑定严格搜索参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: SearchTextArguments, runtime: Runtime) -> ToolOutcome:  # 执行搜索语义。
        command_parts = ["git", "grep", "-n", "--full-name", "-I"]  # 构造不经过 Shell 的安全命令参数。
        if arguments.fixed_strings:  # 根据参数选择普通字符串或正则搜索。
            command_parts.append("-F")  # 使用 Git 的固定字符串匹配模式。
        command_parts.extend(("-e", arguments.query, "--"))  # 使用 -e 防止查询被解释为命令选项。
        result = await runtime.execute(tuple(command_parts), timeout_seconds=self.spec.default_timeout_seconds)  # 执行搜索。
        if result.timed_out:  # 单独识别超时，避免误报为普通无匹配。
            return ToolOutcome(  # 构造搜索超时结果。
                success=False,  # 标记搜索未完成。
                data={"stdout": result.stdout, "stderr": result.stderr},  # 保留超时前部分输出。
                summary="文本搜索超时。",  # 提供 Agent 可读摘要。
                error_type="command_timeout",  # 提供稳定错误类型。
                error_message=result.stderr or "git grep 超过时间限制",  # 提供详细诊断。
                truncated=result.output_truncated,  # 传播底层输出截断状态。
            )  # 完成超时结果。
        if result.return_code == 1:  # Git grep 使用退出码一表示正常但没有匹配。
            return ToolOutcome(success=True, data={"matches": []}, summary="未找到匹配结果。")  # 返回成功空结果。
        if not result.succeeded:  # 处理 Git 不可用或仓库错误等真正失败。
            return ToolOutcome(  # 构造搜索执行失败结果。
                success=False,  # 标记工具失败。
                data={"stdout": result.stdout, "stderr": result.stderr},  # 保存底层命令观察。
                summary="文本搜索命令执行失败。",  # 提供 Agent 可读摘要。
                error_type=result.termination_reason or "command_failed",  # 保留进程启动错误等分类。
                error_message=result.stderr,  # 保存 Git 错误输出。
                truncated=result.output_truncated,  # 传播底层截断状态。
            )  # 完成搜索失败结果。
        matches: list[dict[str, object]] = []  # 创建结构化匹配结果列表。
        for line in result.stdout.splitlines():  # 逐行解析 `path:line:text` 格式。
            if len(matches) >= arguments.max_results:  # 达到调用方结果数量限制时停止解析。
                break  # 避免向 Agent 返回过多重复上下文。
            parts = line.split(":", 2)  # 最多拆分两次以保留代码文本中的冒号。
            if len(parts) != 3 or not parts[1].isdigit():  # 检查结果是否符合预期机器格式。
                continue  # 忽略无法可靠定位的异常输出行。
            matches.append(  # 添加一个结构化代码位置。
                {"path": parts[0], "line": int(parts[1]), "text": parts[2]}  # 保存路径、行号和匹配文本。
            )  # 完成匹配项追加。
        limited = len(result.stdout.splitlines()) > len(matches)  # 判断是否因 max_results 丢弃结果。
        return ToolOutcome(  # 构造成功搜索结果。
            success=True,  # 标记搜索执行成功。
            data={"matches": matches, "query": arguments.query},  # 返回可编程消费的匹配列表。
            summary=f"找到 {len(matches)} 条匹配结果。",  # 提供紧凑 Agent 摘要。
            truncated=result.output_truncated or limited,  # 合并字符截断和条数截断状态。
        )  # 完成成功搜索结果。


class ReadCodeTool(ValidatedTool[ReadCodeArguments]):  # 定义安全代码片段读取工具。
    """读取有限行范围并为每一行添加稳定行号。"""  # 说明工具观察格式。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="read_code",  # 设置模型调用名称。
                description="按路径和行范围读取代码，并显示原始行号。",  # 描述工具用途。
                permission=PermissionLevel.READ_ONLY,  # 声明只读权限。
                read_only=True,  # 标记不会修改工作区。
                default_timeout_seconds=10.0,  # 保留统一工具元数据字段。
                max_output_chars=50_000,  # 允许参数在受控范围内选择读取上限。
            ),  # 完成工具声明。
            ReadCodeArguments,  # 绑定严格读取参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: ReadCodeArguments, runtime: Runtime) -> ToolOutcome:  # 执行代码读取语义。
        content = await runtime.read_file(  # 委托 Runtime 完成路径和符号链接检查。
            arguments.path,  # 传递工作区相对路径。
            start_line=arguments.start_line,  # 传递包含式起始行。
            end_line=arguments.end_line,  # 传递可选包含式结束行。
        )  # 完成安全文件读取。
        numbered_lines = [  # 创建适合 Agent 定位和生成 patch 的带行号文本。
            f"{line_number:>6} | {line}"  # 使用固定宽度行号并保留代码内容。
            for line_number, line in enumerate(content.splitlines(), start=arguments.start_line)  # 从原始起始行计数。
        ]  # 完成带行号文本列表。
        rendered = "\n".join(numbered_lines)  # 把带行号代码合并为单个观察文本。
        limited, truncated = truncate_text(rendered, arguments.max_chars)  # 按调用上限保留文本头尾。
        return ToolOutcome(  # 构造成功读取结果。
            success=True,  # 标记文件读取成功。
            data={  # 保存结构化读取信息。
                "path": arguments.path,  # 返回调用使用的相对路径。
                "start_line": arguments.start_line,  # 返回实际起始行。
                "end_line": arguments.end_line,  # 返回请求结束行。
                "content": limited,  # 返回带行号且受限的代码文本。
            },  # 完成结构化数据。
            summary=f"已读取 {arguments.path} 的 {len(numbered_lines)} 行。",  # 提供紧凑摘要。
            truncated=truncated,  # 明确标记字符截断。
        )  # 完成成功读取结果。


class ApplyPatchTool(ValidatedTool[ApplyPatchArguments]):  # 定义受控补丁应用工具。
    """通过 Runtime 的路径策略与 Git 预检查应用统一 diff。"""  # 说明两层安全检查。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="apply_patch",  # 设置模型调用名称。
                description="检查并应用统一 Git diff，失败时不保留部分修改。",  # 描述工具用途和原子语义。
                permission=PermissionLevel.WORKSPACE_WRITE,  # 声明工作区写权限。
                read_only=False,  # 标记会修改候选工作区。
                default_timeout_seconds=30.0,  # 设置补丁检查和应用默认时限。
                max_output_chars=20_000,  # 限制 Git 诊断进入上下文的长度。
            ),  # 完成工具声明。
            ApplyPatchArguments,  # 绑定严格补丁参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: ApplyPatchArguments, runtime: Runtime) -> ToolOutcome:  # 执行补丁应用语义。
        result = await runtime.apply_patch(arguments.patch)  # 委托 Runtime 执行路径策略和 Git 原子预检。
        if not result.applied:  # 识别被拒绝或无法应用的补丁。
            return ToolOutcome(  # 构造补丁失败观察。
                success=False,  # 标记工具语义失败。
                data={"stdout": result.stdout, "stderr": result.stderr},  # 保留 Git 诊断。
                summary="补丁未应用，工作区保持原状态。",  # 明确失败不会留下半应用修改。
                error_type="patch_rejected",  # 提供稳定错误分类。
                error_message=result.rejection_reason or result.stderr,  # 返回最相关拒绝原因。
            )  # 完成失败观察。
        return ToolOutcome(  # 构造补丁成功观察。
            success=True,  # 标记补丁已应用。
            data={"changed_files": list(result.changed_files)},  # 返回修改文件供后续测试选择使用。
            summary=f"补丁已应用，修改 {len(result.changed_files)} 个文件。",  # 提供紧凑摘要。
        )  # 完成成功观察。


class GitDiffTool(ValidatedTool[GitDiffArguments]):  # 定义当前候选 diff 查看工具。
    """读取相对于基础提交的标准 Git diff。"""  # 说明输出基准。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="git_diff",  # 设置模型调用名称。
                description="查看当前候选相对于任务基础提交的 Git diff。",  # 描述工具用途。
                permission=PermissionLevel.READ_ONLY,  # 声明只读权限。
                read_only=True,  # 标记不会修改工作区。
                default_timeout_seconds=30.0,  # 设置 diff 生成时限。
                max_output_chars=20_000,  # 限制 diff 进入上下文的字符数。
            ),  # 完成工具声明。
            GitDiffArguments,  # 绑定拒绝未知字段的空参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: GitDiffArguments, runtime: Runtime) -> ToolOutcome:  # 执行 diff 读取语义。
        del arguments  # 明确空参数对象仅用于校验，核心逻辑不需要读取它。
        diff = await runtime.get_diff()  # 请求 Runtime 返回相对于基础提交的标准 diff。
        limited, truncated = truncate_text(diff, self.spec.max_output_chars)  # 限制大补丁进入 Agent 上下文。
        summary = "当前工作区没有修改。" if not diff else "已生成当前候选 Git diff。"  # 区分空 diff 与有效修改。
        return ToolOutcome(  # 构造成功 diff 观察。
            success=True,  # 标记 diff 查询成功。
            data={"diff": limited},  # 返回受限 diff 文本。
            summary=summary,  # 返回紧凑状态摘要。
            truncated=truncated,  # 标记 diff 是否被截断。
        )  # 完成成功观察。
