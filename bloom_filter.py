"""Bloom filter & Scalable Bloom Filter (Python 3, standard library only).

Formulas (n = expected elements, p = target false-positive rate):
    m = -n * ln(p) / (ln 2)^2        # bit-array size in bits
    k = (m / n) * ln 2               # number of hash functions
    FPR(n') = (1 - e^(-k*n'/m))^k    # actual FPR after n' insertions

Scalable variant (Almeida et al., 2007): when the current layer fills up,
append a new layer with capacity scaled by `growth_factor` (s) and target
FPR tightened by `tightening_ratio` (r, 0 < r < 1). Overall FPR is bounded
by the union bound:
    P <= sum_i p0 * r^i <= p0 / (1 - r)
"""

import hashlib
import math


def _to_bytes(item):
    if isinstance(item, bytes):
        return item
    if isinstance(item, str):
        return item.encode("utf-8")
    return repr(item).encode("utf-8")


def _hash_pair(data, seed):
    """One 128-bit digest -> two 64-bit hashes (h1, h2).

    The seed is mixed into the digest, so the same item with different seeds
    yields independent positions (reproducible for a fixed seed).
    """
    digest = hashlib.blake2b(
        seed.to_bytes(8, "little") + data, digest_size=16
    ).digest()
    return int.from_bytes(digest[:8], "little"), int.from_bytes(digest[8:], "little")


class BloomFilter:
    """Basic Bloom filter.

    Bit-array size and hash count are derived from the target error rate
    and the expected element count (see module docstring for formulas).
    """

    def __init__(self, capacity, error_rate, seed=0):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if not 0 < error_rate < 1:
            raise ValueError("error_rate must be in (0, 1)")
        self.capacity = int(capacity)
        self.error_rate = float(error_rate)
        self.seed = seed
        # m = -n ln p / (ln 2)^2
        self.num_bits = max(
            64, math.ceil(-self.capacity * math.log(self.error_rate) / (math.log(2) ** 2))
        )
        # k = (m / n) ln 2
        self.num_hashes = max(1, round(self.num_bits / self.capacity * math.log(2)))
        self._bits = bytearray((self.num_bits + 7) // 8)
        self.count = 0

    def _positions(self, item):
        # Kirsch-Mitzenmacher double hashing: g_i(x) = (h1 + i*h2) mod m
        h1, h2 = _hash_pair(_to_bytes(item), self.seed)
        m = self.num_bits
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % m

    def add(self, item):
        for pos in self._positions(item):
            self._bits[pos >> 3] |= 1 << (pos & 7)
        self.count += 1

    def __contains__(self, item):
        bits = self._bits
        for pos in self._positions(item):
            if not (bits[pos >> 3] >> (pos & 7)) & 1:
                return False
        return True

    def __len__(self):
        return self.count

    @property
    def num_bytes(self):
        return len(self._bits)

    def expected_fpr(self, n=None):
        """Theoretical FPR (1 - e^(-k n / m))^k after n insertions."""
        n = self.count if n is None else n
        if n <= 0:
            return 0.0
        return (1 - math.exp(-self.num_hashes * n / self.num_bits)) ** self.num_hashes

    def info(self):
        return {
            "capacity": self.capacity,
            "target_fpr": self.error_rate,
            "num_bits": self.num_bits,
            "num_hashes": self.num_hashes,
            "bytes": self.num_bytes,
            "count": self.count,
        }


class ScalableBloomFilter:
    """Scalable Bloom filter: adds a new layer instead of rebuilding.

    Layer i has capacity n0 * s^i and target FPR p0 * r^i, where
    s = growth_factor and r = tightening_ratio (0 < r < 1).
    Overall FPR <= sum of layer FPRs <= p0 / (1 - r)  (union bound).
    """

    def __init__(self, initial_capacity=1000, error_rate=0.01,
                 growth_factor=2, tightening_ratio=0.9, seed=0):
        if initial_capacity <= 0:
            raise ValueError("initial_capacity must be positive")
        if not 0 < error_rate < 1:
            raise ValueError("error_rate must be in (0, 1)")
        if growth_factor < 1:
            raise ValueError("growth_factor must be >= 1")
        if not 0 < tightening_ratio < 1:
            raise ValueError("tightening_ratio must be in (0, 1)")
        self.initial_capacity = int(initial_capacity)
        self.error_rate = float(error_rate)
        self.growth_factor = growth_factor
        self.tightening_ratio = tightening_ratio
        self.seed = seed
        self.filters = []
        self._add_layer()

    def _add_layer(self):
        i = len(self.filters)
        cap = max(1, round(self.initial_capacity * self.growth_factor ** i))
        p = self.error_rate * self.tightening_ratio ** i
        # Distinct seed per layer keeps layer hashes mutually independent.
        self.filters.append(BloomFilter(cap, p, seed=self.seed + i))

    def add(self, item):
        if item in self:
            return
        top = self.filters[-1]
        if top.count >= top.capacity:
            self._add_layer()
            top = self.filters[-1]
        top.add(item)

    def __contains__(self, item):
        return any(item in f for f in self.filters)

    def __len__(self):
        return sum(f.count for f in self.filters)

    @property
    def num_bytes(self):
        return sum(f.num_bytes for f in self.filters)

    def error_rate_bound(self):
        """Union bound over existing layers; <= p0 / (1 - r)."""
        return sum(f.error_rate for f in self.filters)

    def asymptotic_bound(self):
        return self.error_rate / (1 - self.tightening_ratio)

    def info(self):
        return {
            "layers": len(self.filters),
            "count": len(self),
            "bytes": self.num_bytes,
            "fpr_bound": self.error_rate_bound(),
            "asymptotic_bound": self.asymptotic_bound(),
            "layer_details": [f.info() for f in self.filters],
        }
