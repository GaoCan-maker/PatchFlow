"""统一输出截断函数测试。"""  # 说明本文件聚焦上下文长度控制。

from patchflow.runtime.output import OutputAccumulator, truncate_text  # 导入一次性与流式截断实现。


def test_short_text_is_not_truncated() -> None:  # 测试限制内文本保持不变。
    value, truncated = truncate_text("short output", 20)  # 使用大于原文的限制执行截断。

    assert value == "short output"  # 验证文本未发生变化。
    assert truncated is False  # 验证截断标志为假。


def test_long_text_preserves_head_and_tail() -> None:  # 测试长文本保留头尾证据。
    value, truncated = truncate_text("A" * 100 + "TAIL", 60)  # 对长文本执行严格限制。

    assert len(value) == 60  # 验证最终文本严格遵守字符上限。
    assert value.startswith("A")  # 验证文本头部仍被保留。
    assert value.endswith("TAIL")  # 验证错误栈尾部等关键信息仍被保留。
    assert "输出已截断" in value  # 验证 Agent 可以感知观察不完整。
    assert truncated is True  # 验证截断标志为真。


def test_streaming_accumulator_matches_one_shot_truncation() -> None:  # 验证分块读取不改变最终输出语义。
    output = "开始" + "中" * 500 + "错误结尾"  # 构造同时包含中文头尾的长输出。
    accumulator = OutputAccumulator(80)  # 创建只能保留有限字符的流式收集器。
    for start in range(0, len(output), 13):  # 用不对齐的分块模拟异步管道读取。
        accumulator.append(output[start : start + 13])  # 增量消费当前字符块。
    assert accumulator.finish() == truncate_text(output, 80)  # 验证与已有截断契约完全一致。


def test_streaming_accumulator_handles_short_and_tiny_limits() -> None:  # 验证短输出和极小预算。
    short = OutputAccumulator(10)  # 创建能完整容纳短输出的收集器。
    short.append("ok")  # 添加一个短输出块。
    assert short.finish() == ("ok", False)  # 验证不截断短输出。
    tiny = OutputAccumulator(3)  # 创建不足以放下截断标记的预算。
    tiny.append("abcdef")  # 追加超过预算的输出。
    assert tiny.finish() == ("abc", True)  # 验证仍严格限制长度并标记截断。
