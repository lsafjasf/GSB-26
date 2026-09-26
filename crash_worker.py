"""Child-process driver for kill -9 fault injection.

Usage: python3 crash_worker.py <phase> <dir>
Phases:
  log        - 50 records committed, then SIGKILL mid-write of record 51
               (partial bytes written, no fsync) -> torn tail.
  checkpoint - checkpoint #1 committed (50 records), 50 more logged, then
               SIGKILL after checkpoint.tmp is written but before rename.
  rotate     - SIGKILL at the first segment rotation; the number of
               committed records is persisted to expected.txt first.
"""

import os
import signal
import sys

from wal import KVStore


def kill():
    os.kill(os.getpid(), signal.SIGKILL)


def main():
    phase, directory = sys.argv[1], sys.argv[2]

    if phase == "log":
        writes = {"n": 0}

        def hook(point, **ctx):
            if point == "log_write":
                writes["n"] += 1
                if writes["n"] == 51:
                    # Torn write: only part of the record hits the file.
                    os.write(ctx["fd"], ctx["data"][:13])
                    kill()

        store = KVStore.open(directory, fsync=True, fault_hook=hook)
        for i in range(100):
            store.set("k%d" % i, i)

    elif phase == "checkpoint":
        cps = {"n": 0}

        def hook(point, **ctx):
            if point == "checkpoint_tmp_written":
                cps["n"] += 1
                if cps["n"] == 2:
                    kill()  # die after tmp write, before atomic rename

        store = KVStore.open(directory, fsync=True, fault_hook=hook)
        for i in range(50):
            store.set("k%d" % i, i)
        store.checkpoint()  # committed: lsn 50
        for i in range(50, 100):
            store.set("k%d" % i, i)
        store.checkpoint()  # dies here, mid-checkpoint

    elif phase == "rotate":
        writes = {"n": 0}

        def hook(point, **ctx):
            if point == "log_write":
                writes["n"] += 1
            elif point == "rotate":
                # Persist how many records were committed before dying.
                path = os.path.join(directory, "expected.txt")
                fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
                os.write(fd, str(writes["n"]).encode())
                os.fsync(fd)
                os.close(fd)
                kill()

        store = KVStore.open(directory, max_segment_bytes=256,
                             fsync=True, fault_hook=hook)
        for i in range(500):
            store.set("k%d" % i, i)

    else:
        raise SystemExit("unknown phase: %s" % phase)


if __name__ == "__main__":
    main()
