#!/usr/bin/env python3
"""大规模产物性能基准：生成 N 个产物，测量全量校验耗时与吞吐。"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_check as rc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=20, help="产物数量")
    parser.add_argument("--size-mb", type=int, default=50, help="单个产物大小 (MiB)")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="relcheck-perf-")
    try:
        chunk = os.urandom(1024 * 1024)
        artifacts = []
        gen_start = time.monotonic()
        for idx in range(args.files):
            name = "artifact-%03d.bin" % idx
            digest = hashlib.sha256()
            with open(os.path.join(tmp, name), "wb") as fh:
                for _ in range(args.size_mb):
                    fh.write(chunk)
                    digest.update(chunk)
            artifacts.append({"path": name, "sha256": digest.hexdigest()})
        gen_elapsed = time.monotonic() - gen_start

        with open(os.path.join(tmp, "manifest.json"), "w") as fh:
            json.dump({"artifacts": artifacts}, fh)

        checks = [
            {"id": "hash-%03d" % i, "type": "checksum", "path": a["path"],
             "algorithm": "sha256", "expected": a["sha256"],
             "severity": "critical", "fix": "重新打包"}
            for i, a in enumerate(artifacts)
        ]
        checks.append({"id": "consistency", "type": "artifact_consistency",
                       "manifest": "manifest.json", "severity": "critical",
                       "fix": "重新生成 manifest"})
        config = {"base_dir": tmp, "checks": checks}
        rc.validate_config(config)

        failures, total, elapsed = rc.run_all(config)
        total_mb = args.files * args.size_mb
        # 每个文件被 hash 两次（checksum 项 + consistency 项）
        hashed_mb = total_mb * 2
        print("产物规模:   %d 个文件 x %d MiB = %d MiB" % (args.files, args.size_mb, total_mb))
        print("检查项数:   %d" % total)
        print("生成耗时:   %.2fs" % gen_elapsed)
        print("检查耗时:   %.3fs（实际哈希数据量 %d MiB）" % (elapsed, hashed_mb))
        print("吞吐:       %.1f MiB/s" % (hashed_mb / elapsed if elapsed else 0))
        print("结果:       %s" % ("全部通过" if not failures else "%d 项失败" % len(failures)))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
