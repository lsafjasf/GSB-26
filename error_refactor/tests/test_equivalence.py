"""对拍测试：同一故障场景分别跑 legacy / unified 实现，
逐例比较错误分类、错误码、可重试性与关键上下文，保证重构前后错误等价。"""

import errno
import socket
import unittest

from legacy import auth as legacy_auth
from legacy import net_client as legacy_net
from legacy import storage as legacy_storage
from unified import auth as uni_auth
from unified import net_client as uni_net
from unified import storage as uni_storage
from unified.compat import map_legacy, to_legacy
from unified.errors import ERROR_REGISTRY, AppError


def _timeout_transport(host, port, path):
    raise socket.timeout("timed out")


def _refused_transport(host, port, path):
    raise ConnectionRefusedError("refused")


def _status_transport(status):
    def transport(host, port, path):
        return status, b""
    return transport


def _enospc_write(path, data):
    raise OSError(errno.ENOSPC, "No space left on device")


def _eio_write(path, data):
    raise OSError(errno.EIO, "Input/output error")


def _expired_verifier(token):
    raise legacy_auth.TokenExpired("exp")


def _expired_verifier_uni(token):
    raise uni_auth.TokenExpired("exp")


def _forbidden_verifier(token):
    raise PermissionError("nope")


# (用例名, legacy 调用, unified 调用) —— 两侧输入完全相同
SCENARIOS = [
    ("net_timeout",
     lambda: legacy_net.fetch("example.com", 8080, "/a", _timeout_transport),
     lambda: uni_net.fetch("example.com", 8080, "/a", _timeout_transport)),
    ("net_refused",
     lambda: legacy_net.fetch("example.com", 8080, "/a", _refused_transport),
     lambda: uni_net.fetch("example.com", 8080, "/a", _refused_transport)),
    ("net_http_500",
     lambda: legacy_net.fetch("example.com", 80, "/a", _status_transport(500)),
     lambda: uni_net.fetch("example.com", 80, "/a", _status_transport(500))),
    ("net_http_404",
     lambda: legacy_net.fetch("example.com", 80, "/a", _status_transport(404)),
     lambda: uni_net.fetch("example.com", 80, "/a", _status_transport(404))),
    ("storage_disk_full",
     lambda: legacy_storage.save("/tmp/x.bin", b"d", _enospc_write),
     lambda: uni_storage.save("/tmp/x.bin", b"d", _enospc_write)),
    ("storage_io_error",
     lambda: legacy_storage.save("/tmp/x.bin", b"d", _eio_write),
     lambda: uni_storage.save("/tmp/x.bin", b"d", _eio_write)),
    ("auth_expired",
     lambda: legacy_auth.authenticate("tok", _expired_verifier),
     lambda: uni_auth.authenticate("tok", _expired_verifier_uni)),
    ("auth_forbidden",
     lambda: legacy_auth.authenticate("tok", _forbidden_verifier),
     lambda: uni_auth.authenticate("tok", _forbidden_verifier)),
    ("auth_empty_token",
     lambda: legacy_auth.authenticate("", _forbidden_verifier),
     lambda: uni_auth.authenticate("", _forbidden_verifier)),
]


def _capture(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 对拍就是要捕获一切
        return exc
    raise AssertionError("expected an exception")


class TestErrorEquivalence(unittest.TestCase):
    def test_all_scenarios_equivalent(self):
        for name, legacy_call, unified_call in SCENARIOS:
            with self.subTest(scenario=name):
                legacy_exc = _capture(legacy_call)
                unified_exc = _capture(unified_call)

                self.assertIsInstance(unified_exc, AppError)
                code, legacy_ctx = map_legacy(legacy_exc)

                # 1) 错误码等价
                self.assertEqual(code, unified_exc.code)
                # 2) 分类等价（由错误码唯一决定，且必须已注册）
                self.assertEqual(ERROR_REGISTRY[code].category, unified_exc.category)
                # 3) 可重试性等价
                self.assertEqual(ERROR_REGISTRY[code].retryable, unified_exc.retryable)
                # 4) 原有关键信息不得丢失：legacy 能提供的上下文，
                #    unified 必须全部保留且取值一致
                for key, value in legacy_ctx.items():
                    self.assertIn(key, unified_exc.context,
                                  "context key %r lost in refactor" % key)
                    self.assertEqual(value, unified_exc.context[key],
                                     "context[%r] changed in refactor" % key)

    def test_success_paths_unchanged(self):
        ok = lambda h, p, pa: (200, b"hello")
        self.assertEqual(legacy_net.fetch("h", 1, "/a", ok),
                         uni_net.fetch("h", 1, "/a", ok))
        writes = []
        self.assertEqual(uni_storage.save("/p", b"d", lambda *a: writes.append(a)), "/p")
        self.assertEqual(uni_auth.authenticate("tok", lambda t: {"sub": "u"}),
                         {"sub": "u"})

    def test_registry_is_single_source_of_truth(self):
        # 模块里出现的每个错误码必须已注册（新增错误只需改注册表）
        import re, pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "unified"
        used = set()
        for src in root.glob("*.py"):
            if src.name in ("errors.py", "compat.py"):
                continue
            used |= set(re.findall(r'AppError\("([A-Z_]+)"', src.read_text()))
        self.assertTrue(used, "no AppError usage found - test is broken")
        self.assertEqual(used - set(ERROR_REGISTRY), set())
        # 未注册的错误码是编程错误，立即暴露
        with self.assertRaises(KeyError):
            AppError("NO_SUCH_CODE")

    def test_to_legacy_roundtrip(self):
        # 迁移期适配器：unified 错误可无损转回旧类型供老调用方捕获
        for name, legacy_call, unified_call in SCENARIOS:
            with self.subTest(scenario=name):
                legacy_exc = _capture(legacy_call)
                adapted = to_legacy(_capture(unified_call))
                self.assertIs(type(adapted), type(legacy_exc))
                self.assertEqual(str(adapted), str(legacy_exc))


if __name__ == "__main__":
    unittest.main()
