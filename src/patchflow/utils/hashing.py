"""用于配置和任务快照的稳定摘要。"""

from __future__ import annotations

from hashlib import sha256


def stable_digest(content: str) -> str:
    """返回带算法前缀的 SHA-256 摘要，便于未来迁移算法。"""

    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"

