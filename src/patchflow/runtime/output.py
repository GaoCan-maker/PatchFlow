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


class OutputAccumulator:  # 定义常量级内存的流式文本输出收集器。
    """只保存有限头尾字符，同时统计完整输出长度。"""  # 说明与一次性 communicate 的差别。

    def __init__(self, max_chars: int) -> None:  # 接收允许返回的字符上限。
        if max_chars < 1:  # 验证上限可以保存内容。
            raise ValueError("max_chars 必须大于零")  # 拒绝零或负数。
        self._max_chars = max_chars  # 保存固定内存预算。
        self._head = ""  # 保存最先出现的有限字符。
        self._tail = ""  # 保存最后出现的有限字符。
        self._total_chars = 0  # 统计完整输出字符数。

    def append(self, chunk: str) -> None:  # 增量接收已解码文本。
        self._total_chars += len(chunk)  # 记录包括丢弃部分在内的原始长度。
        head_room = self._max_chars - len(self._head)  # 计算头部还能保留多少字符。
        if head_room > 0:  # 优先保存最初的诊断上下文。
            self._head += chunk[:head_room]  # 限制头部至固定预算。
        self._tail = (self._tail + chunk)[-self._max_chars :]  # 持续保存最后的固定预算字符。

    def finish(self) -> tuple[str, bool]:  # 生成与 truncate_text 相同语义的最终文本。
        if self._total_chars <= self._max_chars:  # 检查输出是否未达到截断阈值。
            return self._head, False  # 返回完整短输出。
        marker = f"\n... [输出已截断，原始长度 {self._total_chars} 字符] ...\n"  # 生成实际长度标记。
        if len(marker) >= self._max_chars:  # 处理极小输出预算。
            return self._head[: self._max_chars], True  # 无法放下标记时只返回头部。
        remaining = self._max_chars - len(marker)  # 计算标记之外的字符配额。
        head_chars = (remaining + 1) // 2  # 给头部略多一点空间。
        tail_chars = remaining // 2  # 给尾部保存最终错误信息。
        suffix = self._tail[-tail_chars:] if tail_chars else ""  # 避免零切片返回整段尾部。
        return self._head[:head_chars] + marker + suffix, True  # 返回受限头尾和截断标记。
