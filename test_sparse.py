"""稀疏矩阵库自测：格式转换 + 与稠密实现对拍 + 边界情形。仅标准库。"""

import random
import sys
import unittest

from sparse import COOMatrix, CSRMatrix, DenseMatrix

TOL = 1e-9


def assert_vec_close(tc, a, b):
    tc.assertEqual(len(a), len(b))
    for x, y in zip(a, b):
        tc.assertAlmostEqual(x, y, delta=TOL)


def assert_dense_close(tc, A, B):
    tc.assertEqual(A.shape, B.shape)
    for ra, rb in zip(A.data, B.data):
        assert_vec_close(tc, ra, rb)


def random_coo(rng, nrows, ncols, density, explicit_zero_ratio=0.0):
    """随机 COO：按密度采样非零，另按比例塞入显式零。"""
    rows, cols, vals = [], [], []
    for i in range(nrows):
        for j in range(ncols):
            r = rng.random()
            if r < density:
                rows.append(i)
                cols.append(j)
                vals.append(rng.uniform(-10, 10))
            elif r < density + explicit_zero_ratio:
                rows.append(i)
                cols.append(j)
                vals.append(0.0)  # 显式零：占用存储但值为 0
    return COOMatrix(nrows, ncols, rows, cols, vals)


class TestConversion(unittest.TestCase):
    def check_roundtrip(self, coo):
        csr = coo.to_csr()
        coo2 = csr.to_coo()
        # 形状保持
        self.assertEqual(coo.shape, csr.shape)
        self.assertEqual(coo.shape, coo2.shape)
        # 全部非零元素保持（与稠密对拍逐元素一致）
        assert_dense_close(self, coo.to_dense(), csr.to_dense())
        assert_dense_close(self, coo.to_dense(), coo2.to_dense())
        # 显式零不丢失：CSR 的 nnz >= 稠密真非零数
        dense_nnz = sum(1 for row in coo.to_dense().data for v in row if v != 0)
        self.assertGreaterEqual(csr.nnz, dense_nnz)

    def test_roundtrip_random(self):
        rng = random.Random(42)
        for _ in range(20):
            m, n = rng.randint(0, 8), rng.randint(0, 8)
            coo = random_coo(rng, m, n, density=0.3, explicit_zero_ratio=0.1)
            self.check_roundtrip(coo)

    def test_duplicate_entries_merge(self):
        coo = COOMatrix(2, 2, [0, 0, 1], [1, 1, 0], [1.5, 2.5, 3.0])
        csr = coo.to_csr()
        self.assertEqual(csr.nnz, 2)
        self.assertEqual(csr.to_dense().data, [[0.0, 4.0], [3.0, 0.0]])


class TestOpsVsDense(unittest.TestCase):
    """随机稀疏矩阵的 matvec / add / transpose 与稠密实现逐元素对拍。"""

    def run_case(self, rng, m, n, density, ez=0.0):
        a_coo = random_coo(rng, m, n, density, ez)
        b_coo = random_coo(rng, m, n, density, ez)
        a_csr, b_csr = a_coo.to_csr(), b_coo.to_csr()
        a_den, b_den = a_csr.to_dense(), b_csr.to_dense()

        # matvec（COO 与 CSR 两条路径都对拍）
        x = [rng.uniform(-5, 5) for _ in range(n)]
        assert_vec_close(self, a_csr.matvec(x), a_den.matvec(x))
        assert_vec_close(self, a_coo.matvec(x), a_den.matvec(x))

        # add
        assert_dense_close(self, a_csr.add(b_csr).to_dense(), a_den.add(b_den))
        assert_dense_close(self, a_coo.add(b_coo).to_dense(), a_den.add(b_den))

        # transpose（COO 与 CSR 两条路径）
        assert_dense_close(self, a_csr.transpose().to_dense(), a_den.transpose())
        assert_dense_close(self, a_coo.transpose().to_dense(), a_den.transpose())
        # 转置两次还原
        assert_dense_close(self, a_csr.transpose().transpose().to_dense(), a_den)

    def test_random_sparse(self):
        rng = random.Random(7)
        for _ in range(30):
            m, n = rng.randint(1, 12), rng.randint(1, 12)
            self.run_case(rng, m, n, density=rng.uniform(0.05, 0.5),
                          ez=rng.uniform(0.0, 0.1))

    def test_zero_matrix(self):
        rng = random.Random(1)
        self.run_case(rng, 5, 4, density=0.0)
        z = CSRMatrix(3, 3, [0, 0, 0, 0], [], [])
        self.assertEqual(z.nnz, 0)
        self.assertEqual(z.matvec([1.0, 2.0, 3.0]), [0.0, 0.0, 0.0])

    def test_all_nonzero(self):
        rng = random.Random(2)
        self.run_case(rng, 6, 6, density=1.0)

    def test_single_row(self):
        rng = random.Random(3)
        self.run_case(rng, 1, 9, density=0.6, ez=0.1)

    def test_single_col(self):
        rng = random.Random(4)
        self.run_case(rng, 9, 1, density=0.6, ez=0.1)

    def test_explicit_zeros(self):
        # 显式零必须被存储、参与转换且不改变数值结果
        coo = COOMatrix(2, 3, [0, 0, 1], [0, 2, 1], [0.0, 5.0, 0.0])
        csr = coo.to_csr()
        self.assertEqual(csr.nnz, 3)  # 显式零保留
        back = csr.to_coo()
        self.assertEqual(back.nnz, 3)
        self.assertEqual(back.vals.count(0.0), 2)
        assert_vec_close(self, csr.matvec([1.0, 1.0, 1.0]), [5.0, 0.0])
        t = csr.transpose()
        self.assertEqual(t.nnz, 3)
        assert_dense_close(self, t.to_dense(), coo.to_dense().transpose())

    def test_shape_mismatch_raises(self):
        a = COOMatrix(2, 2, [0], [0], [1.0]).to_csr()
        b = COOMatrix(2, 3, [0], [0], [1.0]).to_csr()
        with self.assertRaises(ValueError):
            a.add(b)
        with self.assertRaises(ValueError):
            a.matvec([1.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
