"""
A course leaves and comes back through the browser.

Export was owed from the moment material could be written in the app (ADR-0006
said so in as many words): an exercise created here exists in no file until it
has been out through this, and the promise that adding a lesson is a pull
request a non-programmer can make rests on it entirely.

`TestARefusedZip` is the part to be strict about. This is the only code in the
repository that reads a file somebody else made, and each of those zips is a
request to write outside the directory it was unpacked into.
"""

from __future__ import annotations

import io
import textwrap
import zipfile

import pytest

from repetita import store
from repetita.store.bundle import BadBundle, read_bundle
from repetita.web.app import create_app

COURSE = """\
format_version: 1
id: t
title: {en: Test}
l2: {code: pt}
l1: {code: pl}
license: {name: CC BY-SA 4.0}
"""

NOTES = """\
    notetype: gap
    tags: [comida]
    notes:
      - id: a
        prompt: Eu ___ de casa.
        cue: sair
        answers: [saio]
      - id: b
        prompt: Ela ___ cedo.
        cue: sair
        answers: [sai]
    """


@pytest.fixture
def course_dir(tmp_path):
    root = tmp_path / "course"
    (root / "units" / "01" / "notes").mkdir(parents=True)
    (root / "course.yaml").write_text(COURSE)
    (root / "units" / "01" / "notes" / "n.yaml").write_text(textwrap.dedent(NOTES))
    return root


@pytest.fixture
def app(course_dir, tmp_path):
    return create_app(course_dir, db_path=tmp_path / "study.db")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def con(app):
    c = store.connect(app.config["REPETITA_DB"])
    yield c
    c.close()


def _zip(members: dict[str, bytes | str], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, body in members.items():
            if name == symlink:
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o120777 << 16  # S_IFLNK
                zf.writestr(info, body)
            else:
                zf.writestr(name, body)
    return buffer.getvalue()


def _upload(client, data, path="/api/import/preview", **form):
    return client.post(
        path,
        data={"bundle": (io.BytesIO(data), "course.zip"), **form},
        content_type="multipart/form-data",
    )


class TestExport:
    def test_it_downloads_a_zip_named_for_the_course(self, client):
        response = client.get("/api/export")

        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert 'filename="t.zip"' in response.headers["Content-Disposition"]

    def test_the_zip_is_a_course_directory(self, client):
        with zipfile.ZipFile(io.BytesIO(client.get("/api/export").data)) as zf:
            names = zf.namelist()

        assert "t/course.yaml" in names
        assert any(n.endswith(".yaml") and "/units/" in n for n in names)

    def test_an_exercise_written_in_the_app_comes_out(self, client, con):
        # The whole reason export is owed. Before this, material created here
        # existed in no file and no pull request could contain it.
        con.execute(
            "INSERT INTO notes(id,course,unit,notetype,ord,tags,fields,csum,origin,"
            "created_at,updated_at,edited_at,label_custom) VALUES"
            "('mine','t','01','gap',9,'[]',?,0,'','x','x','x',0)",
            ('{"prompt": "Nós ___ juntos.", "cue": "sair", "answers": ["sa\\u00edmos"]}',),
        )
        con.commit()

        with zipfile.ZipFile(io.BytesIO(client.get("/api/export").data)) as zf:
            body = "".join(zf.read(n).decode() for n in zf.namelist() if "/units/" in n)

        assert "mine" in body and "juntos" in body


class TestTheRoundTrip:
    def test_a_course_survives_export_and_import(self, client, con, app):
        data = client.get("/api/export").data
        con.execute("UPDATE notes SET archived_at = '2026-09-12' WHERE id = 'b'")
        con.commit()
        assert "b" not in build(app).notes

        _upload(client, data, path="/api/import/apply")

        assert sorted(build(app).notes) == ["a", "b"]

    def test_the_schedule_does_not_move(self, client, con, app):
        # The one thing an import must never touch. `card_state` cannot be
        # rebuilt from anything, and a round trip is where it would be lost.
        con.execute(
            "INSERT INTO card_state(user_id,card_id,algo,algo_version,state,due,interval,"
            "seen,correct,wrong,lapses) VALUES(1,'a#fill','sm2',1,'{}','2026-10-01',21,5,5,0,0)"
        )
        con.commit()
        data = client.get("/api/export").data

        _upload(client, data, path="/api/import/apply")

        row = con.execute("SELECT * FROM card_state WHERE card_id = 'a#fill'").fetchone()
        assert row["due"] == "2026-10-01" and row["interval"] == 21 and row["seen"] == 5

    def test_a_course_declared_type_round_trips(self, tmp_path, client, con, app):
        con.execute(
            "INSERT INTO notetypes(course,name,spec) VALUES('t','proverb',?)",
            (
                '{"name": "proverb", "fields": {"saying": {"type": "text", '
                '"visibility": "after"}, "meaning": {"type": "text", "visibility": "before"}}, '
                '"cards": {"recall": {"ask": ["meaning"], "expect": "saying", '
                '"grader": "typed", "forms": ["typein"]}}}',
            ),
        )
        con.commit()

        with zipfile.ZipFile(io.BytesIO(client.get("/api/export").data)) as zf:
            assert "t/notetypes.yaml" in zf.namelist()
            assert "proverb" in zf.read("t/notetypes.yaml").decode()


class TestThePreview:
    def test_it_writes_nothing(self, client, con):
        before = con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]
        data = client.get("/api/export").data
        con.execute("DELETE FROM notes WHERE id = 'b'")
        con.commit()

        client_json = _upload(client, data).get_json()

        assert client_json["added"] == 1
        assert con.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == before - 1

    def test_it_names_what_would_be_archived(self, client, con):
        # A count is not something anyone can check against what they meant.
        data = _zip({"t/course.yaml": COURSE})

        report = _upload(client, data).get_json()

        assert sorted(report["archived_ids"]) == ["a", "b"]
        assert report["archived"] == 2

    def test_a_conflict_is_reported_with_both_versions(self, client, con):
        data = client.get("/api/export").data
        con.execute(
            "UPDATE notes SET fields = ?, edited_at = '2026-09-12', content_hash = 'moved' "
            "WHERE id = 'a'",
            ('{"prompt": "Eu ___ tarde.", "cue": "sair", "answers": ["saio"]}',),
        )
        con.commit()

        report = _upload(client, data).get_json()

        assert [c["note_id"] for c in report["conflicts"]] == ["a"]
        assert report["conflicts"][0]["mine"]["prompt"] == "Eu ___ tarde."


class TestApplying:
    def test_keep_mine_is_the_default_for_a_conflict(self, client, con, app):
        data = client.get("/api/export").data
        con.execute(
            "UPDATE notes SET fields = ?, edited_at = '2026-09-12', content_hash = 'moved' "
            "WHERE id = 'a'",
            ('{"prompt": "Eu ___ tarde.", "cue": "sair", "answers": ["saio"]}',),
        )
        con.commit()

        report = _upload(client, data, path="/api/import/apply").get_json()

        assert report["kept_mine"] == ["a"]
        assert build(app).notes["a"].fields["prompt"] == "Eu ___ tarde."

    def test_take_file_takes_the_file(self, client, con, app):
        data = client.get("/api/export").data
        con.execute(
            "UPDATE notes SET fields = ?, edited_at = '2026-09-12', content_hash = 'moved' "
            "WHERE id = 'a'",
            ('{"prompt": "Eu ___ tarde.", "cue": "sair", "answers": ["saio"]}',),
        )
        con.commit()

        report = _upload(client, data, path="/api/import/apply", take_file="a").get_json()

        assert report["kept_mine"] == []
        assert build(app).notes["a"].fields["prompt"] == "Eu ___ de casa."

    def test_it_leaves_a_snapshot_behind(self, client):
        # CLAUDE.md's answer to a frightening operation is to take a snapshot
        # and then do it -- which only helps if you can find out its name.
        report = _upload(
            client, _zip({"t/course.yaml": COURSE}), path="/api/import/apply"
        ).get_json()

        assert report["snapshot"]

    def test_a_zip_for_another_course_is_refused(self, client):
        data = _zip({"u/course.yaml": COURSE.replace("id: t", "id: u")})

        response = _upload(client, data, path="/api/import/apply")

        assert response.status_code == 409
        assert response.get_json()["error"] == "wrong_course"


class TestARefusedZip:
    @pytest.mark.parametrize(
        "name",
        [
            "../escaped.yaml",
            "t/../../escaped.yaml",
            "/etc/repetita.yaml",
            "..\\windows.yaml",
        ],
    )
    def test_a_path_that_leaves_the_directory(self, tmp_path, name):
        # A valid course rides along, so the refusal has to come from the path.
        # Without it `_course_root` rejects the *shape* of the zip and the test
        # passes whether the path check exists or not.
        data = _zip({"t/course.yaml": COURSE, name: "id: x"})

        with pytest.raises(BadBundle, match="unsafe path"):
            read_bundle(data, tmp_path / "out")

        assert not (tmp_path / "escaped.yaml").exists()
        assert not (tmp_path / "out" / ".." / "escaped.yaml").exists()

    def test_a_symlink(self, tmp_path):
        # Unpacking one opens a door the *next* member can write through.
        data = _zip({"t/course.yaml": COURSE, "t/link.yaml": "/etc/passwd"}, symlink="t/link.yaml")

        with pytest.raises(BadBundle, match="symlink"):
            read_bundle(data, tmp_path / "out")

    def test_a_file_that_is_not_yaml(self, tmp_path):
        data = _zip({"t/course.yaml": COURSE, "t/run.sh": "rm -rf /"})

        with pytest.raises(BadBundle, match="only YAML"):
            read_bundle(data, tmp_path / "out")

    def test_something_that_is_not_a_zip(self, tmp_path):
        with pytest.raises(BadBundle):
            read_bundle(b"this is not a zip", tmp_path / "out")

    def test_a_zip_with_no_course_in_it(self, tmp_path):
        with pytest.raises(BadBundle):
            read_bundle(_zip({"a/x.yaml": "id: x", "b/y.yaml": "id: y"}), tmp_path / "out")

    def test_nothing_is_written_when_a_member_is_refused(self, tmp_path):
        out = tmp_path / "out"
        with pytest.raises(BadBundle):
            read_bundle(_zip({"t/course.yaml": COURSE, "t/run.sh": "x"}), out)
        assert not list(out.rglob("*.sh"))

    def test_the_api_refuses_it_too(self, client):
        response = _upload(client, _zip({"t/course.yaml": COURSE, "../escaped.yaml": "id: x"}))

        assert response.status_code == 422
        assert "unsafe path" in response.get_json()["error"]


class TestTheLeakRuleHolds:
    def test_an_export_is_not_reachable_as_an_open_question(self, client):
        # The Manage surface sees everything (ADR-0008) and so does this: it is
        # the material's owner reading their own material. What must stay true
        # is that the *session* endpoints have not changed.
        body = client.get("/api/session").data
        assert b"saio" not in body and b"sai" not in body.replace(b"sair", b"")


def build(app):
    from repetita.web.app import build_library

    return build_library(app.config["REPETITA_DB"], "t")
