"""评估：在构造数据集上计算混淆矩阵、误报率/漏报率，并做阈值扫描。

实体级判定：
  - 命中与某正样本实体类型一致且字符重叠覆盖其实体跨度 >=50% -> TP
  - 命中落在负样本实体上（或类型不符），或不落在任何实体上 -> FP
  - 正样本实体未被任何同类型命中覆盖 -> FN
  - 负样本实体未被任何命中覆盖 -> TN
"""
from __future__ import annotations

import csv
import os

from dataset import Entity, build_dataset
from sensid import Scanner

OVERLAP_RATIO = 0.5
THRESHOLDS = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def evaluate(text: str, entities: list[Entity], threshold: float) -> dict:
    result = Scanner(threshold=threshold).scan(text)
    tp = fp = fn = tn = 0
    matched: set[int] = set()
    per_type: dict[str, dict[str, int]] = {}

    def bucket(etype: str) -> dict[str, int]:
        return per_type.setdefault(etype, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})

    for m in result.matches:
        hit_idx = None
        for i, e in enumerate(entities):
            ov = _overlap(m.start, m.end, e.start, e.end)
            if ov > 0 and ov / (e.end - e.start) >= OVERLAP_RATIO:
                hit_idx = i
                break
        if hit_idx is not None:
            e = entities[hit_idx]
            matched.add(hit_idx)
            if e.expected and e.etype == m.type:
                tp += 1
                bucket(e.etype)["tp"] += 1
            else:
                fp += 1
                bucket(m.type)["fp"] += 1
        else:
            fp += 1
            bucket(m.type)["fp"] += 1

    for i, e in enumerate(entities):
        if i in matched:
            continue
        if e.expected:
            fn += 1
            bucket(e.etype)["fn"] += 1
        else:
            tn += 1
            bucket(e.etype)["tn"] += 1

    def safe(a: float, b: float) -> float:
        return a / b if b else 0.0

    return {
        "threshold": threshold,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": safe(tp, tp + fp),
        "recall": safe(tp, tp + fn),
        "fpr": safe(fp, fp + tn),
        "fnr": safe(fn, fn + tp),
        "per_type": per_type,
        "n_matches": len(result.matches),
        "n_rejected": len(result.rejected),
        "n_conflicts": len(result.conflicts),
        "elapsed_ms": result.elapsed_ms,
    }


def main() -> None:
    text, entities = build_dataset()
    os.makedirs("results", exist_ok=True)

    rows = [evaluate(text, entities, t) for t in THRESHOLDS]
    base = next(r for r in rows if r["threshold"] == 0.50)

    with open("results/confusion_matrix.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scope", "tp", "fp", "fn", "tn", "precision", "recall", "fpr", "fnr"])
        w.writerow(["overall", base["tp"], base["fp"], base["fn"], base["tn"],
                    f"{base['precision']:.4f}", f"{base['recall']:.4f}",
                    f"{base['fpr']:.4f}", f"{base['fnr']:.4f}"])
        for etype in ("id_card", "bank_card", "phone", "passport"):
            c = base["per_type"].get(etype, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
            p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
            r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
            w.writerow([etype, c["tp"], c["fp"], c["fn"], c["tn"],
                        f"{p:.4f}", f"{r:.4f}", "", ""])

    lines = [
        "# 评估报告（构造数据集，seed=42）",
        "",
        f"- 文本长度：{len(text)} 字符",
        f"- 实体总数：{len(entities)}（正样本 {sum(1 for e in entities if e.expected)}，"
        f"负样本 {sum(1 for e in entities if not e.expected)}）",
        f"- 冲突消解记录数（阈值0.5）：{base['n_conflicts']}",
        f"- 被否定候选数（阈值0.5）：{base['n_rejected']}",
        "",
        "## 混淆矩阵（阈值 = 0.50，实体级）",
        "",
        "| 范围 | TP | FP | FN | TN | 精确率 | 召回率 | 误报率FPR | 漏报率FNR |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| 总体 | {base['tp']} | {base['fp']} | {base['fn']} | {base['tn']} "
        f"| {base['precision']:.4f} | {base['recall']:.4f} "
        f"| {base['fpr']:.4f} | {base['fnr']:.4f} |",
    ]
    for etype in ("id_card", "bank_card", "phone", "passport"):
        c = base["per_type"].get(etype, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
        r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
        lines.append(
            f"| {etype} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} "
            f"| {p:.4f} | {r:.4f} | - | - |"
        )
    lines += [
        "",
        "## 阈值扫描（总体指标随阈值变化）",
        "",
        "| 阈值 | TP | FP | FN | TN | 精确率 | 召回率 | 误报率FPR | 漏报率FNR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['threshold']:.2f} | {r['tp']} | {r['fp']} | {r['fn']} | {r['tn']} "
            f"| {r['precision']:.4f} | {r['recall']:.4f} | {r['fpr']:.4f} | {r['fnr']:.4f} |"
        )
    with open("results/evaluation.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
