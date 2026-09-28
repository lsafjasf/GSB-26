"""误伤评估的人工标注样本集（金标）。

每个样本给出：

* ``id``    —— 稳定编号，报告与差异比对引用；
* ``text``  —— 被扫描原文（含全角/繁体/零宽/同音等规避写法）；
* ``labels`` —— 人工标注的“应命中”真值区间列表，元素为
  ``(敏感词原形, start, end)``，半开区间、指向 **原文**。``end`` 省略时
  取 ``start + len(词形)``；若被命中原文片段包含被归一化删除的字符
  （零宽/空白），必须显式给出覆盖这些字符的 ``end``。
  区间合法性由 :func:`spans` 统一断言。
* ``note``  —— 该样本想说明的情形（纯文档用途）。

真值与策略无关：标注的是“这段文本按人工判断应不应该被上报”。各归一化
策略是否能抓到（recall）、是否多报（precision）由 eval 脚本重算。

评估时固定开启词边界（``use_boundary=True``），因为英文子串型误伤由
边界负责；本评估要回答的是“归一化策略本身额外带来的命中与误伤”。
白名单随引擎统一配置（见 :data:`WHITELIST`）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 评估用敏感词表（均给出简体原形）
WORDS = [
    "cat",
    "天安门",
    "赌场",
    "办证",
    "攻击",
    "攻势",
    "法轮功",
]

# 统一白名单：专有名词放行
WHITELIST = ["天安门广场"]


@dataclass(frozen=True)
class Sample:
    id: str
    text: str
    # (word, start[, end])；end 默认 start + len(word)
    labels: tuple[tuple, ...]
    note: str = ""


def spans(sample: Sample) -> tuple[tuple[str, int, int], ...]:
    """把标注展开为 (word,start,end)，并断言区间在原文内合法。"""
    out: list[tuple[str, int, int]] = []
    for label in sample.labels:
        word, start = label[0], label[1]
        end = label[2] if len(label) >= 3 else start + len(word)
        assert 0 <= start < end <= len(sample.text), (
            f"label out of range in {sample.id}: {word!r}[{start}:{end}] "
            f"text_len={len(sample.text)}"
        )
        out.append((word, start, end))
    return tuple(out)


SAMPLES: tuple[Sample, ...] = (
    # -- 基线：英文大小写 / 子串（边界负责）/ 标点包裹 -------------------
    Sample(
        "S01",
        "scatter bobcat category concatenate a cat sat.",
        (("cat", 38),),
        "英文子串误伤由边界消除，只有独立 cat 为真",
    ),
    Sample(
        "S02",
        "CAT and Cat and cAt",
        (("cat", 0), ("cat", 8), ("cat", 16)),
        "大小写折叠应抓到三种写法",
    ),
    Sample(
        "S03",
        "a (cat)!",
        (("cat", 3),),
        "标点包裹的独立词应命中",
    ),
    # -- 白名单 ----------------------------------------------------------
    Sample(
        "S04",
        "去天安门广场，再见天安门",
        (("天安门", 9),),
        "「天安门广场」整词白名单放行，独立天安门仍报",
    ),
    Sample(
        "S05",
        "我愛天安門廣場",
        (),
        "繁体白名单也应放行（白名单走同一归一化），整句无真命中",
    ),
    # -- 全半角 ----------------------------------------------------------
    Sample(
        "S06",
        "（ＣＡＴ）",
        (("cat", 1, 4),),
        "全角大写规避：span 指向原文全角 ＣＡＴ",
    ),
    Sample(
        "S07",
        "这个category很常见",
        (),
        "全/半角不改变子串粘连，边界下无命中（TN）",
    ),
    # -- 繁简 ------------------------------------------------------------
    Sample(
        "S08",
        "這家地下賭場被查了",
        (("赌场", 4, 6),),
        "繁体「賭場」经 1:1 繁→简应抓到",
    ),
    Sample(
        "S09",
        "代辦證件請走正規渠道",
        (),
        "正常政务表述「代办证件」含「办证」二字邻接：t2s 误伤标本（FP）",
    ),
    Sample(
        "S10",
        "嚴禁代辦證件廣告，抓到辦證者重罰",
        (("办证", 11, 13),),
        "前半句为正常表述（FP 来源），末句繁体「辦證」才是真命中",
    ),
    # -- 同音（重点：对抗 vs 误伤） --------------------------------------
    Sample(
        "S11",
        "服务器遭供击瘫痪2小时",
        (("攻击", 4, 6),),
        "同音规避：功→供（均 gōng），开启同音折叠才抓得到",
    ),
    Sample(
        "S12",
        "他去办公室处理公事",
        (),
        "正常词「公事」(gōng shì) 与「攻势」同音折叠后同形：FP 标本",
    ),
    Sample(
        "S13",
        "网传我方将展开供势施压",
        (("攻势", 7, 9),),
        "同音规避：攻→供、势不出现替换；折叠后命中「攻势」",
    ),
    # -- 零宽 ------------------------------------------------------------
    Sample(
        "S14",
        "法\u200b轮\u200c功宣传材料已收缴",
        (("法轮功", 0, 5),),
        "零宽拆字规避：删零宽后命中，原文区间含两个零宽字符",
    ),
    Sample(
        "S15",
        "禁止賭\u200b場广告",
        (("赌场", 2, 5),),
        "繁体+零宽混合规避：原文区间含零宽字符",
    ),
    # -- 空白 ------------------------------------------------------------
    Sample(
        "S16",
        "网上代\u3000辦\u3000證的小广告",
        (("办证", 2, 7),),
        "拆字空格规避：collapse（默认）漏报，remove 抓到（召回对照）",
    ),
    Sample(
        "S17",
        "请去办证大厅办理",
        (("办证", 2),),
        "正常文本中的真命中，所有中文相关策略都应抓到",
    ),
    Sample(
        "S18",
        "不要在办证处逗留",
        (("办证", 3),),
        "正常文本中的真命中",
    ),
    Sample(
        "S19",
        "my cat sat on the mat",
        (("cat", 3),),
        "英文真命中；remove 空白后 mycatsatonthemat 词内粘连 -> 漏报，"
        "量化 remove 对英文的误伤/漏报代价",
    ),
)


def all_expected() -> dict[str, set[tuple[str, int, int]]]:
    """返回 {sample_id: set[(word,start,end)]} 真值集合。"""
    return {s.id: set(spans(s)) for s in SAMPLES}
