"""Per-stage real-SIGKILL recovery matrix.

For every enumerated commit stage (daemon_state.COMMIT_STAGES) this harness:

  1. seeds a complete OLD state (seq=1) via a real child process;
  2. starts a SECOND real child committing a structurally distinct NEW state
     (seq=2), rendezvous-blocking at each commit stage;
  3. delivers a genuine SIGKILL from the parent at exactly the target stage;
  4. audits the raw bytes left on disk (main / .bak / .tmp);
  5. runs the recovery pipeline twice in fresh child processes and asserts:
       - recovered state is exactly OLD (kill before "renamed") or exactly
         NEW (kill at/after "renamed") -- a content digest comparison, not
         just a counter;
       - invariants: counter == len(items), dense contiguous item sequence,
         no duplicate/completed-pending crossing;
       - recovery phase order locate -> ... -> ready;
       - the two recovery rounds are byte-identical (idempotent recovery).

Run:  python3 kill_matrix.py            # prints the matrix, exits nonzero on fail
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import daemon_state as ds

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "daemon_state.py")

OLD_PAYLOAD = {
    "counter": 1,
    "items": [1],
    "pending_cleanups": [{"id": "p-old", "path": "/tmp/old.tmp"}],
    "completed_cleanups": ["c-old"],
}
NEW_PAYLOAD = {
    "counter": 2,
    "items": [1, 2],
    "pending_cleanups": [{"id": "p-new", "path": "/tmp/new.tmp"}],
    "completed_cleanups": ["c-new"],
}

COMMIT_POINT = "renamed"
STAGE_MEANING = {
    "serialize": "record serialized (nothing on disk yet)",
    "tmp_written": "tmp written, not fsynced",
    "tmp_fsynced": "tmp durable, main untouched",
    "bak_rotated": "old main rotated to .bak, main still old",
    "renamed": "atomic rename done -> NEW committed",
    "dir_fsynced": "directory fsynced, NEW durable",
}
WAIT_SECONDS = 30.0


def payload_digest(payload):
    return hashlib.sha256(ds._canonical(payload).encode("utf-8")).hexdigest()


OLD_DIGEST = payload_digest(OLD_PAYLOAD)
NEW_DIGEST = payload_digest(NEW_PAYLOAD)


def _run(args, env=None, timeout=WAIT_SECONDS):
    proc = subprocess.Popen(
        args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _atomic_write(path, text):
    tmp = path + ".write"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _audit_file(path):
    """Classify a raw state file: -- / OLD / NEW / OTHER-valid / INVALID."""
    if not os.path.exists(path):
        return "--"
    try:
        with open(path, "rb") as fh:
            record = ds._decode(fh.read().decode("utf-8"))
    except Exception:
        return "INVALID"
    digest = payload_digest(record["payload"])
    if digest == OLD_DIGEST:
        return "OLD"
    if digest == NEW_DIGEST:
        return "NEW"
    return "OTHER"


def seed_old_state(directory):
    payload_file = os.path.join(directory, "seed.json")
    with open(payload_file, "w", encoding="utf-8") as fh:
        json.dump(OLD_PAYLOAD, fh)
    state_path = os.path.join(directory, "state.json")
    rc, out, err = _run([sys.executable, MODULE, "commit-file",
                         state_path, payload_file])
    assert rc == 0, "seed commit failed rc=%s: %s" % (rc, err)
    assert _audit_file(state_path) == "OLD"
    assert not os.path.exists(state_path + ".tmp")


def kill_child_at_stage(directory, stage):
    """Start a real committer child and SIGKILL it at exactly `stage`.

    Returns (returncode, observed_stage).
    """
    state_path = os.path.join(directory, "state.json")
    payload_file = os.path.join(directory, "new.json")
    with open(payload_file, "w", encoding="utf-8") as fh:
        json.dump(NEW_PAYLOAD, fh)

    sync_dir = os.path.join(directory, "sync")
    os.makedirs(sync_dir, exist_ok=True)
    stage_file = os.path.join(sync_dir, "stage")
    gate = os.path.join(sync_dir, "gate")
    if os.path.exists(stage_file):
        os.remove(stage_file)
    _atomic_write(gate, "0")

    env = dict(os.environ, GSB_SYNC_DIR=sync_dir)
    proc = subprocess.Popen(
        [sys.executable, MODULE, "commit-file", state_path, payload_file],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target_index = ds.COMMIT_STAGES.index(stage)
    observed = None
    deadline = time.time() + WAIT_SECONDS
    handled = -1
    try:
        while time.time() < deadline:
            if os.path.exists(stage_file):
                with open(stage_file) as fh:
                    published = fh.read().strip()
                if published in ds.COMMIT_STAGES:  # noqa: SIM102

                    index = ds.COMMIT_STAGES.index(published)
                    if index > handled:
                        observed = published
                        if index == target_index:
                            proc.send_signal(signal.SIGKILL)
                            proc.wait(timeout=WAIT_SECONDS)
                            proc.stdout.close()
                            proc.stderr.close()
                            return proc.returncode, observed
                        if index > target_index:
                            raise RuntimeError(
                                "child passed target %s (now at %s)"
                                % (stage, published))
                        _atomic_write(gate, str(index + 1))
                        handled = index
            if proc.poll() is not None:
                err_text = proc.stderr.read().decode("utf-8", "replace")
                proc.stderr.close()
                proc.stdout.close()
                raise RuntimeError("child exited before %s rc=%s: %s"
                                   % (stage, proc.returncode, err_text))
            time.sleep(0.001)
        raise TimeoutError("child never reached stage %s" % stage)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def run_recovery(directory):
    state_path = os.path.join(directory, "state.json")
    rc, out, err = _run([sys.executable, MODULE, "recover", state_path])
    try:
        verdict = json.loads(out)
    except ValueError:
        verdict = {"ok": False,
                   "error": "non-JSON recovery output rc=%s: %s | %s"
                            % (rc, out, err)}
    verdict["rc"] = rc
    return verdict


def phases_ordered(phases):
    order = [ds.RECOVERY_PHASES.index(p) for p in phases]
    return (order == sorted(order)
            and phases[0] == "locate" and phases[-1] == "ready")


def run_stage(base_dir, stage):
    """One full kill+recover experiment; returns a result row dict."""
    directory = os.path.join(base_dir, "case-%s" % stage)
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    seed_old_state(directory)
    state_path = os.path.join(directory, "state.json")

    failures = []
    rc, observed = kill_child_at_stage(directory, stage)
    if rc != -9:
        failures.append("SIGKILL returncode: expected -9 got %s" % rc)
    if observed != stage:
        failures.append("kill stage: expected %s observed %s"
                        % (stage, observed))

    disk_before = {
        "main": _audit_file(state_path),
        "bak": _audit_file(state_path + ".bak"),
        "tmp": _audit_file(state_path + ".tmp"),
    }

    expect_old = ds.COMMIT_STAGES.index(stage) < ds.COMMIT_STAGES.index(COMMIT_POINT)
    expected_label = "OLD" if expect_old else "NEW"
    expected_digest = OLD_DIGEST if expect_old else NEW_DIGEST
    expected_seq = 1 if expect_old else 2
    expected_counter = OLD_PAYLOAD["counter"] if expect_old else NEW_PAYLOAD["counter"]

    rounds = []
    for round_no in (1, 2):
        rec = run_recovery(directory)
        if not rec.get("ok"):
            failures.append("recovery R%d failed: %s" % (round_no, rec.get("error")))
            rounds.append(rec)
            continue
        state = rec["state"]
        digest = payload_digest(state)
        if rec["seq"] != expected_seq:
            failures.append("R%d seq=%d expected %d"
                            % (round_no, rec["seq"], expected_seq))
        if digest != expected_digest:
            failures.append("R%d digest=%s expected %s (MIXED/WRONG CONTENT)"
                            % (round_no, digest[:12], expected_digest[:12]))
        if state["counter"] != expected_counter:
            failures.append("R%d counter=%s expected %d"
                            % (round_no, state["counter"], expected_counter))
        # Exhaustive invariant checks (check_invariants already ran inside the
        # recovery child; repeat them here for defense in depth).
        try:
            ds.check_invariants(state)
            invariant_ok = True
        except AssertionError as exc:
            invariant_ok = False
            failures.append("R%d invariant: %s" % (round_no, exc))
        rec["_digest"] = digest
        rec["_invariant_ok"] = invariant_ok
        rec["_disk"] = {
            "main": _audit_file(state_path),
            "bak": _audit_file(state_path + ".bak"),
            "tmp": _audit_file(state_path + ".tmp"),
        }
        rounds.append(rec)

    # Phase ordering (from round 1).
    if rounds and rounds[0].get("ok"):
        if not phases_ordered(rounds[0]["phases"]):
            failures.append("recovery phase order broken: %s"
                            % rounds[0]["phases"])

    # Two independent recovery rounds must be identical (idempotent).
    idempotent = False
    if len(rounds) == 2 and all(r.get("ok") for r in rounds):
        idempotent = (
            rounds[0]["seq"] == rounds[1]["seq"]
            and rounds[0]["_digest"] == rounds[1]["_digest"]
            and rounds[0]["_disk"] == rounds[1]["_disk"]
        )
        if not idempotent:
            failures.append("recovery not idempotent across two rounds")

    labels = []
    for rec in rounds:
        if not rec.get("ok"):
            labels.append("FAIL")
        else:
            labels.append("OLD" if rec["_digest"] == OLD_DIGEST
                          else "NEW" if rec["_digest"] == NEW_DIGEST
                          else "MIXED?")

    return {
        "stage": stage,
        "expected": expected_label,
        "returncode": rc,
        "disk_before": disk_before,
        "disk_after": rounds[-1]["_disk"] if rounds and rounds[-1].get("ok") else None,
        "seq": [r.get("seq") for r in rounds],
        "labels": labels,
        "digests": [r.get("_digest", "")[:8] for r in rounds],
        "invariant": all(r.get("_invariant_ok", False) for r in rounds),
        "phases": rounds[0]["phases"] if rounds and rounds[0].get("ok") else [],
        "idempotent": idempotent,
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
    }


def run_matrix(base_dir=None, cleanup=True):
    created = base_dir is None
    if base_dir is None:
        base_dir = tempfile.mkdtemp(prefix="gsb-kill-matrix-")
    os.makedirs(base_dir, exist_ok=True)
    rows = []
    try:
        for stage in ds.COMMIT_STAGES:
            rows.append(run_stage(base_dir, stage))
    finally:
        if created and cleanup:
            shutil.rmtree(base_dir, ignore_errors=True)
    return rows, base_dir


def print_table(rows):
    headers = ["#", "stage", "expect", "SIGKILL",
               "disk after kill (main/bak/tmp)",
               "seq R1/R2", "content R1/R2", "digest R1/R2",
               "invariants", "idempotent", "verdict"]
    table = []
    for index, row in enumerate(rows):
        disk = row["disk_before"]
        table.append([
            str(index),
            row["stage"],
            row["expected"],
            str(row["returncode"]),
            "%s/%s/%s" % (disk["main"], disk["bak"], disk["tmp"]),
            "%s/%s" % tuple(row["seq"]),
            "%s/%s" % tuple(row["labels"]),
            "%s/%s" % tuple(row["digests"]),
            "ok" if row["invariant"] else "BROKEN",
            "ok" if row["idempotent"] else "BROKEN",
            row["verdict"],
        ])
    widths = [max(len(headers[i]), max(len(r[i]) for r in table))
              for i in range(len(headers))]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in table:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


def main():
    keep_dir = sys.argv[1] if len(sys.argv) > 1 else None
    rows, base_dir = run_matrix(base_dir=keep_dir, cleanup=keep_dir is None)
    print("Real-SIGKILL per-stage recovery matrix")
    print("state dir (per-stage cases): %s" % base_dir)
    print("OLD state: counter=%d items=%r pending=%r completed=%r digest=%s"
          % (OLD_PAYLOAD["counter"], OLD_PAYLOAD["items"],
             [j["id"] for j in OLD_PAYLOAD["pending_cleanups"]],
             OLD_PAYLOAD["completed_cleanups"], OLD_DIGEST[:12]))
    print("NEW state: counter=%d items=%r pending=%r completed=%r digest=%s"
          % (NEW_PAYLOAD["counter"], NEW_PAYLOAD["items"],
             [j["id"] for j in NEW_PAYLOAD["pending_cleanups"]],
             NEW_PAYLOAD["completed_cleanups"], NEW_DIGEST[:12]))
    print("boundary rule: stages before '%s' must recover OLD; "
          "'%s' and after must recover NEW" % (COMMIT_POINT, COMMIT_POINT))
    print()
    print_table(rows)
    print()
    failed = [r for r in rows if r["verdict"] != "PASS"]
    if failed:
        for row in failed:
            print("[FAIL] %s:" % row["stage"])
            for detail in row["failures"]:
                print("    - %s" % detail)
        print()
        print("RESULT: %d/%d stages FAILED -- mixed state or wrong recovery"
              % (len(failed), len(rows)))
        return 1
    print("stage semantics:")
    for index, stage in enumerate(ds.COMMIT_STAGES):
        print("  %d %-13s %s" % (index, stage, STAGE_MEANING[stage]))
    print()
    print("RESULT: %d/%d stages PASS -- every kill yields exactly one "
          "complete state (OLD before commit point, NEW after), "
          "never a mixture" % (len(rows), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
