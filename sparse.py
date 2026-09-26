"""稀疏矩阵库（仅标准库）。

支持两种存储格式：
- COO (Coordinate / 三元组): rows[], cols[], vals[] 三个等长数组。
- CSR (Compressed Sparse Row / 压缩行): indptr[], indices[], data[]。

内部存储使用 array 模块（'i' 32 位整数下标, 'd' 双精度浮点），
因此 memory_bytes() 报告的是真实紧凑内存，而非 Python 对象开销。

约定：
- COO 允许重复坐标（构造时可选择 canonicalize 合并求和）。
- 显式零（stored zero）作为存储元素保留，参与 nnz 计数，但不影响数值结果。
"""

from array import array

_INT = "i"     # 32 位有符号整数下标
_FLOAT = "d"   # 双精度浮点
INT_SIZE = array(_INT).itemsize      # 4
FLOAT_SIZE = array(_FLOAT).itemsize  # 8


def _check_shape(nrows, ncols):
    if nrows < 0 or ncols < 0:
        raise ValueError("矩阵维度必须非负")


class COOMatrix:
    """三元组格式。适合：增量构造、格式转换中转、转置。"""

    __slots__ = ("nrows", "ncols", "rows", "cols", "vals")

    def __init__(self, nrows, ncols, rows, cols, vals, canonicalize=True):
        _check_shape(nrows, ncols)
        if not (len(rows) == len(cols) == len(vals)):
            raise ValueError("rows/cols/vals 长度不一致")
        self.nrows = nrows
        self.ncols = ncols
        self.rows = array(_INT, rows)
        self.cols = array(_INT, cols)
        self.vals = array(_FLOAT, vals)
        for r, c in zip(self.rows, self.cols):
            if not (0 <= r < nrows and 0 <= c < ncols):
                raise IndexError("坐标越界: (%d, %d)" % (r, c))
        if canonicalize:
            self._canonicalize()

    @property
    def nnz(self):
        return len(self.vals)

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    def _canonicalize(self):
        """按 (row, col) 排序并合并重复坐标（值求和）。O(nnz log nnz)。"""
        order = sorted(range(self.nnz), key=lambda k: (self.rows[k], self.cols[k]))
        rows, cols, vals = array(_INT), array(_INT), array(_FLOAT)
        for k in order:
            r, c, v = self.rows[k], self.cols[k], self.vals[k]
            if rows and rows[-1] == r and cols[-1] == c:
                vals[-1] += v
            else:
                rows.append(r)
                cols.append(c)
                vals.append(v)
        self.rows, self.cols, self.vals = rows, cols, vals

    @classmethod
    def from_dense(cls, dense):
        """从稠密二维列表构造（忽略数值零）。"""
        nrows = len(dense)
        ncols = len(dense[0]) if nrows else 0
        rows, cols, vals = [], [], []
        for i, row in enumerate(dense):
            if len(row) != ncols:
                raise ValueError("稠密输入不是矩形")
            for j, v in enumerate(row):
                if v != 0:
                    rows.append(i)
                    cols.append(j)
                    vals.append(float(v))
        return cls(nrows, ncols, rows, cols, vals, canonicalize=False)

    def to_dense(self):
        dense = [[0.0] * self.ncols for _ in range(self.nrows)]
        for r, c, v in zip(self.rows, self.cols, self.vals):
            dense[r][c] += v
        return dense

    def to_csr(self):
        """COO -> CSR：按行计数排序。O(nnz + nrows)。"""
        indptr = array(_INT, bytes(INT_SIZE * (self.nrows + 1)))
        for r in self.rows:
            indptr[r + 1] += 1
        for i in range(self.nrows):
            indptr[i + 1] += indptr[i]
        nnz = self.nnz
        indices = array(_INT, bytes(INT_SIZE * nnz))
        data = array(_FLOAT, bytes(FLOAT_SIZE * nnz))
        next_pos = array(_INT, indptr[:-1])
        for r, c, v in zip(self.rows, self.cols, self.vals):
            p = next_pos[r]
            indices[p] = c
            data[p] = v
            next_pos[r] += 1
        return CSRMatrix(self.nrows, self.ncols, indptr, indices, data)

    def matvec(self, x):
        """y = A x。O(nnz)。COO 下为纯散写累加，适合一次性运算。"""
        if len(x) != self.ncols:
            raise ValueError("向量维度不匹配")
        y = [0.0] * self.nrows
        for r, c, v in zip(self.rows, self.cols, self.vals):
            y[r] += v * x[c]
        return y

    def add(self, other):
        """COO 相加 = 拼接 + 归并。O(nnzA + nnzB)（含排序则为 O(N log N)）。"""
        other = _as_coo(other)
        if self.shape != other.shape:
            raise ValueError("矩阵形状不匹配")
        return COOMatrix(
            self.nrows, self.ncols,
            list(self.rows) + list(other.rows),
            list(self.cols) + list(other.cols),
            list(self.vals) + list(other.vals),
            canonicalize=True,
        )

    def transpose(self):
        """COO 转置 = 交换行列再归一化。O(nnz log nnz)。"""
        return COOMatrix(
            self.ncols, self.nrows,
            self.cols, self.rows, self.vals,
            canonicalize=True,
        )

    def memory_bytes(self):
        return self.nnz * (2 * INT_SIZE + FLOAT_SIZE)

    def __eq__(self, other):
        if not isinstance(other, COOMatrix):
            return NotImplemented
        return (
            self.shape == other.shape
            and self.rows == other.rows
            and self.cols == other.cols
            and self.vals == other.vals
        )

    def __repr__(self):
        return "COOMatrix(shape=%r, nnz=%d)" % (self.shape, self.nnz)


class CSRMatrix:
    """压缩行格式。适合：反复的 matvec、行切片、矩阵加法。"""

    __slots__ = ("nrows", "ncols", "indptr", "indices", "data")

    def __init__(self, nrows, ncols, indptr, indices, data):
        _check_shape(nrows, ncols)
        if len(indptr) != nrows + 1:
            raise ValueError("indptr 长度必须为 nrows+1")
        if len(indices) != len(data):
            raise ValueError("indices/data 长度不一致")
        self.nrows = nrows
        self.ncols = ncols
        self.indptr = array(_INT, indptr)
        self.indices = array(_INT, indices)
        self.data = array(_FLOAT, data)

    @property
    def nnz(self):
        return len(self.data)

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    @classmethod
    def from_dense(cls, dense):
        return COOMatrix.from_dense(dense).to_csr()

    def to_dense(self):
        dense = [[0.0] * self.ncols for _ in range(self.nrows)]
        for i in range(self.nrows):
            row = dense[i]
            for p in range(self.indptr[i], self.indptr[i + 1]):
                row[self.indices[p]] += self.data[p]
        return dense

    def to_coo(self):
        """CSR -> COO：展开行指针。O(nnz)。"""
        nnz = self.nnz
        rows = array(_INT, bytes(INT_SIZE * nnz))
        for i in range(self.nrows):
            for p in range(self.indptr[i], self.indptr[i + 1]):
                rows[p] = i
        return COOMatrix(
            self.nrows, self.ncols, rows, self.indices, self.data,
            canonicalize=False,
        )

    def matvec(self, x):
        """y = A x。O(nnz + nrows)。行内连续访存，缓存友好，是 matvec 的首选格式。"""
        if len(x) != self.ncols:
            raise ValueError("向量维度不匹配")
        y = [0.0] * self.nrows
        for i in range(self.nrows):
            s = 0.0
            for p in range(self.indptr[i], self.indptr[i + 1]):
                s += self.data[p] * x[self.indices[p]]
            y[i] = s
        return y

    def add(self, other):
        """CSR 相加：逐行双指针归并。O(nnzA + nnzB + nrows)。结果列下标有序。"""
        if isinstance(other, COOMatrix):
            other = other.to_csr()
        if self.shape != other.shape:
            raise ValueError("矩阵形状不匹配")
        indptr = array(_INT, [0])
        indices, data = array(_INT), array(_FLOAT)
        for i in range(self.nrows):
            pa, enda = self.indptr[i], self.indptr[i + 1]
            pb, endb = other.indptr[i], other.indptr[i + 1]
            while pa < enda and pb < endb:
                ca, cb = self.indices[pa], other.indices[pb]
                if ca < cb:
                    indices.append(ca)
                    data.append(self.data[pa])
                    pa += 1
                elif cb < ca:
                    indices.append(cb)
                    data.append(other.data[pb])
                    pb += 1
                else:
                    indices.append(ca)
                    data.append(self.data[pa] + other.data[pb])
                    pa += 1
                    pb += 1
            while pa < enda:
                indices.append(self.indices[pa])
                data.append(self.data[pa])
                pa += 1
            while pb < endb:
                indices.append(other.indices[pb])
                data.append(other.data[pb])
                pb += 1
            indptr.append(len(indices))
        return CSRMatrix(self.nrows, self.ncols, indptr, indices, data)

    def transpose(self):
        """CSR 转置：计数排序（等价于 CSR->COO->CSR'）。O(nnz + ncols)。"""
        nnz = self.nnz
        indptr = array(_INT, bytes(INT_SIZE * (self.ncols + 1)))
        for c in self.indices:
            indptr[c + 1] += 1
        for j in range(self.ncols):
            indptr[j + 1] += indptr[j]
        indices = array(_INT, bytes(INT_SIZE * nnz))
        data = array(_FLOAT, bytes(FLOAT_SIZE * nnz))
        next_pos = array(_INT, indptr[:-1])
        for i in range(self.nrows):
            for p in range(self.indptr[i], self.indptr[i + 1]):
                q = next_pos[self.indices[p]]
                indices[q] = i
                data[q] = self.data[p]
                next_pos[self.indices[p]] += 1
        return CSRMatrix(self.ncols, self.nrows, indptr, indices, data)

    def memory_bytes(self):
        return (self.nrows + 1) * INT_SIZE + self.nnz * (INT_SIZE + FLOAT_SIZE)

    def __eq__(self, other):
        if not isinstance(other, CSRMatrix):
            return NotImplemented
        return (
            self.shape == other.shape
            and self.indptr == other.indptr
            and self.indices == other.indices
            and self.data == other.data
        )

    def __repr__(self):
        return "CSRMatrix(shape=%r, nnz=%d)" % (self.shape, self.nnz)


def _as_coo(m):
    if isinstance(m, COOMatrix):
        return m
    if isinstance(m, CSRMatrix):
        return m.to_coo()
    raise TypeError("不支持的矩阵类型: %r" % type(m))


def dense_matvec(dense, x):
    return [sum(row[j] * x[j] for j in range(len(x))) for row in dense]


def dense_add(a, b):
    return [[x + y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def dense_transpose(dense):
    if not dense:
        return []
    return [list(col) for col in zip(*dense)]
