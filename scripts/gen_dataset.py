"""构造评测数据集 data/dataset.jsonl（随机种子固定，可复现）。

每条记录：{"id", "text", "gold": [{"type", "value"}], "note"}
gold 为空表示该样本不应有任何命中（用于量化误报）。
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sensfind.detectors import (id_check_char, luhn_check_digit, luhn_ok,
                                _iin_brand)


def is_card_like(digits):
    return digits.isdigit() and luhn_ok(digits) and _iin_brand(digits) is not None

rng = random.Random(42)
records = []


def add(text, gold, note):
    records.append({"id": len(records), "text": text, "gold": gold, "note": note})


def make_id(region=None, year=None, month=None, day=None):
    region = region or rng.choice(["11", "13", "32", "44", "51", "65"])
    year = year if year is not None else rng.randint(1960, 2005)
    month = month if month is not None else rng.randint(1, 12)
    day = day if day is not None else rng.randint(1, 28)
    body = "%s%04d%04d%02d%02d%03d" % (
        region, rng.randint(0, 9999), year, month, day, rng.randint(0, 999))
    return body + id_check_char(body)


def make_card(iin=None, length=None):
    iin = iin or rng.choice(["622202", "621700", "622848", "436742", "520108", "356889"])
    length = length or rng.choice([16, 16, 16, 19])
    body = iin + "".join(rng.choice("0123456789") for _ in range(length - len(iin) - 1))
    return body + luhn_check_digit(body)


def make_mobile():
    prefix = rng.choice(["134", "138", "139", "150", "156", "158", "176", "186", "199"])
    return prefix + "".join(rng.choice("0123456789") for _ in range(8))


def group4(digits, sep):
    return sep.join(digits[i:i + 4] for i in range(0, len(digits), 4))


def mobile_344(digits, sep):
    return digits[:3] + sep + digits[3:7] + sep + digits[7:]


# ---------------- 正样本：身份证 ----------------
for i in range(120):
    vid = make_id()
    gold = [{"type": "id_card", "value": vid}]
    kind = i % 6
    if kind == 0:
        add("身份证号：%s，请妥善保管。" % vid, gold, "id/带上下文")
    elif kind == 1:
        add("姓名：张三，证件号码 %s 已核实。" % vid, gold, "id/带上下文")
    elif kind == 2:
        add("登记信息 %s 完成" % vid, gold, "id/无上下文")
    elif kind == 3:
        pos = rng.randint(1, 17)
        zw = rng.choice(["​", "‌", "﻿"])
        add("证件号%s%s%s" % (vid[:pos], zw, vid[pos:]), gold, "id/含零宽字符")
    elif kind == 4:
        add("第一行文本\n公民身份号码%s\n第三行文本" % vid, gold, "id/跨行文本")
    else:
        add(vid, gold, "id/裸号码")

# ---------------- 正样本：银行卡 ----------------
for i in range(100):
    card = make_card()
    gold = [{"type": "bank_card", "value": card}]
    kind = i % 5
    if kind == 0:
        add("银行卡号：%s" % card, gold, "card/带上下文")
    elif kind == 1:
        add("收款账户 %s 开户行招商银行" % group4(card, " "), gold, "card/空格分组")
    elif kind == 2:
        add("卡号 %s" % group4(card, "-"), gold, "card/短横线分组")
    elif kind == 3:
        add("卡号分两段记录：%s\n%s" % (card[:8], card[8:]), gold, "card/换行分隔")
    else:
        add(card, gold, "card/裸号码")

# ---------------- 正样本：手机号 ----------------
for i in range(100):
    mob = make_mobile()
    gold = [{"type": "mobile", "value": mob}]
    kind = i % 5
    if kind == 0:
        add("联系电话：%s" % mob, gold, "mobile/带上下文")
    elif kind == 1:
        add("手机 %s 微信同号" % mobile_344(mob, " "), gold, "mobile/3-4-4空格")
    elif kind == 2:
        add("联系方式 %s" % mobile_344(mob, "-"), gold, "mobile/3-4-4短横线")
    elif kind == 3:
        add("紧急联系人手机：\n%s" % mobile_344(mob, " "), gold, "mobile/换行+分隔")
    else:
        add(mob, gold, "mobile/裸号码")

# ---------------- 负样本：身份证近 miss ----------------
for i in range(40):  # 校验位错误
    while True:
        vid = make_id()
        bad_last = rng.choice([c for c in "0123456789X" if c != vid[-1]])
        if not is_card_like(vid[:-1] + bad_last):
            break
    add("身份证号：%s%s" % (vid[:-1], bad_last), [], "neg/id校验位错误")
for i in range(20):  # 日期非法
    while True:
        vid = make_id(month=13)
        if not is_card_like(vid):
            break
    add("证件号 %s" % vid, [], "neg/id日期非法")
for i in range(20):  # 区划非法
    while True:
        vid = make_id(region=rng.choice(["00", "99", "18"]))
        if not is_card_like(vid):
            break
    add("身份证 %s" % vid, [], "neg/id区划非法")

# ---------------- 负样本：银行卡近 miss ----------------
for i in range(25):  # Luhn 失败
    card = make_card()
    pos = rng.randint(6, len(card) - 1)
    bad = card[:pos] + rng.choice([d for d in "0123456789" if d != card[pos]]) + card[pos + 1:]
    if not luhn_ok(bad):
        add("卡号 %s" % bad, [], "neg/card Luhn失败")
for i in range(20):  # IIN 未知（Luhn 通过）
    body = "99" + "".join(rng.choice("0123456789") for _ in range(13))
    add("账号 %s" % (body + luhn_check_digit(body)), [], "neg/card IIN未知")
for i in range(10):  # 长度不足
    add("卡号 %s" % "".join(rng.choice("0123456789") for _ in range(12)), [], "neg/card 长度不足")

# ---------------- 负样本：手机号近 miss ----------------
for i in range(20):  # 号段无效
    mob = rng.choice(["10", "11", "12"]) + "".join(rng.choice("0123456789") for _ in range(9))
    add("电话 %s" % mob, [], "neg/mobile 号段无效")
for i in range(15):  # 长度错误
    mob = "13" + "".join(rng.choice("0123456789") for _ in range(rng.choice([7, 10])))
    add("手机 %s" % mob, [], "neg/mobile 长度错误")
for i in range(10):  # 负向上下文中的类手机号（工单编号，期望被抑制）
    mob = make_mobile()
    add("售后工单编号 %s 请跟进处理" % mob, [], "neg/mobile 负向上下文")

# ---------------- 负样本：良性数字 ----------------
for i in range(40):
    kind = i % 5
    if kind == 0:
        add("时间戳 2026%02d%02d%02d%02d%02d 已记录"
            % (rng.randint(1, 12), rng.randint(1, 28),
               rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)), [], "neg/时间戳")
    elif kind == 1:
        add("订单号 ORDER2026%08d 已支付" % rng.randint(0, 99999999), [], "neg/订单号")
    elif kind == 2:
        add("金额 %d.%02d 元" % (rng.randint(1, 999999), rng.randint(0, 99)), [], "neg/金额")
    elif kind == 3:
        add("固话 010-%08d 已停用" % rng.randint(0, 99999999), [], "neg/固话")
    else:
        add("2026年%02d月%02d日 批次%06d" % (rng.randint(1, 12), rng.randint(1, 28),
                                              rng.randint(0, 999999)), [], "neg/日期批次")

# ---------------- 重叠冲突样本：同时是合法身份证且通过 Luhn ----------------
count = 0
while count < 10:
    vid = make_id(region="62")  # 甘肃区划 62 同时是银联 IIN，构成真实冲突
    if vid[-1].isdigit() and luhn_ok(vid):
        add("证件号码 %s 已登记" % vid, [{"type": "id_card", "value": vid}], "overlap/id同时通过Luhn")
        count += 1

out = os.path.join(os.path.dirname(__file__), "..", "data", "dataset.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
n_pos = sum(len(r["gold"]) for r in records)
print("样本数: %d, 正例实体数: %d, 负样本数: %d"
      % (len(records), n_pos, sum(1 for r in records if not r["gold"])))
print("已写入 %s" % os.path.abspath(out))
