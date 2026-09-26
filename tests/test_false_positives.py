"""误伤（false positive）评估。

评估方法：对一批 **刻意构造** 的样本逐句人工标注“应命中”的原文区间
（``expected``），用引擎输出与标注集合做集合比对，计算：

* 精确率 precision = 正确命中数 / 上报命中数（误伤越多越低）；
* 召回率 recall    = 正确命中数 / 应命中数（漏报越多越低）；
* 并列出所有 FP（误伤）与 FN（漏报），便于归因。

样本覆盖：英文子串误伤（scatter/bobcat/category/concatenate）、大小写与
全半角规避、零宽字符拆分、中文子串正常命中、空白折叠/删除的取舍、
白名单放行。
"""

import unittest

from sensitive import SensitiveEngine
from sensitive.normalize import NormalizeConfig, WS_COLLAPSE, WS_REMOVE


# (文本, 期望命中的 (词, 原文start, 原文end) 列表)
LABELED_NO_BOUNDARY = [
    # 1) 不做边界时：英文子串全部命中（这里记录“真值”，用来量化误伤）
    (
        "scatter bobcat category concatenate a cat sat.",
        # 真值：只有独立的 cat 应命中；其余 4 处子串均为误伤
        [("cat", 38)],
    ),
]

# 期望开启边界后：只命中独立的 cat
LABELED_BOUNDARY = [
    (
        "scatter bobcat category concatenate a cat sat.",
        [("cat", 38)],
    ),
    # 标点包裹算独立词
    ("a (cat)!", [("cat", 3)]),
    # 中文里子串不受 ASCII 边界影响
    ("我爱天安门广场", [("天安门", 2)]),
]

# 规避手段：全角/大小写/零宽
LABELED_EVASION = [
    ("练 习 ＣＡＴ\u200b功", [("cat", None)]),  # None 表示只要求该词出现
    ("这是cat123", []),  # 边界模式下 cat 后贴数字 -> 不上报
    ("CAT and Cat and cAt", [("cat", 0), ("cat", 8), ("cat", 16)]),
]


def evaluate(engine: SensitiveEngine, samples, check_positions: bool = True):
    """返回 (precision, recall, fp 列表, fn 列表, total_reported, total_expected)。"""
    total_tp = total_fp = total_fn = 0
    fp_report: list[str] = []
    fn_report: list[str] = []
    for text, labels in samples:
        actual = {(h.word, h.start, h.end) for h in engine.find_all(text)}
        if check_positions:
            expected = {(w, s, s + len(w)) for w, s in labels if s is not None}
        else:
            # 只比较词集合
            expected = {(w, s, s + len(w)) for w, s in labels if s is not None}
        # None 位置的标签只要求出现过该词
        loose_words = {w for w, s in labels if s is None}
        actual_words = {h.word for h in engine.find_all(text)}
        tp = len(actual & expected)
        fp = len(actual - expected)
        fn = len(expected - actual)
        for w in loose_words:
            if w in actual_words:
                tp += 1
            else:
                fn += 1
        total_tp += tp
        total_fp += fp
        total_fn += fn
        if fp:
            fp_report.append(f"FP in {text!r}: {sorted(actual - expected)}")
        if fn or any(w not in actual_words for w in loose_words):
            fn_report.append(f"FN in {text!r}: missing {sorted(expected - actual) + [w for w in loose_words if w not in actual_words]}")
    reported = total_tp + total_fp
    precision = total_tp / reported if reported else 1.0
    total_expected = total_tp + total_fn
    recall = total_tp / total_expected if total_expected else 1.0
    return precision, recall, fp_report, fn_report, reported, total_expected


class TestFalsePositiveEvaluation(unittest.TestCase):
    def test_without_boundary_quantifies_substring_fps(self):
        eng = SensitiveEngine(["cat"])
        p, r, fps, fns, reported, expected = evaluate(eng, LABELED_NO_BOUNDARY)
        self.assertEqual(r, 1.0)  # 无边界：全部抓到，召回 100%
        # 5 次上报中 4 次误伤，精确率仅 20%；真值 1 个全部抓到
        self.assertEqual((reported, expected), (5, 1))
        self.assertAlmostEqual(p, 0.2)
        self.assertEqual(len(fps), 1)

    def test_boundary_raises_precision(self):
        eng = SensitiveEngine(["cat", "天安门"], use_boundary=True)
        samples = LABELED_BOUNDARY + [
            ("CAT and Cat and cAt", [("cat", 0), ("cat", 8), ("cat", 16)]),
        ]
        p, r, fps, fns, _, _ = evaluate(eng, samples)
        self.assertEqual((p, r), (1.0, 1.0), msg=f"fps={fps} fns={fns}")

    def test_evasion_and_postfix_digit(self):
        eng = SensitiveEngine(["cat"], use_boundary=True)
        # 全角大写 + 零宽仍命中（位置不固定，用词存在性检查）
        hits = [(h.word, h.start, h.end) for h in eng.find_all("练 习 ＣＡＴ\u200b功")]
        # collapse 模式下“练 习”中间有空格不影响，cat 前为空格后为 CJK -> 命中
        self.assertEqual([w for w, _, _ in hits], ["cat"])
        # cat123：数字是词字符，右侧边界失败 -> 不上报
        self.assertEqual(eng.find_all("cat123"), [])

    def test_whitelist_reduces_fps_in_nested_case(self):
        # 场景：敏感词“天安”，但“天安门”为专有名词需放行
        eng = SensitiveEngine(["天安"], whitelist=["天安门"])
        hits = [(h.word, h.start, h.end) for h in eng.find_all("去天安门，天安")]
        # “天安门”覆盖下的天安被放行，独立的天安仍命中
        self.assertEqual(hits, [("天安", 5, 7)])

    def test_whitespace_remove_tradeoff(self):
        # collapse（默认）：拆字空格阻止命中（降误伤/可被绕过）
        eng_collapse = SensitiveEngine(["敏感词"])
        self.assertEqual(eng_collapse.find_all("敏 感 词"), [])
        # remove：拆字空格被无视（防绕过，但可能增加误伤面）
        eng_remove = SensitiveEngine(
            ["敏感词"], config=NormalizeConfig(whitespace=WS_REMOVE)
        )
        hits = eng_remove.find_all("敏 感 词")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].start, 0)
        self.assertEqual(hits[0].end, 5)  # 原文区间含两个空格

    def test_zero_width_split_caught(self):
        eng = SensitiveEngine(["法轮功"])
        text = "法\u200b轮\u200c功"
        hits = eng.find_all(text)
        self.assertEqual(len(hits), 1)
        self.assertEqual(text[hits[0].start:hits[0].end], text)


if __name__ == "__main__":
    unittest.main()
