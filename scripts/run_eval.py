"""评测脚本：混淆矩阵、误报率/漏报率、阈值扫描、冲突消解记录、吞吐。

所有指标均由脚本从 data/dataset.jsonl 重算（数据集由固定种子的
gen_dataset.py 生成），不依赖任何手工填写的数字，可复现。

输出：
- REPORT.md                 指标汇总（混淆矩阵、P/R/F1、误报率、漏报率、
                            阈值多档扫描、冲突消解记录、吞吐）
- eval_out/per_sample.jsonl 每个扫描档位下逐样本结论（tp/fp/fn 明细），
                            按 (threshold, id) 排序，可用 diff 逐条比对
- eval_out/conflicts.jsonl  规则重叠命中的消解记录（默认阈值 0.6，逐条）

用法：
    python3 scripts/run_eval.py                      # 全量评测并写报告
    python3 scripts/run_eval.py --compare 0.6 0.4    # 逐样本比对两个阈值档

指标口径：
- TP/FP/FN 为实体级计数（按 gold value 匹配）；
- TN 为样本×类型级计数：某样本某类型既无 gold 也无预测时记 1；
- 误报率 FPR = FP / (FP + TN)；漏报率 FNR = FN / (TP + FN) = 1 - Recall。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sensfind import Engine

TYPES = ["id_card", "bank_card", "mobile"]
LABELS = TYPES + ["NONE"]
THRESHOLDS = (0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
DEFAULT_THRESHOLD = 0.60
DATASET = os.path.join(os.path.dirname(__file__), "..", "data", "dataset.jsonl")
REPORT = os.path.join(os.path.dirname(__file__), "..", "REPORT.md")
EVAL_OUT = os.path.join(os.path.dirname(__file__), "..", "eval_out")


def load_dataset():
    with open(DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate(records, threshold):
    """返回 (matrix, tp, fp, fn, tn, per_sample, conflicts)。

    per_sample: 逐样本结论 [{"id", "tp", "fp", "fn"}]，值为 "type:value" 列表；
    conflicts: 逐条消解记录 [{"id", "kept", "dropped", "rule"}]。
    """
    engine = Engine(threshold=threshold)
    matrix = {g: {p: 0 for p in LABELS} for g in LABELS}
    tp = {t: 0 for t in TYPES}
    fp = {t: 0 for t in TYPES}
    fn = {t: 0 for t in TYPES}
    tn = {t: 0 for t in TYPES}
    per_sample = []
    conflicts = []
    for rec in records:
        result = engine.scan(rec["text"])
        for c in result.conflicts:
            conflicts.append({"id": rec["id"], "kept": c["kept"],
                              "dropped": c["dropped"], "rule": c["rule"]})
        preds = [{"type": f.type, "value": f.value} for f in result.findings]
        used = [False] * len(preds)
        s_tp, s_fp, s_fn = [], [], []
        for gold in rec["gold"]:
            hit = next((i for i, p in enumerate(preds)
                        if not used[i] and p["value"] == gold["value"]), None)
            if hit is None:
                fn[gold["type"]] += 1
                matrix[gold["type"]]["NONE"] += 1
                s_fn.append("%s:%s" % (gold["type"], gold["value"]))
            else:
                used[hit] = True
                pred_type = preds[hit]["type"]
                matrix[gold["type"]][pred_type] += 1
                if pred_type == gold["type"]:
                    tp[gold["type"]] += 1
                    s_tp.append("%s:%s" % (gold["type"], gold["value"]))
                else:
                    fn[gold["type"]] += 1
                    fp[pred_type] += 1
                    s_fn.append("%s:%s" % (gold["type"], gold["value"]))
                    s_fp.append("%s:%s" % (pred_type, gold["value"]))
        for i, p in enumerate(preds):
            if not used[i]:
                fp[p["type"]] += 1
                matrix["NONE"][p["type"]] += 1
                s_fp.append("%s:%s" % (p["type"], p["value"]))
        gold_types = {g["type"] for g in rec["gold"]}
        pred_types = {p["type"] for p in preds}
        for t in TYPES:
            if t not in gold_types and t not in pred_types:
                tn[t] += 1
        per_sample.append({"id": rec["id"], "tp": sorted(s_tp),
                           "fp": sorted(s_fp), "fn": sorted(s_fn)})
    return matrix, tp, fp, fn, tn, per_sample, conflicts


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def fpr_fnr(tp, fp, fn, tn):
    fpr = fp / (fp + tn) if fp + tn else 0.0
    fnr = fn / (tp + fn) if tp + fn else 0.0
    return fpr, fnr


def fmt_matrix(matrix):
    header = "| gold \\ pred | " + " | ".join(LABELS) + " |"
    sep = "|" + "---|" * (len(LABELS) + 1)
    rows = [header, sep]
    for g in LABELS:
        rows.append("| %s | %s |" % (g, " | ".join(str(matrix[g][p]) for p in LABELS)))
    return "\n".join(rows)


def write_per_sample(records):
    """逐样本结论写入 eval_out/per_sample.jsonl，按 (threshold, id) 排序。"""
    os.makedirs(EVAL_OUT, exist_ok=True)
    path = os.path.join(EVAL_OUT, "per_sample.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for t in THRESHOLDS:
            _, _, _, _, _, per_sample, _ = evaluate(records, t)
            for s in sorted(per_sample, key=lambda x: x["id"]):
                row = {"threshold": t, "id": s["id"],
                       "tp": s["tp"], "fp": s["fp"], "fn": s["fn"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def write_conflicts(conflicts):
    os.makedirs(EVAL_OUT, exist_ok=True)
    path = os.path.join(EVAL_OUT, "conflicts.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for c in conflicts:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return path


def compare_thresholds(records, t1, t2):
    """逐样本比对两个阈值档的结论差异，输出到 stdout。"""
    _, _, _, _, _, ps1, _ = evaluate(records, t1)
    _, _, _, _, _, ps2, _ = evaluate(records, t2)
    by_id1 = {s["id"]: s for s in ps1}
    by_id2 = {s["id"]: s for s in ps2}
    n_diff = 0
    for rid in sorted(by_id1):
        a, b = by_id1[rid], by_id2[rid]
        if a == b:
            continue
        n_diff += 1
        print("id=%d 阈值 %.2f -> %.2f：" % (rid, t1, t2))
        for key in ("tp", "fp", "fn"):
            added = [x for x in b[key] if x not in a[key]]
            removed = [x for x in a[key] if x not in b[key]]
            for x in removed:
                print("  - %s %s" % (key, x))
            for x in added:
                print("  + %s %s" % (key, x))
    print("\n共 %d/%d 条样本结论发生变化（%.2f vs %.2f）"
          % (n_diff, len(by_id1), t1, t2))


def main():
    if "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        t1, t2 = float(sys.argv[i + 1]), float(sys.argv[i + 2])
        compare_thresholds(load_dataset(), t1, t2)
        return

    records = load_dataset()
    lines = []
    lines.append("# sensfind 评测报告")
    lines.append("")
    lines.append("数据集：`data/dataset.jsonl`，共 %d 条样本，正例实体 %d 个，负样本 %d 条。"
                 "所有指标由 `scripts/run_eval.py` 从数据集重算，可复现。"
                 % (len(records), sum(len(r["gold"]) for r in records),
                    sum(1 for r in records if not r["gold"])))
    lines.append("")
    lines.append("指标口径：TP/FP/FN 为实体级计数；TN 为样本×类型级计数；"
                 "误报率 FPR = FP/(FP+TN)；漏报率 FNR = FN/(TP+FN) = 1 - Recall。")
    lines.append("")

    # ---- 默认阈值 ----
    matrix, tp, fp, fn, tn, per_sample, conflicts = evaluate(records, DEFAULT_THRESHOLD)
    lines.append("## 混淆矩阵（阈值 %.2f）" % DEFAULT_THRESHOLD)
    lines.append("")
    lines.append(fmt_matrix(matrix))
    lines.append("")
    lines.append("| 类型 | TP | FP | FN | TN | Precision | Recall | F1 | 误报率FPR | 漏报率FNR |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for t in TYPES:
        p, r, f = prf(tp[t], fp[t], fn[t])
        fpr, fnr = fpr_fnr(tp[t], fp[t], fn[t], tn[t])
        lines.append("| %s | %d | %d | %d | %d | %.4f | %.4f | %.4f | %.4f | %.4f |"
                     % (t, tp[t], fp[t], fn[t], tn[t], p, r, f, fpr, fnr))
    TP = sum(tp.values()); FP = sum(fp.values()); FN = sum(fn.values()); TN = sum(tn.values())
    p, r, f = prf(TP, FP, FN)
    fpr, fnr = fpr_fnr(TP, FP, FN, TN)
    lines.append("| **micro** | %d | %d | %d | %d | %.4f | %.4f | %.4f | %.4f | %.4f |"
                 % (TP, FP, FN, TN, p, r, f, fpr, fnr))
    lines.append("")

    # ---- 冲突消解记录（逐条） ----
    kinds = {}
    for c in conflicts:
        key = "%s 胜出 vs %s 被弃" % (c["kept"]["type"], c["dropped"]["type"])
        kinds[key] = kinds.get(key, 0) + 1
    lines.append("## 规则重叠命中的消解记录（阈值 %.2f）" % DEFAULT_THRESHOLD)
    lines.append("")
    lines.append("共 %d 条（构成为 %s）。消解规则：置信度高者优先；并列时跨度长者优先；"
                 "再并列时类型优先级 id_card > bank_card > mobile；再并列时起始位置靠前者优先。"
                 % (len(conflicts), "，".join("%s %d 条" % (k, v) for k, v in sorted(kinds.items()))))
    lines.append("")
    lines.append("| 样本id | 保留类型 | 保留值 | 保留置信度 | 弃置类型 | 弃置值 | 弃置置信度 |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in conflicts:
        lines.append("| %d | %s | %s | %.4f | %s | %s | %.4f |"
                     % (c["id"], c["kept"]["type"], c["kept"]["value"],
                        c["kept"]["confidence"], c["dropped"]["type"],
                        c["dropped"]["value"], c["dropped"]["confidence"]))
    lines.append("")
    lines.append("逐条记录同时写入 `eval_out/conflicts.jsonl`（含消解规则文本）。")
    lines.append("")

    # ---- 阈值多档扫描 ----
    lines.append("## 阈值多档扫描")
    lines.append("")
    lines.append("| 阈值 | TP | FP | FN | TN | Precision | Recall | F1 | 误报率FPR | 漏报率FNR |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for t in THRESHOLDS:
        _, tp2, fp2, fn2, tn2, _, _ = evaluate(records, t)
        TP = sum(tp2.values()); FP = sum(fp2.values())
        FN = sum(fn2.values()); TN = sum(tn2.values())
        p, r, f = prf(TP, FP, FN)
        fpr, fnr = fpr_fnr(TP, FP, FN, TN)
        lines.append("| %.2f | %d | %d | %d | %d | %.4f | %.4f | %.4f | %.4f | %.4f |"
                     % (t, TP, FP, FN, TN, p, r, f, fpr, fnr))
    lines.append("")
    lines.append("每档逐样本结论见 `eval_out/per_sample.jsonl`（按 threshold、id 排序，"
                 "可用 diff 逐条比对）；两档差异可用 "
                 "`python3 scripts/run_eval.py --compare 0.6 0.4` 直接查看。")
    lines.append("")

    # ---- 逐样本/冲突文件 ----
    ps_path = write_per_sample(records)
    cf_path = write_conflicts(conflicts)

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
    print("已写入 %s" % os.path.abspath(ps_path))
    print("已写入 %s" % os.path.abspath(cf_path))


if __name__ == "__main__":
    main()
