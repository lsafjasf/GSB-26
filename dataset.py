"""构造数据集生成器（确定性，seed 固定）。

生成一段中文文本，其中埋入带标注的实体：
  - 正样本：合法身份证/银行卡/手机号/护照（含加分隔符、换行的变体）
  - 难负样本：校验位错误的身份证、Luhn 错误的卡号、无效号段手机号、
    订单号/时间戳/金额等"长得像"的数字串
返回 (text, entities)，entities 记录每个埋入实体的位置与期望标签。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"

_PROV = ["11", "32", "33", "37", "42", "43", "44", "50", "51", "61", "62", "65"]
_CARD_BINS = ["622202", "622848", "436742", "521899", "356889", "601382"]
_PHONE_PREFIX = ["138", "139", "150", "166", "176", "185", "188", "191", "199", "130"]


@dataclass
class Entity:
    start: int
    end: int
    etype: str      # id_card / bank_card / phone / passport / none
    expected: bool  # True=真实敏感数据（应命中），False=难负样本（不应命中）
    note: str


def _id_checksum(body17: str) -> str:
    return _ID_CHECK_CHARS[sum(int(a) * w for a, w in zip(body17, _ID_WEIGHTS)) % 11]


def _luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body + "0")):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def gen_id(rng: random.Random) -> str:
    region = rng.choice(_PROV) + "".join(rng.choice("0123456789") for _ in range(4))
    year = rng.randint(1955, 2004)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    seq = rng.randint(0, 999)
    body = f"{region}{year:04d}{month:02d}{day:02d}{seq:03d}"
    return body + _id_checksum(body)


def gen_card(rng: random.Random) -> str:
    bin6 = rng.choice(_CARD_BINS)
    length = rng.choice([16, 16, 16, 19])
    body = bin6 + "".join(rng.choice("0123456789") for _ in range(length - 6 - 1))
    return body + _luhn_digit(body)


def gen_phone(rng: random.Random) -> str:
    return rng.choice(_PHONE_PREFIX) + "".join(rng.choice("0123456789") for _ in range(8))


def gen_passport(rng: random.Random) -> str:
    return rng.choice("EG") + "".join(rng.choice("0123456789") for _ in range(8))


def fmt_grouped(num: str, rng: random.Random, allow_newline: bool = False) -> str:
    """把数字串按 3-4 位分组，用空格/连字符/换行连接。"""
    groups, i = [], 0
    while i < len(num):
        step = rng.choice([3, 4, 4])
        groups.append(num[i:i + step])
        i += step
    seps = [" ", "-"]
    if allow_newline:
        seps.append("\n")
    sep = rng.choice(seps)
    return sep.join(groups)


_FILLERS = [
    "本次会议纪要由秘书处整理归档，仅供参考。",
    "系统升级期间部分功能可能短暂不可用，敬请谅解。",
    "请于本周五前将材料提交至综合办公室。",
    "附件清单见下表，如有遗漏请及时反馈。",
    "该批次产品已通过出厂检验，合格率百分之九十九点八。",
    "活动期间注册用户可领取一次抽奖机会。",
    "请妥善保管您的个人信息，谨防电信诈骗。",
    "本通知自发布之日起生效，解释权归运营部门所有。",
]

_NOISE = ["", "", "", "😀", "　", "​", "＃", "★"]

_POS_TPL = {
    "id_card": ["身份证号码为{v}，请核实。", "登记证件号：{v}", "本人身份证 {v} 复印件附后。", "{v}"],
    "bank_card": ["银行卡号：{v}", "请将款项汇入卡号 {v}。", "工资卡 {v}（借记卡）。", "{v}"],
    "phone": ["联系电话{v}，工作时间拨打。", "手机号：{v}", "紧急联系人手机 {v}。", "{v}"],
    "passport": ["护照号码是{v}。", "出境证件（护照）：{v}", "{v}"],
}

_NEG_TPL = ["订单号：{v}", "流水号{v}", "批次编号{v}", "记录编号 {v}", "时间戳{v}", "{v}"]


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.entities: list[Entity] = []

    def emit(self, text: str) -> None:
        self.parts.append(text)
        self.pos += len(text)

    def plant(self, value: str, etype: str, expected: bool, note: str, template: str) -> None:
        seg = template.replace("{v}", value)
        start = self.pos + seg.find(value)
        self.emit(seg)
        self.entities.append(Entity(start, start + len(value), etype, expected, note))


def build_dataset(seed: int = 42) -> tuple[str, list[Entity]]:
    rng = random.Random(seed)
    b = _Builder()

    def filler() -> None:
        b.emit(rng.choice(_FILLERS) + rng.choice(_NOISE) + "\n")

    def plant_positive(etype: str, value: str, note: str) -> None:
        tpl = rng.choice(_POS_TPL[etype])
        b.plant(value, etype, True, note, tpl)
        filler()

    def plant_negative(etype: str, value: str, note: str) -> None:
        tpl = rng.choice(_NEG_TPL)
        b.plant(value, etype, False, note, tpl)
        filler()

    # ---- 正样本 ----
    for _ in range(250):
        plant_positive("id_card", gen_id(rng), "合法18位身份证")
    for _ in range(250):
        card = gen_card(rng)
        if rng.random() < 0.25:
            card = fmt_grouped(card, rng, allow_newline=True)  # 含空格/连字符/换行
        plant_positive("bank_card", card, "Luhn合法银行卡")
    for _ in range(250):
        phone = gen_phone(rng)
        if rng.random() < 0.3:
            phone = fmt_grouped(phone, rng)
        plant_positive("phone", phone, "有效号段手机号")
    for _ in range(120):
        plant_positive("passport", gen_passport(rng), "护照号")

    # ---- 难负样本 ----
    for _ in range(150):  # 校验位错误的身份证
        num = gen_id(rng)
        bad = "0123456789X".replace(num[-1], "")
        num = num[:-1] + rng.choice(bad)
        plant_negative("id_card", num, "身份证校验位错误")
    for _ in range(60):  # 非法日期的身份证
        num = gen_id(rng)
        num = num[:10] + "13" + num[12:]
        plant_negative("id_card", num, "身份证出生月份=13非法")
    for _ in range(150):  # Luhn 错误的卡号
        num = gen_card(rng)
        num = num[:-1] + rng.choice("0123456789".replace(num[-1], ""))
        plant_negative("bank_card", num, "卡号Luhn校验失败")
    for _ in range(150):  # 纯随机16位数字（约10%会碰巧通过Luhn，属真实歧义）
        num = "".join(rng.choice("0123456789") for _ in range(16))
        plant_negative("bank_card", num, "随机16位数字串")
    for _ in range(150):  # 无效号段手机号（能通过 1[3-9] 初筛但未分配）
        prefix = rng.choice(["140", "144", "154", "160", "168", "174", "179", "194"])
        num = prefix + "".join(rng.choice("0123456789") for _ in range(8))
        plant_negative("phone", num, f"无效号段{prefix}")
    for _ in range(200):  # 订单号/时间戳/金额等普通数字
        kind = rng.random()
        if kind < 0.4:
            num = "".join(rng.choice("0123456789") for _ in range(rng.choice([12, 14, 16, 20])))
            note = "订单/流水号"
        elif kind < 0.7:
            num = f"2026{rng.randint(1,12):02d}{rng.randint(1,28):02d}{rng.randint(0,23):02d}{rng.randint(0,59):02d}{rng.randint(0,59):02d}"
            note = "14位时间戳"
        else:
            num = str(rng.randint(1000, 9999999))
            note = "普通数量/金额"
        plant_negative("none", num, note)
    for _ in range(60):  # 护照样式的编号（无上下文应被阈值压制）
        num = "E" + "".join(rng.choice("0123456789") for _ in range(8))
        plant_negative("passport", num, "护照样式产品编号")

    # 纯 filler 段落
    for _ in range(200):
        filler()

    return "".join(b.parts), b.entities


if __name__ == "__main__":
    text, entities = build_dataset()
    pos = sum(1 for e in entities if e.expected)
    neg = len(entities) - pos
    print(f"文本长度: {len(text)} 字符, 实体: {len(entities)} (正样本 {pos}, 负样本 {neg})")
