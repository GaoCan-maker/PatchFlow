"""命令和工具输出的确定性截断逻辑。"""  # 说明本文件负责限制进入上下文的文本长度。

from __future__ import annotations  # 启用延迟解析类型标注。


def truncate_text(value: str, max_chars: int) -> tuple[str, bool]:  # 定义统一文本截断函数。
    """保留文本头尾并返回是否发生截断。"""  # 说明函数语义。

    if max_chars < 1:  # 防止调用者传入没有意义的长度限制。
        raise ValueError("max_chars 必须大于零")  # 对非法限制给出明确错误。
    if len(value) <= max_chars:  # 判断原始文本是否已经位于限制内。
        return value, False  # 原样返回短文本并标记未截断。
    marker = f"\n... [输出已截断，原始长度 {len(value)} 字符] ...\n"  # 构造可观察的截断标记。
    if len(marker) >= max_chars:  # 处理限制甚至放不下完整标记的极端情况。
        return value[:max_chars], True  # 只保留允许长度的文本头部。
    remaining = max_chars - len(marker)  # 计算标记之外还可保留多少正文。
    head_chars = (remaining + 1) // 2  # 将奇数个字符优先分配给文本头部。
    tail_chars = remaining // 2  # 将剩余字符分配给文本尾部。
    truncated = value[:head_chars] + marker + value[-tail_chars:]  # 拼接头部、标记和尾部。
    return truncated, True  # 返回截断文本和截断标志。

