import copy
import os
import random
import tempfile
import unittest

from event_sourcing import (
    AccountDetailProjection,
    AccountSummaryProjection,
    Event,
    EventStore,
    IncompatibleVersionError,
    ProjectionRunner,
    SimulatedKill,
)


def make_event(event_id, aggregate_id, seq, type_, version=1, **data):
    return Event(
        event_id=event_id,
        aggregate_id=aggregate_id,
        seq=seq,
        type=type_,
        version=version,
        data=dict(data),
    )


def account_stream(events):
    """Reference model: expected per-account balance from causal order."""
    from event_sourcing import signed_amount

    expected = {}
    for event in events:
        balance = expected.get(event.aggregate_id, 0)
        expected[event.aggregate_id] = balance + signed_amount(event)
    return expected


class EventSourcingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="es-tests-")

    def checkpoint_dir(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    # 1. empty event stream ------------------------------------------------
    def test_empty_stream(self):
        store = EventStore()
        detail = ProjectionRunner(
            store, AccountDetailProjection(), self.checkpoint_dir("d1")
        )
        summary = ProjectionRunner(
            store, AccountSummaryProjection(), self.checkpoint_dir("s1")
        )
        detail_state = detail.rebuild()
        summary_state = summary.rebuild()
        self.assertEqual(detail_state, AccountDetailProjection().initial_state())
        self.assertEqual(summary_state, AccountSummaryProjection().initial_state())
        self.assertEqual(detail.checkpoint_position(), 0)
        self.assertEqual(summary.checkpoint_position(), 0)

    # 2. single event ------------------------------------------------------
    def test_single_event(self):
        store = EventStore()
        store.append(make_event("e1", "acct-1", 1, "AccountOpened"))
        state = ProjectionRunner(
            store, AccountDetailProjection(), self.checkpoint_dir("single")
        ).rebuild()
        self.assertEqual(list(state["data"]["accounts"]), ["acct-1"])
        entry = state["data"]["accounts"]["acct-1"]["entries"][0]
        self.assertEqual(entry["balance"], 0)
        self.assertEqual(entry["event_id"], "e1")

    # 3. idempotency -------------------------------------------------------
    def test_store_append_idempotent_on_event_id(self):
        store = EventStore()
        p1 = store.append(make_event("dup", "a", 1, "AccountOpened"))
        p2 = store.append(make_event("dup", "a", 1, "AccountOpened"))
        self.assertEqual(p1, p2)
        self.assertEqual(len(store), 1)

    def test_projection_replay_idempotent(self):
        """Same event delivered repeatedly must not change state.

        Dedup basis: (aggregate_id, seq) — seq below the next expected seq is
        a redelivery and skipped.
        """
        projection = AccountDetailProjection()
        state = projection.initial_state()
        opened = make_event("e1", "a", 1, "AccountOpened")
        deposited = make_event("e2", "a", 2, "MoneyDeposited", amount=500)

        projection.apply(state, opened)
        projection.apply(state, deposited)
        before = copy.deepcopy(state)

        projection.apply(state, opened)
        projection.apply(state, deposited)
        projection.apply(state, deposited)
        self.assertEqual(state, before)

        entries = state["data"]["accounts"]["a"]["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[-1]["balance"], 500)

    def test_redelivery_with_new_event_id_still_deduped(self):
        """Same (aggregate_id, seq) with a different event_id is also a dup."""
        projection = AccountSummaryProjection()
        state = projection.initial_state()
        projection.apply(state, make_event("id-1", "a", 1, "MoneyDeposited", amount=10))
        snapshot = copy.deepcopy(state)
        projection.apply(state, make_event("id-2", "a", 1, "MoneyDeposited", amount=10))
        self.assertEqual(state, snapshot)

    # 4. out-of-order arrival ---------------------------------------------
    def test_out_of_order_arrival_gives_same_state(self):
        events = [
            make_event("e1", "a", 1, "AccountOpened"),
            make_event("e2", "a", 2, "MoneyDeposited", amount=100),
            make_event("e3", "a", 3, "MoneyWithdrawn", amount=30),
            make_event("e4", "b", 1, "AccountOpened"),
            make_event("e5", "b", 2, "MoneyDeposited", amount=200),
        ]
        ordered = EventStore()
        ordered.append_all(events)

        shuffled = EventStore()
        rng = random.Random(42)
        shuffled_events = events[:]
        rng.shuffle(shuffled_events)
        shuffled.append_all(shuffled_events)
        self.assertNotEqual(
            [e.event_id for _, e in shuffled.read_from(0)],
            [e.event_id for _, e in ordered.read_from(0)],
        )

        state_ordered = ProjectionRunner(
            ordered, AccountDetailProjection(), self.checkpoint_dir("ord")
        ).rebuild()
        state_shuffled = ProjectionRunner(
            shuffled, AccountDetailProjection(), self.checkpoint_dir("shuf")
        ).rebuild()
        self.assertEqual(state_ordered, state_shuffled)

        # gaps must have been fully drained in the final state
        self.assertEqual(state_shuffled["pending"], {})
        self.assertEqual(
            [entry["seq"] for entry in state_shuffled["data"]["accounts"]["a"]["entries"]],
            [1, 2, 3],
        )

    # 5. kill + resume vs full replay -------------------------------------
    def test_resume_after_kill_matches_full_replay(self):
        store, events = self._build_random_store(seed=7, n_accounts=8, n_events=400)

        full_dir = self.checkpoint_dir("full")
        full_detail = ProjectionRunner(
            store, AccountDetailProjection(), full_dir
        ).rebuild()
        full_summary = ProjectionRunner(
            store, AccountSummaryProjection(), full_dir
        ).rebuild()

        resume_dir = self.checkpoint_dir("resume")
        detail_runner = ProjectionRunner(
            store, AccountDetailProjection(), resume_dir
        )
        summary_runner = ProjectionRunner(
            store, AccountSummaryProjection(), resume_dir
        )

        rng = random.Random(99)
        detail_state = self._run_with_random_kills(detail_runner, rng)
        rng = random.Random(123)
        summary_state = self._run_with_random_kills(summary_runner, rng)

        self.assertEqual(detail_state, full_detail)
        self.assertEqual(summary_state, full_summary)
        self.assertEqual(detail_runner.checkpoint_position(), len(store))
        self.assertEqual(summary_runner.checkpoint_position(), len(store))

        expected = account_stream(events)
        for account_id, balance in expected.items():
            self.assertEqual(
                summary_state["data"]["accounts"][account_id]["balance"], balance
            )

    def test_kill_can_happen_before_first_checkpoint(self):
        store = EventStore()
        store.append_all(
            [
                make_event("e1", "a", 1, "AccountOpened"),
                make_event("e2", "a", 2, "MoneyDeposited", amount=100),
            ]
        )
        runner = ProjectionRunner(
            store, AccountSummaryProjection(), self.checkpoint_dir("earlykill")
        )
        with self.assertRaises(SimulatedKill):
            runner.rebuild(kill_after=1)
        state = runner.resume()
        self.assertEqual(state["data"]["accounts"]["a"]["balance"], 100)
        self.assertEqual(runner.checkpoint_position(), 2)

    def test_commit_interval_larger_than_one(self):
        store, _ = self._build_random_store(seed=3, n_accounts=4, n_events=120)
        full = ProjectionRunner(
            store, AccountSummaryProjection(), self.checkpoint_dir("ci-full")
        ).rebuild()

        runner = ProjectionRunner(
            store,
            AccountSummaryProjection(),
            self.checkpoint_dir("ci-resume"),
            commit_interval=25,
        )
        rng = random.Random(5)
        state = self._run_with_random_kills(runner, rng, kill_choices=(1, 20, 37))
        self.assertEqual(state, full)

    # 6. two projections mutually consistent -------------------------------
    def test_projections_are_mutually_consistent(self):
        store, _ = self._build_random_store(seed=11, n_accounts=12, n_events=900)
        directory = self.checkpoint_dir("cross")
        detail_state = ProjectionRunner(
            store, AccountDetailProjection(), directory
        ).rebuild()
        summary_state = ProjectionRunner(
            store, AccountSummaryProjection(), directory
        ).rebuild()
        self.assert_projections_consistent(detail_state, summary_state)

    # 7. incompatible event version ---------------------------------------
    def test_incompatible_event_version_raises(self):
        store = EventStore()
        store.append(make_event("e1", "a", 1, "AccountOpened"))
        store.append(
            make_event("e2", "a", 2, "MoneyWithdrawn", version=99, amount=10)
        )
        runner = ProjectionRunner(
            store, AccountSummaryProjection(), self.checkpoint_dir("badver")
        )
        with self.assertRaises(IncompatibleVersionError):
            runner.rebuild()

    def test_unknown_event_type_raises(self):
        store = EventStore()
        store.append(make_event("e1", "a", 1, "AccountFrozen", version=1))
        runner = ProjectionRunner(
            store, AccountSummaryProjection(), self.checkpoint_dir("badtype")
        )
        with self.assertRaises(IncompatibleVersionError):
            runner.rebuild()

    # 8. large stream ------------------------------------------------------
    def test_large_stream(self):
        store, events = self._build_random_store(
            seed=1, n_accounts=30, n_events=5000
        )
        self.assertEqual(len(store), 5000)
        directory = self.checkpoint_dir("large")
        runner = ProjectionRunner(
            store,
            AccountSummaryProjection(),
            directory,
            commit_interval=200,
        )
        state = runner.rebuild()
        self.assertEqual(runner.checkpoint_position(), 5000)

        expected = account_stream(events)
        for account_id, balance in expected.items():
            self.assertEqual(
                state["data"]["accounts"][account_id]["balance"], balance
            )
        self.assertEqual(
            sum(acc["tx_count"] for acc in state["data"]["accounts"].values()),
            5000,
        )

    # 9. randomized soak: duplicates + shuffle + repeated kills ------------
    def test_randomized_soak(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                store, events = self._build_random_store(
                    seed=seed, n_accounts=6, n_events=300, duplicate_every=True
                )
                full_dir = self.checkpoint_dir(f"soak-full-{seed}")
                full_detail = ProjectionRunner(
                    store, AccountDetailProjection(), full_dir
                ).rebuild()
                full_summary = ProjectionRunner(
                    store, AccountSummaryProjection(), full_dir
                ).rebuild()

                resume_dir = self.checkpoint_dir(f"soak-resume-{seed}")
                detail_runner = ProjectionRunner(
                    store, AccountDetailProjection(), resume_dir
                )
                summary_runner = ProjectionRunner(
                    store, AccountSummaryProjection(), resume_dir
                )
                detail_state = self._run_with_random_kills(
                    detail_runner, random.Random(seed * 100 + 1)
                )
                summary_state = self._run_with_random_kills(
                    summary_runner, random.Random(seed * 100 + 2)
                )

                self.assertEqual(detail_state, full_detail)
                self.assertEqual(summary_state, full_summary)
                self.assert_projections_consistent(detail_state, summary_state)
                self.assertEqual(detail_runner.checkpoint_position(), len(store))

    # helpers --------------------------------------------------------------
    def _run_with_random_kills(self, runner, rng, kill_choices=(1, 2, 3, 5, 11)):
        while True:
            try:
                return runner.resume(kill_after=rng.choice(kill_choices))
            except SimulatedKill:
                continue

    def assert_projections_consistent(self, detail_state, summary_state):
        detail_accounts = detail_state["data"]["accounts"]
        summary_accounts = summary_state["data"]["accounts"]
        self.assertEqual(set(detail_accounts), set(summary_accounts))
        for account_id in summary_accounts:
            entries = detail_accounts[account_id]["entries"]
            summary = summary_accounts[account_id]

            self.assertEqual(summary["tx_count"], len(entries))
            self.assertEqual(
                summary["balance"],
                sum(entry["amount"] for entry in entries),
            )
            self.assertEqual(
                summary["deposits"],
                sum(e["amount"] for e in entries if e["amount"] > 0),
            )
            self.assertEqual(
                summary["withdrawals"],
                sum(-e["amount"] for e in entries if e["amount"] < 0),
            )
            if entries:
                self.assertEqual(summary["balance"], entries[-1]["balance"])
            seqs = [entry["seq"] for entry in entries]
            self.assertEqual(seqs, sorted(seqs))
            self.assertEqual(len(seqs), len(set(seqs)))

    def _build_random_store(self, seed, n_accounts, n_events, duplicate_every=False):
        rng = random.Random(seed)
        events = []
        seqs = {f"acct-{i}": 1 for i in range(n_accounts)}
        for index in range(n_events):
            account_id = f"acct-{rng.randrange(n_accounts)}"
            seq = seqs[account_id]
            seqs[account_id] += 1
            if seq == 1:
                event = make_event(f"e-{index}", account_id, seq, "AccountOpened")
            else:
                kind = rng.choice(
                    ["MoneyDeposited", "MoneyDeposited", "MoneyWithdrawn"]
                )
                if kind == "MoneyDeposited":
                    event = make_event(
                        f"e-{index}", account_id, seq, kind,
                        amount=rng.randint(1, 1000),
                    )
                else:
                    if rng.random() < 0.5:
                        event = make_event(
                            f"e-{index}", account_id, seq, kind,
                            amount=rng.randint(1, 500),
                        )
                    else:
                        event = make_event(
                            f"e-{index}", account_id, seq, kind, version=2,
                            amount=rng.randint(1, 500), fee=rng.randint(1, 20),
                        )
            events.append(event)
            if duplicate_every and index % 37 == 13:
                events.append(event)

        unique_events = list({event.event_id: event for event in events}.values())
        causal_order = sorted(
            unique_events, key=lambda e: (e.aggregate_id, e.seq)
        )
        rng.shuffle(events)
        store = EventStore()
        store.append_all(events)
        self.assertEqual(len(store), len(unique_events))
        return store, causal_order


if __name__ == "__main__":
    unittest.main()
