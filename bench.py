"""Benchmark: restore time vs chain length and data volume.

Run:  python3 bench.py
"""
import random
import shutil
import tempfile
from pathlib import Path

from snapshotlib import SnapshotStore


def write_tree(root: Path, state: dict) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, data in state.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def build_chain(store, src, chain_len, num_files, file_size, rng):
    state = {f"dir{i % 16}/file{i:05d}.bin": rng.randbytes(file_size)
             for i in range(num_files)}
    write_tree(src, state)
    ids = [store.create_full(src)]
    for _ in range(1, chain_len):
        keys = rng.sample(sorted(state), k=max(1, num_files // 50))  # ~2% churn
        for rel in keys:
            state[rel] = rng.randbytes(file_size)
        for j in range(max(1, num_files // 100)):                    # ~1% adds
            state[f"dir{rng.randint(0,15)}/new{rng.randrange(10**9)}.bin"] = \
                rng.randbytes(file_size)
        write_tree(src, state)
        ids.append(store.create_incremental(src, ids[-1]))
    return ids, state


def timed_restore(store, sid, dest):
    stats = store.restore(sid, dest)
    shutil.rmtree(dest)
    return stats


def main():
    rng = random.Random(7)
    tmp = Path(tempfile.mkdtemp(prefix="snapbench-"))
    try:
        print("== restore time vs chain length (200 files x 16 KiB, ~2% churn/inc) ==")
        print(f"{'chain_len':>9} | {'files':>6} | {'data MiB':>8} | "
              f"{'restore s':>9} | {'MiB/s':>7}")
        for chain_len in (1, 5, 10, 20, 40, 80):
            store = SnapshotStore(tmp / f"s{chain_len}")
            src = tmp / f"src{chain_len}"
            ids, state = build_chain(store, src, chain_len, 200, 16 * 1024, rng)
            st = timed_restore(store, ids[-1], tmp / "out")
            mib = st["bytes"] / 2**20
            print(f"{st['chain_length']:>9} | {st['files']:>6} | {mib:>8.2f} | "
                  f"{st['seconds']:>9.3f} | {mib / st['seconds']:>7.1f}")

        print()
        print("== restore time vs data volume (chain length fixed at 20) ==")
        print(f"{'files':>6} | {'file KiB':>8} | {'data MiB':>8} | "
              f"{'restore s':>9} | {'MiB/s':>7}")
        for num_files, file_size in ((50, 16 * 1024), (200, 16 * 1024),
                                     (800, 16 * 1024), (200, 256 * 1024)):
            store = SnapshotStore(tmp / f"v{num_files}x{file_size}")
            src = tmp / f"vsrc{num_files}x{file_size}"
            ids, state = build_chain(store, src, 20, num_files, file_size, rng)
            st = timed_restore(store, ids[-1], tmp / "out")
            mib = st["bytes"] / 2**20
            print(f"{st['files']:>6} | {file_size // 1024:>8} | {mib:>8.2f} | "
                  f"{st['seconds']:>9.3f} | {mib / st['seconds']:>7.1f}")

        print()
        print("== restore from arbitrary chain positions (chain length 40) ==")
        print(f"{'position':>8} | {'chain_len':>9} | {'restore s':>9}")
        store = SnapshotStore(tmp / "pos")
        src = tmp / "possrc"
        ids, state = build_chain(store, src, 40, 200, 16 * 1024, rng)
        for pos in (0, 9, 19, 29, 39):
            st = timed_restore(store, ids[pos], tmp / "out")
            print(f"{pos:>8} | {st['chain_length']:>9} | {st['seconds']:>9.3f}")
    finally:
        shutil.rmtree(tmp, True)


if __name__ == "__main__":
    main()
