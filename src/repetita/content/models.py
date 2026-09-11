"""
Typed content model. These pydantic models are the source of truth: the JSON
Schema published for editors is generated from them, so the two cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FieldType = Literal["text", "text_list", "image", "audio", "number"]
Visibility = Literal["before", "after"]


class FieldSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: FieldType = "text"
    required: bool = False
    #: "before" = safe to show while the question is open, *when this field is
    #: not the card's answer*. A field that is the answer is hidden regardless;
    #: see NoteType.visible_before.
    visibility: Visibility = "before"


class CardTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ask: tuple[str, ...]
    expect: str
    grader: str
    forms: tuple[str, ...]
    #: Fields that must be present and non-empty, or no card is generated.
    requires: tuple[str, ...] = ()


class NoteType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    fields: dict[str, FieldSpec]
    cards: dict[str, CardTemplate]

    def visible_before(self, template: str) -> tuple[str, ...]:
        """
        Fields that may be shown while this card's question is open.

        Visibility is a property of (field, card), not of the field alone. In a
        `vocab` note `l1` is the prompt for the `produce` card and the *answer*
        for `recognize`; declaring it "before" once would show the answer half
        the time, and declaring it "after" would hide the question.

        So the rule is composed from both directions: a card's `ask` fields are
        always shown because they are the question, and its `expect` field is
        always hidden because it is the answer. The field spec only decides the
        rest. The answer is never in the payload while the question is open, and
        that rule has no exceptions to weigh.
        """
        tpl = self.cards[template]
        asked = set(tpl.ask)
        return tuple(
            n
            for n, f in self.fields.items()
            if (f.visibility == "before" or n in asked) and n != tpl.expect
        )


class Note(BaseModel):
    """One atom of content, as authored."""

    model_config = ConfigDict(extra="forbid")

    id: str
    notetype: str
    fields: dict[str, Any]
    tags: tuple[str, ...] = ()
    lesson: date | None = None
    unit: str = ""
    ord: int = 0
    #: A short name for this exercise. Derived unless someone wrote one, in
    #: which case it is authored content and travels in the course file.
    label: str = ""
    #: Where it came from, for error messages. Never shown to a learner.
    origin: str = ""

    def get(self, name: str) -> Any:
        return self.fields.get(name)

    def text(self, name: str) -> str:
        v = self.fields.get(name)
        if isinstance(v, list):
            return str(v[0]) if v else ""
        return "" if v is None else str(v)

    def answers(self, name: str) -> list[str]:
        v = self.fields.get(name)
        if v is None:
            return []
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]


class Card(BaseModel):
    """A (note, template) pair -- the unit of scheduling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    note_id: str
    template: str
    notetype: str
    grader: str
    forms: tuple[str, ...]


class LanguageSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    #: Not optional in practice for pluricentric languages: pt-BR and pt-PT
    #: differ in ways a course must commit to.
    variant: str | None = None


class LicenseSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    link: str | None = None


class GradingSpec(BaseModel):
    """Language-specific grading behaviour. This is why the engine needs none."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold_accents: bool = True
    ignore_punctuation: bool = True
    ignore_case: bool = True
    sentence_slack: int = 1


class PathStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: str
    requires: tuple[str, ...] = ()


class FacetAxis(BaseModel):
    """
    One dimension a course sorts its material along -- level, track, topic.

    Tags stay the authoring surface. `importers/hub.py` records that the
    predecessor had `track`, `topic` and `level` as three separate columns and
    the import deliberately flattened all three into tags, because "a tag is the
    general form of something true about this note that a course may weight or
    filter on". That decision is kept. An axis does not replace a tag; it says
    which question a given tag answers, so the flat bag becomes groupable
    without becoming rigid.

    Declared per course, never in the engine -- the engine contains no
    Portuguese and no Polish, and `vocabulario` is not a concept it should know.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[str, ...] = ()
    title: dict[str, str] = Field(default_factory=dict)
    #: Values have a meaningful sequence (A1 before A2), so a UI may sort by it.
    ordered: bool = False
    #: Where tags that match no declared value land. At most one axis per course.
    catch_all: bool = False
    #: How many values one note may carry on this axis. `None` is unlimited,
    #: which is the normal case: a note about ordering food in a market is
    #: genuinely both `comida` and `cidade`, and forcing a choice loses that.
    max_per_note: int | None = None


class FamilySpec(BaseModel):
    """
    How a course marks several forms of one word.

    Optional, and course configuration rather than engine behaviour. The obvious
    key -- the note id's stem -- was measured against a real course and refused:
    it produced 404 groups for 676 notes, 350 of them singletons, and its biggest
    "families" were a topic plus a sequence number rather than a word in several
    forms.

    What is actually authored is the cue: `"morar -- imperfeito, eu"`, on 540 of
    560 gap notes. Splitting a string on a separator is not knowledge of
    Portuguese, so this stays on the right side of the no-language-in-the-engine
    rule; grouping `morava` with `moravas` by their shared verb would not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The field carrying "lemma <separator> description".
    field: str = "cue"
    separator: str = "\u2014"


class Facets(BaseModel):
    """A course's `facets.yaml`: how its tags are to be read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    axes: dict[str, FacetAxis] = Field(default_factory=dict)
    #: Old tag -> current tag. A rename leaves one of these behind so that
    #: material tagged before the rename keeps resolving.
    aliases: dict[str, str] = Field(default_factory=dict)
    #: How to recognise several forms of one word. Absent means no families.
    family: FamilySpec | None = None

    @field_validator("axes")
    @classmethod
    def _one_catch_all(cls, v: dict[str, FacetAxis]) -> dict[str, FacetAxis]:
        catch = [n for n, a in v.items() if a.catch_all]
        if len(catch) > 1:
            raise ValueError(f"only one axis may be catch_all; got {sorted(catch)}")
        return v


class Unit(BaseModel):
    """
    A named group of notes -- one directory under `units/`.

    Its `unit.yaml` was parsed by nothing until now: the loader skipped the file
    outright, so a unit's title and CEFR level existed in the course and reached
    neither the database nor the app.
    """

    model_config = ConfigDict(extra="forbid")

    #: The directory name. It is the id every note in the unit carries.
    id: str
    title: dict[str, str] = Field(default_factory=dict)
    cefr: str | None = None
    #: Position in `Course.path`, or after every listed unit in directory order.
    ord: int = 0
    #: Prerequisite units, from `Course.path`. Declared since the first course
    #: and, like the rest of this model, read by nothing until now.
    requires: tuple[str, ...] = ()


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = 1
    id: str
    title: dict[str, str] = Field(default_factory=dict)
    l2: LanguageSpec
    l1: LanguageSpec
    license: LicenseSpec
    grading: GradingSpec = Field(default_factory=GradingSpec)
    scheduler: str = "sm2"
    #: Relative share of each tag when new material is introduced.
    tag_weights: dict[str, float] = Field(default_factory=dict)
    path: list[PathStep] = Field(default_factory=list)

    @field_validator("format_version")
    @classmethod
    def _supported(cls, v: int) -> int:
        # Refusing an unknown major with a clear message is what lets the schema
        # evolve without breaking every fork's course.
        if v != 1:
            raise ValueError(f"unsupported format_version {v}; this build reads 1")
        return v


class Problem(BaseModel):
    """Something wrong with a content file."""

    model_config = ConfigDict(frozen=True)

    origin: str
    note_id: str | None
    kind: str  # shape | leak | duplicate | schema | ambiguous | taxonomy
    detail: str
    #: Fatal problems quarantine the note: it never reaches the pool, so it
    #: cannot be practised even if something downstream ignores this report.
    fatal: bool = True

    def __str__(self) -> str:
        where = f"{self.origin}:{self.note_id}" if self.note_id else self.origin
        return f"[{self.kind}] {where}: {self.detail}"
