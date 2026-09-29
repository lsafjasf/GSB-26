"""
lock_inspector —— 锁目录巡检：识别过期租约 / 内容损坏 / 孤儿锁，并给出可逐条核对的判定依据。

对每个候选锁文件输出：
  * 判定类别：valid（有效）/ expired（过期租约）/ orphan（持有进程已死）/ corrupt（内容损坏）
  * 判定依据：内容校验结果、代际（锁文件 vs sidecar）、修改时间、持有者进程存活状态
  * 建议动作：wait（等待）/ preempt（抢占）/ cleanup（清理）

默认只预览（dry-run），不做任何修改；--execute 才执行建议动作。
清理只删除锁文件本身，代际 sidecar（<lock>.gen）始终保留，因此清理后
新的加锁能立即成功且代际连续（sidecar_gen + 1）。

用法：
    python3 lock_inspector.py <锁目录> [--lease-duration 5.0] [--json] [--execute]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

from lease_lock import _Guard

__all__ = [
    "Inspection",
    "inspect_path",
    "inspect_directory",
    "execute_inspection",
    "ACTION_WAIT",
    "ACTION_PREEMPT",
    "ACTION_CLEANUP",
    "ACTION_NONE",
]

ACTION_WAIT = "wait"          # 等待：锁仍有效（或损坏文件仍在保守等待期）
ACTION_PREEMPT = "preempt"    # 抢占：租约已过期，可立即抢占
ACTION_CLEANUP = "cleanup"    # 清理：孤儿锁 / 损坏且已过保守等待期
ACTION_NONE = "none"          # 无需处理（文件已不存在）

ACTION_LABELS = {
    ACTION_WAIT: "等待",
    ACTION_PREEMPT: "抢占",
    ACTION_CLEANUP: "清理",
    ACTION_NONE: "无需处理",
}

CATEGORY_LABELS = {
    "valid": "有效租约",
    "expired": "过期租约",
    "orphan": "孤儿锁（持有进程已不存在）",
    "corrupt": "内容损坏",
    "missing": "文件不存在",
}

_SIDECAR_SUFFIXES = (".guard", ".gen", ".fencing")
_REQUIRED_KEYS = ("holder", "token", "generation", "expires_at", "renewed_at")


# ---------------------------------------------------------------- 数据结构

@dataclass
class Inspection:
    """单个锁文件的巡检结论。evidence 中保留全部判定依据，可逐条核对。"""

    path: str
    category: str                 # valid / expired / orphan / corrupt / missing
    action: str                   # wait / preempt / cleanup / none
    reasons: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "action": self.action,
            "action_label": ACTION_LABELS.get(self.action, self.action),
            "reasons": list(self.reasons),
            "evidence": dict(self.evidence),
        }


# ---------------------------------------------------------------- 内部工具

def _fmt_ts(ts) -> str:
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _pid_alive(pid: int):
    """本机进程存活探测：True 存活 / False 不存在 / None 无法判定。"""
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _parse_holder(holder):
    """解析 'host:pid:rand' 形式的 holder；失败返回 None。"""
    if not isinstance(holder, str):
        return None
    parts = holder.split(":")
    if len(parts) != 3:
        return None
    host, pid_s, _rand = parts
    try:
        pid = int(pid_s)
    except ValueError:
        return None
    return host, pid


def _validate_record(data) -> list:
    """返回内容问题列表；空列表表示内容校验通过。"""
    problems = []
    if not isinstance(data, dict):
        return ["JSON 顶层不是对象"]
    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        problems.append("缺少必需字段: " + ", ".join(missing))
        return problems
    try:
        int(data["generation"])
    except (TypeError, ValueError):
        problems.append(f"generation 不是整数: {data['generation']!r}")
    for key in ("expires_at", "renewed_at"):
        try:
            float(data[key])
        except (TypeError, ValueError):
            problems.append(f"{key} 不是数值: {data[key]!r}")
    return problems


def _read_sidecar_gen(path: str) -> int:
    try:
        with open(path + ".gen", "r", encoding="utf-8") as f:
            return max(0, int(f.read().strip()))
    except (OSError, ValueError):
        return 0


# ---------------------------------------------------------------- 单文件巡检

def inspect_path(path: str, lease_duration: float = 5.0, now: float | None = None) -> Inspection:
    """巡检单个锁文件，返回 Inspection（判定类别 + 依据 + 建议动作）。

    lease_duration 用于损坏文件的保守等待期判定（与 LeaseLock 默认一致）。
    """
    now = time.time() if now is None else now
    ev = {"now": now, "now_readable": _fmt_ts(now)}

    try:
        st = os.stat(path)
    except FileNotFoundError:
        return Inspection(path, "missing", ACTION_NONE, ["锁文件不存在，无需处理"], ev)

    ev["mtime"] = st.st_mtime
    ev["mtime_readable"] = _fmt_ts(st.st_mtime)
    ev["mtime_age_sec"] = round(now - st.st_mtime, 3)
    ev["sidecar_generation"] = _read_sidecar_gen(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        problems = _validate_record(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        data, problems = None, [f"JSON 解析失败: {exc}"]
    except OSError as exc:
        data, problems = None, [f"读取失败: {exc}"]

    if problems:
        # 内容损坏：保守等待一个完整租约周期（按 mtime）后才建议清理，
        # 与 LeaseLock.try_acquire 对损坏文件的抢占策略保持一致。
        wait_until = st.st_mtime + lease_duration
        ev["content_check"] = "corrupt"
        ev["content_problems"] = problems
        ev["conservative_wait_until"] = wait_until
        ev["conservative_wait_until_readable"] = _fmt_ts(wait_until)
        if wait_until > now:
            return Inspection(
                path, "corrupt", ACTION_WAIT,
                ["内容损坏，但仍在保守等待期内（mtime + 租约周期未过），"
                 "持有者可能正在写入，建议等待"],
                ev,
            )
        return Inspection(
            path, "corrupt", ACTION_CLEANUP,
            ["内容损坏且已过保守等待期（mtime + 租约周期），可安全清理；"
             "代际由 sidecar 延续"],
            ev,
        )

    # 内容校验通过
    ev["content_check"] = "ok"
    ev["content_problems"] = []
    ev["holder"] = data["holder"]
    ev["token"] = data["token"]
    ev["generation"] = int(data["generation"])
    ev["renewed_at"] = float(data["renewed_at"])
    ev["expires_at"] = float(data["expires_at"])
    ev["expires_at_readable"] = _fmt_ts(ev["expires_at"])
    ev["lease_remaining_sec"] = round(ev["expires_at"] - now, 3)
    if ev["generation"] < ev["sidecar_generation"]:
        ev["generation_note"] = (
            f"锁文件代际 {ev['generation']} 落后于 sidecar {ev['sidecar_generation']}"
        )
    else:
        ev["generation_note"] = "锁文件代际与 sidecar 一致"

    holder = _parse_holder(data["holder"])
    if holder is None:
        pid_alive = None
        ev["pid_check"] = "holder 非 host:pid:rand 格式，无法检查进程存活"
    elif holder[0] != os.uname().nodename:
        pid_alive = None
        ev["pid_check"] = f"持有者来自其他主机 {holder[0]!r}，本机无法判定进程存活"
    else:
        host, pid = holder
        ev["holder_host"] = host
        ev["holder_pid"] = pid
        pid_alive = _pid_alive(pid)
        ev["pid_check"] = {True: "进程存活", False: "进程不存在", None: "无法判定"}[pid_alive]
    ev["pid_alive"] = pid_alive

    expired = ev["expires_at"] <= now
    ev["expired"] = expired

    if pid_alive is False:
        return Inspection(
            path, "orphan", ACTION_CLEANUP,
            [f"持有进程 {host}:{pid} 在本机已不存在，租约不会再被续期，可安全清理"
             + ("（租约亦已过期）" if expired else "（租约虽未到 expires_at，但持有者已死）")],
            ev,
        )
    if expired:
        return Inspection(
            path, "expired", ACTION_PREEMPT,
            [f"租约已过期 {-ev['lease_remaining_sec']:.3f}s，任何加锁者都可立即抢占"],
            ev,
        )
    return Inspection(
        path, "valid", ACTION_WAIT,
        [f"租约有效，剩余 {ev['lease_remaining_sec']:.3f}s"
         + ("，持有进程存活" if pid_alive else "（进程存活状态无法判定，保守等待）")],
        ev,
    )


# ---------------------------------------------------------------- 目录巡检

def _looks_like_lock(path: str) -> bool:
    name = os.path.basename(path)
    if name.endswith(".lock"):
        return True
    if os.path.exists(path + ".guard") or os.path.exists(path + ".gen"):
        return True
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and any(k in data for k in _REQUIRED_KEYS)
    except (OSError, ValueError):
        return False


def inspect_directory(directory: str, lease_duration: float = 5.0):
    """巡检目录，返回 (inspections, skipped)。skipped 为 (path, 原因) 列表。"""
    inspections, skipped = [], []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if name.endswith(_SIDECAR_SUFFIXES) or ".tmp." in name:
            continue  # 伴随文件 / 临时文件，不是锁本体
        if _looks_like_lock(path):
            inspections.append(inspect_path(path, lease_duration=lease_duration))
        else:
            skipped.append((path, "非锁文件（无锁记录结构，且无 .guard/.gen 伴随文件）"))
    return inspections, skipped


# ---------------------------------------------------------------- 执行处置

def execute_inspection(insp: Inspection, lease_duration: float = 5.0):
    """执行建议动作，返回 (changed, message)。

    - wait / none：不改动。
    - preempt / cleanup：在 guard 临界区内复检，状态仍成立才删除锁文件；
      代际 sidecar（<lock>.gen）始终保留，保证下次加锁代际连续。
    """
    if insp.action in (ACTION_WAIT, ACTION_NONE):
        return False, f"建议{ACTION_LABELS[insp.action]}，未改动"

    with _Guard(insp.path + ".guard"):
        fresh = inspect_path(insp.path, lease_duration=lease_duration)
        if fresh.category == "missing":
            return False, "复检：文件已不存在（可能已被其他进程处理），未改动"
        if fresh.action in (ACTION_WAIT, ACTION_NONE):
            return False, f"复检：状态已变为「{ACTION_LABELS[fresh.action]}」"
        os.unlink(insp.path)
        gen = _read_sidecar_gen(insp.path)
        return True, (
            f"已删除锁文件（{CATEGORY_LABELS[fresh.category]}）；"
            f"代际 sidecar 保留 gen={gen}，下次加锁将为 gen={gen + 1}"
        )


# ---------------------------------------------------------------- CLI

def _render_text(inspections, skipped) -> str:
    lines = []
    total = len(inspections)
    for i, insp in enumerate(inspections, 1):
        ev = insp.evidence
        lines.append(
            f"[{i}/{total}] {insp.path}\n"
            f"  判定: {CATEGORY_LABELS.get(insp.category, insp.category)}"
            f"    建议动作: {ACTION_LABELS.get(insp.action, insp.action)}"
        )
        for reason in insp.reasons:
            lines.append(f"  理由: {reason}")
        lines.append("  依据:")
        lines.append(f"    内容校验: {ev.get('content_check', '-')}"
                     + (("  问题: " + "; ".join(ev["content_problems"]))
                        if ev.get("content_problems") else ""))
        if "holder" in ev:
            lines.append(f"    持有者: {ev['holder']}  (pid={ev.get('holder_pid')}, "
                         f"进程检查: {ev.get('pid_check', '-')})")
        if "generation" in ev:
            lines.append(f"    代际: 锁文件={ev['generation']}  "
                         f"sidecar={ev.get('sidecar_generation')}  ({ev.get('generation_note', '')})")
        else:
            lines.append(f"    代际: 锁文件不可解析  sidecar={ev.get('sidecar_generation')}")
        lines.append(f"    修改时间: {ev.get('mtime_readable')} "
                     f"({ev.get('mtime_age_sec')}s 前)")
        if "expires_at" in ev:
            state = "已过期" if ev.get("expired") else "未过期"
            lines.append(f"    租约到期: {ev.get('expires_at_readable')}  ({state}, "
                         f"剩余 {ev.get('lease_remaining_sec')}s)")
        if "conservative_wait_until" in ev:
            lines.append(f"    保守等待期至: {ev.get('conservative_wait_until_readable')}")
    for path, why in skipped:
        lines.append(f"[跳过] {path}\n  原因: {why}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="锁目录巡检：识别过期租约 / 内容损坏 / 孤儿锁，默认只预览不修改。"
    )
    parser.add_argument("directory", help="锁文件所在目录")
    parser.add_argument("--lease-duration", type=float, default=5.0,
                        help="损坏文件保守等待期所用的租约周期（默认 5.0s）")
    parser.add_argument("--json", action="store_true", help="以 JSON Lines 输出，便于逐条核对")
    parser.add_argument("--execute", action="store_true",
                        help="执行建议动作（默认 dry-run 只预览）")
    args = parser.parse_args(argv)

    inspections, skipped = inspect_directory(args.directory, lease_duration=args.lease_duration)

    if args.json:
        for insp in inspections:
            print(json.dumps(insp.to_dict(), ensure_ascii=False, sort_keys=True))
        for path, why in skipped:
            print(json.dumps({"path": path, "skipped": why}, ensure_ascii=False))
    else:
        mode = "执行模式" if args.execute else "预览模式（dry-run，未做任何修改）"
        print(f"# 巡检目录: {args.directory}    模式: {mode}")
        print(_render_text(inspections, skipped))

    counts = {}
    for insp in inspections:
        counts[insp.action] = counts.get(insp.action, 0) + 1
    summary = ", ".join(f"{ACTION_LABELS[a]} {n}" for a, n in sorted(counts.items())) or "无锁文件"
    print(f"# 汇总: {summary}", file=sys.stderr if args.json else sys.stdout)

    if args.execute:
        print("# 执行结果:")
        for insp in inspections:
            changed, message = execute_inspection(insp, lease_duration=args.lease_duration)
            mark = "已处理" if changed else "未改动"
            print(f"  [{mark}] {insp.path}\n    {message}")
    elif any(i.action in (ACTION_PREEMPT, ACTION_CLEANUP) for i in inspections):
        print("# 提示: 加 --execute 执行上述建议动作（清理只删锁文件，代际 sidecar 保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
