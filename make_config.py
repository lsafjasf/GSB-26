"""生成排序规则配置 sort_rules.json。

规则内容（字母表、拼音表、符号位次）在此以数据表形式维护；
expected_order 字段用本库计算后冻结进配置，作为测试对拍的声明基准。
"""

import json

from localesort import LocaleSorter

# 变音符号等级: 无=0 尖=1 抑=2 弯=3 分=4 鼻=5 软=6
ACCENTED = {
    "á": ("a", 1), "à": ("a", 2), "â": ("a", 3), "ä": ("a", 4),
    "é": ("e", 1), "è": ("e", 2), "ê": ("e", 3), "ë": ("e", 4),
    "í": ("i", 1), "ì": ("i", 2), "î": ("i", 3), "ï": ("i", 4),
    "ó": ("o", 1), "ò": ("o", 2), "ô": ("o", 3), "ö": ("o", 4),
    "ú": ("u", 1), "ù": ("u", 2), "û": ("u", 3), "ü": ("u", 4),
    "ñ": ("n", 5),
    "ç": ("c", 6),
}

# 汉字 -> (拼音, 声调, [其他读音])；多音字以第一读音参与排序
CJK = {
    "安": ("an", 1, []),
    "北": ("bei", 3, []),
    "长": ("chang", 2, ["zhang3"]),
    "成": ("cheng", 2, []),
    "川": ("chuan", 1, []),
    "大": ("da", 4, []),
    "东": ("dong", 1, []),
    "都": ("du", 1, ["dou1"]),
    "福": ("fu", 2, []),
    "甘": ("gan", 1, []),
    "广": ("guang", 3, []),
    "贵": ("gui", 4, []),
    "国": ("guo", 2, []),
    "海": ("hai", 3, []),
    "汉": ("han", 4, []),
    "杭": ("hang", 2, []),
    "河": ("he", 2, []),
    "湖": ("hu", 2, []),
    "建": ("jian", 4, []),
    "江": ("jiang", 1, []),
    "京": ("jing", 1, []),
    "津": ("jin", 1, []),
    "乐": ("le", 4, ["yue4"]),
    "南": ("nan", 2, []),
    "宁": ("ning", 2, []),
    "青": ("qing", 1, []),
    "庆": ("qing", 4, []),
    "陕": ("shan", 3, []),
    "上": ("shang", 4, []),
    "深": ("shen", 1, []),
    "苏": ("su", 1, []),
    "肃": ("su", 4, []),
    "天": ("tian", 1, []),
    "武": ("wu", 3, []),
    "西": ("xi", 1, []),
    "小": ("xiao", 3, []),
    "云": ("yun", 2, []),
    "浙": ("zhe", 4, []),
    "圳": ("zhen", 4, []),
    "中": ("zhong", 1, []),
    "重": ("zhong", 4, ["chong2"]),
    "州": ("zhou", 1, []),
}

SYMBOLS = {" ": 0, "-": 1, ".": 2, "_": 3}


def build_letters():
    letters = {}
    for code in range(ord("a"), ord("z") + 1):
        ch = chr(code)
        letters[ch] = [ch, 0]
        letters[ch.upper()] = [ch, 0]
    for ch, (base, rank) in ACCENTED.items():
        letters[ch] = [base, rank]
        letters[ch.upper()] = [base, rank]
    return letters


def main():
    config = {
        "name": "zh-pinyin+latin-accent",
        "numeric": True,
        "case_first": "lower",
        "unknown_position": "end",
        "uncovered_policy": "codepoint",
        "symbols": SYMBOLS,
        "letters": build_letters(),
        "cjk": {
            ch: ({"pinyin": p, "tone": t, "alt": alt} if alt
                 else {"pinyin": p, "tone": t})
            for ch, (p, t, alt) in CJK.items()
        },
    }

    sorter = LocaleSorter(config)
    charset = (
        list(SYMBOLS)
        + [str(d) for d in range(10)]
        + sorted(config["letters"])
        + list(CJK)
    )
    keys = [sorter.key(ch) for ch in charset]
    if len(set(keys)) != len(keys):
        raise SystemExit("字符集中存在相同排序键，无法声明唯一期望顺序")
    expected = "".join(sorter.sort(charset))
    config["expected_order"] = expected

    with open("sort_rules.json", "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print("字符数:", len(charset))
    print("expected_order:", expected)


if __name__ == "__main__":
    main()
