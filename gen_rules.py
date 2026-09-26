"""生成 rules.json（区域化排序规则配置）。

规则语义：
  - 主级(primary):   字母/读音。拉丁字符用基础字母，汉字用拼音（无声调），
                     数字用自身字符，符号用自身。
  - 次级(secondary): 变音符号 / 声调。0=无，1..n 按声明顺序。
  - 三级(tertiary):  大小写。0=小写/无 case，1=大写。

多音字处理：配置只声明一个“默认读音”（见 README 的说明），
需要其他读音的场景应提供另一份规则配置。
"""

import json
from collections import OrderedDict

# 拉丁字母的变体: base -> [(字符, 次级权重), ...]
LATIN_VARIANTS = {
    "a": [("á", 1), ("à", 2), ("â", 3), ("ä", 4), ("ã", 5), ("å", 6)],
    "c": [("ç", 1)],
    "e": [("é", 1), ("è", 2), ("ê", 3), ("ë", 4)],
    "i": [("í", 1), ("ì", 2), ("î", 3), ("ï", 4)],
    "n": [("ñ", 1)],
    "o": [("ó", 1), ("ò", 2), ("ô", 3), ("ö", 4), ("õ", 5)],
    "u": [("ú", 1), ("ù", 2), ("û", 3), ("ü", 4)],
    "y": [("ý", 1), ("ÿ", 2)],
}

# 汉字 -> (拼音, 声调)。多音字取一个默认读音并在注释中说明。
HANZI = OrderedDict([
    ("爱", ("ai", 4)), ("安", ("an", 1)),
    ("八", ("ba", 1)), ("拔", ("ba", 2)), ("把", ("ba", 3)), ("爸", ("ba", 4)),
    ("白", ("bai", 2)), ("北", ("bei", 3)), ("本", ("ben", 3)), ("不", ("bu", 4)),
    ("才", ("cai", 2)), ("长", ("chang", 2)),  # 多音字：另有 zhǎng
    ("成", ("cheng", 2)), ("大", ("da", 4)), ("的", ("de", 5)),
    ("东", ("dong", 1)), ("都", ("du", 1)),  # 多音字：另有 dōu
    ("二", ("er", 4)), ("发", ("fa", 1)), ("分", ("fen", 1)),
    ("高", ("gao", 1)), ("个", ("ge", 4)), ("国", ("guo", 2)),
    ("海", ("hai", 3)), ("好", ("hao", 3)), ("河", ("he", 2)),
    ("红", ("hong", 2)), ("华", ("hua", 2)), ("会", ("hui", 4)),
    ("家", ("jia", 1)), ("江", ("jiang", 1)), ("金", ("jin", 1)),
    ("九", ("jiu", 3)), ("开", ("kai", 1)), ("可", ("ke", 3)),
    ("乐", ("le", 4)),  # 多音字：另有 yuè
    ("李", ("li", 3)), ("林", ("lin", 2)), ("龙", ("long", 2)),
    ("马", ("ma", 3)), ("美", ("mei", 3)), ("民", ("min", 2)),
    ("明", ("ming", 2)), ("南", ("nan", 2)), ("年", ("nian", 2)),
    ("女", ("nv", 3)),  # nǚ，按惯例写作 nv
    ("朋", ("peng", 2)), ("平", ("ping", 2)), ("七", ("qi", 1)),
    ("青", ("qing", 1)), ("人", ("ren", 2)), ("日", ("ri", 4)),
    ("三", ("san", 1)), ("山", ("shan", 1)), ("上", ("shang", 4)),
    ("生", ("sheng", 1)), ("十", ("shi", 2)), ("水", ("shui", 3)),
    ("四", ("si", 4)), ("他", ("ta", 1)), ("天", ("tian", 1)),
    ("王", ("wang", 2)), ("文", ("wen", 2)), ("五", ("wu", 3)),
    ("西", ("xi", 1)), ("下", ("xia", 4)), ("小", ("xiao", 3)),
    ("新", ("xin", 1)), ("星", ("xing", 1)), ("学", ("xue", 2)),
    ("一", ("yi", 1)), ("英", ("ying", 1)), ("月", ("yue", 4)),
    ("云", ("yun", 2)), ("张", ("zhang", 1)),
    ("中", ("zhong", 1)),  # 多音字：另有 zhòng
    ("周", ("zhou", 1)), ("子", ("zi", 3)),
])

# 常见符号：主级为自身（码位序在字母之前，保证确定的全序）
SYMBOLS = [" ", "-", "'", ".", "_", ","]


def build():
    mappings = OrderedDict()

    for base, variants in LATIN_VARIANTS.items():
        entries = [(base, 0)] + variants
        for ch, secondary in entries:
            mappings[ch] = [base, secondary, 0]
            mappings[ch.upper()] = [base, secondary, 1]

    for base in "bdfghjklmpqrstvwxz":
        if base in LATIN_VARIANTS:
            continue
        mappings[base] = [base, 0, 0]
        mappings[base.upper()] = [base, 0, 1]

    for digit in "0123456789":
        mappings[digit] = [digit, 0, 0]

    for sym in SYMBOLS:
        mappings[sym] = [sym, 0, 0]

    for ch, (pinyin, tone) in HANZI.items():
        mappings[ch] = [pinyin, tone, 0]

    config = OrderedDict([
        ("description", "中文(拼音)+拉丁(变音/大小写) 区域化排序规则"),
        ("levels", ["primary", "secondary", "tertiary"]),
        ("numeric_collation", True),
        ("fallback", "end"),
        ("mappings", mappings),
        # 对拍基准：每个分组内的元素按本配置排序后必须与声明顺序一致
        ("expected_order_groups", [
            list("安八才大二发高海江开李马南平青人三他王西一张"),
            ["八", "拔", "把", "爸"],
            ["a", "A", "á", "à", "b", "c", "ç", "d", "e", "E", "é"],
            ["1", "2", "9", "10", "20", "100"],
        ]),
    ])
    return config


if __name__ == "__main__":
    config = build()
    with open("rules.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("rules.json: %d 个字符映射" % len(config["mappings"]))
