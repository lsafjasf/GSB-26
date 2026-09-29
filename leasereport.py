"""leasereport: filelease 租约使用报告。

读取 `<锁文件>.events`（JSONL 事件流，由 FileLeaseLock(report=True) 产生），
聚合输出：

* 加锁等待时长分布（含获取超时次数）
* 续期频率与续期时剩余租约
* 抢占事件与代际变化
* 持锁进程异常退出（未释放即过期）后的回收情况
* 与当前锁文件内容的一致性校验（可对数）

CLI:
    python3 leasereport.py <锁文件路径> [--start TS] [--end TS] [--json] [--check]

TS 支持 epoch 秒或 ISO 8601（如 2026-09-29T12:00:00）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os


# ---------------------------------------------------------------- 读取与过滤

def read_events(log_path, start=None, end=None):
    """读取事件流，按 [start, end]（epoch 秒，含端点）过滤。坏行跳过。"""
    events = []
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                ts = ev.get("ts")
                if not isinstance(ts, (int, float)):
                    continue
                if start is not None and ts < start:
                    continue
                if end is not None and ts > end:
                    continue
                events.append(ev)
    except FileNotFoundError:
        pass
    return events


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _stats(vals):
    vals = sorted(vals)
    if not vals:
        return None
    return {
        "count": len(vals),
        "min": vals[0],
        "p50": _percentile(vals, 0.50),
        "p90": _percentile(vals, 0.90),
        "p99": _percentile(vals, 0.99),
        "max": vals[-1],
        "mean": sum(vals) / len(vals),
    }


# ---------------------------------------------------------------- 聚合

def build_report(events):
    """把事件流聚合为报告 dict（全部为可 JSON 序列化的基本类型）。"""
    acquires = [e for e in events if e.get("event") == "acquire"]
    renews = [e for e in events if e.get("event") == "renew"]
    releases = [e for e in events if e.get("event") == "release"]
    timeouts = [e for e in events if e.get("event") == "acquire_timeout"]
    quarantines = [e for e in events if e.get("event") == "corrupt_quarantine"]

    # 1) 加锁等待时长
    wait = _stats([e["wait_ms"] for e in acquires if "wait_ms" in e])

    # 2) 续期频率与剩余租约：按 (holder, generation) 分组看相邻续期间隔
    renew_groups = {}
    for e in renews:
        renew_groups.setdefault((e.get("holder"), e.get("generation")), []).append(e)
    intervals_ms, per_lease = [], []
    for (holder, gen), group in sorted(renew_groups.items(),
                                       key=lambda kv: min(x["ts"] for x in kv[1])):
        group.sort(key=lambda x: x["ts"])
        gaps = [(b["ts"] - a["ts"]) * 1000.0 for a, b in zip(group, group[1:])]
        intervals_ms.extend(gaps)
        per_lease.append({
            "holder": holder,
            "generation": gen,
            "renew_count": len(group),
            "mean_interval_ms": (sum(gaps) / len(gaps)) if gaps else None,
        })
    renew = {
        "total": len(renews),
        "interval_ms": _stats(intervals_ms),
        "remaining_ms": _stats([e["remaining_ms"] for e in renews
                                if "remaining_ms" in e]),
        "per_lease": per_lease,
    }

    # 3) 抢占事件与代际变化
    preemptions = [{
        "ts": e["ts"],
        "new_holder": e.get("holder"),
        "new_generation": e.get("generation"),
        "prev_holder": e["preempted"].get("holder"),
        "prev_generation": e["preempted"].get("generation"),
        "expired_at": e["preempted"].get("expired_at"),
        "reclaim_delay_ms": e["preempted"].get("reclaim_delay_ms"),
    } for e in acquires if e.get("preempted")]
    generations = [{
        "ts": e["ts"],
        "generation": e.get("generation"),
        "prev_generation": e.get("prev_generation"),
        "holder": e.get("holder"),
    } for e in acquires]

    # 4) 异常退出后的回收：上一持有者未释放、租约过期后被他人抢走
    reclaim_delays = [p["reclaim_delay_ms"] for p in preemptions
                      if p.get("reclaim_delay_ms") is not None]
    reclamation = {
        "count": len(preemptions),
        "reclaim_delay_ms": _stats(reclaim_delays),
        "events": preemptions,
    }

    return {
        "event_counts": {
            "acquire": len(acquires),
            "renew": len(renews),
            "release": len(releases),
            "acquire_timeout": len(timeouts),
            "corrupt_quarantine": len(quarantines),
        },
        "wait_ms": wait,
        "renew": renew,
        "preemptions": preemptions,
        "generations": generations,
        "reclamation": reclamation,
        "window": {
            "start": min((e["ts"] for e in events), default=None),
            "end": max((e["ts"] for e in events), default=None),
        },
    }


# ---------------------------------------------------------------- 一致性校验

def check_consistency(lock_path, events=None):
    """校验事件流中最后一次状态快照与当前锁文件内容一致（可对数）。

    返回 (ok, detail)。锁文件不存在（未持有/已清理）时，
    只要最后一条状态事件是 release 也视为一致。
    """
    log_path = lock_path + ".events"
    if events is None:
        events = read_events(log_path)
    snapshots = [e for e in events if isinstance(e.get("record"), dict)]
    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            current = json.load(fh)
    except FileNotFoundError:
        current = None
    except ValueError:
        return False, "锁文件当前内容无法解析（损坏）"

    if not snapshots:
        return (current is None,
                "无状态事件" if current is None else "无状态事件但锁文件存在")

    last = snapshots[-1]
    want = last["record"]
    if current is None:
        return False, "最后事件(%s, gen=%s)有锁文件快照，但锁文件不存在" % (
            last.get("event"), last.get("generation"))
    keys = ("state", "holder", "generation", "issued_at", "ttl", "expires_at")
    for key in keys:
        if current.get(key) != want.get(key):
            return False, "字段 %r 不一致: 锁文件=%r 事件快照=%r (事件 ts=%.3f)" % (
                key, current.get(key), want.get(key), last.get("ts", 0.0))
    return True, "锁文件与最后一条 %s 事件快照一致 (gen=%s, holder=%s)" % (
        last.get("event"), current.get("generation"), current.get("holder"))


# ---------------------------------------------------------------- 渲染

def _fmt_stats(label, stats, unit="ms"):
    if not stats:
        return ["  %s: 无样本" % label]
    return ["  %s: n=%d min=%.1f%s p50=%.1f p90=%.1f p99=%.1f max=%.1f mean=%.1f"
            % (label, stats["count"], stats["min"], unit, stats["p50"],
               stats["p90"], stats["p99"], stats["max"], stats["mean"])]


def _fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.") \
        + ("%03d" % int((ts % 1) * 1000))


def render_text(report, lock_path=None):
    lines = []
    title = "租约使用报告"
    if lock_path:
        title += " — %s" % lock_path
    lines.append(title)
    win = report["window"]
    if win["start"] is not None:
        lines.append("时间范围: %s ~ %s" % (_fmt_ts(win["start"]), _fmt_ts(win["end"])))
    counts = report["event_counts"]
    lines.append("事件数: acquire=%d renew=%d release=%d timeout=%d corrupt=%d"
                 % (counts["acquire"], counts["renew"], counts["release"],
                    counts["acquire_timeout"], counts["corrupt_quarantine"]))

    lines.append("")
    lines.append("[加锁等待时长]")
    lines.extend(_fmt_stats("wait", report["wait_ms"]))
    if counts["acquire_timeout"]:
        lines.append("  获取超时: %d 次" % counts["acquire_timeout"])

    lines.append("")
    lines.append("[续期频率与剩余租约]")
    renew = report["renew"]
    lines.append("  续期总数: %d" % renew["total"])
    lines.extend(_fmt_stats("续期间隔", renew["interval_ms"]))
    lines.extend(_fmt_stats("续期时剩余租约", renew["remaining_ms"]))
    for pl in renew["per_lease"]:
        mean_iv = pl["mean_interval_ms"]
        lines.append("  holder=%s gen=%s: 续期 %d 次, 平均间隔 %s"
                     % (pl["holder"], pl["generation"], pl["renew_count"],
                        "%.1fms" % mean_iv if mean_iv is not None else "n/a"))

    lines.append("")
    lines.append("[抢占事件与代际变化]")
    if report["preemptions"]:
        for p in report["preemptions"]:
            lines.append("  %s  %s(gen=%d) 抢占 %s(gen=%d), 过期后 %.1fms 被回收"
                         % (_fmt_ts(p["ts"]), p["new_holder"], p["new_generation"],
                            p["prev_holder"], p["prev_generation"],
                            p["reclaim_delay_ms"]))
    else:
        lines.append("  无抢占事件")
    for g in report["generations"]:
        prev = g["prev_generation"]
        lines.append("  %s  代际 %s -> %d (holder=%s)"
                     % (_fmt_ts(g["ts"]), prev if prev is not None else "-",
                        g["generation"], g["holder"]))

    lines.append("")
    lines.append("[异常退出回收]")
    rec = report["reclamation"]
    lines.append("  回收次数: %d" % rec["count"])
    lines.extend(_fmt_stats("过期到回收延迟", rec["reclaim_delay_ms"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _parse_ts(text):
    try:
        return float(text)
    except ValueError:
        pass
    dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description="filelease 租约使用报告")
    ap.add_argument("lock", help="锁文件路径（事件流为 <锁文件>.events）")
    ap.add_argument("--start", help="起始时间：epoch 秒或 ISO 8601")
    ap.add_argument("--end", help="结束时间：epoch 秒或 ISO 8601")
    ap.add_argument("--json", action="store_true", help="以 JSON 导出")
    ap.add_argument("--check", action="store_true",
                    help="校验事件流与当前锁文件内容一致")
    args = ap.parse_args(argv)

    start = _parse_ts(args.start) if args.start else None
    end = _parse_ts(args.end) if args.end else None
    events = read_events(args.lock + ".events", start=start, end=end)
    report = build_report(events)
    report["lock_path"] = os.path.abspath(args.lock)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report, lock_path=args.lock))

    if args.check:
        ok, detail = check_consistency(args.lock, events=None)  # 全量事件对数
        print("\n[一致性校验] %s: %s" % ("通过" if ok else "失败", detail))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
