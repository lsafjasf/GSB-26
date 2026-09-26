"""隔离清单管理：不稳定测试移入隔离集合，但仍定期执行并汇报。

清单为 JSON 文件，每条记录包含可审计字段：
  test_id / who（谁隔离）/ reason（原因）/ quarantined_at（何时隔离）/
  review_after（何时复查）/ status（active | resolved）
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def load(path):
    if not path or not os.path.exists(path):
        return {"entries": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add(path, test_id, who, reason, review_days=14):
    if not who or not reason:
        raise ValueError("隔离必须填写 --who 与 --reason（审计要求）")
    data = load(path)
    for e in data["entries"]:
        if e["test_id"] == test_id and e["status"] == "active":
            raise ValueError(f"{test_id} 已在隔离清单中")
    data["entries"].append({
        "test_id": test_id,
        "who": who,
        "reason": reason,
        "quarantined_at": _now().isoformat(),
        "review_after": (_now() + timedelta(days=review_days)).isoformat(),
        "status": "active",
        "history": [],
    })
    save(path, data)


def resolve(path, test_id, who, note=""):
    data = load(path)
    for e in data["entries"]:
        if e["test_id"] == test_id and e["status"] == "active":
            e["status"] = "resolved"
            e["history"].append({
                "action": "resolved", "who": who,
                "at": _now().isoformat(), "note": note,
            })
            save(path, data)
            return
    raise ValueError(f"{test_id} 不在有效隔离清单中")


def active_ids(path):
    return {e["test_id"] for e in load(path)["entries"] if e["status"] == "active"}


def due_for_review(path):
    now = _now()
    return [e for e in load(path)["entries"]
            if e["status"] == "active"
            and datetime.fromisoformat(e["review_after"]) <= now]
