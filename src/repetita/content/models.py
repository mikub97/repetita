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
    kind: str  # shape | leak | duplicate | schema | ambiguous
    detail: str
    #: Fatal problems quarantine the note: it never reaches the pool, so it
    #: cannot be practised even if something downstream ignores this report.
    fatal: bool = True

    def __str__(self) -> str:
        where = f"{self.origin}:{self.note_id}" if self.note_id else self.origin
        return f"[{self.kind}] {where}: {self.detail}"
