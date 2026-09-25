"""构造用例集：逐个构建并打印检出结果（python3 cases_demo.py）。"""

from sigchain import Link, issue, verify

T0 = 1_760_000_000
DAY = 86400
KEYS = {"root": b"root-secret-key", "ca": b"ca-secret-key", "svc": b"svc-secret-key"}
ANCHORS = {"root"}


def good_chain3():
    return [
        issue(KEYS, "root", "root", {"read", "write", "admin"}, T0 - DAY, T0 + DAY),
        issue(KEYS, "root", "ca", {"read", "write"}, T0 - DAY, T0 + DAY),
        issue(KEYS, "ca", "svc", {"read"}, T0 - DAY, T0 + DAY),
    ]


def build_cases():
    cases = []

    cases.append(("单级合法链（根自签）",
                  [issue(KEYS, "root", "root", {"read"}, T0 - DAY, T0 + DAY)],
                  dict(required_purposes={"read"})))

    cases.append(("三级合法链，用途逐级收紧",
                  good_chain3(), dict(required_purposes={"read"})))

    c = good_chain3(); c[1], c[2] = c[2], c[1]
    cases.append(("顺序错乱：中间级与末端互换", c, {}))

    c = good_chain3(); c.insert(2, c[1])
    cases.append(("重复级别：中间级出现两次", c, {}))

    c = good_chain3(); mid = c[1]
    c[1] = Link(mid.issuer, mid.subject, frozenset({"read", "write", "admin"}),
                mid.not_before, mid.not_after, mid.signature)
    cases.append(("被篡改的中间级：私自扩权但未重签", c, {}))

    c = good_chain3()
    c[1] = issue(KEYS, "root", "ca", {"read", "write"}, T0 - 10 * DAY, T0 - DAY)
    cases.append(("中间级已过期", c, {}))

    c = good_chain3()
    c[2] = issue(KEYS, "ca", "svc", {"read"}, T0 + DAY, T0 + 2 * DAY)
    cases.append(("末端级尚未生效", c, {}))

    c = good_chain3()
    c[2] = issue(KEYS, "ca", "svc", {"read", "admin"}, T0 - DAY, T0 + DAY)
    cases.append(("用途越权：末端申请上级未授予的 admin", c, {}))

    cases.append(("深度超限：3 级链但 max_depth=2",
                  good_chain3(), dict(max_depth=2)))

    cases.append(("末端用途不足：要求 write 但末端只有 read",
                  good_chain3(), dict(required_purposes={"read", "write"})))

    return cases


def main():
    for name, chain, kwargs in build_cases():
        print("=" * 72)
        print(f"用例: {name}")
        report = verify(chain, KEYS, ANCHORS, T0, **kwargs)
        print(report.render())
    print("=" * 72)


if __name__ == "__main__":
    main()
