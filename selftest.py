"""Self-test and exhaustive differential testing for SECDED."""

from __future__ import annotations

import itertools
import random
import sys
import time
import unittest

from brute_force_reference import BruteForceSECDED, ReferenceStatus
from secded import (
    SECDED,
    Status,
    UncorrectableCodewordError,
)


STATUS_MAP = {
    ReferenceStatus.NO_ERROR: Status.NO_ERROR,
    ReferenceStatus.SINGLE_ERROR_CORRECTED: Status.SINGLE_ERROR_CORRECTED,
    ReferenceStatus.UNCORRECTABLE_ERROR_DETECTED: Status.UNCORRECTABLE_ERROR_DETECTED,
}


def flip_bits(codeword: int, positions) -> int:
    for position in positions:
        codeword ^= 1 << position
    return codeword


def sampled_combinations(n: int, weight: int, count: int, rng: random.Random):
    total_combos = math_comb(n, weight)
    if total_combos <= count:
        return tuple(itertools.combinations(range(n), weight))

    samples = []
    seen = set()
    while len(samples) < count:
        candidate = tuple(sorted(rng.sample(range(n), weight)))
        if candidate not in seen:
            seen.add(candidate)
            samples.append(candidate)
    return tuple(samples)


def math_comb(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    result = 1
    for value in range(1, k + 1):
        result = result * (n - value + 1) // value
    return result


def assert_results_match(test_case: unittest.TestCase, code: SECDED,
                         reference: BruteForceSECDED, received: int) -> None:
    actual = code.decode(received)
    expected = reference.decode(received)

    test_case.assertEqual(actual.status, STATUS_MAP[expected.status])
    test_case.assertEqual(actual.error_position, expected.error_position)
    test_case.assertEqual(actual.corrected_codeword, expected.corrected_codeword)
    test_case.assertEqual(actual.data, expected.data)

    if expected.status is not ReferenceStatus.UNCORRECTABLE_ERROR_DETECTED:
        test_case.assertEqual(code.extract_data(actual.corrected_codeword), expected.data)
        test_case.assertEqual(code.encode(expected.data), expected.corrected_codeword)


class SECDEDTests(unittest.TestCase):
    def assert_code_matches_reference(self, data_bits: int, data_values,
                                      double_count: int, high_counts,
                                      seed: int) -> None:
        code = SECDED(data_bits)
        reference = BruteForceSECDED(data_bits)
        rng = random.Random(seed)

        self.assertEqual(code.hamming_length, reference.hamming_length)
        self.assertEqual(code.total_length, reference.total_length)

        for data in data_values:
            codeword = code.encode(data)
            self.assertTrue(reference.is_valid_codeword(codeword))
            assert_results_match(self, code, reference, codeword)

            for position in range(code.total_length):
                assert_results_match(
                    self,
                    code,
                    reference,
                    flip_bits(codeword, (position,)),
                )

            weights = (2,) + tuple(weight for weight, count in high_counts.items() if count)
            for weight in weights:
                count = double_count if weight == 2 else high_counts[weight]
                for positions in sampled_combinations(
                    code.total_length, weight, count, rng
                ):
                    assert_results_match(
                        self,
                        code,
                        reference,
                        flip_bits(codeword, positions),
                    )

    def test_basic_positioning_and_rejection_api(self) -> None:
        code = SECDED(4)
        self.assertEqual((code.data_bits, code.hamming_parity_bits, code.total_length), (4, 3, 8))
        self.assertEqual(code.data_positions, (3, 5, 6, 7))

        codeword = code.encode(0)
        clean = code.decode(codeword)
        self.assertIs(clean.status, Status.NO_ERROR)
        self.assertEqual(clean.data, 0)

        bad = code.decode(codeword ^ 0b0000_1100)
        self.assertIs(bad.status, Status.UNCORRECTABLE_ERROR_DETECTED)
        self.assertIsNone(bad.data)
        with self.assertRaises(UncorrectableCodewordError):
            bad.data_or_raise()

    def test_all_zero_all_one_and_random_widths(self) -> None:
        rng = random.Random(20260926)
        widths = [1, 2, 3, 4, 5, 7, 8, 11, 12, 16, 32, 64, 128, 256, 512, 1024]
        for width in widths:
            with self.subTest(width=width):
                values = [0, (1 << width) - 1]
                random_count = 20 if width <= 128 else 10
                values.extend(rng.getrandbits(width) for _ in range(random_count))

                if width >= 512:
                    double_count = 20
                    high_counts = {3: 20, 4: 10, 5: 10}
                elif width >= 128:
                    double_count = 60
                    high_counts = {3: 40, 4: 25, 5: 15}
                else:
                    double_count = 120
                    high_counts = {3: 80, 4: 50, 5: 30}

                self.assert_code_matches_reference(
                    width,
                    values,
                    double_count,
                    high_counts,
                    seed=1000 + width,
                )

    def test_exhaustive_data_and_errors_up_to_double_for_small_widths(self) -> None:
        for width in range(1, 12):
            code = SECDED(width)
            reference = BruteForceSECDED(width)
            for data in range(1 << width):
                codeword = code.encode(data)
                self.assertTrue(reference.is_valid_codeword(codeword))
                for weight in range(3):
                    for positions in itertools.combinations(
                        range(code.total_length), weight
                    ):
                        assert_results_match(
                            self,
                            code,
                            reference,
                            flip_bits(codeword, positions),
                        )

    def test_round_trip_and_scatter_tables_for_many_widths(self) -> None:
        rng = random.Random(77)
        for width in range(1, 257):
            code = SECDED(width)
            values = [0, (1 << width) - 1, rng.getrandbits(width)]
            if width <= 12:
                values.extend(range(1 << width))
            for data in values:
                codeword = code.encode(data)
                self.assertEqual(code.extract_data(codeword), data)
                self.assertEqual(code.decode(codeword).data, data)

    def test_single_error_locations(self) -> None:
        code = SECDED(8)
        data = 0b1010_0111
        codeword = code.encode(data)

        for position in range(code.total_length):
            result = code.decode(codeword ^ (1 << position))
            self.assertIs(result.status, Status.SINGLE_ERROR_CORRECTED)
            self.assertEqual(result.error_position, position)
            self.assertEqual(result.data, data)

    def test_derivation_text(self) -> None:
        code = SECDED(4)
        explanation = code.explain_decode(code.encode(5) ^ (1 << 6))
        self.assertIn("S = 6", explanation)
        self.assertIn("single_error_corrected", explanation)


def run_demo() -> None:
    code = SECDED(4)
    data = 0b1010
    encoded = code.encode_detail(data)
    print("=== SEC-DED derivation example, k=4, n=8 ===")
    print(f"data d3..d0 = 1010, codeword bits MSB..LSB = {encoded['codeword']:08b}")
    for equation in encoded["hamming_equations"]:
        positions = ",".join(map(str, equation["covered_positions"]))
        print(
            f"P{equation['parity_position']} = XOR(positions {positions}) "
            f"= {equation['value']}"
        )
    overall = encoded["overall_parity_bit"]
    print(f"P{overall['position']} = XOR(positions 1..7) = {overall['value']}")
    print()
    print(code.explain_decode(encoded["codeword"] ^ (1 << 6)))
    print()


def main() -> int:
    run_demo()
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SECDEDTests)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    elapsed = time.perf_counter() - started
    print(f"selftest elapsed: {elapsed:.3f}s")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
