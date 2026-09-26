from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import threading
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import legacy_ids
import secure_ids


def _worker_batch(batch_size: int) -> list[str]:
    return [secure_ids.generate_session_id() for _ in range(batch_size)]


class LegacyDefectRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_frozen = os.environ.pop("LEGACY_FROZEN_SECOND", None)
        self._old_race = os.environ.pop("LEGACY_ENABLE_RACE_BARRIER", None)
        legacy_ids.reset_counter()

    def tearDown(self) -> None:
        if self._old_frozen is None:
            os.environ.pop("LEGACY_FROZEN_SECOND", None)
        else:
            os.environ["LEGACY_FROZEN_SECOND"] = self._old_frozen
        if self._old_race is None:
            os.environ.pop("LEGACY_ENABLE_RACE_BARRIER", None)
        else:
            os.environ["LEGACY_ENABLE_RACE_BARRIER"] = self._old_race

    def test_processes_starting_in_same_second_collide(self) -> None:
        env = dict(os.environ)
        env["LEGACY_FROZEN_SECOND"] = "1700000000"
        code = "import legacy_ids; print(legacy_ids.generate_session_id(16))"
        values = {
            subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            for _ in range(3)
        }
        self.assertEqual(len(values), 1)

    def test_consecutive_values_follow_timestamp_and_counter_pattern(self) -> None:
        os.environ["LEGACY_FROZEN_SECOND"] = "1700000000"
        values = [legacy_ids.generate_session_id(16) for _ in range(3)]
        self.assertEqual(3, len(set(values)))
        self.assertTrue(all(value.startswith("1700000000") for value in values))
        self.assertEqual(["000000", "000001", "000002"], [value[10:] for value in values])

    def test_concurrent_counter_race_duplicates_values(self) -> None:
        workers = 16
        os.environ["LEGACY_ENABLE_RACE_BARRIER"] = "1"
        start = threading.Barrier(workers)

        def racy_call() -> str:
            start.wait()
            return legacy_ids.generate_session_id(16)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            values = list(pool.map(lambda _: racy_call(), range(workers)))

        self.assertLess(len(set(values)), workers)

    def test_default_length_is_below_security_floor(self) -> None:
        value = legacy_ids.generate_session_id()
        self.assertLess(len(value), secure_ids.MIN_SESSION_ID_LENGTH)


class SecureIdentifierTests(unittest.TestCase):
    def test_length_floor_and_character_set(self) -> None:
        allowed = set(secure_ids.ALPHABET)
        for requested in range(32, 41):
            session_id = secure_ids.generate_session_id(requested)
            salt = secure_ids.generate_salt(requested)
            self.assertEqual(requested, len(session_id))
            self.assertEqual(requested, len(salt))
            self.assertTrue(set(session_id).issubset(allowed))
            self.assertTrue(set(salt).issubset(allowed))

        with self.assertRaises(ValueError):
            secure_ids.generate_session_id(31)
        with self.assertRaises(ValueError):
            secure_ids.generate_salt(31)

    def test_consecutive_values_are_unpredictable_and_not_prefix_related(self) -> None:
        values = [secure_ids.generate_session_id() for _ in range(10_000)]
        self.assertEqual(len(values), len(set(values)))
        adjacent_prefixes = [
            common_prefix(values[index], values[index + 1])
            for index in range(len(values) - 1)
        ]
        self.assertLessEqual(max(adjacent_prefixes), 8)

        counts = Counter("".join(values))
        expected = len(values) * 32 / 64
        self.assertLess(chi_square(counts, expected), 120)
        self.assertTrue(all(counts[symbol] > 0 for symbol in secure_ids.ALPHABET))
        self.assertAlmostEqual(0.5, bit_distribution(values)["one_fraction"], delta=0.02)

    def test_threads_and_processes_do_not_duplicate_values(self) -> None:
        thread_workers = 32
        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_workers) as pool:
            thread_batches = list(pool.map(_worker_batch, [256] * thread_workers))
        thread_values = [value for batch in thread_batches for value in batch]
        self.assertEqual(len(thread_values), len(set(thread_values)))

        with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
            process_batches = list(pool.map(_worker_batch, [128] * 8))
        process_values = [value for batch in process_batches for value in batch]
        self.assertEqual(len(process_values), len(set(process_values)))


def common_prefix(left: str, right: str) -> int:
    size = 0
    for a, b in zip(left, right):
        if a != b:
            break
        size += 1
    return size


def chi_square(counts: Counter[str], expected: float) -> float:
    return sum((counts[symbol] - expected) ** 2 / expected for symbol in secure_ids.ALPHABET)


def bit_distribution(values: list[str]) -> dict[str, float]:
    bits_per_symbol = {symbol: f"{index:06b}" for index, symbol in enumerate(secure_ids.ALPHABET)}
    total_bits = 0
    one_bits = 0
    for value in values:
        for symbol in value:
            bits = bits_per_symbol[symbol]
            total_bits += len(bits)
            one_bits += bits.count("1")
    return {"one_fraction": one_bits / total_bits}


if __name__ == "__main__":
    unittest.main(verbosity=2)
