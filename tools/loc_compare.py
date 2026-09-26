"""统计重构前后错误处理相关代码行数：python3 tools/loc_compare.py"""

import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
ERROR_KEYWORDS = ("raise ", "except ", "try:", "class ", "ErrorSpec(",
                  "_REGISTRY", "AppError(", "def from_legacy",
                  "def _from_legacy_text")


def count(path, error_only):
    total = err = 0
    with open(path) as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", '"""', "'''")):
                continue
            total += 1
            if any(k in stripped for k in ERROR_KEYWORDS):
                err += 1
    return total, err


def report(label, files, error_only=True):
    total = err = 0
    for f in files:
        t, e = count(os.path.join(ROOT, f), error_only)
        total += t
        err += e
    print("%-12s 有效行 %3d | 错误处理相关行 %3d" % (label, total, err))
    return total, err


legacy_files = ["legacy/net.py", "legacy/store.py", "legacy/cache.py",
                "legacy/service.py"]
new_files = ["refactored/errors.py", "refactored/net.py",
             "refactored/store.py", "refactored/cache.py",
             "refactored/service.py"]
compat_files = ["refactored/compat.py"]

old_total, old_err = report("重构前", legacy_files)
new_total, new_err = report("重构后", new_files)
compat_total, compat_err = report("兼容层", compat_files)
print("-" * 48)
print("错误处理行数: %d -> %d (含兼容层 %d)" %
      (old_err, new_err, new_err + compat_err))
