#!/usr/bin/env python3
"""从标注样本集重算各归一化策略的命中/误伤/P/R，并逐条比对策略差异。

指标与清单全部由本脚本现场扫描 ``eval/corpus.py`` 的人工标注样本得出，
不硬编码任何结果。固定开启词边界（``use_boundary=True``）并统一使用
语料自带白名单，使“策略之间的差异”只来自归一化配置。

输出：

1. 每个 profile：上报命中数 reported、真命中 TP、误伤 FP、漏报 FN、
   precision、recall，以及每条误伤/漏报所在样本与原文片段；
2. 策略间命中集合差异：相对基线（或 ``--diff-from`` 指定档）逐条列出
   新增命中（+，多为多抓；其中落在真值外的即为新增误伤）与消失命中
   （-，多为漏报），标注 TP/FP/FN 类别；
3. ``--json`` 导出机读结果，``--out`` 同时落盘文本报告。

用法：
    python3 eval/run_eval.py                      # 终端报告
    python3 eval/run_eval.py --json out.json     # 机读
    python3 eval/run_eval.py --out report.txt    # 落盘
    python3 eval/run_eval.py --diff-from raw homophone_on
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.corpus import SAMPLES, WHITELIST, WORDS, spans  # noqa: E402
from eval.profiles import PROFILE_BY_NAME, PROFILES, Profile  # noqa: E402
from sensitive import SensitiveEngine  # noqa: E402


@dataclass(frozen=True)
class HitRecord:
    """跨 profile 可比的一条命中：以原文区间为身份，与归一化配置无关。"""

    sample_id: str
    word: str
    start: int
    end: int

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.sample_id, self.word, self.start, self.end)


def scan_profile(profile: Profile) -> dict[str, list[HitRecord]]:
    """在该 profile 下扫描全部样本，返回 {sample_id: [HitRecord,...]}。"""
    eng = SensitiveEngine(
        WORDS,
        whitelist=WHITELIST,
        config=profile.config,
        use_boundary=True,
    )
    result: dict[str, list[HitRecord]] = {}
    for sample in SAMPLES:
        result[sample.id] = [
            HitRecord(sample.id, h.word, h.start, h.end)
            for h in eng.find_all(sample.text)
        ]
    return result


# 真值：{sample_id: set[HitRecord]}
EXPECTED: dict[str, set[HitRecord]] = {
    s.id: {HitRecord(s.id, w, a, b) for w, a, b in spans(s)} for s in SAMPLES
}
SAMPLE_BY_ID = {s.id: s for s in SAMPLES}


@dataclass
class Metrics:
    reported: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    fp_records: list[HitRecord]
    fn_records: list[HitRecord]


def evaluate(hits_by_sample: dict[str, list[HitRecord]]) -> Metrics:
    tp = fp = fn = 0
    fp_records: list[HitRecord] = []
    fn_records: list[HitRecord] = []
    for sample in SAMPLES:
        actual = set(hits_by_sample[sample.id])
        expected = EXPECTED[sample.id]
        tp += len(actual & expected)
        for rec in sorted(actual - expected, key=lambda r: r.key):
            fp += 1
            fp_records.append(rec)
        for rec in sorted(expected - actual, key=lambda r: r.key):
            fn += 1
            fn_records.append(rec)
    reported = tp + fp
    precision = tp / reported if reported else 1.0
    total_expected = tp + fn
    recall = tp / total_expected if total_expected else 1.0
    return Metrics(reported, tp, fp, fn, precision, recall, fp_records, fn_records)


def _snippet(rec: HitRecord) -> str:
    sample = SAMPLE_BY_ID[rec.sample_id]
    return sample.text[rec.start:rec.end]


def _fmt_record(rec: HitRecord) -> str:
    sample = SAMPLE_BY_ID[rec.sample_id]
    note = ""
    return (
        f"{rec.sample_id} 词={rec.word!r} [{rec.start}:{rec.end}] "
        f"原文片段={sample.text[rec.start:rec.end]!r}"
    )


def all_hit_set(hits_by_sample: dict[str, list[HitRecord]]) -> set[HitRecord]:
    out: set[HitRecord] = set()
    for recs in hits_by_sample.values():
        out.update(recs)
    return out


def build_report(
    scans: dict[str, dict[str, list[HitRecord]]],
    metrics: dict[str, Metrics],
    diff_from: str,
    diff_targets: list[str],
) -> str:
    lines: list[str] = []
    add = lines.append

    total_expected = sum(len(v) for v in EXPECTED.values())
    add("误伤评估报告（指标由 eval/run_eval.py 从 eval/corpus.py 标注集现场重算）")
    add(f"样本数={len(SAMPLES)}  真值命中数={total_expected}  "
        f"词表={len(WORDS)} 词  白名单={len(WHITELIST)} 词  边界=开启")
    add("")
    add("一、各策略命中/误伤对照（micro 汇总；身份=样本+词+原文区间）")
    add("-" * 104)
    add(f"{'profile':<15}{'reported':>9}{'TP':>5}{'FP(误伤)':>9}"
        f"{'FN(漏报)':>9}{'precision':>11}{'recall':>9}")
    for profile in PROFILES:
        m = metrics[profile.name]
        add(
            f"{profile.name:<15}{m.reported:>9}{m.tp:>5}{m.fp:>9}"
            f"{m.fn:>9}{m.precision:>10.1%} {m.recall:>8.1%}"
        )
    add("-" * 104)
    add("")

    add("二、被误伤样例清单（FP：上报但不在人工真值内）与漏报清单（FN）")
    for profile in PROFILES:
        m = metrics[profile.name]
        add(f"[{profile.name}] {profile.description}")
        if m.fp_records:
            add("  误伤 FP:")
            for rec in m.fp_records:
                add("    - " + _fmt_record(rec))
        else:
            add("  误伤 FP: 无")
        if m.fn_records:
            add("  漏报 FN:")
            for rec in m.fn_records:
                add("    - " + _fmt_record(rec))
        else:
            add("  漏报 FN: 无")
        add("")

    add(f"三、策略切换命中集合逐条差异（base={diff_from}，+新增 / -消失）")
    base_hits = all_hit_set(scans[diff_from])
    targets = diff_targets or [p.name for p in PROFILES if p.name != diff_from]
    for name in targets:
        if name == diff_from:
            continue
        target_hits = all_hit_set(scans[name])
        added = sorted(target_hits - base_hits, key=lambda r: r.key)
        removed = sorted(base_hits - target_hits, key=lambda r: r.key)
        add(f"[{diff_from} -> {name}]  +{len(added)} 条 / -{len(removed)} 条")
        expected_all = set().union(*EXPECTED.values())
        for rec in added:
            kind = "TP" if rec in expected_all else "FP"
            add(f"  + {kind} {_fmt_record(rec)}")
        for rec in removed:
            kind = "TP" if rec in expected_all else "FP"
            add(f"  - {kind}(原{'命中' if kind == 'TP' else '误伤'}消失) {_fmt_record(rec)}")
        if not added and not removed:
            add("  （命中集合无差异）")
        add("")
    return "\n".join(lines)


def build_json(
    scans: dict[str, dict[str, list[HitRecord]]],
    metrics: dict[str, Metrics],
    diff_from: str,
) -> dict:
    def rec_dict(rec: HitRecord) -> dict:
        return {
            "sample_id": rec.sample_id,
            "word": rec.word,
            "start": rec.start,
            "end": rec.end,
            "orig_text": _snippet(rec),
        }

    profiles_out = {}
    for profile in PROFILES:
        m = metrics[profile.name]
        profiles_out[profile.name] = {
            "description": profile.description,
            "reported": m.reported,
            "tp": m.tp,
            "fp": m.fp,
            "fn": m.fn,
            "precision": round(m.precision, 6),
            "recall": round(m.recall, 6),
            "fp_records": [rec_dict(r) for r in m.fp_records],
            "fn_records": [rec_dict(r) for r in m.fn_records],
            "hits": {
                sid: [rec_dict(r) for r in recs]
                for sid, recs in scans[profile.name].items()
                if recs
            },
        }

    base = all_hit_set(scans[diff_from])
    diffs = {}
    for profile in PROFILES:
        if profile.name == diff_from:
            continue
        target = all_hit_set(scans[profile.name])
        expected_all = set().union(*EXPECTED.values())
        diffs[profile.name] = {
            "added": [
                {**rec_dict(r), "kind": "TP" if r in expected_all else "FP"}
                for r in sorted(target - base, key=lambda r: r.key)
            ],
            "removed": [
                {**rec_dict(r), "kind": "TP" if r in expected_all else "FP"}
                for r in sorted(base - target, key=lambda r: r.key)
            ],
        }
    return {
        "corpus": {
            "samples": len(SAMPLES),
            "expected_hits": sum(len(v) for v in EXPECTED.values()),
            "words": WORDS,
            "whitelist": WHITELIST,
            "use_boundary": True,
        },
        "diff_from": diff_from,
        "profiles": profiles_out,
        "diffs": diffs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", metavar="PATH", help="导出机读 JSON 结果")
    parser.add_argument("--out", metavar="PATH", help="文本报告落盘路径")
    parser.add_argument(
        "--diff-from",
        default="base_default",
        choices=sorted(PROFILE_BY_NAME),
        help="差异比对的基准档（默认 base_default）",
    )
    parser.add_argument(
        "diff_targets",
        nargs="*",
        help="只输出这些目标档相对基准的差异（默认全部）",
    )
    args = parser.parse_args(argv)

    scans = {p.name: scan_profile(p) for p in PROFILES}
    metrics = {name: evaluate(hits) for name, hits in scans.items()}
    report = build_report(scans, metrics, args.diff_from, args.diff_targets)
    print(report)

    if args.json:
        Path(args.json).write_text(
            json.dumps(build_json(scans, metrics, args.diff_from),
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n[json 已写入 {args.json}]", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"[报告已写入 {args.out}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
