"""迁移日志：落盘保存迁移状态，支持崩溃恢复。

每次状态变更先写临时文件再 os.replace 原子替换，
保证任意时刻磁盘上要么是旧状态要么是新状态，不会出现半写状态。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


class Journal:
    def __init__(self, path: str):
        self.path = path
        self.state: Optional[Dict[str, Any]] = None

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            self.state = json.load(f)
        return self.state

    def save(self, state: Optional[Dict[str, Any]] = None) -> None:
        if state is not None:
            self.state = state
        assert self.state is not None
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
