"""原因链测试：多层包装后仍能追溯到底层原因。"""

import errno
import socket
import unittest

from unified import service
from unified.errors import AppError, format_chain, iter_chain
from unified.storage import save


def _timeout_transport(host, port, path):
    raise socket.timeout("timed out")


class TestCauseChain(unittest.TestCase):
    def test_multi_layer_chain_reaches_root(self):
        try:
            service.fetch_and_cache("db.internal", 9000, "/metrics",
                                    "/tmp/cache", _timeout_transport, lambda p, d: None)
        except AppError as err:
            chain = list(iter_chain(err))
        else:
            raise AssertionError("expected AppError")

        # 服务层 -> 网络层 -> 底层 socket.timeout，三层都在链上
        self.assertEqual([n.code for n in chain if isinstance(n, AppError)],
                         ["INTERNAL_UNEXPECTED", "NET_CONNECT_TIMEOUT"])
        self.assertIsInstance(chain[-1], socket.timeout)
        # 可重试性沿链传播
        self.assertTrue(chain[0].retryable)

    def test_os_error_errno_preserved_as_cause(self):
        try:
            save("/data/f.bin", b"x", _raise_enospc)
        except AppError as err:
            self.assertEqual(err.code, "STORAGE_DISK_FULL")
            self.assertIsInstance(err.__cause__, OSError)
            self.assertEqual(err.__cause__.errno, errno.ENOSPC)
        else:
            raise AssertionError("expected AppError")

    def test_format_chain_output(self):
        try:
            service.fetch_and_cache("db.internal", 9000, "/metrics",
                                    "/tmp/cache", _timeout_transport, lambda p, d: None)
        except AppError as err:
            text = format_chain(err)
        else:
            raise AssertionError("expected AppError")
        self.assertIn("[0] internal/INTERNAL_UNEXPECTED", text)
        self.assertIn("[1] network/NET_CONNECT_TIMEOUT", text)
        self.assertIn("[2] root TimeoutError", text)  # socket.timeout 是 TimeoutError 别名
        print("\n--- 原因链输出样例 ---\n%s\n----------------------" % text)


def _raise_enospc(path, data):
    raise OSError(errno.ENOSPC, "No space left on device")


if __name__ == "__main__":
    unittest.main()
