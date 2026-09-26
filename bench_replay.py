"""Replay benchmark. Run: python3 bench_replay.py [num_records]"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wal_kv import WALKV


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    d = tempfile.mkdtemp(prefix="walbench-")
    try:
        t0 = time.perf_counter()
        s = WALKV(d, max_segment_bytes=8 << 20, fsync=False)
        for i in range(n):
            s.set("key%08d" % i, "value-%d" % i)
        s.close()
        t1 = time.perf_counter()

        s = WALKV(d)  # full replay, no checkpoint
        t2 = time.perf_counter()
        assert len(s) == n, (len(s), n)
        s.close()

        s = WALKV(d)
        s.checkpoint()
        tail = 10_000
        for i in range(tail):
            s.set("tail%05d" % i, "v%d" % i)
        s.close()

        t3 = time.perf_counter()
        s = WALKV(d)  # checkpoint load + replay of 10k-record tail only
        t4 = time.perf_counter()
        assert len(s) == n + tail
        s.close()

        load_s, replay_s, cp_s = t1 - t0, t2 - t1, t4 - t3
        print("records            : %d" % n)
        print("load (no fsync)    : %.3fs  -> %.0f rec/s" % (load_s, n / load_s))
        print("full replay        : %.3fs  -> %.0f rec/s" % (replay_s, n / replay_s))
        print("checkpoint+10k tail: %.3fs  (vs %.3fs full replay, %.1fx faster)"
              % (cp_s, replay_s, replay_s / cp_s))
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
