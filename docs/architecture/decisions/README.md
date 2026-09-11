# Architecture decision records

One file per decision. Each says what was decided, what it rules out, and —
most importantly — **what evidence decided it**. If you are about to propose
"wouldn't it be better to…", look here first.

The format is deliberately short. An ADR that takes ten minutes to write gets
written; one that takes an afternoon does not, and then the reasoning lives only
in someone's head until they forget it.

| # | Decision |
| :-- | :-- |
| [0001](0001-note-card-form.md) | Content is a note; scheduling is per card; presentation is a form |
| [0002](0002-hard-is-a-pass.md) | HARD is a pass, not a lapse |
| [0003](0003-scheduler-is-a-plugin.md) | Scheduler state is an opaque blob beside a denormalised `due` |
| [0004](0004-imported-history-is-not-corrected.md) | Imported history is not corrected |
| [0005](0005-the-client-never-sees-a-card-id.md) | The client never sees a card id |
| [0006](0006-the-database-owns-the-material.md) | The database owns the material |
| [0007](0007-a-plan-is-data.md) | A study plan is data, and every answer records which revision produced it |
| [0008](0008-the-management-surface-sees-everything.md) | The management surface sees everything |
| [0009](0009-material-is-captured-before-it-is-shaped.md) | Material is captured before it is shaped |
