"""sigchain 自测：覆盖单级链、多级链、顺序错乱、重复级别、
被篡改的中间级、过期、未生效、用途越权、深度超限、末端用途不足。"""

import unittest

from sigchain import FailCode, Link, issue, verify

T0 = 1_760_000_000  # 固定的验证时刻，保证用例可复现
DAY = 86400

KEYS = {
    "root": b"root-secret-key",
    "ca": b"ca-secret-key",
    "svc": b"svc-secret-key",
}
ANCHORS = {"root"}


def root_link(purposes=("read", "write", "admin"), nb=T0 - DAY, na=T0 + DAY):
    return issue(KEYS, "root", "root", purposes, nb, na)


def good_chain3():
    """root -> ca -> svc，用途逐级收紧的三级合法链。"""
    return [
        issue(KEYS, "root", "root", {"read", "write", "admin"}, T0 - DAY, T0 + DAY),
        issue(KEYS, "root", "ca", {"read", "write"}, T0 - DAY, T0 + DAY),
        issue(KEYS, "ca", "svc", {"read"}, T0 - DAY, T0 + DAY),
    ]


def codes(report):
    return {lv.code for lv in report.levels if not lv.ok}


class TestValidChains(unittest.TestCase):
    def test_single_level_chain(self):
        report = verify([root_link()], KEYS, ANCHORS, T0,
                        required_purposes={"read"})
        self.assertTrue(report.ok, report.render())
        self.assertEqual(len(report.levels), 1)

    def test_multi_level_chain(self):
        report = verify(good_chain3(), KEYS, ANCHORS, T0,
                        required_purposes={"read"})
        self.assertTrue(report.ok, report.render())
        self.assertEqual(len(report.levels), 3)
        self.assertTrue(all(lv.ok for lv in report.levels))


class TestChainIntegrity(unittest.TestCase):
    def test_out_of_order_chain(self):
        chain = good_chain3()
        chain[1], chain[2] = chain[2], chain[1]  # 顺序错乱
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertIn(FailCode.CHAIN_BROKEN, codes(report))
        # 第 1 级 issuer 应接续 root，却被换成 ca -> 报出具体级别
        self.assertEqual(report.levels[1].code, FailCode.CHAIN_BROKEN)
        self.assertIn("ca", report.levels[1].detail)

    def test_duplicate_level(self):
        chain = good_chain3()
        chain.insert(2, chain[1])  # 重复中间级
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertIn(FailCode.CHAIN_BROKEN, codes(report))
        self.assertTrue(
            any("重复" in lv.detail for lv in report.levels if not lv.ok),
            report.render(),
        )

    def test_tampered_middle_level(self):
        chain = good_chain3()
        mid = chain[1]
        # 篡改中间级的用途但不重新签名
        chain[1] = Link(mid.issuer, mid.subject,
                        frozenset({"read", "write", "admin"}),
                        mid.not_before, mid.not_after, mid.signature)
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[1].code, FailCode.SIGNATURE_MISMATCH)
        # 第 0、2 级自身签名仍然有效
        self.assertTrue(report.levels[0].ok)
        self.assertNotEqual(report.levels[2].code, FailCode.SIGNATURE_MISMATCH)

    def test_root_not_self_signed(self):
        bad = issue(KEYS, "root", "ca", {"read"}, T0 - DAY, T0 + DAY)
        report = verify([bad], KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[0].code, FailCode.CHAIN_BROKEN)

    def test_untrusted_anchor(self):
        report = verify([root_link()], KEYS, {"other-anchor"}, T0)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[0].code, FailCode.CHAIN_BROKEN)

    def test_empty_chain(self):
        report = verify([], KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)


class TestValidityWindow(unittest.TestCase):
    def test_expired_middle_level(self):
        chain = good_chain3()
        chain[1] = issue(KEYS, "root", "ca", {"read", "write"},
                         T0 - 10 * DAY, T0 - DAY)  # 已过期
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[1].code, FailCode.EXPIRED)

    def test_not_yet_valid_leaf(self):
        chain = good_chain3()
        chain[2] = issue(KEYS, "ca", "svc", {"read"},
                         T0 + DAY, T0 + 2 * DAY)  # 尚未生效
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[2].code, FailCode.NOT_YET_VALID)


class TestPurposeConstraints(unittest.TestCase):
    def test_purpose_escalation_reports_level_and_purpose(self):
        chain = good_chain3()
        chain[2] = issue(KEYS, "ca", "svc", {"read", "admin"},
                         T0 - DAY, T0 + DAY)  # admin 上级未授予
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertFalse(report.ok)
        lv = report.levels[2]
        self.assertEqual(lv.code, FailCode.PURPOSE_ESCALATION)
        self.assertIn("admin", lv.detail)
        self.assertIn("第2级", lv.detail)

    def test_leaf_missing_required_purpose(self):
        report = verify(good_chain3(), KEYS, ANCHORS, T0,
                        required_purposes={"read", "write"})
        self.assertFalse(report.ok)
        lv = report.levels[-1]
        self.assertEqual(lv.code, FailCode.PURPOSE_ESCALATION)
        self.assertIn("write", lv.detail)


class TestDepthLimit(unittest.TestCase):
    def test_depth_exceeded(self):
        report = verify(good_chain3(), KEYS, ANCHORS, T0, max_depth=2)
        self.assertFalse(report.ok)
        self.assertEqual(report.levels[2].code, FailCode.DEPTH_EXCEEDED)

    def test_depth_within_limit(self):
        report = verify(good_chain3(), KEYS, ANCHORS, T0, max_depth=3)
        self.assertTrue(report.ok, report.render())


class TestReportCompleteness(unittest.TestCase):
    def test_report_covers_every_level_with_reason(self):
        chain = good_chain3()
        chain[1] = issue(KEYS, "root", "ca", {"read", "write"},
                         T0 - 10 * DAY, T0 - DAY)
        report = verify(chain, KEYS, ANCHORS, T0)
        self.assertEqual(len(report.levels), 3)
        for lv in report.levels:
            self.assertTrue(lv.detail)  # 每级都有依据说明
        self.assertIn("第1级", report.render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
