"""Self-tests for snapshotlib: chain validation + restore differential tests.

Run:  python3 test_snapshotlib.py -v
"""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import snapshotlib as sl


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def write_tree(root: Path, state: dict) -> None:
    """Make *root* contain exactly the files in state (path -> bytes)."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, data in state.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def read_tree(root: Path) -> dict:
    state = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            state[p.relative_to(root).as_posix()] = p.read_bytes()
    return state


def mutate(state: dict, rng: random.Random, heavy: bool = False) -> None:
    """Randomly add/modify/delete files in *state* (in place)."""
    n_add = rng.randint(5, 15) if heavy else rng.randint(1, 6)
    for _ in range(n_add):
        rel = f"dir{rng.randint(0, 4)}/f{rng.randrange(10**6):06d}.bin"
        state[rel] = rng.randbytes(rng.randint(0, 2048))
    keys = list(state)
    rng.shuffle(keys)
    n_mod = min(len(keys), rng.randint(0, 5))
    for rel in keys[:n_mod]:
        state[rel] = rng.randbytes(rng.randint(1, 1024))
    n_del = min(len(keys) - n_mod, rng.randint(0, 4))
    for rel in keys[n_mod:n_mod + n_del]:
        del state[rel]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="snaptest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = sl.SnapshotStore(self.tmp / "store")
        self.src = self.tmp / "src"
        self.src.mkdir()

    def snap_record_path(self, sid) -> Path:
        return self.tmp / "store" / "snapshots" / (sid + ".json")

    def rewrite_record(self, sid, **changes) -> None:
        """Apply changes to a record and fix its checksum (simulates a
        well-formed but inconsistent/tampered-with record)."""
        path = self.snap_record_path(sid)
        rec = json.loads(path.read_text("utf-8"))
        rec.update(changes)
        rec["checksum"] = sl._record_checksum(rec)
        path.write_text(json.dumps(rec, indent=2, sort_keys=True), "utf-8")

    def build_chain(self, lengths=5):
        """Build full + (lengths-1) incrementals; return (ids, states)."""
        rng = random.Random(1234)
        state, ids, states = {}, [], []
        mutate(state, rng, heavy=True)
        write_tree(self.src, state)
        ids.append(self.store.create_full(self.src, "s0"))
        states.append(dict(state))
        for i in range(1, lengths):
            mutate(state, rng)
            write_tree(self.src, state)
            ids.append(self.store.create_incremental(self.src, ids[-1], f"s{i}"))
            states.append(dict(state))
        return ids, states


# ---------------------------------------------------------------------------
# happy-path / differential tests
# ---------------------------------------------------------------------------
class TestHappyPath(Base):
    def test_empty_snapshot(self):
        write_tree(self.src, {})
        sid = self.store.create_full(self.src, "empty")
        dest = self.tmp / "out"
        stats = self.store.restore(sid, dest)
        self.assertEqual(read_tree(dest), {})
        self.assertEqual(stats["files"], 0)
        # incremental on top of empty, still empty
        sid2 = self.store.create_incremental(self.src, sid, "empty2")
        self.assertEqual(read_tree(self.tmp / "out2") if False else
                         (self.store.restore(sid2, self.tmp / "out2"),
                          read_tree(self.tmp / "out2"))[1], {})

    def test_single_full(self):
        state = {"a.txt": b"hello", "sub/b.bin": b"\x00\x01" * 100}
        write_tree(self.src, state)
        sid = self.store.create_full(self.src, "full1")
        dest = self.tmp / "out"
        self.store.restore(sid, dest)
        self.assertEqual(read_tree(dest), state)

    def test_long_chain(self):
        rng = random.Random(99)
        state = {f"f{i}.bin": rng.randbytes(64) for i in range(10)}
        write_tree(self.src, state)
        ids, states = [self.store.create_full(self.src, "c0")], [dict(state)]
        for i in range(1, 50):  # 1 full + 49 incrementals
            mutate(state, rng)
            write_tree(self.src, state)
            ids.append(self.store.create_incremental(self.src, ids[-1], f"c{i}"))
            states.append(dict(state))
        for idx in (0, 1, 25, 48, 49):  # arbitrary positions incl. tip
            dest = self.tmp / f"out{idx}"
            stats = self.store.restore(ids[idx], dest)
            self.assertEqual(read_tree(dest), states[idx])
            self.assertEqual(stats["chain_length"], idx + 1)

    def test_incremental_delete(self):
        state = {"keep.txt": b"k", "gone.txt": b"g", "dir/gone2.txt": b"g2"}
        write_tree(self.src, state)
        s0 = self.store.create_full(self.src, "d0")
        del state["gone.txt"], state["dir/gone2.txt"]
        state["new.txt"] = b"n"
        write_tree(self.src, state)
        s1 = self.store.create_incremental(self.src, s0, "d1")
        # tip reflects deletion
        dest = self.tmp / "out1"
        self.store.restore(s1, dest)
        self.assertEqual(read_tree(dest), state)
        self.assertFalse((dest / "gone.txt").exists())
        # earlier snapshot still restores deleted files
        dest0 = self.tmp / "out0"
        self.store.restore(s0, dest0)
        self.assertEqual(read_tree(dest0),
                         {"keep.txt": b"k", "gone.txt": b"g",
                          "dir/gone2.txt": b"g2"})

    def test_differential_random(self):
        """对拍: random tree + random mutations; every snapshot restored and
        compared file-by-file against the state captured at backup time."""
        rng = random.Random(20260926)
        state, ids, states = {}, [], []
        mutate(state, rng, heavy=True)
        write_tree(self.src, state)
        ids.append(self.store.create_full(self.src, "r0"))
        states.append(dict(state))
        for i in range(1, 20):
            mutate(state, rng)
            write_tree(self.src, state)
            ids.append(self.store.create_incremental(self.src, ids[-1], f"r{i}"))
            states.append(dict(state))
        for sid, expected in zip(ids, states):
            dest = self.tmp / f"diff-{sid}"
            self.store.restore(sid, dest)
            self.assertEqual(read_tree(dest), expected, f"mismatch at {sid}")

    def test_restore_idempotent_and_self_cleaning(self):
        ids, states = self.build_chain(6)
        dest = self.tmp / "idem"
        self.store.restore(ids[-1], dest)
        first = read_tree(dest)
        self.store.restore(ids[-1], dest)  # repeat: must not change result
        self.assertEqual(read_tree(dest), first)
        self.assertEqual(first, states[-1])
        # dirty destination gets synchronised to the snapshot state
        (dest / "junk.txt").write_bytes(b"junk")
        (dest / "junkdir").mkdir()
        (dest / "junkdir" / "x").write_bytes(b"x")
        self.store.restore(ids[-1], dest)
        self.assertEqual(read_tree(dest), states[-1])
        self.assertFalse((dest / "junkdir").exists())


# ---------------------------------------------------------------------------
# failure-path tests: distinct errors with locateable break points
# ---------------------------------------------------------------------------
class TestChainValidation(Base):
    def test_missing_parent(self):
        ids, _ = self.build_chain(6)
        self.snap_record_path("s3").unlink()  # remove a middle snapshot
        with self.assertRaises(sl.SnapshotNotFoundError) as ctx:
            self.store.restore("s5", self.tmp / "out")
        self.assertEqual(ctx.exception.snapshot_id, "s3")
        self.assertIn("s4", str(ctx.exception))  # names the dependent child
        # chain below the break is unaffected
        self.store.restore("s2", self.tmp / "out2")

    def test_missing_target(self):
        with self.assertRaises(sl.SnapshotNotFoundError) as ctx:
            self.store.validate_chain("nope")
        self.assertEqual(ctx.exception.snapshot_id, "nope")

    def test_tampered_snapshot_record(self):
        ids, _ = self.build_chain(4)
        path = self.snap_record_path("s2")
        text = path.read_text("utf-8")
        # flip one hex digit inside the record without fixing the checksum
        i = text.index('"sha256"')
        j = text.index("0", i) if "0" in text[i:i + 90] else text.index("a", i)
        path.write_text(text[:j] + ("1" if text[j] != "1" else "2") + text[j + 1:],
                        "utf-8")
        with self.assertRaises(sl.SnapshotCorruptError) as ctx:
            self.store.restore("s3", self.tmp / "out")
        self.assertEqual(ctx.exception.snapshot_id, "s2")
        # nothing was written
        self.assertFalse((self.tmp / "out").exists()
                         and any((self.tmp / "out").iterdir()))

    def test_version_mismatch(self):
        ids, _ = self.build_chain(3)
        self.rewrite_record("s1", format_version=999)
        with self.assertRaises(sl.VersionMismatchError) as ctx:
            self.store.restore("s2", self.tmp / "out")
        self.assertEqual(ctx.exception.snapshot_id, "s1")

    def test_chain_broken_topology(self):
        ids, _ = self.build_chain(4)
        # incremental that suddenly claims no parent
        self.rewrite_record("s2", parent=None)
        with self.assertRaises(sl.ChainBrokenError) as ctx:
            self.store.validate_chain("s3")
        self.assertEqual(ctx.exception.snapshot_id, "s2")
        # full snapshot that declares a parent
        self.rewrite_record("s0", parent="ghost")
        with self.assertRaises(sl.ChainBrokenError) as ctx:
            self.store.validate_chain("s1")
        self.assertEqual(ctx.exception.snapshot_id, "s0")

    def test_tampered_object(self):
        ids, states = self.build_chain(3)
        rel = sorted(states[-1])[0]
        digest = states[-1][rel] and self.store.effective_state("s2")[rel]["sha256"]
        obj = self.tmp / "store" / "objects" / digest[:2] / digest
        obj.write_bytes(b"corrupted!")
        with self.assertRaises(sl.ObjectCorruptError) as ctx:
            self.store.restore("s2", self.tmp / "out")
        self.assertIsNotNone(ctx.exception.snapshot_id)
        self.assertIn(rel, str(ctx.exception))

    def test_missing_object(self):
        ids, _ = self.build_chain(3)
        digest = self.store.effective_state("s2")[sorted(
            self.store.effective_state("s2"))[0]]["sha256"]
        (self.tmp / "store" / "objects" / digest[:2] / digest).unlink()
        with self.assertRaises(sl.ObjectCorruptError):
            self.store.validate_chain("s2")

    def test_no_partial_output_on_failure(self):
        ids, states = self.build_chain(5)
        self.snap_record_path("s3").unlink()
        dest = self.tmp / "partial"
        with self.assertRaises(sl.SnapshotNotFoundError):
            self.store.restore("s4", dest)
        # restore must fail before writing anything
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
