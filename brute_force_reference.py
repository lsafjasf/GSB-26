"""Independent brute-force SEC-DED reference decoder.

This module intentionally does not import ``secded``.  It rebuilds the parity
equations directly, accepts a received word only when every check is zero, and
tests every possible one-bit flip to decide whether a unique valid codeword is
at Hamming distance one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReferenceStatus(str, Enum):
    NO_ERROR = "no_error"
    SINGLE_ERROR_CORRECTED = "single_error_corrected"
    UNCORRECTABLE_ERROR_DETECTED = "uncorrectable_error_detected"


@dataclass(frozen=True)
class ReferenceResult:
    status: ReferenceStatus
    received: int
    error_position: Optional[int]
    corrected_codeword: Optional[int]
    data: Optional[int]
    reason: str


class BruteForceSECDED:
    def __init__(self, data_bits: int) -> None:
        if data_bits <= 0:
            raise ValueError("data_bits must be positive")
        self.data_bits = data_bits
        parity_bits = 0
        while (1 << parity_bits) < data_bits + parity_bits + 1:
            parity_bits += 1
        self.hamming_parity_bits = parity_bits
        self.total_parity_bits = parity_bits + 1
        self.hamming_length = data_bits + parity_bits
        self.total_length = self.hamming_length + 1

        self.hamming_parity_positions = tuple(
            1 << i for i in range(parity_bits)
        )
        self.data_positions = tuple(
            position
            for position in range(1, self.hamming_length + 1)
            if not (position & (position - 1)) == 0
        )
        self.hamming_masks = tuple(
            sum(
                1 << position
                for position in range(1, self.hamming_length + 1)
                if position & parity_position
            )
            for parity_position in self.hamming_parity_positions
        )

    def is_valid_codeword(self, codeword: int) -> bool:
        if codeword.bit_count() & 1:
            return False
        for mask in self.hamming_masks:
            if (codeword & mask).bit_count() & 1:
                return False
        return True

    def extract_data(self, codeword: int) -> int:
        data = 0
        for data_bit, position in enumerate(self.data_positions):
            data |= ((codeword >> position) & 1) << data_bit
        return data

    def decode(self, received: int) -> ReferenceResult:
        if self.is_valid_codeword(received):
            return ReferenceResult(
                ReferenceStatus.NO_ERROR,
                received,
                None,
                received,
                self.extract_data(received),
                "received word itself satisfies all parity equations",
            )

        neighbors = []
        for position in range(self.total_length):
            candidate = received ^ (1 << position)
            if self.is_valid_codeword(candidate):
                neighbors.append((position, candidate))

        if len(neighbors) == 1:
            position, candidate = neighbors[0]
            return ReferenceResult(
                ReferenceStatus.SINGLE_ERROR_CORRECTED,
                received,
                position,
                candidate,
                self.extract_data(candidate),
                f"only bit {position} flip reaches a valid codeword",
            )

        if not neighbors:
            reason = "neither the word nor any one-bit flip is a valid codeword"
        else:
            reason = "multiple one-bit flips reach valid codewords; correction is ambiguous"
        return ReferenceResult(
            ReferenceStatus.UNCORRECTABLE_ERROR_DETECTED,
            received,
            None,
            None,
            None,
            reason,
        )
