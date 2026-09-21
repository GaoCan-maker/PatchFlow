"""供确定性 Agent 测试使用的脚本模型。"""  # 声明 FakeModel 不调用远程 API。

from __future__ import annotations  # 延迟解析类型标注。

from collections import deque  # 使用队列保证脚本回复按顺序消费。

from patchflow.model.protocol import ModelRequest, ModelResponse  # 复用稳定模型协议。


class FakeModel:  # 定义可重复播放决策序列的模型替身。
    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:  # 接收测试预先写好的回复。
        self._responses = deque(responses)  # 保存尚未发送的脚本回复。
        self.requests: list[ModelRequest] = []  # 保留每次请求供测试检查上下文反馈。

    async def complete(self, request: ModelRequest) -> ModelResponse:  # 实现真实模型共用的异步接口。
        self.requests.append(request)  # 记录本次模型看到的完整输入。
        if not self._responses:  # 在脚本意外耗尽时报告明确错误。
            raise RuntimeError("FakeModel 脚本回复已耗尽")  # 避免无限循环或伪造结束回复。
        return self._responses.popleft()  # 消费并返回下一条预设回复。
