"""为线性基线构造有界且可审计的模型可见历史。"""  # 完整轨迹仍保存在 JSONL 中。

from __future__ import annotations  # 延迟解析类型标注。

import json  # 估算消息和工具定义的 UTF-8 请求体大小。
from dataclasses import dataclass  # 保存不可变上下文选择结果。

from patchflow.domain.tools import ToolSpec  # 计入工具定义占用的请求容量。
from patchflow.model.protocol import ModelMessage  # 保留原生函数调用消息对。


class ContextTooLargeError(ValueError):  # 标记固定区或最新反馈超出配置上限。
    """不可通过丢弃历史安全恢复的上下文超限。"""  # 避免发送不完整函数调用对。


@dataclass(frozen=True, slots=True)  # 让单次模型请求的选择结果不可变。
class ContextSlice:  # 记录送往模型的消息与省略情况。
    messages: tuple[ModelMessage, ...]  # 保存保持顺序的模型可见消息。
    dropped_messages: int  # 记录未进入本轮请求的历史消息数。
    estimated_bytes: int  # 保存保守估算的请求 UTF-8 字节数。


class ContextBuilder:  # 为固定 Issue 与最近的完整工具对分配上下文空间。
    def __init__(self, max_bytes: int = 64_000) -> None:  # 接收独立于模型 token 预算的请求字节上限。
        if max_bytes < 512:  # 为固定说明和消息结构保留最低空间。
            raise ValueError("max_bytes 不能小于 512")  # 拒绝不可用的配置。
        self.max_bytes = max_bytes  # 保存一次模型请求的硬上限。

    @staticmethod  # 估算方法不依赖实例状态。
    def _message_bytes(message: ModelMessage) -> int:  # 计算消息内容与关联元数据的上界近似。
        size = len(json.dumps(message.content, ensure_ascii=False).encode("utf-8")) + len(message.role.encode("utf-8")) + 96  # 计入正文转义和消息结构开销。
        if message.tool_call is not None:  # 原生函数调用包含完整参数而不只是名称。
            size += len(message.tool_call.model_dump_json().encode("utf-8")) * 2  # 为 JSON 再编码预留一次转义容量。
        if message.tool_call_id is not None:  # 工具消息必须携带关联 ID。
            size += len(message.tool_call_id.encode("utf-8"))  # 计入调用 ID 字节。
        return size  # 返回保守的消息估算值。

    def _estimate(self, messages: tuple[ModelMessage, ...], tools: tuple[ToolSpec, ...]) -> int:  # 统计完整请求开销。
        tool_bytes = sum(len(tool.model_dump_json().encode("utf-8")) * 2 for tool in tools)  # 计入函数名、描述和参数模式。
        return 512 + tool_bytes + sum(self._message_bytes(message) for message in messages)  # 预留 provider 包装字段。

    @staticmethod  # 成对分组逻辑不依赖实例状态。
    def _groups(history: tuple[ModelMessage, ...]) -> list[tuple[ModelMessage, ...]]:  # 保证函数调用和反馈一同保留。
        groups: list[tuple[ModelMessage, ...]] = []  # 按时间顺序收集完整历史单元。
        index = 1  # 跳过必须固定保留的第一条 Issue 消息。
        while index < len(history):  # 逐条扫描剩余历史。
            current = history[index]  # 读取当前消息。
            if current.tool_call is not None:  # 原生函数调用必须跟随匹配的工具结果。
                if index + 1 >= len(history):  # 缺少反馈时不能构造合法 API 历史。
                    raise ContextTooLargeError("工具调用缺少反馈消息")  # 不发送悬空调用。
                following = history[index + 1]  # 读取预期的工具回复。
                if following.role != "tool" or following.tool_call_id != current.tool_call.call_id:  # 检查调用 ID 配对。
                    raise ContextTooLargeError("工具调用与反馈 ID 不匹配")  # 避免上下文错配。
                groups.append((current, following))  # 把调用和回复作为不可拆分的历史单元。
                index += 2  # 跳过已经分组的两条消息。
            else:  # 处理普通文本历史消息。
                if current.role == "tool":  # 孤立的工具反馈不合法。
                    raise ContextTooLargeError("工具反馈缺少对应调用")  # 禁止发送无因果归属的反馈。
                groups.append((current,))  # 保留单条普通消息。
                index += 1  # 继续扫描后续历史。
        return groups  # 返回完整而有序的消息分组。

    @staticmethod  # 摘要只能使用结构化元数据，不能提升工具内容为系统指令。
    def _summary(groups: list[tuple[ModelMessage, ...]]) -> ModelMessage:  # 压缩被省略的较旧动作。
        attempts: list[str] = []  # 保存最近几个已尝试动作的短记录。
        for group in groups[-5:]:  # 只总结最后五个被丢弃的单元。
            first = group[0]  # 读取动作消息。
            if first.tool_call is None:  # 普通文本消息没有结构化工具名称。
                continue  # 不把可能含指令的旧文本拼进摘要。
            label = first.tool_call.tool_name  # 使用经领域模型验证的工具名。
            if len(group) == 2:  # 成功或失败状态位于对应工具结果。
                try:  # 尝试解析内部生成的结构化观察。
                    data = json.loads(group[1].content)  # 只读取已知元数据字段。
                except json.JSONDecodeError:  # 历史可能来自其他模型适配器。
                    data = {}  # 无法解析时保持保守摘要。
                label += ":" + str(data.get("error_type") or ("ok" if data.get("success") else "failed"))[:64]  # 保留稳定结果类别。
            attempts.append(label)  # 添加单个短尝试记录。
        content = f"先前有 {len(groups)} 组交互被压缩；最近尝试：{', '.join(attempts)}。完整轨迹保存在事件日志。"  # 只陈述过程事实。
        return ModelMessage("user", content)  # 使用低权限角色传递摘要而非系统指令。

    def build(self, history: tuple[ModelMessage, ...], tools: tuple[ToolSpec, ...]) -> ContextSlice:  # 构造单次可见请求。
        if not history:  # Issue 不能为空。
            raise ContextTooLargeError("模型历史缺少固定 Issue")  # 拒绝无法解释的空请求。
        fixed = (history[0],)  # 固定保留任务问题描述。
        if self._estimate(fixed, tools) > self.max_bytes:  # 问题描述和工具定义可能本身超限。
            raise ContextTooLargeError("固定 Issue 或工具模式超过上下文上限")  # 不默默截断核心约束。
        groups = self._groups(history)  # 把历史整理为完整调用与反馈对。
        selected: list[tuple[ModelMessage, ...]] = []  # 从最近往前选择可保留单元。
        for group in reversed(groups):  # 优先保留最新测试或错误反馈。
            proposed = fixed + tuple(message for unit in [group, *selected] for message in unit)  # 计算加入当前单元后的原始顺序。
            if self._estimate(proposed, tools) > self.max_bytes:  # 检查是否触及请求上限。
                break  # 丢弃更早的整组历史，不拆开函数调用对。
            selected.insert(0, group)  # 保留当前完整单元并继续向前扫描。
        if groups and not selected:  # 最新一组本身过大时不能安全压缩其反馈。
            raise ContextTooLargeError("最新工具调用与反馈超过上下文上限")  # 终止而不是发送残缺补丁。
        dropped = groups[: len(groups) - len(selected)]  # 记录被省略的历史前缀。
        chosen = fixed + tuple(message for unit in selected for message in unit)  # 组装固定区和最近完整交互。
        if dropped:  # 为较旧尝试添加受限摘要。
            summary = self._summary(dropped)  # 只包含工具名和状态元数据。
            while selected and self._estimate((*fixed, summary, *(message for unit in selected for message in unit)), tools) > self.max_bytes:  # 摘要可能需要挪出最旧的一组。
                dropped.append(selected.pop(0))  # 按整组移动到省略区。
                summary = self._summary(dropped)  # 根据新的省略集合重建短摘要。
            if not selected:  # 摘要不能挤掉最新的完整工具反馈。
                raise ContextTooLargeError("最新反馈与历史摘要无法同时容纳")  # 保证关键失败仍可见。
            chosen = (*fixed, summary, *(message for unit in selected for message in unit))  # 把摘要放在最近交互之前。
            if self._estimate(chosen, tools) > self.max_bytes:  # 即使只剩固定区与摘要也可能超限。
                raise ContextTooLargeError("压缩摘要超过上下文上限")  # 不突破配置硬限制。
        dropped_count = sum(len(group) for group in dropped)  # 按真实消息数统计省略规模。
        return ContextSlice(chosen, dropped_count, self._estimate(chosen, tools))  # 返回可审计的有界请求。
