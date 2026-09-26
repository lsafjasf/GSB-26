"""encsniff 自测: 往返无损、策略行为、边界情形。运行: python3 -m unittest -v"""

import codecs
import unittest

from encsniff import SniffError, convert, sniff


# --- 合法样本（各编码） -----------------------------------------------------
ZH = "编码嗅探要宁可拒绝，不可猜错。中文文本用于测试。" * 4
FR = "L'encodage c'est compliqué: à bientôt, garçon! Noël, naïve, œuvre." * 3
EN = "Plain ASCII text, no surprises here.\n" * 5

SAMPLES = {
    "utf-8": ZH.encode("utf-8") + FR.encode("utf-8"),
    "utf-8-sig": codecs.BOM_UTF8 + ZH.encode("utf-8"),
    "utf-16": ZH.encode("utf-16"),            # 含 BOM
    "utf-16-le": (ZH + EN).encode("utf-16-le"),  # 无 BOM
    "utf-16-be": (ZH + EN).encode("utf-16-be"),  # 无 BOM
    "gbk": ZH.encode("gbk"),
    "cp1252": FR.encode("cp1252"),
}


class TestSniff(unittest.TestCase):
    def test_each_encoding_detected(self):
        expected = {
            "utf-8": "utf-8",
            "utf-8-sig": "utf-8-sig",
            "utf-16": "utf-16",
            "utf-16-le": "utf-16-le",
            "utf-16-be": "utf-16-be",
            "gbk": "gbk",
            "cp1252": "cp1252",
        }
        for name, data in SAMPLES.items():
            with self.subTest(name=name):
                result = sniff(data)
                self.assertEqual(result.encoding, expected[name])
                self.assertGreaterEqual(result.confidence, 0.6)
                self.assertTrue(result.reasons)  # 必须输出判定依据

    def test_empty_file(self):
        result = sniff(b"")
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(convert(b""), b"")

    def test_bom_only(self):
        for data, enc in (
            (codecs.BOM_UTF8, "utf-8-sig"),
            (codecs.BOM_UTF16_LE, "utf-16"),
            (codecs.BOM_UTF16_BE, "utf-16"),
        ):
            with self.subTest(enc=enc):
                self.assertEqual(sniff(data).encoding, enc)
                self.assertEqual(convert(data), b"")

    def test_pure_ascii(self):
        result = sniff(EN.encode("ascii"))
        self.assertEqual(result.encoding, "utf-8")
        self.assertEqual(result.confidence, 1.0)

    def test_ambiguous_rejected_with_candidates(self):
        # 单个高位字节: 唯一候选 cp1252 得分低于阈值，必须拒绝并报告候选。
        with self.assertRaises(SniffError) as ctx:
            sniff(b"\xe9")
        self.assertTrue(ctx.exception.candidates)
        self.assertEqual(ctx.exception.candidates[0].encoding, "cp1252")

    def test_undecodable_rejected(self):
        # 0x81 在 cp1252 未定义，且不是合法 UTF-8/GBK 序列开头组合。
        # 0x81 在 cp1252 未定义; 后随 0x20 使 GBK 双字节非法; 也非 UTF-8。
        with self.assertRaises(SniffError):
            sniff(b"\x81\x20\x81\x20")


class TestErrorPolicy(unittest.TestCase):
    BAD = b"abc\xff\xfedef"  # 非法 UTF-8 序列

    def test_strict_raises(self):
        with self.assertRaises(UnicodeDecodeError):
            convert(self.BAD, source_encoding="utf-8", errors="strict")

    def test_replace_keeps_data(self):
        out = convert(self.BAD, source_encoding="utf-8", errors="replace")
        text = out.decode("utf-8")
        # 好字节一个不少，坏字节变成 U+FFFD，无静默丢失。
        self.assertTrue(text.startswith("abc"))
        self.assertTrue(text.endswith("def"))
        self.assertIn("\ufffd", text)
        self.assertEqual(len(text), 8)

    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError):
            convert(self.BAD, errors="ignore")  # 静默丢数据的策略不允许


class TestRoundTrip(unittest.TestCase):
    """无损保证: 合法输入 -> UTF-8 -> 原编码，必须逐字节一致。"""

    def test_roundtrip_all_encodings(self):
        for name, data in SAMPLES.items():
            with self.subTest(name=name):
                source = sniff(data).encoding
                utf8 = convert(data, "utf-8", source_encoding=source)
                back = convert(utf8, source, source_encoding="utf-8")
                self.assertEqual(back, data)

    def test_roundtrip_long_single_line(self):
        # 超长单行（约 2 MiB，无换行）
        line = (ZH + FR).replace("\n", " ") * 8000
        data = line.encode("utf-8")
        self.assertNotIn(b"\n", data)
        source = sniff(data).encoding
        utf8 = convert(data, source_encoding=source)
        back = convert(utf8, source, source_encoding="utf-8")
        self.assertEqual(back, data)

    def test_roundtrip_large_multibyte(self):
        data = ZH.encode("gbk") * 20000  # 约 3.6 MiB GBK
        source = sniff(data).encoding
        self.assertEqual(source, "gbk")
        utf8 = convert(data, source_encoding=source)
        back = convert(utf8, source, source_encoding="utf-8")
        self.assertEqual(back, data)


if __name__ == "__main__":
    unittest.main()
