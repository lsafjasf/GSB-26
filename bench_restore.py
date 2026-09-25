"""恢复耗时基准：测量恢复时间与链长度、数据量的关系。

运行: python3 bench_restore.py
输出: Markdown 表格（可直接贴入 README）
"""

import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import snapshotlib as sl


def build_chain(repo, src, num_files, file_size, chain_len, change_ratio=0.02):
    """构造一条链：1 个全量 + (chain_len-1) 个增量，返回所有快照 id。"""
    rng = random.Random(42)
    files = {}
    for i in range(num_files):
        files["dir%03d/f%05d.bin" % (i % 16, i)] = rng.randbytes(file_size)

    def materialize():
        shutil.rmtree(src, ignore_errors=True)
        for rel, data in files.items():
            path = os.path.join(src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)

    snap_ids = []
    materialize()
    snap_ids.append(repo.create_full(src))
    n_change = max(1, int(num_files * change_ratio))
    keys = list(files)
    for _ in range(chain_len - 1):
        for rel in rng.sample(keys, n_change):
            files[rel] = rng.randbytes(file_size)
        materialize()
        snap_ids.append(repo.create_incremental(src))
    return snap_ids


def bench(tmp, num_files, file_size, chain_len, repeat=3):
    src = os.path.join(tmp, "src")
    dst = os.path.join(tmp, "dst")
    repo = sl.BackupRepo(os.path.join(tmp, "repo"))
    snap_ids = build_chain(repo, src, num_files, file_size, chain_len)
    # 从链尾恢复（最长链），取多次最小值
    best = None
    report = None
    for _ in range(repeat):
        report = repo.restore(snap_ids[-1], dst)
        if best is None or report.elapsed_seconds < best:
            best = report.elapsed_seconds
    return best, report


def main():
    print("机器: Python %s, %s" % (sys.version.split()[0], sys.platform))
    print()
    print("### 链长度对恢复耗时的影响（1000 文件 x 1 KiB，总量 ~1 MiB）")
    print()
    print("| 链长度 | 恢复文件数 | 恢复字节数 | 恢复耗时 (s) |")
    print("|---:|---:|---:|---:|")
    tmp = tempfile.mkdtemp(prefix="snapbench-")
    try:
        for chain_len in (1, 10, 50, 100, 200, 400):
            t, rep = bench(tmp, 1000, 1024, chain_len)
            print("| %d | %d | %d | %.4f |"
                  % (chain_len, rep.files, rep.bytes_restored, t))
            for child in os.listdir(tmp):
                p = os.path.join(tmp, child)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

        print()
        print("### 数据量对恢复耗时的影响（链长度固定 50）")
        print()
        print("| 文件数 | 单文件大小 | 链长度 | 恢复字节数 | 恢复耗时 (s) |")
        print("|---:|---:|---:|---:|---:|")
        for num_files, file_size in ((100, 1024), (1000, 1024),
                                     (5000, 1024), (10000, 1024),
                                     (20, 1024 * 1024), (100, 1024 * 1024)):
            t, rep = bench(tmp, num_files, file_size, 50)
            print("| %d | %d B | %d | %d | %.4f |"
                  % (num_files, file_size, 50, rep.bytes_restored, t))
            for child in os.listdir(tmp):
                p = os.path.join(tmp, child)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
