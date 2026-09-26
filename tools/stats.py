"""统计重构前后的代码行数与埋点数量（运行: python3 tools/stats.py）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def count(path, markers):
    lines = path.read_text(encoding="utf-8").splitlines()
    code = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    hits = [l for l in code if any(m in l for m in markers)]
    return len(lines), len(code), len(hits)


def main():
    legacy = ROOT / "legacy" / "order_service.py"
    new = ROOT / "structured" / "order_service.py"
    schema = ROOT / "structured" / "fields.py"

    l_total, l_code, l_logs = count(legacy, ["print("])
    n_total, n_code, n_logs = count(new, ["emit("])
    s_total, s_code, _ = count(schema, [])

    print(f"{'':14}{'总行数':>8}{'代码行':>8}{'埋点行数':>8}{'埋点数量':>8}")
    print(f"{'重构前':14}{l_total:>8}{l_code:>8}{l_logs:>8}{l_logs:>8}  (legacy/order_service.py)")
    print(f"{'重构后':14}{n_total:>8}{n_code:>8}{n_logs:>8}{n_logs:>8}  (structured/order_service.py)")
    print(f"{'事件声明':14}{s_total:>8}{s_code:>8}{'-':>8}{'-':>8}  (structured/fields.py，一次声明处处复用)")


if __name__ == "__main__":
    main()
