"""自测：边界情形 + 功能正确性。运行：python3 tests/selftest.py"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from sensfind import Engine
from sensfind.detectors import id_check_char, luhn_ok, luhn_check_digit
import run_eval

PASSED = []


def check(name, cond, detail=""):
    assert cond, "FAIL: %s %s" % (name, detail)
    PASSED.append(name)
    print("PASS %s %s" % (name, detail))


engine = Engine()

# 1. 空文本 / 纯空白
r = engine.scan("")
check("空文本", r.findings == [] and r.rejected == [] and r.stats["chars"] == 0)
r = engine.scan("   \n\t  ")
check("纯空白文本", r.findings == [])

# 2. 编码异常字符（孤立代理项、零宽字符）不崩溃
r = engine.scan("乱码\ud800\udfff测试​文本")
check("异常字符不崩溃", isinstance(r.findings, list))

# 3. 零宽字符插入号码中间仍可识别，且位置映射回原文
body = "11010119900307793"
vid = body + id_check_char(body)
text = "证件号" + vid[:6] + "​" + vid[6:]
r = engine.scan(text)
check("零宽字符内嵌身份证", len(r.findings) == 1 and r.findings[0].value == vid)
f = r.findings[0]
check("位置映射回原文", text[f.start:f.end] == vid[:6] + "​" + vid[6:])

# 4. 含换行与分隔符的卡号
card_body = "62220212345678901"
card = card_body + luhn_check_digit(card_body)
text = "卡号分两段：%s\n%s" % (card[:8], card[8:])
r = engine.scan(text)
check("换行分隔卡号", any(f.type == "bank_card" and f.value == card for f in r.findings))
text = "卡号 %s" % "-".join(card[i:i + 4] for i in range(0, len(card), 4))
r = engine.scan(text)
check("短横线分组卡号", any(f.type == "bank_card" and f.value == card for f in r.findings))

# 5. 校验否定原因可追溯
bad_id = vid[:-1] + ("0" if vid[-1] != "0" else "1")
r = engine.scan("身份证号：" + bad_id)
check("坏校验位被否定", r.findings == [] and any(
    x["stage"] == "checksum" for x in r.rejected), r.rejected)
r = engine.scan("电话 12345678901")
check("无效号段被否定", r.findings == [] and any(
    x["stage"] == "prefix" for x in r.rejected))

# 6. 冲突消解：甘肃身份证(62 开头)同时通过 Luhn
rng = random.Random(0)
while True:
    b = "62010219900101%03d" % rng.randint(0, 999)
    overlap_id = b + id_check_char(b)
    if overlap_id[-1].isdigit() and luhn_ok(overlap_id):
        break
r = engine.scan("证件号码 %s 已登记" % overlap_id)
check("冲突只保留一个命中", len(r.findings) == 1 and r.findings[0].type == "id_card")
check("冲突被记录", len(r.conflicts) == 1
      and r.conflicts[0]["dropped"]["type"] == "bank_card")

# 7. 阈值行为：负向上下文中的手机号在低阈值下可放行
text = "售后工单编号 13812345678 请跟进"
check("负向上下文被阈值抑制", engine.scan(text).findings == [])
r = Engine(threshold=0.4).scan(text)
check("降低阈值后放行", len(r.findings) == 1 and r.findings[0].type == "mobile")

# 8. 超大文本吞吐与正确性
unit = "联系电话：13812345678，身份证号：%s。\n" % vid
big = unit * (5 * 1024 * 1024 // len(unit.encode("utf-8")))
t0 = time.perf_counter()
r = engine.scan(big)
dt = time.perf_counter() - t0
mb = len(big.encode("utf-8")) / 1e6
expected = big.count("13812345678")
got = sum(1 for f in r.findings if f.type == "mobile")
check("超大文本不丢命中", got == expected, "%d/%d" % (got, expected))
check("超大文本耗时合理", dt < 60, "%.1f MB 用时 %.1f s (%.1f MB/s)" % (mb, dt, mb / dt))
print("\n吞吐参考: %.1f MB 用时 %.2f s = %.1f MB/s" % (mb, dt, mb / dt))

# 9. 评测指标口径不变量（从数据集重算，可复现）
records = run_eval.load_dataset()
_, tp, fp, fn, tn, per_sample, conflicts = run_eval.evaluate(records, 0.6)
TP, FP, FN, TN = sum(tp.values()), sum(fp.values()), sum(fn.values()), sum(tn.values())
check("默认阈值零误报零漏报", (TP, FP, FN) == (330, 0, 0),
      "TP=%d FP=%d FN=%d" % (TP, FP, FN))
fpr, fnr = run_eval.fpr_fnr(TP, FP, FN, TN)
check("误报率/漏报率为零", fpr == 0.0 and fnr == 0.0, "FPR=%.4f FNR=%.4f" % (fpr, fnr))
_, tp2, fp2, fn2, tn2, _, _ = run_eval.evaluate(records, 0.4)
fpr4, fnr4 = run_eval.fpr_fnr(sum(tp2.values()), sum(fp2.values()),
                              sum(fn2.values()), sum(tn2.values()))
check("低阈值误报率上升", sum(fp2.values()) == 10 and fpr4 > 0.0 and fnr4 == 0.0,
      "FP=%d FPR=%.4f" % (sum(fp2.values()), fpr4))
_, tp9, fp9, fn9, tn9, _, _ = run_eval.evaluate(records, 0.9)
fpr9, fnr9 = run_eval.fpr_fnr(sum(tp9.values()), sum(fp9.values()),
                              sum(fn9.values()), sum(tn9.values()))
check("高阈值漏报率上升", sum(fn9.values()) == 80 and fnr9 > 0.0 and fpr9 == 0.0,
      "FN=%d FNR=%.4f" % (sum(fn9.values()), fnr9))
p9, r9, _ = run_eval.prf(sum(tp9.values()), sum(fp9.values()), sum(fn9.values()))
check("漏报率 = 1 - 召回率", abs(fnr9 - (1 - r9)) < 1e-12)
check("逐样本结论覆盖全部样本", len(per_sample) == len(records)
      and sorted(s["id"] for s in per_sample) == [r["id"] for r in records])
check("消解记录逐条可追溯", len(conflicts) == 16
      and all("id" in c and "kept" in c and "dropped" in c and "rule" in c
              for c in conflicts))

print("\n全部 %d 项自测通过" % len(PASSED))
