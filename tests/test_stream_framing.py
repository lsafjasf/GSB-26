from __future__ import annotations

import random
import struct
import unittest

from stream_framing import (
    BufferLimitError,
    DelimiterFramer,
    FramingError,
    LengthLimitError,
    LengthPrefixFramer,
    encode_length_prefix,
)


def all_partitions(data: bytes):
    if not data:
        yield [b""]
        return

    for mask in range(1 << (len(data) - 1)):
        chunks = []
        start = 0
        for index in range(len(data) - 1):
            if mask & (1 << index):
                chunks.append(data[start : index + 1])
                start = index + 1
        chunks.append(data[start:])
        yield chunks


def random_partitions(data: bytes, count: int, seed: int):
    rng = random.Random(seed)
    for _ in range(count):
        cuts = sorted(rng.randrange(1, len(data)) for _ in range(rng.randrange(1, 10)))
        cuts = [0] + cuts + [len(data)]
        yield [data[cuts[i] : cuts[i + 1]] for i in range(len(cuts) - 1)]


def decode_length_prefix(stream: bytes, max_payload_size: int = 64 * 1024):
    framer = LengthPrefixFramer(max_payload_size=max_payload_size)
    return framer.feed(stream)


def decode_delimiter(
    stream: bytes,
    delimiter: bytes = b"\n",
    max_message_size: int = 64 * 1024,
):
    framer = DelimiterFramer(
        delimiter=delimiter, max_message_size=max_message_size
    )
    return framer.feed(stream)


class LengthPrefixFramerTests(unittest.TestCase):
    def test_empty_feed(self):
        framer = LengthPrefixFramer()
        self.assertEqual(framer.feed(b""), [])
        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.buffer_capacity, 0)
        self.assertEqual(framer.peak_buffer_size, 0)

    def test_zero_length_packet(self):
        framer = LengthPrefixFramer(max_payload_size=8)
        self.assertEqual(framer.feed(struct.pack(">I", 0)), [b""])
        self.assertEqual(framer.buffer_size, 0)

    def test_all_split_points_are_equivalent(self):
        messages = [b"", b"a", b"bc"]
        stream = b"".join(encode_length_prefix(message) for message in messages)
        expected = decode_length_prefix(stream)

        for chunks in all_partitions(stream):
            framer = LengthPrefixFramer()
            produced = []
            for chunk in chunks:
                produced.extend(framer.feed(chunk))
            self.assertEqual(produced, expected)

        self.assertEqual(expected, messages)

    def test_random_split_points_are_equivalent(self):
        rng = random.Random(26026)
        messages = [
            bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            for _ in range(12)
        ]
        stream = b"".join(encode_length_prefix(message) for message in messages)
        expected = decode_length_prefix(stream)

        for chunks in random_partitions(stream, 200, seed=926):
            framer = LengthPrefixFramer()
            produced = []
            for chunk in chunks:
                produced.extend(framer.feed(chunk))
            self.assertEqual(produced, expected)

    def test_packet_spans_more_than_three_feeds(self):
        message = b"0123456789-abcdef"
        stream = encode_length_prefix(message)
        chunks = [stream[0:2], stream[2:4], stream[4:9], stream[9:13], stream[13:]]

        framer = LengthPrefixFramer()
        produced = []
        for chunk in chunks:
            produced.extend(framer.feed(chunk))

        self.assertEqual(produced, [message])

    def test_many_packets_arrive_together(self):
        messages = [b"alpha", b"", b"beta", b"gamma"]
        stream = b"".join(encode_length_prefix(message) for message in messages)

        self.assertEqual(LengthPrefixFramer().feed(stream), messages)

    def test_payload_may_contain_delimiter_bytes(self):
        message = b"line one\nline two\r\nline three"

        self.assertEqual(
            LengthPrefixFramer().feed(encode_length_prefix(message)),
            [message],
        )

    def test_malicious_declared_length_is_rejected_with_frame_offset(self):
        framer = LengthPrefixFramer(max_payload_size=8)
        malicious = struct.pack(">I", 9)

        with self.assertRaises(LengthLimitError) as caught:
            framer.feed(malicious[:2])
            framer.feed(malicious[2:] + b"payload")

        error = caught.exception
        self.assertEqual(error.frame_offset, 0)
        self.assertEqual(error.stream_offset, 4)
        self.assertEqual(error.limit, 8)
        self.assertEqual(framer.peak_buffer_size, 0)

        with self.assertRaises(FramingError):
            framer.feed(b"more")

    def test_huge_malicious_chunk_does_not_enter_buffer(self):
        framer = LengthPrefixFramer(max_payload_size=64 * 1024)
        stream = struct.pack(">I", 10 * 1024 * 1024) + b"x" * (1024 * 1024)

        with self.assertRaises(LengthLimitError) as caught:
            framer.feed(stream)

        self.assertEqual(caught.exception.frame_offset, 0)
        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.buffer_capacity, 0)
        self.assertEqual(framer.peak_buffer_size, 0)

    def test_fragmented_buffer_is_bounded_and_reused(self):
        framer = LengthPrefixFramer(max_payload_size=8, max_buffer_size=8)

        framer.feed(struct.pack(">I", 8) + b"abc")
        self.assertEqual(framer.buffer_size, 3)
        self.assertEqual(framer.buffer_capacity, 8)
        self.assertEqual(framer.peak_buffer_size, 3)

        framer.feed(b"de")
        self.assertEqual(framer.buffer_size, 5)
        self.assertEqual(framer.buffer_capacity, 8)

        self.assertEqual(framer.feed(b"fgh"), [b"abcdefgh"])
        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.peak_buffer_size, 8)

    def test_zero_max_payload_allows_only_zero_frames(self):
        stream = encode_length_prefix(b"") * 3
        framer = LengthPrefixFramer(max_payload_size=0, max_buffer_size=0)

        self.assertEqual(framer.feed(stream), [b"", b"", b""])
        self.assertEqual(framer.buffer_capacity, 0)

    def test_header_without_payload_does_not_allocate_buffer(self):
        framer = LengthPrefixFramer(max_payload_size=8)
        framer.feed(struct.pack(">I", 5))

        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.buffer_capacity, 0)

    def test_malicious_offset_follows_previous_frame(self):
        first = encode_length_prefix(b"ok")
        malicious = struct.pack(">I", 9)
        framer = LengthPrefixFramer(max_payload_size=8)

        with self.assertRaises(LengthLimitError) as caught:
            framer.feed(first + malicious)

        self.assertEqual(caught.exception.frame_offset, len(first))


class DelimiterFramerTests(unittest.TestCase):
    def test_empty_feed(self):
        framer = DelimiterFramer()
        self.assertEqual(framer.feed(b""), [])
        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.peak_buffer_size, 0)

    def test_zero_length_packets(self):
        self.assertEqual(DelimiterFramer().feed(b"\n\n"), [b"", b""])
        self.assertEqual(DelimiterFramer().feed(b"a\n\n"), [b"a", b""])

    def test_all_split_points_are_equivalent(self):
        stream = b"alpha\n\nbeta\ngamma\n"
        expected = decode_delimiter(stream)

        for chunks in all_partitions(stream):
            framer = DelimiterFramer()
            produced = []
            for chunk in chunks:
                produced.extend(framer.feed(chunk))
            self.assertEqual(produced, expected)

        self.assertEqual(expected, [b"alpha", b"", b"beta", b"gamma"])

    def test_random_split_points_are_equivalent(self):
        messages = [b"", b"one", b"two two", b"\xff\x00\xfe", b"four"]
        stream = b"\n".join(messages) + b"\n"
        expected = decode_delimiter(stream)

        for chunks in random_partitions(stream, 200, seed=326):
            framer = DelimiterFramer()
            produced = []
            for chunk in chunks:
                produced.extend(framer.feed(chunk))
            self.assertEqual(produced, expected)

    def test_delimiter_inside_stream_marks_a_boundary(self):
        self.assertEqual(
            DelimiterFramer().feed(b"packet\nbody"),
            [b"packet"],
        )

    def test_many_packets_arrive_together(self):
        stream = b"one\ntwo\n\nthree\n"
        self.assertEqual(
            DelimiterFramer().feed(stream),
            [b"one", b"two", b"", b"three"],
        )

    def test_multibyte_delimiter_spans_three_feeds(self):
        framer = DelimiterFramer(delimiter=b"\r\n\r\n", max_message_size=16)

        self.assertEqual(framer.feed(b"payload\r"), [])
        self.assertEqual(framer.feed(b"\n\r"), [])
        self.assertEqual(framer.feed(b"\nremaining"), [b"payload"])

    def test_multibyte_split_equivalence(self):
        stream = b"a\r\n\r\nbb\r\n\r\n"
        expected = decode_delimiter(stream, delimiter=b"\r\n\r\n")

        for chunks in all_partitions(stream):
            framer = DelimiterFramer(delimiter=b"\r\n\r\n")
            produced = []
            for chunk in chunks:
                produced.extend(framer.feed(chunk))
            self.assertEqual(produced, expected)

        self.assertEqual(expected, [b"a", b"bb"])

    def test_overlapping_delimiter_candidates(self):
        framer = DelimiterFramer(delimiter=b"aaaa", max_message_size=8)
        self.assertEqual(framer.feed(b"xxaaaa"), [b"xx"])
        self.assertEqual(framer.feed(b"aaaab"), [b""])

    def test_missing_delimiter_reports_limit_and_frame_offset(self):
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=3)
        framer.feed(b"abc")

        with self.assertRaises(BufferLimitError) as caught:
            framer.feed(b"x")

        error = caught.exception
        self.assertEqual(error.frame_offset, 0)
        self.assertEqual(error.stream_offset, 3)
        self.assertEqual(error.limit, 3)
        self.assertEqual(framer.buffer_size, 3)

    def test_limit_error_uses_current_frame_offset(self):
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=3)
        framer.feed(b"abc\n")
        framer.feed(b"xyz")

        with self.assertRaises(BufferLimitError) as caught:
            framer.feed(b"!")

        self.assertEqual(caught.exception.frame_offset, 4)
        self.assertEqual(caught.exception.stream_offset, 7)

    def test_huge_chunk_without_delimiter_is_bounded(self):
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=64 * 1024)

        with self.assertRaises(BufferLimitError):
            framer.feed(b"x" * (1024 * 1024))

        self.assertEqual(framer.buffer_size, 64 * 1024)
        self.assertEqual(framer.buffer_capacity, 64 * 1024)
        self.assertEqual(framer.peak_buffer_size, 64 * 1024)

    def test_maximum_complete_message_can_still_be_emitted(self):
        framer = DelimiterFramer(delimiter=b"\n", max_message_size=8)
        self.assertEqual(framer.feed(b"12345678\n"), [b"12345678"])
        self.assertEqual(framer.buffer_size, 0)
        self.assertEqual(framer.peak_buffer_size, 8)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            DelimiterFramer(delimiter=b"")
        with self.assertRaises(ValueError):
            LengthPrefixFramer(max_payload_size=8, max_buffer_size=7)


if __name__ == "__main__":
    unittest.main()
