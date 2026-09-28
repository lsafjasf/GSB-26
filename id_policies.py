"""Configurable identifier policies with generation classification.

Generations:
  1 - legacy ``legacy_ids.py`` timestamp + counter formats.
  2 - fixed ``secure_ids.py`` 32+ char URL-safe Base64 tokens.
  3 - configurable policies defined in this module.

All generation uses ``os.urandom()`` only; no time, PID, counter, or
user-space PRNG state is involved, so switching policies does not make
output predictable.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

MIN_ENTROPY_BITS = 128.0

ALPHABETS = {
    "base64url": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
    "base62": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "base32": "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
    "hex": "0123456789abcdef",
    "HEX": "0123456789ABCDEF",
    "digits": "0123456789",
}

GEN2_ALPHABET = ALPHABETS["base64url"]
GEN2_MIN_LENGTH = 32

_LEGACY_MIN_EPOCH_SECOND = 1_000_000_000  # 2001-09-09, below this is not a plausible legacy value
_LEGACY_MAX_EPOCH_SECOND = 4_000_000_000  # 2068, above this is not a plausible legacy value


@dataclass(frozen=True)
class IdPolicy:
    """One configurable identifier strategy (generation 3)."""

    name: str
    alphabet: str
    random_length: int
    prefix: str = ""
    segment_size: int = 0
    separator: str = "-"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("policy name must not be empty")
        if len(self.alphabet) < 2:
            raise ValueError("alphabet must contain at least 2 characters")
        if len(set(self.alphabet)) != len(self.alphabet):
            raise ValueError("alphabet must not contain duplicate characters")
        if isinstance(self.random_length, bool) or not isinstance(self.random_length, int):
            raise TypeError("random_length must be an integer")
        if self.random_length < 1:
            raise ValueError("random_length must be at least 1")
        if self.entropy_bits < MIN_ENTROPY_BITS:
            raise ValueError(
                f"policy {self.name!r} provides {self.entropy_bits:.1f} bits of entropy, "
                f"below the {MIN_ENTROPY_BITS:.0f}-bit floor"
            )
        if self.segment_size < 0:
            raise ValueError("segment_size must not be negative")
        if self.segment_size == 1:
            raise ValueError("segment_size of 1 is not a useful segmentation")
        if self.segment_size and self.segment_size >= self.random_length:
            raise ValueError("segment_size must be smaller than random_length")
        if self.segment_size and self.separator and self.separator in self.alphabet:
            raise ValueError("separator must not be part of the alphabet")
        if self.prefix and self.prefix[-1:] in self.alphabet and not self.prefix.endswith(
            ("_", "-", ":", ".")
        ):
            raise ValueError("prefix must end with a delimiter outside the alphabet")

    @property
    def bits_per_char(self) -> float:
        return math.log2(len(self.alphabet))

    @property
    def entropy_bits(self) -> float:
        return self.random_length * self.bits_per_char

    @property
    def separator_count(self) -> int:
        if not self.segment_size:
            return 0
        return (self.random_length - 1) // self.segment_size

    @property
    def total_length(self) -> int:
        return len(self.prefix) + self.random_length + self.separator_count


POLICIES: dict[str, IdPolicy] = {}


def _register(policy: IdPolicy) -> IdPolicy:
    POLICIES[policy.name] = policy
    return policy


v3_standard = _register(
    IdPolicy(
        name="v3_standard",
        alphabet=ALPHABETS["base64url"],
        random_length=32,
        prefix="s3_",
        description="Prefixed URL-safe Base64, drop-in replacement for generation 2.",
    )
)
v3_segmented = _register(
    IdPolicy(
        name="v3_segmented",
        alphabet=ALPHABETS["base32"],
        random_length=32,
        segment_size=4,
        description="Uppercase Base32 in 4-character groups, human-readable.",
    )
)
v3_hex = _register(
    IdPolicy(
        name="v3_hex",
        alphabet=ALPHABETS["hex"],
        random_length=40,
        prefix="hx_",
        description="Lowercase hex with type tag, easy to embed in logs.",
    )
)
v3_compact = _register(
    IdPolicy(
        name="v3_compact",
        alphabet=ALPHABETS["base62"],
        random_length=22,
        description="Short unprefixed Base62 for space-constrained storage.",
    )
)

DEFAULT_POLICY = v3_standard


def _random_chars(alphabet: str, length: int) -> str:
    size = len(alphabet)
    mask = (1 << (size - 1).bit_length()) - 1
    out: list[str] = []
    while len(out) < length:
        for byte in os.urandom(length * 2):
            index = byte & mask
            if index < size:
                out.append(alphabet[index])
                if len(out) == length:
                    break
    return "".join(out)


def generate_id(policy: IdPolicy = DEFAULT_POLICY) -> str:
    """Return a random identifier under ``policy`` using only ``os.urandom()``."""
    body = _random_chars(policy.alphabet, policy.random_length)
    if policy.segment_size:
        body = policy.separator.join(
            body[start : start + policy.segment_size]
            for start in range(0, policy.random_length, policy.segment_size)
        )
    return policy.prefix + body


def _birthday_log10_probability(count: int, entropy_bits: float) -> float:
    return 2 * math.log10(count) - math.log10(2) - entropy_bits * math.log10(2)


def estimate_policy(policy: IdPolicy) -> dict[str, object]:
    """Return length and entropy estimates for one policy."""
    return {
        "policy": policy.name,
        "generation": 3,
        "alphabet_size": len(policy.alphabet),
        "prefix": policy.prefix,
        "random_chars": policy.random_length,
        "segment_size": policy.segment_size,
        "separator_count": policy.separator_count,
        "total_length": policy.total_length,
        "bits_per_char": round(policy.bits_per_char, 4),
        "entropy_bits": round(policy.entropy_bits, 2),
        "collision_probability_1m": f"10^{_birthday_log10_probability(1_000_000, policy.entropy_bits):.1f}",
        "description": policy.description,
    }


def legacy_estimates() -> list[dict[str, object]]:
    """Length and entropy estimates for generations 1 and 2."""
    return [
        {
            "policy": "legacy_session",
            "generation": 1,
            "alphabet_size": 10,
            "prefix": "",
            "random_chars": 6,
            "segment_size": 0,
            "separator_count": 0,
            "total_length": 16,
            "bits_per_char": round(math.log2(10), 4),
            "entropy_bits": round(math.log2(1_000_000), 2),
            "collision_probability_1m": "~1 (counter resets per process per second)",
            "description": "10-digit epoch second + 6-digit counter; timestamp is public, only the counter varies.",
        },
        {
            "policy": "legacy_salt",
            "generation": 1,
            "alphabet_size": 10,
            "prefix": "",
            "random_chars": 0,
            "segment_size": 0,
            "separator_count": 0,
            "total_length": 8,
            "bits_per_char": round(math.log2(10), 4),
            "entropy_bits": 0.0,
            "collision_probability_1m": "~1 (fully determined by the clock)",
            "description": "First 8 digits of the epoch second; no random component at all.",
        },
        {
            "policy": "secure_ids_default",
            "generation": 2,
            "alphabet_size": 64,
            "prefix": "",
            "random_chars": 32,
            "segment_size": 0,
            "separator_count": 0,
            "total_length": 32,
            "bits_per_char": 6.0,
            "entropy_bits": 192.0,
            "collision_probability_1m": f"10^{_birthday_log10_probability(1_000_000, 192):.1f}",
            "description": "Fixed-shape 32-char URL-safe Base64 token from secure_ids.py.",
        },
    ]


@dataclass(frozen=True)
class Classification:
    """Deterministic per-record classification result."""

    value: str
    matched: bool
    generation: int | None
    policy: str | None
    rule: str
    reason: str
    candidates: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "matched": self.matched,
            "generation": self.generation,
            "policy": self.policy,
            "rule": self.rule,
            "reason": self.reason,
            "candidates": list(self.candidates),
        }


def _match_policy(policy: IdPolicy, value: str) -> str | None:
    """Return None if ``value`` matches ``policy`` exactly, else a reason."""
    if policy.prefix:
        if not value.startswith(policy.prefix):
            return f"missing prefix {policy.prefix!r}"
        body = value[len(policy.prefix) :]
    else:
        if value.startswith(policy.separator) and policy.segment_size:
            return "unexpected leading separator"
        body = value

    if policy.segment_size:
        parts = body.split(policy.separator)
        full_groups, remainder = divmod(policy.random_length, policy.segment_size)
        expected_sizes = [policy.segment_size] * full_groups + ([remainder] if remainder else [])
        if len(parts) != len(expected_sizes):
            return f"expected {len(expected_sizes)} segments, found {len(parts)}"
        for index, (part, size) in enumerate(zip(parts, expected_sizes)):
            if len(part) != size:
                return f"segment {index} has length {len(part)}, expected {size}"
        chars = "".join(parts)
    else:
        chars = body

    if len(chars) != policy.random_length:
        return f"random part has {len(chars)} characters, expected {policy.random_length}"
    invalid = sorted(set(chars) - set(policy.alphabet))
    if invalid:
        return f"characters outside alphabet: {''.join(invalid)!r}"
    return None


def _match_legacy(value: str) -> tuple[str, str] | None:
    if not value.isdigit():
        return None
    length = len(value)
    if length == 8:
        return (
            "legacy_salt",
            "8 digits: truncated epoch-second prefix used as the old salt",
        )
    if 10 <= length <= 16:
        second = int(value[:10])
        if _LEGACY_MIN_EPOCH_SECOND <= second <= _LEGACY_MAX_EPOCH_SECOND:
            counter = value[10:] or "(truncated)"
            return (
                "legacy_session",
                f"10-digit epoch second {second} plus counter {counter!r}",
            )
    return None


def _matches_gen2(value: str) -> bool:
    return len(value) >= GEN2_MIN_LENGTH and bool(value) and set(value) <= set(GEN2_ALPHABET)


def classify_id(value: str) -> Classification:
    """Classify one historical identifier into its generation.

    The result is deterministic and self-describing: ``rule`` and ``reason``
    let each record be checked by hand or re-verified with
    :func:`verify_classification`.
    """
    candidates: list[str] = []
    failures: list[str] = []
    for policy in POLICIES.values():
        problem = _match_policy(policy, value)
        if problem is None:
            candidates.append(policy.name)
        else:
            failures.append(f"{policy.name}: {problem}")

    if candidates:
        primary = candidates[0]
        note = "exact shape match"
        if len(candidates) > 1:
            note = "ambiguous: matches multiple policies, first by registry order reported"
        return Classification(
            value=value,
            matched=True,
            generation=3,
            policy=primary,
            rule="generation-3-policy-shape",
            reason=f"{note}: {', '.join(candidates)}",
            candidates=tuple(candidates),
        )

    legacy = _match_legacy(value)
    if legacy is not None:
        kind, reason = legacy
        return Classification(
            value=value,
            matched=True,
            generation=1,
            policy=kind,
            rule="legacy-digit-format",
            reason=reason,
        )

    if _matches_gen2(value):
        return Classification(
            value=value,
            matched=True,
            generation=2,
            policy="secure_ids_default",
            rule="gen2-charset-and-length",
            reason=f"{len(value)} URL-safe Base64 characters, no generation-3 policy matched",
        )

    return Classification(
        value=value,
        matched=False,
        generation=None,
        policy=None,
        rule="no-match",
        reason="; ".join(failures) if failures else "empty or unrecognised value",
    )


def verify_classification(value: str, expected: Classification) -> bool:
    """Re-run classification and confirm it reproduces ``expected`` exactly."""
    return classify_id(value) == expected
