"""Two-dimensional cross-tabulation (pivot) built on GroupByAggregator."""

from .core import GroupByAggregator, OPS


class CrossTabResult:
    """Result of a cross-tabulation.

    Attributes
    ----------
    op : str
    cells : dict[(row_key, col_key)] -> value
    row_totals / col_totals : dict key -> value
        Only computed for additive ops (sum, count); empty otherwise.
    Missing row/col keys appear as None.
    """

    def __init__(self, op, cells, row_totals, col_totals):
        self.op = op
        self.cells = cells
        self.row_totals = row_totals
        self.col_totals = col_totals

    @property
    def grand_total(self):
        if self.op in ("sum", "count"):
            return sum(self.row_totals.values())
        return None

    def as_table(self):
        """Nested dict {row_key: {col_key: value}}."""
        table = {}
        for (row, col), value in self.cells.items():
            table.setdefault(row, {})[col] = value
        return table

    def __eq__(self, other):
        return (isinstance(other, CrossTabResult)
                and self.op == other.op
                and self.cells == other.cells
                and self.row_totals == other.row_totals
                and self.col_totals == other.col_totals)


class CrossTab:
    """Memory-bounded 2-D cross summary.

    Parameters
    ----------
    row, col : str | callable
        Field names (or callables record -> key) for the two dimensions.
    op : str
        One of count, sum, min, max, count_distinct.
    value : str | callable | None
        Field to aggregate (ignored for count).
    memory_budget, spill_dir : passed through to GroupByAggregator.
    """

    def __init__(self, row, col, op="sum", value=None,
                 memory_budget=64 << 20, spill_dir=None):
        if op not in OPS:
            raise ValueError("unknown op: %r" % (op,))
        self.op = op
        self._row = row
        self._col = col
        self._agg = GroupByAggregator(
            key=self._composite_key,
            aggs={op: value},
            memory_budget=memory_budget,
            spill_dir=spill_dir,
        )

    def _composite_key(self, record):
        return (self._field(record, self._row), self._field(record, self._col))

    @staticmethod
    def _field(record, field):
        if callable(field):
            return field(record)
        return record.get(field)

    def add(self, record):
        self._agg.add(record)

    def add_many(self, records):
        self._agg.add_many(records)

    @property
    def spill_count(self):
        return self._agg.spill_count

    def result(self):
        cells = {}
        for (row, col), stats in self._agg.iter_results():
            cells[(row, col)] = stats[self.op]
        row_totals, col_totals = {}, {}
        if self.op in ("sum", "count"):
            for (row, col), value in cells.items():
                row_totals[row] = row_totals.get(row, 0) + value
                col_totals[col] = col_totals.get(col, 0) + value
        return CrossTabResult(self.op, cells, row_totals, col_totals)
