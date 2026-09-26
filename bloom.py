"""Bloom filter library (Python 3, standard library only).

Provides:
  - BloomFilter:           basic bloom filter, m/k derived from target error rate
  - ScalableBloomFilter:   grows by appending layers instead of rebuilding
  - CountingBloomFilter:   alternative that supports deletion

Hash strategy: one SHA-256 digest per item, expanded into k positions via
double hashing (Kirsch & Mitzenmacher, "Less Hashing, Same Performance",
2006): h_i(x) = (h1 + i*h2) mod m.  The paper proves this preserves the
same asymptotic false-positive rate as k fully independent hash functions,
while costing a single digest computation.  A user-supplied seed makes the
hash family injectable and runs reproducible.
"""

from __future__ import annotations

import hashlib
import math
import struct

_LN2 = math.log(2.0)
_LN2_SQ = _LN2 * _LN2


def optimal_num_bits(capacity: int, error_rate: float) -> int:
    """m = -n * ln(p) / (ln 2)^2   (minimum bits for target fp rate p)."""
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if not 0.0 < error_rate < 1.0:
        raise ValueError("error_rate must be in (0, 1)")
    return max(8, math.ceil(-capacity * math.log(error_rate) / _LN2_SQ))


def optimal_num_hashes(num_bits: int, capacity: int) -> int:
    """k = (m / n) * ln 2   (hash count minimising the fp rate)."""
    return max(1, round((num_bits / capacity) * _LN2))


def _item_bytes(item) -> bytes:
    if isinstance(item, bytes):
        return item
    if isinstance(item, str):
        return item.encode("utf-8")
    return repr(item).encode("utf-8")


def _positions(item, seed: int, num_hashes: int, num_bits: int):
    """Yield k bit positions for item via seeded double hashing."""
    digest = hashlib.sha256(struct.pack("<Q", seed) + _item_bytes(item)).digest()
    h1 = int.from_bytes(digest[:16], "little")
    h2 = int.from_bytes(digest[16:], "little") | 1  # odd => full cycle
    for i in range(num_hashes):
        yield (h1 + i * h2) % num_bits


class BloomFilter:
    """Basic bloom filter sized from (capacity, error_rate)."""

    __slots__ = ("capacity", "error_rate", "seed", "num_bits", "num_hashes",
                 "_bits", "count")

    def __init__(self, capacity: int, error_rate: float, seed: int = 0):
        self.capacity = capacity
        self.error_rate = error_rate
        self.seed = seed
        self.num_bits = optimal_num_bits(capacity, error_rate)
        self.num_hashes = optimal_num_hashes(self.num_bits, capacity)
        self._bits = bytearray((self.num_bits + 7) // 8)
        self.count = 0

    def add(self, item) -> None:
        bits = self._bits
        for pos in _positions(item, self.seed, self.num_hashes, self.num_bits):
            bits[pos >> 3] |= 1 << (pos & 7)
        self.count += 1

    def __contains__(self, item) -> bool:
        bits = self._bits
        for pos in _positions(item, self.seed, self.num_hashes, self.num_bits):
            if not (bits[pos >> 3] >> (pos & 7)) & 1:
                return False
        return True

    def __len__(self) -> int:
        return self.count

    @property
    def nbytes(self) -> int:
        return len(self._bits)

    def theoretical_fp(self, n: int | None = None) -> float:
        """(1 - e^(-k*n/m))^k for n inserted items (n=count if omitted)."""
        n = self.count if n is None else n
        if n <= 0:
            return 0.0
        return (1.0 - math.exp(-self.num_hashes * n / self.num_bits)) ** self.num_hashes


class ScalableBloomFilter:
    """Scalable bloom filter: appends a larger layer when full, never rebuilds.

    Layer i gets capacity  n_i = initial_capacity * growth**i
    and target error       p_i = error_rate * (1 - tightening) * tightening**i
    so the overall fp rate is bounded by the geometric series
        sum(p_i) < error_rate            (for any number of layers).
    """

    __slots__ = ("initial_capacity", "error_rate", "growth", "tightening",
                 "seed", "_layers", "_next_capacity")

    def __init__(self, initial_capacity: int = 1024, error_rate: float = 0.01,
                 growth: float = 2.0, tightening: float = 0.9, seed: int = 0):
        if initial_capacity <= 0:
            raise ValueError("initial_capacity must be positive")
        if not 0.0 < error_rate < 1.0:
            raise ValueError("error_rate must be in (0, 1)")
        if growth <= 1.0:
            raise ValueError("growth must be > 1")
        if not 0.0 < tightening < 1.0:
            raise ValueError("tightening must be in (0, 1)")
        self.initial_capacity = initial_capacity
        self.error_rate = error_rate
        self.growth = growth
        self.tightening = tightening
        self.seed = seed
        self._layers: list[BloomFilter] = []
        self._next_capacity = initial_capacity
        self._grow()

    def _layer_target(self, index: int) -> float:
        return self.error_rate * (1.0 - self.tightening) * self.tightening ** index

    def _grow(self) -> None:
        idx = len(self._layers)
        layer = BloomFilter(int(self._next_capacity), self._layer_target(idx),
                            seed=self.seed + idx)
        self._layers.append(layer)
        self._next_capacity = int(self._next_capacity * self.growth)

    def add(self, item) -> bool:
        """Add item; returns False if it was already (probably) present."""
        if item in self:
            return False
        top = self._layers[-1]
        if top.count >= top.capacity:
            self._grow()
            top = self._layers[-1]
        top.add(item)
        return True

    def __contains__(self, item) -> bool:
        return any(item in layer for layer in self._layers)

    def __len__(self) -> int:
        return sum(len(layer) for layer in self._layers)

    @property
    def num_layers(self) -> int:
        return len(self._layers)

    @property
    def nbytes(self) -> int:
        return sum(layer.nbytes for layer in self._layers)

    def theoretical_fp_bound(self) -> float:
        """Union bound: P(any layer false-positives) <= sum of layer rates."""
        return sum(layer.theoretical_fp() for layer in self._layers)


class CountingBloomFilter:
    """Counting bloom filter: supports deletion via 8-bit saturating counters.

    Costs ~8x the memory of a basic BloomFilter with the same parameters;
    use only when deletion is a hard requirement.
    """

    __slots__ = ("capacity", "error_rate", "seed", "num_bits", "num_hashes",
                 "_counters", "count")

    def __init__(self, capacity: int, error_rate: float, seed: int = 0):
        self.capacity = capacity
        self.error_rate = error_rate
        self.seed = seed
        self.num_bits = optimal_num_bits(capacity, error_rate)
        self.num_hashes = optimal_num_hashes(self.num_bits, capacity)
        self._counters = bytearray(self.num_bits)  # one byte per counter
        self.count = 0

    def add(self, item) -> None:
        counters = self._counters
        for pos in _positions(item, self.seed, self.num_hashes, self.num_bits):
            if counters[pos] < 255:
                counters[pos] += 1
        self.count += 1

    def discard(self, item) -> bool:
        """Remove item if present; returns True when it was found."""
        if item not in self:
            return False
        counters = self._counters
        for pos in _positions(item, self.seed, self.num_hashes, self.num_bits):
            if counters[pos] > 0:
                counters[pos] -= 1
        self.count -= 1
        return True

    def __contains__(self, item) -> bool:
        counters = self._counters
        for pos in _positions(item, self.seed, self.num_hashes, self.num_bits):
            if counters[pos] == 0:
                return False
        return True

    def __len__(self) -> int:
        return self.count

    @property
    def nbytes(self) -> int:
        return len(self._counters)
