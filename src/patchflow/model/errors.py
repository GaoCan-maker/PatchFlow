"""所有模型适配器共用的稳定错误分类。"""  # 让 Agent 不依赖具体 provider。


class ModelProviderError(RuntimeError):  # 定义远程服务或网络层错误。
    def __init__(self, kind: str, *, retryable: bool) -> None:  # 保存不含服务响应正文的稳定分类。
        super().__init__(kind)  # 使异常文本只包含安全的机器类别。
        self.kind = kind  # 供 Agent 写入可审计停止原因。
        self.retryable = retryable  # 供未来预算内重试策略使用。


class ModelOutputError(ValueError):  # 定义模型回复无法转成单个安全动作的错误。
    """无效结构化回复不能靠猜测补齐。"""  # 解释拒绝不完整调用的原因。
