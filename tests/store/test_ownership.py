"""
Whose set it is.

Other people's material is **visible, not editable** (ADR-0008's 2026-09-12
amendment, ADR-0016). The half that needs testing is the second word: reading is
unrestricted by decision, and every write path has to refuse.

Enforced in the store rather than in the UI, so these are store tests. Greying
out a button is a courtesy; `POST /api/sets/<unit>/exercises` is reachable with
`curl` whatever the page is showing.
"""

from __future__ import annotations

import textwrap

import pytest

from repetita import store
from repetita.content.loader import load_course
from repetita.store import material as M
from repetita.store import users as U

COURSE = """\
format_version: 1
id: t
l2: {code: it}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

FACETS = """\
axes:
  tutor: {values: [karolina, radek]}
  topic: {catch_all: true}
"""

NOTES = """\
    notetype: vocab
    tags: [karolina, liczby]
    notes:
      - id: uno
        l2: uno
        l1: jeden
      - id: due
        l2: due
        l1: dwa
    """


@pytest.fixture
def con(tmp_path):
    root = tmp_path / "c"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "facets.yaml").write_text(FACETS)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    c = store.connect(tmp_path / "t.db")
    store.sync(c, load_course(root))
    U.rename(c, U.DEFAULT_USER, "mikub")
    U.add(c, "karo")
    U.add(c, "rzadki")
    yield c
    c.close()


def me(con, name):
    who = U.by_name(con, name)
    assert who is not None
    return who.id


class TestTheRule:
    def test_a_set_nobody_owns_is_everybodys(self, con):
        # Every set imported before accounts existed says this, and it is the
        # honest answer for material whose author nobody recorded.
        assert M.owner_of(con, "t", "01") == ""
        assert M.may_edit(con, "t", "01", user_id=me(con, "karo"))

    def test_your_own_set_is_yours(self, con):
        M.set_owner(con, "t", "01", "karo")
        assert M.may_edit(con, "t", "01", user_id=me(con, "karo"))

    def test_somebody_elses_is_not(self, con):
        M.set_owner(con, "t", "01", "karo")
        assert not M.may_edit(con, "t", "01", user_id=me(con, "rzadki"))

    def test_an_admin_may_edit_anybodys(self, con):
        # mikub is the seeded owner and is an admin. Without this the admin page
        # could see every table and fix nothing.
        M.set_owner(con, "t", "01", "karo")
        assert M.may_edit(con, "t", "01", user_id=me(con, "mikub"))


class TestEveryWritePathRefuses:
    @pytest.fixture
    def hers(self, con):
        M.set_owner(con, "t", "01", "karo")
        return me(con, "rzadki")

    def test_staging_a_field_change(self, con, hers):
        with pytest.raises(M.NotYours):
            M.stage(con, "uno", "fields", {"l1": "sabotage"}, user_id=hers)
        note = M.get_note(con, "uno")
        assert note is not None and note.fields["l1"] == "jeden"

    def test_staging_a_tag_change(self, con, hers):
        with pytest.raises(M.NotYours):
            M.stage(con, "uno", "tags", ["mine-now"], user_id=hers)

    def test_staging_a_removal_of_the_whole_set(self, con, hers):
        with pytest.raises(M.NotYours):
            M.stage(con, "01", "remove_set", True, user_id=hers)

    def test_staging_a_rename_of_the_set(self, con, hers):
        with pytest.raises(M.NotYours):
            M.stage(con, "01", "set_name", {"title": {"pl": "moje"}}, user_id=hers)

    def test_saving_exercises_into_it(self, con, hers):
        with pytest.raises(M.NotYours):
            M.save_set(
                con,
                "t",
                "01",
                [{"notetype": "vocab", "fields": {"l2": "tre", "l1": "trzy"}}],
                {},
                None,
                user_id=hers,
            )

    def test_removing_it(self, con, hers):
        with pytest.raises(M.NotYours):
            M.remove_set(con, "t", "01", "2026-09-12", user_id=hers)
        live = con.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE unit = '01' AND archived_at IS NULL"
        ).fetchone()["n"]
        assert live == 2, "the set is still there"

    def test_renaming_it(self, con, hers):
        with pytest.raises(M.NotYours):
            M.rename_unit(con, "t", "01", title={"pl": "moje"}, user_id=hers)

    def test_the_refusal_says_whose_it_is(self, con, hers):
        # A refusal that does not say who to ask is a dead end.
        with pytest.raises(M.NotYours, match="karo"):
            M.stage(con, "uno", "tags", ["x"], user_id=hers)

    def test_a_refusal_is_a_noteditable(self, con, hers):
        # Every caller that already turns NotEditable into a refusal turns this
        # into one too -- there is no handler written before ownership existed
        # that reports this as success.
        with pytest.raises(M.NotEditable):
            M.stage(con, "uno", "tags", ["x"], user_id=hers)


class TestOwnershipIsCheckedTwice:
    def test_a_set_handed_over_after_staging_is_not_applied(self, con):
        # Staged while it was nobody's, confirmed after it became Karo's. The
        # check at `stage` cannot cover this; the one at `apply_pending` is the
        # moment material actually changes, so it is the moment that has to be
        # right.
        radek = me(con, "rzadki")
        M.stage(con, "uno", "tags", ["radek-was-here"], user_id=radek)
        M.set_owner(con, "t", "01", "karo")
        with pytest.raises(M.NotYours):
            M.apply_pending(con, {}, user_id=radek, course="t")
        note = M.get_note(con, "uno")
        assert note is not None and "radek-was-here" not in note.tags


class TestSeedingFromAnAxis:
    def test_it_finds_the_sets_a_tutor_taught(self, con):
        assert M.sets_by_facet(con, "t", "tutor", "karolina") == ["01"]
        assert M.sets_by_facet(con, "t", "tutor", "radek") == []

    def test_a_new_set_belongs_to_whoever_made_it(self, con):
        # Empty means "nobody's in particular", and a set somebody just typed a
        # name for is somebody's.
        M.create_unit(con, "t", "nowy", user_id=me(con, "karo"))
        assert M.owner_of(con, "t", "nowy") == "karo"

    def test_a_rename_carries_the_sets(self, con):
        M.set_owner(con, "t", "01", "karo")
        U.rename(con, "karo", "karolina")
        assert M.owner_of(con, "t", "01") == "karolina"
        assert M.may_edit(con, "t", "01", user_id=me(con, "karolina"))
