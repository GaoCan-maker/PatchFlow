"""生成适合文件系统和日志使用的运行标识。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from secrets import token_hex


def create_run_id(task_id: str) -> str:
    """使用 UTC 时间、任务短名和随机后缀生成唯一 run ID。"""

    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-._")
    if not safe_task_id:
        safe_task_id = "task"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{safe_task_id[:48]}-{token_hex(3)}"

