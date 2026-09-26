"""隔离区（quarantine）：把不稳定测试隔离到单独集合，仍可审计、仍定期执行。

隔离清单存为 JSON 文件，每条记录包含可审计字段：
  - test_id        测试名
  - quarantined_by 谁隔离的
  - reason         隔离原因
  - quarantined_at 隔离时间（ISO 8601）
  - review_after   复查截止（ISO 8601 日期），到期后 `due` 命令会列出
隔离不等于跳过：runner 仍执行隔离测试，report 单独汇报。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class QuarantineRegistry:
    def __init__(self, path: str):
        self.path = path
        self.entries: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.entries = json.load(fh)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, indent=2)

    def add(self, test_id: str, by: str, reason: str, review_after: str) -> dict:
        if not by or not reason:
            raise ValueError("隔离必须填写操作人(--by)与原因(--reason)，保证可审计")
        for e in self.entries:
            if e["test_id"] == test_id:
                raise ValueError(f"{test_id} 已在隔离清单中")
        entry = {
            "test_id": test_id,
            "quarantined_by": by,
            "reason": reason,
            "quarantined_at": _now(),
            "review_after": review_after,
        }
        self.entries.append(entry)
        self._save()
        return entry

    def remove(self, test_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["test_id"] != test_id]
        if len(self.entries) != before:
            self._save()
            return True
        return False

    def ids(self) -> set[str]:
        return {e["test_id"] for e in self.entries}

    def due(self) -> list[dict]:
        today = _today()
        return [e for e in self.entries if e["review_after"] <= today]
