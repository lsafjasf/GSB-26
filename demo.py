"""演示：构造三副本故障场景，输出差异报告 -> 修复 -> 复验。"""

from replica_sync import (
    ReplicaStore, compare_replicas, format_report,
    repair_replicas, format_repair_report,
)


def main():
    chunks = {i: f"chunk-{i}-payload".encode() * 32 for i in range(8)}
    names = ["node-a", "node-b", "node-c"]
    replicas = [ReplicaStore(n) for n in names]
    for r in replicas:
        for idx, data in chunks.items():
            r.write_chunk(idx, data)

    # 故障注入：node-b 缺块 #1、位腐烂块 #3、内容落后块 #5
    replicas[1].delete_chunk(1)
    replicas[1].corrupt_chunk(3, b"bit-rotted-data")
    replicas[1].write_chunk(5, b"stale-old-version")

    print("【第一步】比对")
    report = compare_replicas(replicas)
    print(format_report(report))

    print("\n【第二步】按多数派修复")
    repair = repair_replicas(replicas, report)
    print(format_repair_report(repair))

    print("\n【第三步】修复后复验")
    print(format_report(compare_replicas(replicas)))

    print("\n【第四步】幂等性：再次修复")
    before = sum(r.write_count for r in replicas)
    repair2 = repair_replicas(replicas)
    after = sum(r.write_count for r in replicas)
    print(format_repair_report(repair2))
    print(f"第二次修复新增写入次数: {after - before}（应为 0）")


if __name__ == "__main__":
    main()
