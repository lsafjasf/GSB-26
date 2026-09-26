"""稀疏矩阵库自测：与稠密实现对拍 + 边界情形。

运行: python3 test_sparse.py  (或 python3 -m unittest test_sparse -v)
"""

import random
import unittest

from sparse import (
    COOMatrix,
    CSRMatrix,
    dense_add,
    dense_matvec,
    dense_transpose,
)

TOL = 1e-9


def random_sparse_dense(rng, nrows, ncols, density, explicit_zero_prob=0.0):
    """生成随机稠密矩阵（大多数元素为零），并可选插入显式零。"""
    dense = [[0.0] * ncols for _ in range(nrows)]
    explicit_zeros = []
    for i in range(nrows):
        for j in range(ncols):
            u = rng.random()
            if u < density:
                dense[i][j] = rng.uniform(-10.0, 10.0)
            elif u < density + explicit_zero_prob:
                explicit_zeros.append((i, j))
    return dense, explicit_zeros


def assert_dense_close(tc, a, b, tol=TOL):
    tc.assertEqual(len(a), len(b))
    for ra, rb in zip(a, b):
        tc.assertEqual(len(ra), len(rb))
        for x, y in zip(ra, rb):
            tc.assertAlmostEqual(x, y, delta=tol)


class EdgeCaseTests(unittest.TestCase):
    def check_all_ops(self, dense):
        """对同一稠密矩阵，验证两种格式的转换/乘法/加法/转置都与稠密一致。"""
        nrows = len(dense)
        ncols = len(dense[0]) if nrows else 0
        rng = random.Random(7)
        x = [rng.uniform(-5, 5) for _ in range(ncols)]
        xt = [rng.uniform(-5, 5) for _ in range(nrows)]

        coo = COOMatrix.from_dense(dense)
        csr = CSRMatrix.from_dense(dense)

        # 形状与 nnz 守恒
        self.assertEqual(coo.shape, (nrows, ncols))
        self.assertEqual(csr.shape, (nrows, ncols))
        self.assertEqual(coo.nnz, csr.nnz)
        self.assertEqual(coo.nnz, sum(v != 0 for row in dense for v in row))

        # 往返转换保持全部非零元素
        self.assertEqual(coo.to_csr().to_coo(), coo)
        self.assertEqual(csr.to_coo().to_csr(), csr)
        assert_dense_close(self, coo.to_dense(), dense)
        assert_dense_close(self, csr.to_dense(), dense)

        # matvec
        assert_dense_close(self, [coo.matvec(x)], [dense_matvec(dense, x)])
        assert_dense_close(self, [csr.matvec(x)], [dense_matvec(dense, x)])

        # 转置
        dt = dense_transpose(dense)
        assert_dense_close(self, coo.transpose().to_dense(), dt)
        assert_dense_close(self, csr.transpose().to_dense(), dt)
        assert_dense_close(self, [csr.transpose().matvec(xt)],
                           [dense_matvec(dt, xt)])

        # 加法（自身 + 转置的转置 = 2A）
        doubled = dense_add(dense, dense)
        assert_dense_close(self, coo.add(coo).to_dense(), doubled)
        assert_dense_close(self, csr.add(csr).to_dense(), doubled)
        assert_dense_close(self, coo.add(csr).to_dense(), doubled)  # 混合格式
        assert_dense_close(self, csr.add(coo).to_dense(), doubled)

    def test_zero_matrix(self):
        self.check_all_ops([[0.0] * 6 for _ in range(4)])
        coo = COOMatrix.from_dense([[0.0, 0.0], [0.0, 0.0]])
        self.assertEqual(coo.nnz, 0)
        self.assertEqual(coo.to_csr().nnz, 0)
        self.assertEqual(coo.matvec([1.0, 2.0]), [0.0, 0.0])

    def test_all_nonzero(self):
        rng = random.Random(1)
        dense = [[rng.uniform(-9, 9) or 1.0 for _ in range(8)] for _ in range(8)]
        self.check_all_ops(dense)

    def test_single_row(self):
        self.check_all_ops([[0.0, 3.5, 0.0, -2.0, 0.0, 1.0]])

    def test_single_column(self):
        self.check_all_ops([[0.0], [4.0], [0.0], [-1.5], [0.0]])

    def test_single_element_and_empty(self):
        self.check_all_ops([[2.5]])
        self.check_all_ops([[0.0]])
        empty = COOMatrix(3, 3, [], [], [])
        self.assertEqual(empty.nnz, 0)
        self.assertEqual(empty.to_csr().transpose().shape, (3, 3))

    def test_explicit_zeros(self):
        """显式零作为存储元素保留：转换不丢失，数值结果不受影响。"""
        coo = COOMatrix(3, 3, [0, 1, 2], [0, 1, 2], [5.0, 0.0, -3.0])
        self.assertEqual(coo.nnz, 3)  # 显式零计入 nnz
        csr = coo.to_csr()
        self.assertEqual(csr.nnz, 3)  # 转换保留显式零
        self.assertEqual(csr.to_coo(), coo)
        self.assertEqual(csr.matvec([1.0, 1.0, 1.0]), [5.0, 0.0, -3.0])
        # 显式零 + 显式零 仍为存储元素
        s = csr.add(csr)
        self.assertEqual(s.nnz, 3)
        self.assertEqual(s.to_dense(), [[10.0, 0, 0], [0, 0, 0], [0, 0, -6.0]])

    def test_duplicate_entries_summed(self):
        coo = COOMatrix(2, 2, [0, 0, 1], [1, 1, 0], [1.0, 2.0, 3.0])
        self.assertEqual(coo.nnz, 2)
        self.assertEqual(coo.to_dense(), [[0.0, 3.0], [3.0, 0.0]])

    def test_shape_errors(self):
        a = CSRMatrix.from_dense([[1.0]])
        b = CSRMatrix.from_dense([[1.0, 2.0]])
        with self.assertRaises(ValueError):
            a.add(b)
        with self.assertRaises(ValueError):
            a.matvec([1.0, 2.0])
        with self.assertRaises(IndexError):
            COOMatrix(2, 2, [2], [0], [1.0])


class DifferentialTests(unittest.TestCase):
    """随机稀疏矩阵与稠密实现逐元素对拍。"""

    def test_random_against_dense(self):
        for seed in range(60):
            rng = random.Random(seed)
            nrows = rng.randint(1, 25)
            ncols = rng.randint(1, 25)
            density = rng.choice([0.0, 0.02, 0.1, 0.4, 1.0])
            dense, _ = random_sparse_dense(rng, nrows, ncols, density)
            x = [rng.uniform(-8, 8) for _ in range(ncols)]
            dense2, _ = random_sparse_dense(rng, nrows, ncols, density)

            coo = COOMatrix.from_dense(dense)
            csr = coo.to_csr()
            coo2 = COOMatrix.from_dense(dense2)
            csr2 = coo2.to_csr()

            with self.subTest(seed=seed, shape=(nrows, ncols), density=density):
                # matvec
                expect = dense_matvec(dense, x)
                for got in (coo.matvec(x), csr.matvec(x)):
                    self.assertEqual(len(got), nrows)
                    for g, e in zip(got, expect):
                        self.assertAlmostEqual(g, e, delta=TOL)
                # add
                assert_dense_close(self, coo.add(coo2).to_dense(),
                                   dense_add(dense, dense2))
                assert_dense_close(self, csr.add(csr2).to_dense(),
                                   dense_add(dense, dense2))
                # transpose（含双重转置还原）
                assert_dense_close(self, csr.transpose().to_dense(),
                                   dense_transpose(dense))
                assert_dense_close(self, coo.transpose().transpose().to_dense(),
                                   dense)
                # 转换守恒：nnz 与形状
                self.assertEqual(coo.nnz, csr.nnz)
                self.assertEqual(coo.to_csr(), csr)
                self.assertEqual(csr.to_coo(), coo)

    def test_rectangular_and_structured(self):
        rng = random.Random(99)
        cases = [
            (1, 50, 0.3),   # 单行
            (50, 1, 0.3),   # 单列
            (1, 1, 1.0),    # 1x1
            (30, 7, 0.05),  # 高稀疏瘦矩阵
            (7, 30, 0.9),   # 近稠密宽矩阵
        ]
        for nrows, ncols, density in cases:
            dense, _ = random_sparse_dense(rng, nrows, ncols, density)
            csr = CSRMatrix.from_dense(dense)
            with self.subTest(shape=(nrows, ncols)):
                assert_dense_close(self, csr.transpose().transpose().to_dense(), dense)
                assert_dense_close(
                    self, csr.add(COOMatrix.from_dense(dense)).to_dense(),
                    dense_add(dense, dense),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
