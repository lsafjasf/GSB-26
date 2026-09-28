"""误伤评估管线的自测：标注合法性 + 指标由脚本重算 + 差异可逐条归因。

这些断言锁定的是“管线逻辑”（同一语料、同一 profile 应稳定产出给定
TP/FP/FN 与集合差异），不是手写指标；数字若随语料变化应同步更新。
"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.corpus import SAMPLES, spans  # noqa: E402
from eval.profiles import PROFILE_BY_NAME  # noqa: E402
from eval.run_eval import EXPECTED, evaluate, scan_profile  # noqa: E402


def metrics_for(name: str):
    return evaluate(scan_profile(PROFILE_BY_NAME[name]))


class TestCorpusLabels(unittest.TestCase):
    def test_all_label_spans_valid_and_word_in_vocab(self):
        vocab = {w for s in SAMPLES for w, _, _ in spans(s)}
        known = {
            "cat", "天安门", "赌场", "办证", "攻击", "攻势", "法轮功",
        }
        self.assertTrue(vocab <= known, vocab - known)
        # spans() 自身已断言区间合法；再确保无重复标注
        for s in SAMPLES:
            sp = spans(s)
            self.assertEqual(len(sp), len(set(sp)), f"dup labels in {s.id}")


class TestRecomputedMetrics(unittest.TestCase):
    def test_raw_is_high_precision_low_recall(self):
        m = metrics_for("raw")
        self.assertEqual(m.fp, 0)  # 不做归一化：抓得少，但几乎不误伤
        self.assertLess(m.recall, 0.5)  # 大量规避写法漏报
        self.assertEqual(m.precision, 1.0)

    def test_default_baseline_trades_some_fp_for_recall(self):
        m = metrics_for("base_default")
        # 繁简 1:1 + 零宽把繁体/零宽样本都抓回（recall 明显高于 raw）
        self.assertGreater(m.recall, 0.8)
        # 「代辦證件」里的「辦證」邻接造成 2 条 t2s 误伤
        self.assertEqual(m.fp, 2)
        fp_snippets = sorted(
            (r.sample_id, r.word) for r in m.fp_records
        )
        self.assertEqual(fp_snippets, [("S09", "办证"), ("S10", "办证")])
        # 同音替字与拆字空格在默认档仍漏报
        fn_ids = {r.sample_id for r in m.fn_records}
        self.assertEqual(fn_ids, {"S11", "S13", "S16"})

    def test_t2s_off_removes_both_tp_and_fp(self):
        # 关繁简：繁体真命中消失，同时 t2s 带来的误伤也消失
        m = metrics_for("no_t2s")
        self.assertEqual(m.fp, 0)
        self.assertIn(("S08"), {r.sample_id for r in m.fn_records})

    def test_homophone_gains_evasion_hits_but_adds_realword_fps(self):
        m = metrics_for("homophone_on")
        # 同音规避样本被抓回（攻击<-供击；攻势<-供势）
        tp_keys = {(r.sample_id, r.word) for r in m.fp_records} | set()
        hits = scan_profile(PROFILE_BY_NAME["homophone_on"])
        hit_words = {
            (sid, rec.word) for sid, recs in hits.items() for rec in recs
        }
        self.assertIn(("S11", "攻击"), hit_words)
        self.assertIn(("S13", "攻势"), hit_words)
        # 但「办公室/公事」这类同音正常词被误伤
        fp_ids = {r.sample_id for r in m.fp_records}
        self.assertIn("S12", fp_ids)
        self.assertGreaterEqual(m.fp, 4)
        self.assertEqual(m.recall, 16 / 17)

    def test_ws_remove_causes_english_glue_misses(self):
        # 空白删除：拆字空格抓到了一部分，但英文跨词粘连反而漏报
        m = metrics_for("ws_remove")
        fn_ids = {r.sample_id for r in m.fn_records}
        self.assertIn("S01", fn_ids)  # a cat sat 粘连
        self.assertIn("S19", fn_ids)


class TestSetDifferences(unittest.TestCase):
    def test_single_toggle_diff_is_attributable(self):
        from eval.run_eval import all_hit_set, EXPECTED

        base = all_hit_set(scan_profile(PROFILE_BY_NAME["base_default"]))
        no_width = all_hit_set(scan_profile(PROFILE_BY_NAME["no_width"]))
        removed = base - no_width
        added = no_width - base
        # 只关全半角：差异应恰好是全角规避那一条，且为 TP
        self.assertEqual(
            {(r.sample_id, r.word) for r in removed}, {("S06", "cat")}
        )
        self.assertFalse(added)
        self.assertTrue(removed <= set().union(*EXPECTED.values()))

    def test_homophone_diff_lists_each_added_hit(self):
        from eval.run_eval import all_hit_set

        base = all_hit_set(scan_profile(PROFILE_BY_NAME["base_default"]))
        on = all_hit_set(scan_profile(PROFILE_BY_NAME["homophone_on"]))
        added = on - base
        added_keys = {(r.sample_id, r.word, r.start) for r in added}
        self.assertEqual(
            added_keys,
            {
                ("S11", "攻击", 4),
                ("S13", "攻势", 7),
                ("S12", "攻势", 3),
                ("S12", "攻势", 7),
            },
        )


if __name__ == "__main__":
    unittest.main()
