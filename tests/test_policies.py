from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import id_policies
import secure_ids


class PolicyValidationTests(unittest.TestCase):
    def test_entropy_floor_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(name="weak", alphabet=id_policies.ALPHABETS["hex"], random_length=16)
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(name="tiny", alphabet=id_policies.ALPHABETS["digits"], random_length=20)

    def test_alphabet_must_have_unique_characters(self) -> None:
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(name="dup", alphabet="aabc", random_length=64)

    def test_separator_must_not_collide_with_alphabet(self) -> None:
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(
                name="badsep",
                alphabet=id_policies.ALPHABETS["base64url"],
                random_length=32,
                segment_size=4,
            )

    def test_segment_size_bounds(self) -> None:
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(
                name="seg1",
                alphabet=id_policies.ALPHABETS["base32"],
                random_length=32,
                segment_size=1,
            )
        with self.assertRaises(ValueError):
            id_policies.IdPolicy(
                name="segbig",
                alphabet=id_policies.ALPHABETS["base32"],
                random_length=32,
                segment_size=32,
            )

    def test_custom_policy_above_floor_is_accepted(self) -> None:
        policy = id_policies.IdPolicy(
            name="custom",
            alphabet=id_policies.ALPHABETS["HEX"],
            random_length=32,
            prefix="cu_",
        )
        value = id_policies.generate_id(policy)
        self.assertTrue(value.startswith("cu_"))
        self.assertEqual(35, len(value))
        self.assertTrue(set(value[3:]).issubset(set(id_policies.ALPHABETS["HEX"])))


class PolicyGenerationTests(unittest.TestCase):
    def test_shape_matches_each_registered_policy(self) -> None:
        for policy in id_policies.POLICIES.values():
            with self.subTest(policy=policy.name):
                for _ in range(50):
                    value = id_policies.generate_id(policy)
                    self.assertEqual(policy.total_length, len(value))
                    self.assertTrue(value.startswith(policy.prefix))
                    body = value[len(policy.prefix):]
                    if policy.segment_size:
                        parts = body.split(policy.separator)
                        self.assertEqual(policy.separator_count + 1, len(parts))
                        self.assertTrue(all(len(part) == policy.segment_size for part in parts))
                        body = "".join(parts)
                    self.assertEqual(policy.random_length, len(body))
                    self.assertTrue(set(body).issubset(set(policy.alphabet)))

    def test_case_and_charset_are_respected(self) -> None:
        hex_value = id_policies.generate_id(id_policies.POLICIES["v3_hex"])
        self.assertEqual(hex_value[3:], hex_value[3:].lower())
        segmented = id_policies.generate_id(id_policies.POLICIES["v3_segmented"])
        self.assertEqual(segmented.replace("-", ""), segmented.replace("-", "").upper())

    def test_policy_switch_keeps_output_unique_and_unpredictable(self) -> None:
        seen: set[str] = set()
        previous_body = ""
        for policy in list(id_policies.POLICIES.values()) * 2:
            batch = [id_policies.generate_id(policy) for _ in range(2_000)]
            self.assertEqual(len(batch), len(set(batch)))
            overlap = seen.intersection(batch)
            self.assertEqual(set(), overlap)
            seen.update(batch)
            bodies = [value[len(policy.prefix):].replace(policy.separator, "") for value in batch]
            for body in bodies:
                prefix = 0
                for a, b in zip(body, previous_body):
                    if a != b:
                        break
                    prefix += 1
                self.assertLessEqual(prefix, 8)
                previous_body = body

    def test_entropy_estimates_are_consistent(self) -> None:
        for policy in id_policies.POLICIES.values():
            estimate = id_policies.estimate_policy(policy)
            self.assertAlmostEqual(
                policy.random_length * math.log2(len(policy.alphabet)),
                estimate["entropy_bits"],
                places=2,
            )
            self.assertEqual(policy.total_length, estimate["total_length"])
            self.assertGreaterEqual(estimate["entropy_bits"], id_policies.MIN_ENTROPY_BITS)


class ClassificationTests(unittest.TestCase):
    def _expected_corpus(self) -> list[tuple[str, int, str]]:
        rows: list[tuple[str, int, str]] = []
        rows.append(("1700000000000000", 1, "legacy_session"))
        rows.append(("1700000000", 1, "legacy_session"))
        rows.append(("1700000000000042"[:16], 1, "legacy_session"))
        rows.append(("17000000", 1, "legacy_salt"))
        for _ in range(20):
            rows.append((secure_ids.generate_session_id(), 2, "secure_ids_default"))
        for policy in id_policies.POLICIES.values():
            for _ in range(20):
                rows.append((id_policies.generate_id(policy), 3, policy.name))
        return rows

    def test_each_record_classifies_into_its_generation(self) -> None:
        for value, generation, policy_name in self._expected_corpus():
            with self.subTest(value=value):
                result = id_policies.classify_id(value)
                self.assertTrue(result.matched)
                self.assertEqual(generation, result.generation)
                self.assertEqual(policy_name, result.policy)
                self.assertTrue(id_policies.verify_classification(value, result))

    def test_classification_is_deterministic(self) -> None:
        for value, _, _ in self._expected_corpus():
            with self.subTest(value=value):
                self.assertEqual(id_policies.classify_id(value), id_policies.classify_id(value))

    def test_unknown_values_are_rejected_with_reasons(self) -> None:
        for value in ("", "not-an-id", "170000000000000X", "s3_tooshort", "hx_ZZ99", "abc"):
            with self.subTest(value=value):
                result = id_policies.classify_id(value)
                self.assertFalse(result.matched)
                self.assertIsNone(result.generation)
                self.assertEqual("no-match", result.rule)
                self.assertTrue(result.reason)

    def test_gen2_values_are_not_shadowed_by_prefixed_policies(self) -> None:
        crafted = "s3_" + "a" * 29  # prefix matches but body length does not
        result = id_policies.classify_id(crafted)
        self.assertEqual(2, result.generation)
        self.assertEqual("secure_ids_default", result.policy)

    def test_malformed_segmentation_is_not_gen3(self) -> None:
        value = "7M52-U2ZO-PLIB"  # too few segments for v3_segmented
        result = id_policies.classify_id(value)
        self.assertNotEqual(3, result.generation)


if __name__ == "__main__":
    unittest.main(verbosity=2)
