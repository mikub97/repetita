"""
Intent: what the learner wants studied, and what they think is wrong with how it
is organised. Neither is recoverable from a review log, so neither is derived.
"""

from __future__ import annotations

import pytest

from repetita import store
from repetita.store import issues as I
from repetita.store import plans as P


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


class TestPlans:
    def test_a_plan_holds_an_ordered_list(self, con):
        plan = P.create(con, "Numbers push", "t")
        plan = P.set_priorities(
            con, plan.id, [P.Priority(0, "topic", "numeros"), P.Priority(1, "topic", "comida")]
        )
        assert [p.value for p in plan.priorities] == ["numeros", "comida"]

    def test_priorities_are_replaced_wholesale(self, con):
        # The list *is* the ordering. Applying two independent edits to it would
        # produce an order neither person chose.
        plan = P.create(con, "p", "t")
        P.set_priorities(con, plan.id, [P.Priority(0, "topic", "a"), P.Priority(1, "topic", "b")])
        plan = P.set_priorities(con, plan.id, [P.Priority(0, "topic", "b")])
        assert [p.value for p in plan.priorities] == ["b"]

    def test_an_unknown_knob_is_refused(self, con):
        # A knob nothing reads is a dial that does nothing, which is worse than
        # no dial at all.
        plan = P.create(con, "p", "t")
        with pytest.raises(ValueError):
            P.set_knobs(con, plan.id, {"turbo": 11})

    def test_only_one_plan_is_active(self, con):
        first = P.create(con, "first", "t", active=True)
        second = P.create(con, "second", "t", active=True)
        assert P.active(con).id == second.id
        assert P.get(con, first.id).active is False

    def test_every_change_writes_a_revision(self, con):
        # ADR-0003's lesson: "did making it harder help?" cannot be answered from
        # aggregates, so the sequence is recorded from the first day.
        plan = P.create(con, "p", "t")
        P.set_priorities(con, plan.id, [P.Priority(0, "topic", "a")])
        P.set_knobs(con, plan.id, {"new_every": 2})
        n = con.execute(
            "SELECT COUNT(*) AS n FROM plan_revisions WHERE plan_id = ?", (plan.id,)
        ).fetchone()["n"]
        assert n == 3

    def test_a_revision_records_what_the_plan_looked_like(self, con):
        plan = P.create(con, "p", "t")
        P.set_priorities(con, plan.id, [P.Priority(0, "topic", "numeros")])
        row = con.execute("SELECT snapshot FROM plan_revisions ORDER BY id DESC LIMIT 1").fetchone()
        assert "numeros" in row["snapshot"]

    def test_deleting_a_plan_keeps_its_revisions(self, con):
        # Answers in `review_log` point at those rows. Deleting them would turn a
        # recorded fact into a dangling id nobody can resolve.
        plan = P.create(con, "p", "t")
        P.delete(con, plan.id)
        assert P.get(con, plan.id) is None
        assert con.execute("SELECT COUNT(*) AS n FROM plan_revisions").fetchone()["n"] == 1


class TestWhosePlanItIs:
    """
    `plan_id` is an autoincrementing integer shared by every account, and until
    this every function here but `activate` took it alone. "Radek's plan 3" and
    "my plan 3" were the same argument, so reading, reordering, re-knobbing and
    deleting somebody else's plan needed nothing but the number.
    """

    def test_another_persons_plan_is_no_plan_at_all(self, con):
        hers = P.create(con, "Karo's push", "t", user_id=2)
        assert P.get(con, hers.id, user_id=2) is not None
        assert P.get(con, hers.id, user_id=1) is None

    def test_it_will_not_reorder_somebody_elses_plan(self, con):
        hers = P.create(con, "hers", "t", user_id=2)
        P.set_priorities(con, hers.id, [P.Priority(0, "topic", "comida")], user_id=2)
        with pytest.raises(P.NotYours):
            P.set_priorities(con, hers.id, [P.Priority(0, "topic", "numeros")], user_id=1)
        plan = P.get(con, hers.id, user_id=2)
        assert plan is not None
        assert [x.value for x in plan.priorities] == ["comida"], "unchanged"

    def test_it_will_not_turn_somebody_elses_knobs(self, con):
        hers = P.create(con, "hers", "t", user_id=2)
        with pytest.raises(P.NotYours):
            P.set_knobs(con, hers.id, {"daily_target": 40}, user_id=1)
        assert P.get(con, hers.id, user_id=2).knobs == {}, "unchanged"

    def test_it_will_not_delete_somebody_elses_plan(self, con):
        hers = P.create(con, "hers", "t", user_id=2)
        with pytest.raises(P.NotYours):
            P.delete(con, hers.id, user_id=1)
        assert P.get(con, hers.id, user_id=2) is not None

    def test_the_revision_an_answer_is_filed_under_is_your_own(self, con):
        # `latest_revision` decides what `review_log.plan_revision_id` records
        # (ADR-0007). Reading it off somebody else's plan files your answer
        # under a plan you have never seen.
        hers = P.create(con, "hers", "t", user_id=2)
        P.set_priorities(con, hers.id, [P.Priority(0, "topic", "comida")], user_id=2)
        assert P.latest_revision(con, hers.id, user_id=2) is not None
        assert P.latest_revision(con, hers.id, user_id=1) is None

    def test_a_list_is_only_ever_your_own(self, con):
        P.create(con, "mine", "t", user_id=1)
        P.create(con, "hers", "t", user_id=2)
        assert [p.name for p in P.all_plans(con, user_id=1)] == ["mine"]
        assert [p.name for p in P.all_plans(con, user_id=2)] == ["hers"]


class TestIssues:
    def test_an_observation_is_kept_with_what_prompted_it(self, con):
        # Without the selector, "these two are the same" is unactionable a week
        # later, because nobody remembers which two.
        issue = I.raise_issue(
            con,
            body="tempo and tempo-adverbios are one subject",
            kind="duplicate",
            selector="topic=tempo",
        )
        assert issue.selector == "topic=tempo"
        assert issue.open

    def test_an_empty_issue_is_refused(self, con):
        with pytest.raises(ValueError):
            I.raise_issue(con, body="   ")

    def test_an_unknown_kind_is_refused(self, con):
        with pytest.raises(ValueError):
            I.raise_issue(con, body="something", kind="vibes")

    def test_resolving_records_what_was_done(self, con):
        issue = I.raise_issue(con, body="two tags, one subject", kind="duplicate")
        closed = I.resolve(con, issue.id, note="merged with repetita tag merge")
        assert closed.resolution == "merged with repetita tag merge"
        assert I.open_issues(con) == []

    def test_it_will_not_resolve_twice(self, con):
        # Re-resolving would overwrite the record of what actually fixed it with
        # whatever the second person assumed.
        issue = I.raise_issue(con, body="x")
        I.resolve(con, issue.id, note="first")
        assert I.resolve(con, issue.id, note="second") is None
        assert I.all_issues(con)[0].resolution == "first"

    def test_a_closed_issue_is_kept(self, con):
        issue = I.raise_issue(con, body="x")
        I.resolve(con, issue.id)
        assert len(I.all_issues(con)) == 1
