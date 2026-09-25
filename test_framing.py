"""framing 库自测：切分等价性对拍 + 边界用例集。

运行: python3 test_framing.py -v
"""

import random
import unittest

from framing import (BufferLimitExceededError, DelimiterFramer,
                     FrameTooLargeError, LengthPrefixFramer)


def feed_in_chunks(framer, stream, sizes):
    """按 sizes 指定的切块大小依次喂入，汇总产出。"""
    out, pos = [], 0
    for size in sizes:
        out.extend(framer.feed(stream[pos:pos + size]))
        pos += size
    if pos < len(stream):
        out.extend(framer.feed(stream[pos:]))
    return out


def all_two_way_splits(framer_factory, stream):
    """对拍：一次性喂入 vs 每个可能的两段切分点。"""
    one_shot = framer_factory().feed(stream)
    assert stream and one_shot, "need a non-trivial stream"
    for cut in range(1, len(stream)):
        framer = framer_factory()
        got = framer.feed(stream[:cut]) + framer.feed(stream[cut:])
        assert got == one_shot, f"mismatch at cut={cut}"
    return one_shot


def random_chunkings(framer_factory, stream, rounds, seed=42):
    """对拍：一次性喂入 vs 随机多段切分。"""
    one_shot = framer_factory().feed(stream)
    rng = random.Random(seed)
    for _ in range(rounds):
        framer = framer_factory()
        got, pos = [], 0
        while pos < len(stream):
            size = rng.randint(1, max(1, len(stream) // 3))
            got.extend(framer.feed(stream[pos:pos + size]))
            pos += size
        assert got == one_shot, "random chunking mismatch"
    return one_shot


class LengthPrefixTest(unittest.TestCase):
    def make(self, **kw):
        kw.setdefault("max_frame_size", 64)
        return LengthPrefixFramer(**kw)

    def test_empty_data(self):
        f = self.make()
        self.assertEqual(f.feed(b""), [])
        self.assertEqual(f.buffered, 0)

    def test_zero_length_packet(self):
        f = self.make()
        self.assertEqual(f.feed(f.encode(b"")), [b""])
        # 零长度包与普通包混合
        stream = f.encode(b"") + f.encode(b"ab") + f.encode(b"")
        self.assertEqual(self.make().feed(stream), [b"", b"ab", b""])

    def test_multiple_packets_one_feed(self):
        f = self.make()
        msgs = [b"hello", b"", b"world" * 3, b"\x00\x01\x02"]
        stream = b"".join(f.encode(m) for m in msgs)
        self.assertEqual(self.make().feed(stream), msgs)

    def test_packet_split_across_many_chunks(self):
        f = self.make()
        msg = bytes(range(50))
        stream = f.encode(msg)
        framer = self.make()
        out = []
        for i in range(len(stream)):  # 逐字节喂入（跨 50+ 次切分）
            out.extend(framer.feed(stream[i:i + 1]))
        self.assertEqual(out, [msg])

    def test_header_split_across_feeds(self):
        f = self.make()
        stream = f.encode(b"payload")
        framer = self.make()
        self.assertEqual(framer.feed(stream[:1]), [])   # 头都不完整
        self.assertEqual(framer.feed(stream[1:3]), [])
        self.assertEqual(framer.feed(stream[3:]), [b"payload"])

    def test_delimiter_bytes_inside_body(self):
        f = self.make()
        msg = b"line1\nline2\r\n\t\x00"
        self.assertEqual(self.make().feed(f.encode(msg)), [msg])

    def test_chunking_equivalence_exhaustive_two_way(self):
        f = self.make()
        msgs = [b"", b"a", b"bc", b"\n\r\n", bytes(range(32))]
        stream = b"".join(f.encode(m) for m in msgs)
        expected = all_two_way_splits(self.make, stream)
        self.assertEqual(expected, msgs)

    def test_chunking_equivalence_random(self):
        f = self.make(max_frame_size=256)
        rng = random.Random(7)
        msgs = [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 100)))
                for _ in range(20)]
        stream = b"".join(f.encode(m) for m in msgs)
        expected = random_chunkings(lambda: self.make(max_frame_size=256),
                                    stream, rounds=200)
        self.assertEqual(expected, msgs)

    def test_malicious_length_rejected_with_offset(self):
        f = self.make(max_frame_size=16)
        good = f.encode(b"ok")
        evil = (2**32 - 1).to_bytes(4, "big")  # 4 GiB 声明长度
        framer = self.make(max_frame_size=16)
        self.assertEqual(framer.feed(good), [b"ok"])
        with self.assertRaises(FrameTooLargeError) as ctx:
            framer.feed(evil)
        err = ctx.exception
        self.assertEqual(err.offset, len(good))   # 指向流中恶帧起点
        self.assertEqual(err.declared, 2**32 - 1)
        self.assertEqual(err.limit, 16)
        self.assertEqual(framer.buffered, 0)      # 不为恶帧累积数据

    def test_malicious_length_arriving_byte_by_byte(self):
        f = self.make(max_frame_size=8)
        evil = (10**9).to_bytes(4, "big") + b"junk"
        framer = self.make(max_frame_size=8)
        with self.assertRaises(FrameTooLargeError):
            for i in range(len(evil)):
                framer.feed(evil[i:i + 1])

    def test_buffer_bound(self):
        # 缓冲峰值 <= header + max_frame_size，与喂入切分无关
        f = self.make(max_frame_size=100)
        msg = bytes(100)
        stream = f.encode(msg) * 10
        framer = self.make(max_frame_size=100)
        for i in range(len(stream)):
            framer.feed(stream[i:i + 1])
        self.assertLessEqual(framer.buffer_peak, 4 + 100)


class DelimiterTest(unittest.TestCase):
    def make(self, **kw):
        kw.setdefault("max_frame_size", 64)
        return DelimiterFramer(**kw)

    def test_empty_data(self):
        self.assertEqual(self.make().feed(b""), [])

    def test_zero_length_packet(self):
        f = self.make()
        self.assertEqual(f.feed(b"\n"), [b""])
        self.assertEqual(self.make().feed(b"\n\na\n\n"), [b"", b"", b"a", b""])

    def test_multiple_packets_one_feed(self):
        f = self.make()
        self.assertEqual(self.make().feed(b"ab\ncd\nef\n"), [b"ab", b"cd", b"ef"])

    def test_packet_split_across_many_chunks(self):
        msg = b"x" * 40
        stream = msg + b"\n"
        framer = self.make()
        out = []
        for i in range(len(stream)):
            out.extend(framer.feed(stream[i:i + 1]))
        self.assertEqual(out, [msg])

    def test_multi_byte_delimiter(self):
        f = self.make(delimiter=b"\r\n")
        framer = self.make(delimiter=b"\r\n")
        stream = b"aa\r\nbb\r\n"
        # 把 \r 和 \n 拆开喂
        cut = stream.index(b"\n")
        out = framer.feed(stream[:cut]) + framer.feed(stream[cut:])
        self.assertEqual(out, [b"aa", b"bb"])
        self.assertEqual(f.feed(stream), [b"aa", b"bb"])

    def test_delimiter_inside_body_semantics(self):
        # 分隔符协议下，包体内的分隔符即帧边界（文档化语义）；
        # 需要内嵌分隔符的场景见 LengthPrefixTest.test_delimiter_bytes_inside_body
        self.assertEqual(self.make().feed(b"a\nb\n"), [b"a", b"b"])

    def test_chunking_equivalence_exhaustive_two_way(self):
        stream = b"\nhello\n\nworld!!\nlast-no-eol"
        expected = all_two_way_splits(self.make, stream)
        self.assertEqual(expected, [b"", b"hello", b"", b"world!!"])

    def test_chunking_equivalence_random(self):
        rng = random.Random(9)
        alphabet = b"abc\n"
        stream = bytes(rng.choice(alphabet) for _ in range(500))
        random_chunkings(self.make, stream, rounds=200)

    def test_missing_delimiter_errors_not_unbounded_buffer(self):
        framer = self.make(max_frame_size=100)  # max_buffer = 101
        with self.assertRaises(BufferLimitExceededError) as ctx:
            framer.feed(b"x" * 200)
        self.assertEqual(ctx.exception.offset, 0)
        self.assertEqual(framer.buffered, 0)
        # 持续喂入仍报错，缓冲永不无限增长
        with self.assertRaises(BufferLimitExceededError):
            framer.feed(b"y" * 500)
        self.assertLessEqual(framer.buffer_peak, 600)

    def test_missing_delimiter_across_incremental_feeds(self):
        framer = self.make(max_frame_size=10)
        with self.assertRaises(BufferLimitExceededError):
            for _ in range(20):
                framer.feed(b"chunk")  # 缓冲逐步越过 max_buffer

    def test_frame_too_large_with_offset(self):
        framer = self.make(max_frame_size=4)
        self.assertEqual(framer.feed(b"ok\n"), [b"ok"])
        with self.assertRaises(FrameTooLargeError) as ctx:
            framer.feed(b"this-is-way-too-long\n")
        self.assertEqual(ctx.exception.offset, 3)  # "ok\n" 之后

    def test_buffer_bound(self):
        framer = self.make(max_frame_size=50)
        for _ in range(30):
            framer.feed(b"data\n")
        self.assertLessEqual(framer.buffer_peak, 50 + 1)


if __name__ == "__main__":
    unittest.main()
