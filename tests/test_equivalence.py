"""对拍测试：同一故障场景下，逐例比较重构前后错误的
分类、错误码、可重试性与关键上下文，保证错误语义等价、信息不丢失。
"""

import unittest

from legacy import cache as legacy_cache
from legacy import net as legacy_net
from legacy import service as legacy_service
from legacy import store as legacy_store
from refactored import cache as new_cache
from refactored import compat
from refactored import net as new_net
from refactored import service as new_service
from refactored import store as new_store
from refactored.errors import AppError

# 每个错误码必须保留的关键上下文字段
KEY_CONTEXT = {
    "NET_TIMEOUT": {"url", "timeout"},
    "NET_CONN_REFUSED": {"url"},
    "STORE_CONN": {"dsn"},
    "STORE_QUERY": {"sql"},
    "CACHE_BACKEND_DOWN": {"key"},
    "PROFILE_LOAD_FAILED": {"user_id"},
}


def projection(err):
    """把任一版本错误投影为可比较的规范化元组。"""
    mapped = err if isinstance(err, AppError) else compat.from_legacy(err)
    assert mapped is not None, "unmapped legacy error: %r" % (err,)
    key_ctx = {k: mapped.context[k] for k in KEY_CONTEXT[mapped.code]}
    return (mapped.code, mapped.category, mapped.retryable, key_ctx)


def boom_timeout(url, timeout):
    raise TimeoutError("timed out")


def boom_refused(url, timeout):
    raise ConnectionRefusedError(111, "Connection refused")


def boom_dial(dsn):
    raise ConnectionRefusedError(111, "Connection refused")


def boom_backend(key):
    raise OSError(113, "No route to host")


class ModuleLevelEquivalenceTest(unittest.TestCase):
    """模块级对拍：同一注入故障分别打进新旧实现。"""

    def assertEquivalent(self, legacy_fn, new_fn):
        with self.assertRaises(Exception) as old_ctx:
            legacy_fn()
        with self.assertRaises(AppError) as new_ctx:
            new_fn()
        self.assertEqual(projection(old_ctx.exception),
                         projection(new_ctx.exception))

    def test_net_timeout(self):
        self.assertEquivalent(
            lambda: legacy_net.http_get("http://api.internal/users/7",
                                        transport=boom_timeout, timeout=1.5),
            lambda: new_net.http_get("http://api.internal/users/7",
                                     transport=boom_timeout, timeout=1.5))

    def test_net_conn_refused(self):
        self.assertEquivalent(
            lambda: legacy_net.http_get("http://api.internal/users/7",
                                        transport=boom_refused),
            lambda: new_net.http_get("http://api.internal/users/7",
                                     transport=boom_refused))

    def test_store_connect(self):
        self.assertEquivalent(
            lambda: legacy_store.connect("db://main", dial=boom_dial),
            lambda: new_store.connect("db://main", dial=boom_dial))

    def test_store_query(self):
        def make_conn(deadlock_cls):
            class DeadlockConn:
                def execute(self, sql, params):
                    raise deadlock_cls("deadlock detected")
            return DeadlockConn()

        sql = "UPDATE profile SET name=%s"
        self.assertEquivalent(
            lambda: legacy_store.query(
                make_conn(legacy_store.DeadlockError), sql),
            lambda: new_store.query(
                make_conn(new_store.DeadlockError), sql))

    def test_cache_backend_down(self):
        self.assertEquivalent(
            lambda: legacy_cache.get("profile:7", backend=boom_backend),
            lambda: new_cache.get("profile:7", backend=boom_backend))


class ServiceLevelEquivalenceTest(unittest.TestCase):
    """服务级对拍：多层包装后，外层与内层错误的语义都等价。"""

    SCENARIOS = {
        "net_timeout": dict(transport=boom_timeout,
                            dial=lambda dsn: object(),
                            backend=lambda key: b"x"),
        "net_refused": dict(transport=boom_refused,
                            dial=lambda dsn: object(),
                            backend=lambda key: b"x"),
        "store_down": dict(transport=lambda url, t: b"{}",
                           dial=boom_dial,
                           backend=lambda key: b"x"),
        "cache_down": dict(transport=lambda url, t: b"{}",
                           dial=lambda dsn: object(),
                           backend=boom_backend),
    }

    def test_all_scenarios(self):
        for name, deps in self.SCENARIOS.items():
            with self.subTest(scenario=name):
                with self.assertRaises(Exception) as old_ctx:
                    legacy_service.load_profile("42", **deps)
                with self.assertRaises(AppError) as new_ctx:
                    new_service.load_profile("42", **deps)
                old_outer = compat.from_legacy(old_ctx.exception)
                new_outer = new_ctx.exception
                # 外层错误等价
                self.assertEqual(projection(old_outer), projection(new_outer))
                # 内层（被包装的下游）错误等价
                self.assertEqual(projection(old_outer.__cause__),
                                 projection(new_outer.__cause__))

    def test_no_key_info_lost(self):
        """新旧错误的上下文都必须完整覆盖关键字段。"""
        for name, deps in self.SCENARIOS.items():
            with self.subTest(scenario=name):
                with self.assertRaises(AppError) as ctx:
                    new_service.load_profile("42", **deps)
                err = ctx.exception
                while isinstance(err, AppError):
                    self.assertTrue(
                        KEY_CONTEXT[err.code] <= err.context.keys(),
                        "%s lost context: %r" % (err.code, err.context))
                    err = err.__cause__


if __name__ == "__main__":
    unittest.main()
