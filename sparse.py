"""稀疏矩阵库（仅标准库）。

支持两种格式：
- COO（三元组: row, col, val）：构造/增量插入友好，转置 O(1) 换轴。
- CSR（压缩行: indptr, indices, data）：行访问与 matvec/加法友好。

约定：
- 显式零（stored zero）会被保留并计入 nnz，转换不丢弃。
- COO 允许重复 (row, col) 项；转换为 CSR 时按数学语义合并求和。
"""

__all__ = ["COOMatrix", "CSRMatrix", "DenseMatrix"]


class COOMatrix:
    """三元组格式：rows[i], cols[i], vals[i] 表示一个存储元素。"""

    __slots__ = ("nrows", "ncols", "rows", "cols", "vals")

    def __init__(self, nrows, ncols, rows=None, cols=None, vals=None):
        rows = list(rows) if rows is not None else []
        cols = list(cols) if cols is not None else []
        vals = list(vals) if vals is not None else []
        if not (len(rows) == len(cols) == len(vals)):
            raise ValueError("rows/cols/vals 长度不一致")
        if nrows < 0 or ncols < 0:
            raise ValueError("形状必须非负")
        for r, c in zip(rows, cols):
            if not (0 <= r < nrows and 0 <= c < ncols):
                raise IndexError(f"坐标越界: ({r}, {c}) 形状 ({nrows}, {ncols})")
        self.nrows = nrows
        self.ncols = ncols
        self.rows = rows
        self.cols = cols
        self.vals = vals

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    @property
    def nnz(self):
        return len(self.vals)

    @classmethod
    def from_dense(cls, dense):
        """从 DenseMatrix 构造，仅存储非零。"""
        rows, cols, vals = [], [], []
        for i, row in enumerate(dense.data):
            for j, v in enumerate(row):
                if v != 0:
                    rows.append(i)
                    cols.append(j)
                    vals.append(v)
        return cls(dense.nrows, dense.ncols, rows, cols, vals)

    def to_csr(self):
        """COO -> CSR，计数排序 O(nnz + nrows)，重复坐标合并求和。"""
        nrows, ncols = self.nrows, self.ncols
        indptr = [0] * (nrows + 1)
        for r in self.rows:
            indptr[r + 1] += 1
        for i in range(nrows):
            indptr[i + 1] += indptr[i]
        indices = [0] * self.nnz
        data = [0.0] * self.nnz
        cursor = indptr[:-1]
        for r, c, v in zip(self.rows, self.cols, self.vals):
            pos = cursor[r]
            indices[pos] = c
            data[pos] = v
            cursor[r] = pos + 1
        # 合并每行内重复列（求和），保留显式零
        out_indices, out_data, out_ptr = [], [], [0]
        for i in range(nrows):
            seg = sorted(zip(indices[indptr[i]:indptr[i + 1]],
                             data[indptr[i]:indptr[i + 1]]))
            last_c = None
            acc = 0.0
            for c, v in seg:
                if c == last_c:
                    acc += v
                    out_data[-1] = acc
                else:
                    last_c = c
                    acc = v
                    out_indices.append(c)
                    out_data.append(v)
            out_ptr.append(len(out_indices))
        return CSRMatrix(nrows, ncols, out_ptr, out_indices, out_data)

    def transpose(self):
        """COO 转置：交换行列即可，O(nnz) 拷贝、无排序。"""
        return COOMatrix(self.ncols, self.nrows,
                         self.cols, self.rows, self.vals)

    def matvec(self, x):
        """y = A x，O(nnz)。适合一次性/构造期计算；反复乘建议转 CSR。"""
        if len(x) != self.ncols:
            raise ValueError("向量维度不匹配")
        y = [0.0] * self.nrows
        for r, c, v in zip(self.rows, self.cols, self.vals):
            y[r] += v * x[c]
        return y

    def add(self, other):
        """经 CSR 合并完成加法，O(nnz_A + nnz_B + nrows)。"""
        return self.to_csr().add(_as_csr(other))

    def to_dense(self):
        return self.to_csr().to_dense()

    def __repr__(self):
        return f"COOMatrix(shape={self.shape}, nnz={self.nnz})"


class CSRMatrix:
    """压缩行格式：第 i 行元素为 data[indptr[i]:indptr[i+1]]，列号在 indices。"""

    __slots__ = ("nrows", "ncols", "indptr", "indices", "data")

    def __init__(self, nrows, ncols, indptr, indices, data):
        indptr = list(indptr)
        indices = list(indices)
        data = list(data)
        if len(indptr) != nrows + 1 or indptr[0] != 0 or indptr[-1] != len(data):
            raise ValueError("indptr 非法")
        if len(indices) != len(data):
            raise ValueError("indices/data 长度不一致")
        for c in indices:
            if not (0 <= c < ncols):
                raise IndexError(f"列号越界: {c} (ncols={ncols})")
        self.nrows = nrows
        self.ncols = ncols
        self.indptr = indptr
        self.indices = indices
        self.data = data

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    @property
    def nnz(self):
        return len(self.data)

    @classmethod
    def from_dense(cls, dense):
        return COOMatrix.from_dense(dense).to_csr()

    def to_coo(self):
        """CSR -> COO，O(nnz)，保持全部存储元素（含显式零）。"""
        rows, cols, vals = [], [], []
        for i in range(self.nrows):
            for p in range(self.indptr[i], self.indptr[i + 1]):
                rows.append(i)
                cols.append(self.indices[p])
                vals.append(self.data[p])
        return COOMatrix(self.nrows, self.ncols, rows, cols, vals)

    def transpose(self):
        """CSR 转置 = 计数排序的 CSR->CSC 构造，O(nnz + ncols)。"""
        nrows, ncols = self.nrows, self.ncols
        indptr = [0] * (ncols + 1)
        for c in self.indices:
            indptr[c + 1] += 1
        for j in range(ncols):
            indptr[j + 1] += indptr[j]
        indices = [0] * self.nnz
        data = [0.0] * self.nnz
        cursor = indptr[:-1]
        for i in range(nrows):
            for p in range(self.indptr[i], self.indptr[i + 1]):
                pos = cursor[self.indices[p]]
                indices[pos] = i
                data[pos] = self.data[p]
                cursor[self.indices[p]] = pos + 1
        return CSRMatrix(ncols, nrows, indptr, indices, data)

    def matvec(self, x):
        """y = A x，按行连续扫描，O(nnz)，缓存友好。CSR 的主场运算。"""
        if len(x) != self.ncols:
            raise ValueError("向量维度不匹配")
        y = [0.0] * self.nrows
        for i in range(self.nrows):
            acc = 0.0
            for p in range(self.indptr[i], self.indptr[i + 1]):
                acc += self.data[p] * x[self.indices[p]]
            y[i] = acc
        return y

    def add(self, other):
        """CSR + CSR：双指针按列归并，O(nnz_A + nnz_B)。要求同形状。"""
        other = _as_csr(other)
        if self.shape != other.shape:
            raise ValueError(f"形状不一致: {self.shape} vs {other.shape}")
        nrows = self.nrows
        indptr = [0] * (nrows + 1)
        indices, data = [], []
        for i in range(nrows):
            pa, pb = self.indptr[i], other.indptr[i]
            ea, eb = self.indptr[i + 1], other.indptr[i + 1]
            while pa < ea and pb < eb:
                ca, cb = self.indices[pa], other.indices[pb]
                if ca < cb:
                    indices.append(ca)
                    data.append(self.data[pa])
                    pa += 1
                elif ca > cb:
                    indices.append(cb)
                    data.append(other.data[pb])
                    pb += 1
                else:
                    indices.append(ca)
                    data.append(self.data[pa] + other.data[pb])
                    pa += 1
                    pb += 1
            while pa < ea:
                indices.append(self.indices[pa])
                data.append(self.data[pa])
                pa += 1
            while pb < eb:
                indices.append(other.indices[pb])
                data.append(other.data[pb])
                pb += 1
            indptr[i + 1] = len(indices)
        return CSRMatrix(nrows, self.ncols, indptr, indices, data)

    def to_dense(self):
        dense = DenseMatrix.zeros(self.nrows, self.ncols)
        for i in range(self.nrows):
            row = dense.data[i]
            for p in range(self.indptr[i], self.indptr[i + 1]):
                row[self.indices[p]] += self.data[p]
        return dense

    def __repr__(self):
        return f"CSRMatrix(shape={self.shape}, nnz={self.nnz})"


def _as_csr(m):
    if isinstance(m, CSRMatrix):
        return m
    if isinstance(m, COOMatrix):
        return m.to_csr()
    raise TypeError(f"不支持的类型: {type(m)!r}")


class DenseMatrix:
    """稠密参照实现（list of lists），用于对拍。"""

    __slots__ = ("nrows", "ncols", "data")

    def __init__(self, nrows, ncols, data):
        self.nrows = nrows
        self.ncols = ncols
        self.data = data

    @classmethod
    def zeros(cls, nrows, ncols):
        return cls(nrows, ncols, [[0.0] * ncols for _ in range(nrows)])

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    def matvec(self, x):
        return [sum(row[j] * x[j] for j in range(self.ncols))
                for row in self.data]

    def add(self, other):
        assert self.shape == other.shape
        return DenseMatrix(self.nrows, self.ncols,
                           [[a + b for a, b in zip(ra, rb)]
                            for ra, rb in zip(self.data, other.data)])

    def transpose(self):
        return DenseMatrix(self.ncols, self.nrows,
                           [list(col) for col in zip(*self.data)])
