"""原因链测试：多层包装后仍能追溯到底层 OSError。"""

import unittest

from refactored import service
from refactored.errors import AppError, format_chain, iter_chain


def boom_dial(dsn):
    raise ConnectionRefusedError(111, "Connection refused")


class CauseChainTest(unittest.TestCase):
    DEPS = dict(transport=lambda url, t: b"{}",
                dial=boom_dial,
                backend=lambda key: b"x")

    def test_chain_reaches_root_cause(self):
        with self.assertRaises(AppError) as ctx:
            service.load_profile("42", **self.DEPS)
        chain = list(iter_chain(ctx.exception))
        self.assertEqual([e.code for e in chain if isinstance(e, AppError)],
                         ["PROFILE_LOAD_FAILED", "STORE_CONN"])
        self.assertIsInstance(chain[-1], ConnectionRefusedError)

    def test_format_chain_output(self):
        with self.assertRaises(AppError) as ctx:
            service.load_profile("42", **self.DEPS)
        text = format_chain(ctx.exception)
        self.assertIn("PROFILE_LOAD_FAILED [internal]", text)
        self.assertIn("caused by: STORE_CONN [storage]", text)
        self.assertIn("caused by: ConnectionRefusedError", text)
        self.assertIn("user_id='42'", text)
        self.assertIn("dsn='db://main'", text)

    def test_unknown_code_rejected(self):
        with self.assertRaises(ValueError):
            AppError("NO_SUCH_CODE", foo=1)


if __name__ == "__main__":
    unittest.main()
