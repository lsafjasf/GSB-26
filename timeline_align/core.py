"""Multi-source timeline alignment and merging (standard library only).

Time is represented as integers (e.g. milliseconds or microseconds since an
arbitrary epoch). Each source records events on its own clock, which may have
a fixed offset and a slight linear drift relative to a chosen reference
clock:

    t_ref ~= a * t_src + b        (a = 1 + drift, b = offset)

The library provides:

* Offset / drift estimation
    - ``estimate_offset_from_common_events``: least-squares fit over events
      observed by both sources (matched by a shared ``key``).
    - ``estimate_offset_xcorr``: sparse cross-correlation of the raw event
      streams when no shared keys exist (offset only, no drift).
* Merging
    - ``merge``: maps every event onto the reference timeline, assigns it to
      a fixed-width bucket (interval-attribution rule, see ``merge``), and
      sorts buckets by aligned time. No event is ever dropped.
* Quality
    - ``alignment_residuals`` / ``quality_report``: residual distribution of
      common events after alignment.
* Missing vs. empty
    - Each source declares ``coverage`` intervals (its own clock). Buckets
      outside coverage are marked MISSING (no data could have been recorded),
      buckets inside coverage with no events are marked EMPTY (the source was
      recording and nothing happened).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

PRESENT = "present"   # bucket contains events from this source
EMPTY = "empty"       # source was recording, no events in this bucket
MISSING = "missing"   # source was not recording (gap / not started / ended)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """A single timestamped observation on a source's own clock.

    ``key`` identifies an event observed by multiple sources (common event)
    and is used for offset estimation and residual computation. ``value`` is
    arbitrary payload and is never interpreted by the library.
    """

    t: int
    value: Any = None
    key: Optional[str] = None


@dataclass
class Source:
    """A named event stream with recording-coverage intervals.

    ``coverage`` is a list of inclusive ``(start, end)`` intervals on the
    source's own clock during which the source was recording. If omitted, it
    defaults to ``[(min_t, max_t)]`` over the source's events.
    """

    name: str
    events: List[Event] = field(default_factory=list)
    coverage: Optional[List[Tuple[int, int]]] = None

    def __post_init__(self) -> None:
        self.events = sorted(self.events, key=lambda e: e.t)
        if self.coverage is None:
            if self.events:
                self.coverage = [(self.events[0].t, self.events[-1].t)]
            else:
                self.coverage = []
        else:
            self.coverage = sorted(self.coverage)


@dataclass(frozen=True)
class Alignment:
    """Affine map from a source clock to the reference clock.

    ``t_ref = a * t_src + b``. ``offset`` equals ``b`` (the map's value at
    ``t_src = 0``); ``drift`` equals ``a - 1``. ``offset_uncertainty`` and
    ``drift_uncertainty`` are 1-sigma standard errors. ``residual_std`` is
    the standard deviation of the fit residuals (in reference-time units).
    """

    a: float = 1.0
    b: float = 0.0
    offset_uncertainty: float = 0.0
    drift_uncertainty: float = 0.0
    residual_std: float = 0.0
    n_samples: int = 0
    method: str = "identity"

    @property
    def offset(self) -> float:
        return self.b

    @property
    def drift(self) -> float:
        return self.a - 1.0

    def to_ref(self, t: int) -> float:
        return self.a * t + self.b

    @staticmethod
    def identity() -> "Alignment":
        return Alignment()


# ---------------------------------------------------------------------------
# Offset estimation
# ---------------------------------------------------------------------------

def _matched_pairs(ref: Source, src: Source) -> List[Tuple[int, int]]:
    """Return (t_ref, t_src) pairs for events sharing a non-None key."""
    ref_by_key: Dict[str, int] = {}
    for e in ref.events:
        if e.key is not None:
            if e.key in ref_by_key:
                raise ValueError(f"duplicate key {e.key!r} in source {ref.name!r}")
            ref_by_key[e.key] = e.t
    pairs = []
    for e in src.events:
        if e.key is not None and e.key in ref_by_key:
            pairs.append((ref_by_key[e.key], e.t))
    return pairs


def estimate_offset_from_common_events(ref: Source, src: Source) -> Alignment:
    """Estimate offset and drift by least squares over common events.

    Fits ``t_ref = a * t_src + b``. Uncertainties are the usual OLS standard
    errors; ``residual_std`` characterises the per-event alignment error.

    Raises ``ValueError`` if fewer than 2 common events exist (drift is then
    unidentifiable; use ``estimate_offset_xcorr`` for offset-only).
    """
    pairs = _matched_pairs(ref, src)
    n = len(pairs)
    if n < 2:
        raise ValueError(
            f"need >=2 common events between {ref.name!r} and {src.name!r}, got {n}"
        )
    xs = [p[1] for p in pairs]  # source times
    ys = [p[0] for p in pairs]  # reference times
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        raise ValueError("common events have identical source timestamps; "
                         "cannot fit drift")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx
    b = my - a * mx
    residuals = [y - (a * x + b) for x, y in zip(xs, ys)]
    if n > 2:
        s2 = sum(r * r for r in residuals) / (n - 2)
    else:
        s2 = 0.0
    s = math.sqrt(s2)
    se_a = s / math.sqrt(sxx) if sxx > 0 else 0.0
    # Var(b) for intercept of y = a*x + b fit: s^2 * sum(x^2) / (n * Sxx)
    se_b = s * math.sqrt(sum(x * x for x in xs) / (n * sxx))
    residual_std = statistics.pstdev(residuals) if n > 1 else 0.0
    return Alignment(
        a=a,
        b=b,
        offset_uncertainty=se_b,
        drift_uncertainty=se_a,
        residual_std=residual_std,
        n_samples=n,
        method="common_events_ols",
    )


def estimate_offset_xcorr(
    ref: Source,
    src: Source,
    max_lag: int,
    bin_width: int = 1,
) -> Alignment:
    """Estimate a pure offset by sparse cross-correlation of event times.

    For every pair (t_ref, t_src) with ``|t_ref - t_src| <= max_lag`` the lag
    ``t_ref - t_src`` votes in a histogram of width ``bin_width``. The peak
    bin is refined to a weighted centroid of its lags. This finds a constant
    offset only; drift, if significant over the observation window, smears
    the peak and inflates the reported uncertainty.

    Uncertainty combines the standard error of the centroid and the
    quantisation error of the bin width (uniform, bin_width/sqrt(12)).

    Raises ``ValueError`` if no lag pairs fall within ``max_lag``.
    """
    if bin_width < 1:
        raise ValueError("bin_width must be >= 1")
    ref_ts = [e.t for e in ref.events]
    src_ts = [e.t for e in src.events]
    hist: Dict[int, List[int]] = {}
    j0 = 0
    for tr in ref_ts:
        # advance window start (both lists sorted)
        while j0 < len(src_ts) and src_ts[j0] < tr - max_lag:
            j0 += 1
        j = j0
        while j < len(src_ts) and src_ts[j] <= tr + max_lag:
            lag = tr - src_ts[j]
            hist.setdefault(lag // bin_width, []).append(lag)
            j += 1
    if not hist:
        raise ValueError("no event pairs within max_lag; cannot estimate offset")
    peak_bin = max(hist, key=lambda k: len(hist[k]))
    lags = hist[peak_bin]
    n = len(lags)
    mean = statistics.fmean(lags)
    if n > 1:
        se = statistics.stdev(lags) / math.sqrt(n)
    else:
        se = float(bin_width)
    quant = bin_width / math.sqrt(12.0)
    uncertainty = math.hypot(se, quant)
    return Alignment(
        a=1.0,
        b=mean,
        offset_uncertainty=uncertainty,
        drift_uncertainty=0.0,
        residual_std=statistics.pstdev(lags) if n > 1 else 0.0,
        n_samples=n,
        method="xcorr",
    )


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------

def alignment_residuals(ref: Source, src: Source, alignment: Alignment) -> List[float]:
    """Residuals of common events after mapping src onto the ref clock.

    residual = t_ref - alignment.to_ref(t_src); 0 means perfect alignment.
    """
    return [
        t_ref - alignment.to_ref(t_src)
        for t_ref, t_src in _matched_pairs(ref, src)
    ]


def quality_report(ref: Source, sources: Sequence[Source],
                   alignments: Dict[str, Alignment]) -> Dict[str, Dict[str, float]]:
    """Residual distribution per source (against ``ref``) after alignment.

    Returns ``{source_name: {"n", "mean", "std", "max_abs", "p95_abs"}}``.
    Sources without common events are reported with ``n == 0``.
    """
    report: Dict[str, Dict[str, float]] = {}
    for src in sources:
        if src.name == ref.name:
            continue
        al = alignments[src.name]
        res = alignment_residuals(ref, src, al)
        if not res:
            report[src.name] = {"n": 0, "mean": 0.0, "std": 0.0,
                                "max_abs": 0.0, "p95_abs": 0.0}
            continue
        abs_sorted = sorted(abs(r) for r in res)
        p95 = abs_sorted[min(len(abs_sorted) - 1, math.ceil(0.95 * len(abs_sorted)) - 1)]
        report[src.name] = {
            "n": len(res),
            "mean": statistics.fmean(res),
            "std": statistics.pstdev(res) if len(res) > 1 else 0.0,
            "max_abs": abs_sorted[-1],
            "p95_abs": p95,
        }
    return report


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    """One source's slice of one bucket."""

    status: str  # PRESENT | EMPTY | MISSING
    events: List[Tuple[float, Event]] = field(default_factory=list)
    # each entry: (aligned_time_on_ref_clock, original_event)


@dataclass
class Bucket:
    """A half-open interval [t_start, t_end) on the reference clock."""

    t_start: int
    t_end: int
    cells: Dict[str, Cell] = field(default_factory=dict)


@dataclass
class MergedTimeline:
    """Result of ``merge``: ordered buckets plus per-source bookkeeping."""

    bin_width: int
    ref_name: str
    source_names: List[str]
    buckets: List[Bucket]

    def iter_events(self) -> Iterator[Tuple[float, str, Event]]:
        """Yield (aligned_time, source_name, event) sorted by aligned time."""
        for bucket in self.buckets:
            entries = [
                (aligned_t, name, ev)
                for name in self.source_names
                for aligned_t, ev in bucket.cells[name].events
            ]
            entries.sort(key=lambda item: item[0])
            yield from entries

    def count_by_source(self) -> Dict[str, int]:
        counts = {name: 0 for name in self.source_names}
        for bucket in self.buckets:
            for name in self.source_names:
                counts[name] += len(bucket.cells[name].events)
        return counts

    def status_grid(self) -> Dict[str, List[str]]:
        """Per-source list of bucket statuses, in bucket order."""
        return {
            name: [bucket.cells[name].status for bucket in self.buckets]
            for name in self.source_names
        }


def _coverage_on_ref(source: Source, alignment: Alignment) -> List[Tuple[float, float]]:
    intervals = []
    for start, end in source.coverage or []:
        lo = alignment.to_ref(start)
        hi = alignment.to_ref(end)
        if lo > hi:  # negative drift would flip the interval; guard anyway
            lo, hi = hi, lo
        intervals.append((lo, hi))
    return intervals


def merge(
    sources: Sequence[Source],
    alignments: Dict[str, Alignment],
    bin_width: int,
    ref_name: Optional[str] = None,
) -> MergedTimeline:
    """Align and merge sources into fixed-width buckets on the ref clock.

    Interval-attribution / resampling rule: every event is mapped to the
    reference clock via its source's alignment, then assigned to the bucket
    ``floor(t_aligned / bin_width)``. Buckets therefore partition the
    reference timeline into half-open intervals
    ``[k*bin_width, (k+1)*bin_width)`` and every event lands in exactly one
    bucket -- nothing is dropped and nothing is duplicated. Sources with
    different sampling rates simply contribute different numbers of events
    per bucket; consumers needing a uniform grid should aggregate each
    bucket's cell (e.g. mean of values) downstream.

    Every bucket spans the union of all sources' aligned ranges, so gaps
    inside a source's coverage appear as EMPTY cells and gaps outside
    coverage as MISSING cells.

    ``alignments`` must contain an entry per source; the reference source's
    alignment should be ``Alignment.identity()`` (the default constructed
    alignment). ``ref_name`` defaults to the first source.
    """
    if bin_width < 1:
        raise ValueError("bin_width must be >= 1")
    if not sources:
        raise ValueError("at least one source is required")
    names = [s.name for s in sources]
    if len(set(names)) != len(names):
        raise ValueError("source names must be unique")
    ref_name = ref_name or names[0]
    for name in names:
        if name not in alignments:
            raise KeyError(f"missing alignment for source {name!r}")

    by_name = {s.name: s for s in sources}
    cov_ref = {name: _coverage_on_ref(by_name[name], alignments[name])
               for name in names}

    # Assign events to buckets.
    bucket_events: Dict[int, Dict[str, List[Tuple[float, Event]]]] = {}
    for name in names:
        al = alignments[name]
        for ev in by_name[name].events:
            t_aligned = al.to_ref(ev.t)
            k = math.floor(t_aligned / bin_width)
            bucket_events.setdefault(k, {}).setdefault(name, []).append(
                (t_aligned, ev)
            )
    if not bucket_events:
        return MergedTimeline(bin_width, ref_name, names, [])

    k_min = min(bucket_events)
    k_max = max(bucket_events)
    buckets: List[Bucket] = []
    for k in range(k_min, k_max + 1):
        t_start = k * bin_width
        t_end = t_start + bin_width
        bucket = Bucket(t_start=t_start, t_end=t_end)
        for name in names:
            events = sorted(bucket_events.get(k, {}).get(name, []),
                            key=lambda p: p[0])
            if events:
                status = PRESENT
            else:
                covered = any(lo < t_end and t_start <= hi
                              for lo, hi in cov_ref[name])
                status = EMPTY if covered else MISSING
            bucket.cells[name] = Cell(status=status, events=events)
        buckets.append(bucket)
    return MergedTimeline(bin_width, ref_name, names, buckets)
