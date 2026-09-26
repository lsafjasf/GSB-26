"""评测脚本：混淆矩阵、阈值扫描、吞吐测试，结果写入 REPORT.md。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sensfind import Engine

TYPES = ["id_card", "bank_card", "mobile"]
LABELS = TYPES + ["NONE"]
DATASET = os.path.join(os.path.dirname(__file__), "..", "data", "dataset.jsonl")
REPORT = os.path.join(os.path.dirname(__file__), "..", "REPORT.md")


def load_dataset():
    with open(DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate(records, threshold):
    engine = Engine(threshold=threshold)
    matrix = {g: {p: 0 for p in LABELS} for g in LABELS}
    tp = {t: 0 for t in TYPES}
    fp = {t: 0 for t in TYPES}
    fn = {t: 0 for t in TYPES}
    n_conflicts = 0
    for rec in records:
        result = engine.scan(rec["text"])
        n_conflicts += len(result.conflicts)
        preds = [{"type": f.type, "value": f.value} for f in result.findings]
        used = [False] * len(preds)
        for gold in rec["gold"]:
            hit = next((i for i, p in enumerate(preds)
                        if not used[i] and p["value"] == gold["value"]), None)
            if hit is None:
                fn[gold["type"]] += 1
                matrix[gold["type"]]["NONE"] += 1
            else:
                used[hit] = True
                pred_type = preds[hit]["type"]
                matrix[gold["type"]][pred_type] += 1
                if pred_type == gold["type"]:
                    tp[gold["type"]] += 1
                else:
                    fn[gold["type"]] += 1
                    fp[pred_type] += 1
        for i, p in enumerate(preds):
            if not used[i]:
                fp[p["type"]] += 1
                matrix["NONE"][p["type"]] += 1
    return matrix, tp, fp, fn, n_conflicts


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def fmt_matrix(matrix):
    header = "| gold \\ pred | " + " | ".join(LABELS) + " |"
    sep = "|" + "---|" * (len(LABELS) + 1)
    rows = [header, sep]
    for g in LABELS:
        rows.append("| %s | %s |" % (g, " | ".join(str(matrix[g][p]) for p in LABELS)))
    return "\n".join(rows)


def _conflict_kinds(records):
    engine = Engine()
    kinds = {}
    for rec in records:
        for c in engine.scan(rec["text"]).conflicts:
            key = "%s 胜出 vs %s 被弃" % (c["kept"]["type"], c["dropped"]["type"])
            kinds[key] = kinds.get(key, 0) + 1
    return "，".join("%s %d 条" % (k, v) for k, v in sorted(kinds.items()))


def main():
    records = load_dataset()
    lines = []
    lines.append("# sensfind 评测报告")
    lines.append("")
    lines.append("数据集：`data/dataset.jsonl`，共 %d 条样本，正例实体 %d 个，负样本 %d 条。"
                 % (len(records), sum(len(r["gold"]) for r in records),
                    sum(1 for r in records if not r["gold"])))
    lines.append("")

    # ---- 默认阈值 0.6 ----
    matrix, tp, fp, fn, n_conflicts = evaluate(records, 0.6)
    lines.append("## 混淆矩阵（阈值 0.6）")
    lines.append("")
    lines.append(fmt_matrix(matrix))
    lines.append("")
    lines.append("| 类型 | TP | FP | FN | Precision | Recall | F1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in TYPES:
        p, r, f = prf(tp[t], fp[t], fn[t])
        lines.append("| %s | %d | %d | %d | %.4f | %.4f | %.4f |" % (t, tp[t], fp[t], fn[t], p, r, f))
    TP, FP, FN = sum(tp.values()), sum(fp.values()), sum(fn.values())
    p, r, f = prf(TP, FP, FN)
    lines.append("| **micro** | %d | %d | %d | %.4f | %.4f | %.4f |" % (TP, FP, FN, p, r, f))
    lines.append("")
    lines.append("冲突消解记录数：%d（构成为 %s）"
                 % (n_conflicts, _conflict_kinds(records)))
    lines.append("")

    # ---- 阈值扫描 ----
    lines.append("## 阈值扫描")
    lines.append("")
    lines.append("| 阈值 | TP | FP | FN | Precision | Recall | F1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in (0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
        _, tp2, fp2, fn2, _ = evaluate(records, t)
        TP, FP, FN = sum(tp2.values()), sum(fp2.values()), sum(fn2.values())
        p, r, f = prf(TP, FP, FN)
        lines.append("| %.2f | %d | %d | %d | %.4f | %.4f | %.4f |" % (t, TP, FP, FN, p, r, f))
    lines.append("")

    # ---- 吞吐 ----
    lines.append("## 吞吐")
    lines.append("")
    engine = Engine()
    base = "\n".join(r["text"] for r in records)
    big = base
    while len(big.encode("utf-8")) < 8 * 1024 * 1024:
        big += "\n" + big
    t0 = time.perf_counter()
    engine.scan(big)
    dt = time.perf_counter() - t0
    mb = len(big.encode("utf-8")) / 1e6
    lines.append("- 混合文本 %.1f MB：耗时 %.2f s，吞吐 **%.1f MB/s**" % (mb, dt, mb / dt))
    noise = ("时间戳 20260926123045 订单 ORDER202600123456 金额 1234.56 元\n" * 1)
    while len(noise.encode("utf-8")) < 8 * 1024 * 1024:
        noise += noise
    t0 = time.perf_counter()
    engine.scan(noise)
    dt = time.perf_counter() - t0
    mb = len(noise.encode("utf-8")) / 1e6
    lines.append("- 纯噪声数字文本 %.1f MB：耗时 %.2f s，吞吐 **%.1f MB/s**" % (mb, dt, mb / dt))
    lines.append("")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("\n已写入 %s" % os.path.abspath(REPORT))


if __name__ == "__main__":
    main()
