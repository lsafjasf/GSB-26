"""Self-tests for the event_sourcing library.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
import copy
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_sourcing import (
    AggregateViewProjection,
    DetailViewProjection,
    Event,
    EventStore,
    IncompatibleEventVersionError,
    ProjectionKilled,
    Replayer,
    UnknownEventTypeError,
    new_event_id,
    view_inconsistencies,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def build_events(num_accounts, ops_per_account, seed=0):
    """Generate a valid event stream. ~30% of money events use the legacy
    v1 payload so upcasting is exercised in every scenario."""
    rng = random.Random(seed)
    events = []
    for a in range(num_accounts):
        sid = f"acct-{a:03d}"
        seq = 0
        events.append(Event(new_event_id(), sid, seq, "AccountOpened", 1,
                            {"owner": f"user-{a}"}))
        seq += 1
        balance = 0
        for _ in range(ops_per_account):
            if balance == 0 or rng.random() < 0.55:
                cents = rng.randint(1, 50000)
                etype = "Deposited"
                balance += cents
            else:
                cents = rng.randint(1, balance)
                etype = "Withdrawn"
                balance -= cents
            if rng.random() < 0.3:
                data, version = {"amount": cents / 100.0}, 1   # legacy v1
            else:
                data, version = {"amount_cents": cents}, 2
            events.append(Event(new_event_id(), sid, seq, etype, version, data))
            seq += 1
    return events


def expected_model(events):
    """Independent in-memory model: balances computed straight from the
    event list (canonical order), used as ground truth for the projections."""
    balances = {}
    for e in sorted(events, key=lambda x: (x.stream_id, x.seq)):
        if e.type == "AccountOpened":
            balances[e.stream_id] = 0
        else:
            cents = e.data["amount_cents"] if e.version >= 2 \
                else round(e.data["amount"] * 100)
            balances[e.stream_id] += cents if e.type == "Deposited" else -cents
    return balances


def replay_to_completion(store, checkpoint_dir, tag=""):
    """Full replay of both projections; returns (detail, aggregate)."""
    results = []
    for cls in (DetailViewProjection, AggregateViewProjection):
        ckpt = os.path.join(checkpoint_dir, f"{cls.name}{tag}.json")
        results.append(Replayer(store, cls(), ckpt).run())
    return results[0], results[1]


class EventSourcingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def make_store(self, events, shuffle_seed=None, name="log.jsonl"):
        store = EventStore(os.path.join(self.dir, name))
        ordered = list(events)
        if shuffle_seed is not None:
            random.Random(shuffle_seed).shuffle(ordered)
        for e in ordered:
            store.append(e)
        return store

    def assert_views_consistent(self, detail, aggregate):
        problems = view_inconsistencies(detail.state, aggregate.state)
        self.assertEqual(problems, [], "projection cross-check failed")

    def assert_matches_model(self, events, aggregate):
        expected = expected_model(events)
        actual = {sid: acc["balance_cents"]
                  for sid, acc in aggregate.state["accounts"].items()}
        self.assertEqual(expected, actual)


# --------------------------------------------------------------------------
# Scenario: empty stream / single event
# --------------------------------------------------------------------------

class TestEmptyAndSingle(EventSourcingTestCase):
    def test_empty_stream(self):
        store = self.make_store([])
        detail, aggregate = replay_to_completion(store, self.dir)
        self.assertEqual(detail.state, {"accounts": {}})
        self.assertEqual(aggregate.state, {"accounts": {}})
        self.assertEqual(detail.processed_count, 0)
        self.assertEqual(aggregate.processed_count, 0)
        self.assert_views_consistent(detail, aggregate)

    def test_single_event(self):
        events = [Event(new_event_id(), "acct-0", 0, "AccountOpened", 1,
                        {"owner": "ada"})]
        store = self.make_store(events)
        detail, aggregate = replay_to_completion(store, self.dir)
        self.assertEqual(detail.state["accounts"]["acct-0"],
                         {"owner": "ada", "txs": []})
        self.assertEqual(aggregate.state["accounts"]["acct-0"]["balance_cents"], 0)
        self.assertEqual(detail.processed_count, 1)
        self.assert_views_consistent(detail, aggregate)
        self.assert_matches_model(events, aggregate)


# --------------------------------------------------------------------------
# Scenario: many events + cross-projection consistency
# --------------------------------------------------------------------------

class TestManyEvents(EventSourcingTestCase):
    def test_many_events(self):
        events = build_events(num_accounts=5, ops_per_account=300, seed=42)
        self.assertGreater(len(events), 1000)
        store = self.make_store(events, shuffle_seed=1)
        detail, aggregate = replay_to_completion(store, self.dir)
        self.assertEqual(detail.processed_count, len(events))
        self.assertEqual(aggregate.processed_count, len(events))
        self.assert_views_consistent(detail, aggregate)
        self.assert_matches_model(events, aggregate)


# --------------------------------------------------------------------------
# Scenario: out-of-order arrival must not change the result
# --------------------------------------------------------------------------

class TestOutOfOrder(EventSourcingTestCase):
    def test_shuffled_arrival_matches_in_order(self):
        events = build_events(num_accounts=4, ops_per_account=50, seed=11)

        in_order_store = self.make_store(events, name="in_order.jsonl")
        d1, a1 = replay_to_completion(in_order_store, self.dir, tag="-in")

        shuffled_store = self.make_store(events, shuffle_seed=777,
                                         name="shuffled.jsonl")
        d2, a2 = replay_to_completion(shuffled_store, self.dir, tag="-shuf")

        self.assertEqual(d1.state, d2.state)
        self.assertEqual(a1.state, a2.state)
        self.assert_views_consistent(d2, a2)
        self.assert_matches_model(events, a2)


# --------------------------------------------------------------------------
# Scenario: duplicate delivery must be idempotent
# --------------------------------------------------------------------------

class TestIdempotency(EventSourcingTestCase):
    def test_duplicate_append_is_noop(self):
        events = build_events(num_accounts=2, ops_per_account=20, seed=3)
        store = self.make_store(events)
        size_before = len(store)
        for e in events:  # redeliver every event to the store
            pos = store.append(e)
            self.assertLess(pos, size_before)  # returns original position
        self.assertEqual(len(store), size_before)

    def test_duplicate_apply_does_not_change_state(self):
        events = build_events(num_accounts=2, ops_per_account=20, seed=4)
        store = self.make_store(events)
        for cls in (DetailViewProjection, AggregateViewProjection):
            proj = cls()
            for e in store.read_all():
                proj.apply(e)
            snapshot = copy.deepcopy(proj.state)
            for e in store.read_all():  # redeliver the whole stream
                proj.apply(e)
            self.assertEqual(proj.state, snapshot,
                             f"{cls.name} changed after duplicate delivery")

    def test_replay_after_completion_is_noop(self):
        events = build_events(num_accounts=2, ops_per_account=20, seed=5)
        store = self.make_store(events)
        detail, aggregate = replay_to_completion(store, self.dir)
        snap_d, snap_a = copy.deepcopy(detail.state), copy.deepcopy(aggregate.state)
        # Re-run both replayers against the same checkpoints: nothing to do.
        detail2, aggregate2 = replay_to_completion(store, self.dir)
        self.assertEqual(detail2.state, snap_d)
        self.assertEqual(aggregate2.state, snap_a)
        self.assertEqual(detail2.processed_count, len(events))


# --------------------------------------------------------------------------
# Scenario: kill mid-replay, resume from checkpoint == full replay
# --------------------------------------------------------------------------

class TestKillResume(EventSourcingTestCase):
    def test_kill_resume_matches_full_replay(self):
        events = build_events(num_accounts=3, ops_per_account=100, seed=7)
        store = self.make_store(events, shuffle_seed=99)
        n = len(store)

        # Ground truth: uninterrupted full replay.
        full_dir = os.path.join(self.dir, "full")
        os.makedirs(full_dir)
        full_detail, full_agg = replay_to_completion(store, full_dir)

        # Differential test: kill at many positions, resume, compare.
        rng = random.Random(1234)
        kill_points = sorted({0, 1, 2, n // 3, n // 2, n - 2, n - 1}
                             | {rng.randrange(n) for _ in range(15)})
        for kp in kill_points:
            for cls, full in ((DetailViewProjection, full_detail),
                              (AggregateViewProjection, full_agg)):
                ckpt = os.path.join(self.dir, f"kill-{cls.name}-{kp}.json")
                with self.assertRaises(ProjectionKilled):
                    Replayer(store, cls(), ckpt, kill_after=kp).run()
                # A fresh process: new projection, same checkpoint file.
                resumed = Replayer(store, cls(), ckpt).run()
                self.assertEqual(resumed.processed_count, n,
                                 f"{cls.name} killed at {kp}: wrong position")
                self.assertEqual(resumed.state, full.state,
                                 f"{cls.name} killed at {kp}: state diverged")
                self.assertEqual(resumed.seen_event_ids, full.seen_event_ids)

    def test_checkpoint_records_position(self):
        events = build_events(num_accounts=1, ops_per_account=10, seed=8)
        store = self.make_store(events)
        ckpt = os.path.join(self.dir, "pos.json")
        with self.assertRaises(ProjectionKilled):
            Replayer(store, DetailViewProjection(), ckpt, kill_after=4).run()
        import json
        with open(ckpt, encoding="utf-8") as fh:
            snap = json.load(fh)
        self.assertEqual(snap["processed_count"], 4)
        self.assertEqual(len(snap["seen_event_ids"]), 4)


# --------------------------------------------------------------------------
# Scenario: event version compatibility
# --------------------------------------------------------------------------

class TestVersions(EventSourcingTestCase):
    def test_legacy_v1_events_are_upcast(self):
        events = [
            Event(new_event_id(), "a", 0, "AccountOpened", 1, {"owner": "x"}),
            Event(new_event_id(), "a", 1, "Deposited", 1, {"amount": 12.34}),
            Event(new_event_id(), "a", 2, "Withdrawn", 1, {"amount": 2.34}),
        ]
        store = self.make_store(events)
        detail, aggregate = replay_to_completion(store, self.dir)
        self.assertEqual(
            aggregate.state["accounts"]["a"]["balance_cents"], 1000)
        self.assert_views_consistent(detail, aggregate)

    def test_incompatible_version_raises(self):
        events = [
            Event(new_event_id(), "a", 0, "AccountOpened", 1, {"owner": "x"}),
            Event(new_event_id(), "a", 1, "Deposited", 99,
                  {"amount_cents": 100}),
        ]
        store = self.make_store(events)
        for cls in (DetailViewProjection, AggregateViewProjection):
            ckpt = os.path.join(self.dir, f"bad-{cls.name}.json")
            with self.assertRaises(IncompatibleEventVersionError):
                Replayer(store, cls(), ckpt).run()

    def test_unknown_event_type_raises(self):
        events = [Event(new_event_id(), "a", 0, "SomethingElse", 1, {})]
        store = self.make_store(events)
        ckpt = os.path.join(self.dir, "unknown.json")
        with self.assertRaises(UnknownEventTypeError):
            Replayer(store, DetailViewProjection(), ckpt).run()


if __name__ == "__main__":
    unittest.main()
