"""memlimit_agg: memory-bounded group-by aggregation and cross-tabulation.

Only the Python standard library is used.
"""

from .core import GroupByAggregator, normalize_key
from .crosstab import CrossTab, CrossTabResult
from .reference import reference_crosstab, reference_groupby

__all__ = [
    "GroupByAggregator",
    "CrossTab",
    "CrossTabResult",
    "normalize_key",
    "reference_groupby",
    "reference_crosstab",
]
