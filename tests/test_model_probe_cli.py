"""模型兼容性探针的默认零费用保护测试。"""  # 测试不会读取 API key 或发起网络请求。

from __future__ import annotations  # 延迟解析测试类型。

import pytest  # 断言 argparse 的安全退出行为。

from patchflow.model_probe_cli import main  # 调用真实低成本探针入口。


def test_model_probe_requires_explicit_api_spend() -> None:  # 验证缺少许可时在客户端构造前退出。
    with pytest.raises(SystemExit) as caught:  # argparse.error 应产生标准 SystemExit。
        main(["--provider", "openai_compatible", "--model-id", "deepseek-v3.2", "--base-url", "https://api.agicto.cn/v1"])  # 故意不提供付费许可开关。
    assert caught.value.code == 2  # 未授权路径必须返回参数错误退出码。
