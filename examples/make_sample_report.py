"""生成 examples/diff_report_sample.json 与 repair_report_sample.json。

场景：3 份副本、每块 64 字节、共 6 块——
- 块 1：replica-c 内容损坏（不一致块，无清单）
- 块 2：replica-b 静默损坏，清单记录正确哈希（校验失败块）
- 块 3：replica-b/c 各执一词且 b 弃权，无法形成多数派（分歧块）
- 块 4、5：replica-c 缺失（缺失块）
仅 replica-b 带清单。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reheal

CHUNK = 64
HERE = os.path.dirname(os.path.abspath(__file__))


def chunk(seed: int) -> bytes:
    return bytes((seed * 31 + i) % 256 for i in range(CHUNK))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="reheal-sample-")
    try:
        good = [chunk(i) for i in range(6)]
        b = list(good)
        b[2] = chunk(902)              # 静默损坏（清单仍是正确哈希）
        b[3] = chunk(903)              # 与 c 分歧
        c = list(good)
        c[1] = chunk(901)              # 普通损坏（不一致块）
        c[3] = chunk(904)              # 与 b 分歧
        c = c[:4]                      # 缺失块 4、5

        paths = []
        for name, chunks in (("replica-a.bin", good), ("replica-b.bin", b), ("replica-c.bin", c)):
            p = os.path.join(tmp, name)
            with open(p, "wb") as fh:
                fh.write(b"".join(chunks))
            paths.append(p)

        # 仅 replica-b 带清单，按正确内容生成
        reheal.save_manifest(paths[1], {i: reheal._sha256(ch) for i, ch in enumerate(good)})

        report = reheal.scan_replicas(paths, CHUNK)
        for diff in report.replicas:  # 报告里用相对文件名，便于阅读
            diff.path = os.path.basename(diff.path)
        with open(os.path.join(HERE, "diff_report_sample.json"), "w", encoding="utf-8") as fh:
            fh.write(report.to_json() + "\n")

        repair = reheal.repair_replicas(paths, CHUNK)
        repair_json = repair.to_dict()
        for key in ("applied", "truncated", "manifests_updated", "skipped_concurrent"):
            repair_json[key] = {os.path.basename(k): v for k, v in repair_json[key].items()}
        with open(os.path.join(HERE, "repair_report_sample.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(repair_json, indent=2, ensure_ascii=False) + "\n")

        print(report.to_json())
        print("---")
        print(json.dumps(repair_json, indent=2, ensure_ascii=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
