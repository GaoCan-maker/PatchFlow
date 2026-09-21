"""统一输出截断函数测试。"""  # 说明本文件聚焦上下文长度控制。

from patchflow.runtime.output import truncate_text  # 导入待测试截断函数。


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

