"""Joint alignment of three or more sources (standard library only).

Pairwise estimation (``estimate_offset_from_common_events``) fits each source
against the reference independently. When sources also share common events
among themselves (B-C, C-D, ...), that information is discarded. This module
instead builds one equation system from *all* common events and solves for
every source's offset and drift simultaneously.

Model
-----
Each source ``i`` maps its local clock to the reference clock by
``t_ref = a_i * t_i + b_i``. Every common event ``k`` has a latent true time
``tau_k`` on the reference clock, so each observation of the event gives one
equation::

    a_i * t_i + b_i = tau_k + noise        (i observes event k at local t_i)

The reference source is the gauge constraint (``a_ref = 1``, ``b_ref = 0``),
which anchors the latent times. Eliminating the per-event ``tau_k`` from the
normal equations (Schur complement, i.e. demeaning each event's aligned
times) leaves a small dense system in the 2*(n_sources - 1) unknowns, solved
by Gauss-Jordan elimination. Parameter uncertainties are 1-sigma standard
errors from ``sigma^2 * (X^T X)^-1`` with ``sigma^2`` estimated from the
residuals; they can be verified by resampling (``bootstrap_joint``).

Because the reference constraint is the same one the pairwise estimator
uses, joint and pairwise estimates of the same parameter are directly
comparable and must agree within their combined uncertainties.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from .core import Alignment, Source

__all__ = ["estimate_joint", "bootstrap_joint"]


# ---------------------------------------------------------------------------
# Equation system construction
# ---------------------------------------------------------------------------

def _collect_common_events(
    sources: Sequence[Source],
) -> List[Dict[str, int]]:
    """Return one ``{source_name: t_local}`` dict per common event.

    Only events (keys) observed by at least two sources are kept; singleton
    observations carry no alignment information.
    """
    by_key: Dict[str, Dict[str, int]] = {}
    for src in sources:
        seen = set()
        for ev in src.events:
            if ev.key is None:
                continue
            if ev.key in seen:
                raise ValueError(
                    f"duplicate key {ev.key!r} in source {src.name!r}")
            seen.add(ev.key)
            by_key.setdefault(ev.key, {})[src.name] = ev.t
    return [obs for obs in by_key.values() if len(obs) >= 2]


def _solve_dense(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Solve ``matrix @ x = rhs`` by Gauss-Jordan with partial pivoting."""
    n = len(matrix)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        col_scale = max(abs(aug[r][col]) for r in range(n)) or 1.0
        if abs(aug[piv][col]) <= 1e-12 * col_scale:
            raise ValueError(
                "joint system is underdetermined: the common-event graph "
                "must connect every source to the reference, and each "
                "source's common events must span more than one timestamp"
            )
        aug[col], aug[piv] = aug[piv], aug[col]
        inv_piv = 1.0 / aug[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col] * inv_piv
            if factor:
                for c in range(col, n + 1):
                    aug[r][c] -= factor * aug[col][c]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def _invert_dense(matrix: List[List[float]]) -> List[List[float]]:
    n = len(matrix)
    cols = []
    for j in range(n):
        unit = [0.0] * n
        unit[j] = 1.0
        cols.append(_solve_dense(matrix, unit))
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _design_row(name: str, t: int, ref_name: str,
                slot: Dict[str, int]) -> Tuple[List[Tuple[int, float]], float]:
    """One observation equation as (sparse design entries, constant term).

    Aligned time is ``sum(coef * theta[s] for s, coef in entries) + const``;
    the reference source contributes only the constant (gauge constraint).
    """
    if name == ref_name:
        return [], float(t)
    sa = slot[name]
    return [(sa, float(t)), (sa + 1, 1.0)], 0.0


def _fit_joint(
    names: Sequence[str],
    ref_name: str,
    common: List[Dict[str, int]],
) -> Tuple[Dict[str, Tuple[float, float]], List[List[float]], float, int, int]:
    """Solve the joint system.

    Returns ``(params, cov, rss, total_obs, n_events)`` where ``params`` maps
    each non-reference source to ``(a, b)`` and ``cov`` is the unscaled
    ``(X^T X)^-1`` matrix in ``[(a_i, b_i)]`` order.
    """
    params = [n for n in names if n != ref_name]
    slot = {n: 2 * i for i, n in enumerate(params)}
    p = 2 * len(params)
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    total_obs = 0

    for obs in common:
        rows = [_design_row(name, t, ref_name, slot)
                for name, t in obs.items()]
        n_k = len(rows)
        total_obs += n_k
        # Schur-complement (within-event demeaned) normal equations:
        #   M += sum(x x^T) - n_k * xbar xbar^T
        #   r -= sum((x - xbar) * (c - cbar))
        mean: Dict[int, float] = {}
        mean_c = 0.0
        for entries, c in rows:
            mean_c += c
            for s, coef in entries:
                mean[s] = mean.get(s, 0.0) + coef
        mean_c /= n_k
        mean = {s: v / n_k for s, v in mean.items()}
        for entries, c in rows:
            dc = c - mean_c
            centered = dict(mean)
            for s, coef in entries:
                centered[s] = centered.get(s, 0.0) - coef
            # x - xbar = -centered; accumulate (x-xbar)(x-xbar)^T and
            # -(x-xbar)(c-cbar) so signs work out directly.
            items = list(centered.items())
            for s1, v1 in items:
                xty[s1] += v1 * dc
                for s2, v2 in items:
                    xtx[s1][s2] += v1 * v2

    theta = _solve_dense(xtx, xty)
    cov = _invert_dense(xtx)

    # Residual sum of squares at the solution: aligned times minus their
    # per-event mean (the eliminated latent event time).
    rss = 0.0
    for obs in common:
        aligned = []
        for name, t in obs.items():
            if name == ref_name:
                aligned.append(float(t))
            else:
                s = slot[name]
                aligned.append(theta[s] * t + theta[s + 1])
        mu = sum(aligned) / len(aligned)
        rss += sum((z - mu) ** 2 for z in aligned)

    estimates = {n: (theta[slot[n]], theta[slot[n] + 1]) for n in params}
    return estimates, cov, rss, total_obs, len(common)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_joint(
    sources: Sequence[Source],
    ref_name: Optional[str] = None,
) -> Dict[str, Alignment]:
    """Jointly estimate every source's offset and drift from common events.

    Builds the equation system ``a_i * t_i + b_i = tau_k`` over all common
    events (including events shared only between non-reference sources),
    eliminates the latent event times ``tau_k``, and solves for all
    ``(a_i, b_i)`` simultaneously. The reference source is fixed to the
    identity map (the same constraint the pairwise estimator uses), so joint
    and pairwise estimates are commensurate.

    Returns ``{source_name: Alignment}`` with per-parameter 1-sigma
    uncertainties (``offset_uncertainty`` / ``drift_uncertainty``) from the
    joint covariance matrix. The reference source maps to the identity with
    zero uncertainty. The result can be passed straight to ``merge``.

    Raises ``ValueError`` if the common-event graph does not identify every
    source (e.g. a source shares no events, directly or transitively, with
    the reference).
    """
    if len(sources) < 2:
        raise ValueError("joint estimation needs at least two sources")
    names = [s.name for s in sources]
    if len(set(names)) != len(names):
        raise ValueError("source names must be unique")
    ref_name = ref_name or names[0]
    if ref_name not in names:
        raise ValueError(f"reference source {ref_name!r} not in sources")

    common = _collect_common_events(sources)
    if not common:
        raise ValueError("no common events (keys shared by >= 2 sources)")

    estimates, cov, rss, total_obs, n_events = _fit_joint(
        names, ref_name, common)

    p = 2 * (len(names) - 1)
    dof = total_obs - n_events - p
    sigma2 = rss / dof if dof > 0 else 0.0
    residual_std = math.sqrt(rss / max(1, total_obs - n_events))

    counts = {n: 0 for n in names}
    for obs in common:
        for n in obs:
            counts[n] += 1

    alignments: Dict[str, Alignment] = {
        ref_name: Alignment(method="joint_reference"),
    }
    for name, (a, b) in estimates.items():
        s = 2 * ([n for n in names if n != ref_name].index(name))
        alignments[name] = Alignment(
            a=a,
            b=b,
            offset_uncertainty=math.sqrt(max(0.0, sigma2 * cov[s + 1][s + 1])),
            drift_uncertainty=math.sqrt(max(0.0, sigma2 * cov[s][s])),
            residual_std=residual_std,
            n_samples=counts[name],
            method="joint_gls",
        )
    return alignments


def bootstrap_joint(
    sources: Sequence[Source],
    ref_name: Optional[str] = None,
    n_resamples: int = 200,
    seed: int = 0,
) -> Dict[str, Dict[str, float]]:
    """Verify joint parameter uncertainties by resampling common events.

    Cluster bootstrap: whole common events (all sources' observations of a
    key) are resampled with replacement and the joint system is re-solved,
    preserving within-event correlation. Returns per source::

        {"offset_std", "drift_std", "offset_mean", "drift_mean",
         "n_resamples", "n_failed"}

    where ``*_std`` are the bootstrap standard deviations of the estimates,
    directly comparable to ``offset_uncertainty`` / ``drift_uncertainty``
    from ``estimate_joint``. Degenerate resamples (underdetermined system)
    are skipped and counted in ``n_failed``.
    """
    if n_resamples < 2:
        raise ValueError("n_resamples must be >= 2")
    names = [s.name for s in sources]
    ref_name = ref_name or names[0]
    common = _collect_common_events(sources)
    if not common:
        raise ValueError("no common events (keys shared by >= 2 sources)")

    rng = random.Random(seed)
    params = [n for n in names if n != ref_name]
    draws: Dict[str, List[Tuple[float, float]]] = {n: [] for n in params}
    n_failed = 0
    for _ in range(n_resamples):
        resampled = [common[rng.randrange(len(common))] for _ in common]
        try:
            estimates, _, _, _, _ = _fit_joint(names, ref_name, resampled)
        except ValueError:
            n_failed += 1
            continue
        for n in params:
            draws[n].append(estimates[n])

    report: Dict[str, Dict[str, float]] = {}
    for n in params:
        a_vals = [ab[0] - 1.0 for ab in draws[n]]
        b_vals = [ab[1] for ab in draws[n]]
        report[n] = {
            "offset_std": statistics.pstdev(b_vals) if len(b_vals) > 1 else 0.0,
            "drift_std": statistics.pstdev(a_vals) if len(a_vals) > 1 else 0.0,
            "offset_mean": statistics.fmean(b_vals) if b_vals else 0.0,
            "drift_mean": statistics.fmean(a_vals) if a_vals else 0.0,
            "n_resamples": float(len(b_vals)),
            "n_failed": float(n_failed),
        }
    return report
