"""生成报告样例：python3 demo.py"""
from tablediff import Table, compare

left = Table.from_dicts("迁移前.users", [
    {"id": 1, "name": "alice", "balance": 100.0,  "updated_at": "2026-09-01"},
    {"id": 2, "name": "bob",   "balance": 250.5,  "updated_at": "2026-09-02"},
    {"id": 3, "name": "carol", "balance": 0.0,    "updated_at": "2026-09-03"},
    {"id": 4, "name": "dave",  "balance": 88.88,  "updated_at": "2026-09-04"},
    {"id": 4, "name": "dave2", "balance": 1.0,    "updated_at": "2026-09-04"},  # 重复主键
    {"id": None, "name": "ghost", "balance": 5.0, "updated_at": "2026-09-05"},  # 主键缺失
])
right = Table.from_dicts("迁移后.users", [
    {"id": 1, "name": "alice", "balance": 100.004, "updated_at": "2026-09-09", "vip": 1},
    {"id": 2, "name": "bob",   "balance": 250.5,   "updated_at": "2026-09-09", "vip": 0},
    {"id": 3, "name": "carol", "balance": 10.0,    "updated_at": "2026-09-09", "vip": 0},
    {"id": 5, "name": "erin",  "balance": 7.7,     "updated_at": "2026-09-09", "vip": 1},
])

rep = compare(left, right, key="id",
              ignore_columns=["updated_at"],
              tolerances={"balance": {"atol": 0.01}})

text = rep.to_text()
print(text)
with open("sample_report.txt", "w", encoding="utf-8") as f:
    f.write(text + "\n\n\n========== JSON 完整报告 ==========\n")
    f.write(rep.to_json() + "\n")
print("\n已写入 sample_report.txt")
