"""SEC-DED extended Hamming coding using only the Python standard library.

Bit positions in the transmitted integer are zero based:

* bit 0: overall even-parity bit P0;
* bits 1..h: Hamming section, where powers of two are Hamming parity bits;
* all other positions in the Hamming section contain data bits.

Data bit i is placed at the i-th non-power-of-two position in increasing
order.  For example, k=4 uses transmitted positions 3,5,6,7 for d0..d3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(str, Enum):
    NO_ERROR = "no_error"
    SINGLE_ERROR_CORRECTED = "single_error_corrected"
    UNCORRECTABLE_ERROR_DETECTED = "uncorrectable_error_detected"


class UncorrectableCodewordError(ValueError):
    """Raised when a caller asks for data from a rejected codeword."""


@dataclass(frozen=True)
class DecodeResult:
    """Decoder conclusion.

    ``data`` and ``corrected_codeword`` are ``None`` for a rejected block.
    ``error_position`` is a zero-based index into the transmitted integer.
    """

    status: Status
    received: int
    syndrome: int
    overall_parity_check: int
    data: Optional[int]
    corrected_codeword: Optional[int]
    error_position: Optional[int]
    error_data_bit: Optional[int]
    reason: str

    @property
    def rejected(self) -> bool:
        return self.status is Status.UNCORRECTABLE_ERROR_DETECTED

    def data_or_raise(self) -> int:
        if self.rejected:
            raise UncorrectableCodewordError(self.reason)
        assert self.data is not None
        return self.data


def _required_hamming_parity_bits(data_bits: int) -> int:
    parity_bits = 0
    while (1 << parity_bits) < data_bits + parity_bits + 1:
        parity_bits += 1
    return parity_bits


class SECDED:
    """Configurable-width single-error-correcting, double-error-detecting code."""

    def __init__(self, data_bits: int) -> None:
        if isinstance(data_bits, bool) or not isinstance(data_bits, int):
            raise TypeError("data_bits must be an integer")
        if data_bits <= 0:
            raise ValueError("data_bits must be positive")

        self.data_bits = data_bits
        self.hamming_parity_bits = _required_hamming_parity_bits(data_bits)
        self.total_parity_bits = self.hamming_parity_bits + 1
        self.hamming_length = data_bits + self.hamming_parity_bits
        self.total_length = self.hamming_length + 1

        self.hamming_parity_positions = tuple(
            1 << offset for offset in range(self.hamming_parity_bits)
        )
        self.data_positions = tuple(
            position
            for position in range(1, self.hamming_length + 1)
            if not (position & (position - 1)) == 0
        )
        if len(self.data_positions) != data_bits:
            raise RuntimeError("internal Hamming position construction error")

        self.position_to_data_bit = {
            position: index for index, position in enumerate(self.data_positions)
        }
        self._codeword_mask = (1 << self.total_length) - 1
        self._hamming_mask = ((1 << (self.hamming_length + 1)) - 1) & ~1

        self._parity_masks = tuple(
            (
                parity_position,
                sum(
                    1 << position
                    for position in range(1, self.hamming_length + 1)
                    if position & parity_position
                ),
            )
            for parity_position in self.hamming_parity_positions
        )

        self._encode_tables = self._build_encode_tables()
        self._extract_tables = self._build_extract_tables()

    def _validate_data(self, data: int) -> None:
        if isinstance(data, bool) or not isinstance(data, int):
            raise TypeError("data must be an integer")
        if data < 0 or data >= (1 << self.data_bits):
            raise ValueError(f"data must fit in {self.data_bits} bits")

    def _validate_codeword(self, codeword: int) -> None:
        if isinstance(codeword, bool) or not isinstance(codeword, int):
            raise TypeError("codeword must be an integer")
        if codeword < 0 or codeword > self._codeword_mask:
            raise ValueError(f"codeword must fit in {self.total_length} bits")

    def _build_encode_tables(self):
        tables = []
        for start in range(0, self.data_bits, 8):
            positions = self.data_positions[start : start + 8]
            table = [0] * 256
            for value in range(256):
                scattered = 0
                for bit_offset, position in enumerate(positions):
                    if (value >> bit_offset) & 1:
                        scattered |= 1 << position
                table[value] = scattered
            tables.append((start, table))
        return tuple(tables)

    def _build_extract_tables(self):
        groups = {}
        for data_bit, position in enumerate(self.data_positions):
            groups.setdefault(position >> 3, []).append((data_bit, position))

        tables = []
        for byte_base in sorted(groups):
            entries = groups[byte_base]
            first_data_bit = entries[0][0]
            table = [0] * 256
            for value in range(256):
                compact = 0
                for local_index, (_, position) in enumerate(entries):
                    if (value >> (position - (byte_base << 3))) & 1:
                        compact |= 1 << local_index
                table[value] = compact
            tables.append((byte_base << 3, first_data_bit, table))
        return tuple(tables)

    def _place_data(self, data: int) -> int:
        codeword = 0
        for start, table in self._encode_tables:
            codeword |= table[(data >> start) & 255]
        return codeword

    def extract_data(self, codeword: int) -> int:
        """Extract data without running decoder checks."""
        self._validate_codeword(codeword)
        data = 0
        for base, output_shift, table in self._extract_tables:
            data |= table[(codeword >> base) & 255] << output_shift
        return data

    def encode_detail(self, data: int) -> dict:
        """Return a codeword together with the derivation of each check bit."""
        self._validate_data(data)
        codeword = self._place_data(data)

        hamming_equations = []
        hamming_check_bits = {}
        for parity_position, mask in self._parity_masks:
            covered_positions = tuple(
                position
                for position in range(1, self.hamming_length + 1)
                if position & parity_position
            )
            value = (codeword & mask).bit_count() & 1
            hamming_check_bits[parity_position] = value
            if value:
                codeword |= 1 << parity_position
            hamming_equations.append(
                {
                    "parity_position": parity_position,
                    "covered_positions": covered_positions,
                    "value": value,
                }
            )

        overall_value = (codeword & self._hamming_mask).bit_count() & 1
        if overall_value:
            codeword |= 1

        return {
            "codeword": codeword,
            "hamming_check_bits": hamming_check_bits,
            "hamming_equations": hamming_equations,
            "overall_parity_bit": {"position": 0, "value": overall_value},
        }

    def encode(self, data: int) -> int:
        """Encode ``data`` and return the transmitted integer."""
        return self.encode_detail(data)["codeword"]

    def hamming_syndrome(self, codeword: int) -> tuple[int, tuple[dict, ...]]:
        """Compute S and the individual parity equations of a received block."""
        self._validate_codeword(codeword)
        syndrome = 0
        equations = []
        for parity_position, mask in self._parity_masks:
            covered_positions = tuple(
                position
                for position in range(1, self.hamming_length + 1)
                if position & parity_position
            )
            value = (codeword & mask).bit_count() & 1
            if value:
                syndrome |= parity_position
            equations.append(
                {
                    "syndrome_bit_weight": parity_position,
                    "covered_positions": covered_positions,
                    "value": value,
                }
            )
        return syndrome, tuple(equations)

    def _location(self, position: int) -> tuple[str, Optional[int]]:
        if position == 0:
            return "overall parity bit", None
        if position in self.hamming_parity_positions:
            return "Hamming parity bit", None
        return "data bit", self.position_to_data_bit[position]

    def decode(self, codeword: int) -> DecodeResult:
        """Decode a block as no-error, corrected, or rejected."""
        self._validate_codeword(codeword)
        syndrome, _ = self.hamming_syndrome(codeword)
        overall_check = codeword.bit_count() & 1

        if syndrome == 0 and overall_check == 0:
            return DecodeResult(
                status=Status.NO_ERROR,
                received=codeword,
                syndrome=syndrome,
                overall_parity_check=overall_check,
                data=self.extract_data(codeword),
                corrected_codeword=codeword,
                error_position=None,
                error_data_bit=None,
                reason="syndrome and overall parity are both zero",
            )

        if overall_check == 1 and syndrome == 0:
            corrected = codeword ^ 1
            return DecodeResult(
                status=Status.SINGLE_ERROR_CORRECTED,
                received=codeword,
                syndrome=syndrome,
                overall_parity_check=overall_check,
                data=self.extract_data(corrected),
                corrected_codeword=corrected,
                error_position=0,
                error_data_bit=None,
                reason="overall parity failed and S=0, so bit 0 is flipped",
            )

        if overall_check == 1 and 1 <= syndrome <= self.hamming_length:
            corrected = codeword ^ (1 << syndrome)
            location_name, data_bit = self._location(syndrome)
            return DecodeResult(
                status=Status.SINGLE_ERROR_CORRECTED,
                received=codeword,
                syndrome=syndrome,
                overall_parity_check=overall_check,
                data=self.extract_data(corrected),
                corrected_codeword=corrected,
                error_position=syndrome,
                error_data_bit=data_bit,
                reason=f"S={syndrome} and odd parity identify one flipped {location_name}",
            )

        if overall_check == 0:
            reason = (
                f"even parity with nonzero syndrome S={syndrome}: "
                "an even-weight error such as a double bit error is present"
            )
        else:
            reason = (
                f"odd parity with out-of-range syndrome S={syndrome}: "
                "no valid codeword is one bit flip away"
            )

        return DecodeResult(
            status=Status.UNCORRECTABLE_ERROR_DETECTED,
            received=codeword,
            syndrome=syndrome,
            overall_parity_check=overall_check,
            data=None,
            corrected_codeword=None,
            error_position=None,
            error_data_bit=None,
            reason=reason,
        )

    def explain_decode(self, codeword: int) -> str:
        """Return a human-readable derivation of the decoder decision."""
        self._validate_codeword(codeword)
        syndrome, equations = self.hamming_syndrome(codeword)
        overall_check = codeword.bit_count() & 1
        lines = [
            f"received=0b{codeword:0{self.total_length}b}",
            f"hamming section positions: 1..{self.hamming_length}",
            f"overall parity position: 0",
        ]
        for equation in equations:
            weight = equation["syndrome_bit_weight"]
            bit_index = weight.bit_length() - 1
            positions = ",".join(map(str, equation["covered_positions"]))
            lines.append(
                f"s{bit_index} = XOR(positions {positions}) = {equation['value']}"
            )
        lines.append(f"S = {syndrome}")
        lines.append(f"overall parity check = {overall_check}")
        result = self.decode(codeword)
        lines.append(result.reason)
        lines.append(f"status = {result.status.value}")
        return "\n".join(lines)
