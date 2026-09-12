# The admin page

A fifth tab, visible only to an account marked admin. It shows every table in
the database — 25 of them — and lets you edit most.

```bash
repetita user add someone --admin      # or
repetita own --help                    # the ownership form has its own command
```

## What it will not let you change

This is the interesting half, and the reason the page is safe to have at all.

### Study history is read-only

`review_log`, `card_state` and `plan_revisions` can be read and not written.
They are the only things in this system that no export brings back: material
comes back from `repetita export`, a year of scheduling does not. A text field
over them in a browser is how a month of study disappears at one in the morning.

If you genuinely need to remove history, `repetita purge --with-history` is the
operation that does it — it names itself, takes a snapshot first, reports what
goes, and asks twice.

### Material is edited where material is edited

`notes`, `cards`, `units`, `note_facets` and `distractors` are read-only here
too, for a different reason: a write to any of them owes four other things —
set `edited_at`, leave `content_hash` alone, re-expand the cards, `reclassify` —
and the Manage and Create tabs already do all four. The page says so and names
the tab.

`card_handles` is read-only because those rows are regenerated and mean nothing
on their own.

### Credentials are not data

Two things never reach the page, however admin you are:

* **`users.password_hash`** is not shown and cannot be written. Shown, it is a
  hash over somebody's shoulder; writable, it is pasting a hash you know into
  another person's row and signing in as them. To set a password, use the
  account form or `repetita user passwd`.
* **`meta.secret_key`** signs session cookies — anybody who reads it can forge
  one for any account. The row is visible and its value is dots, because a key
  nobody can see the existence of is a key somebody goes looking for in
  `sqlite3`.

## What it will

Everything else: accounts, enrolments, plans and their priorities, settings,
drafts, issues, tag aliases, facet declarations, courses and exercise types.

* Click a cell to edit it, click away to save.
* A primary key is not edited in place — changing one is deleting a row and
  writing another, and whatever pointed at the old one would not follow. For the
  one case where that is wanted, `repetita rename-id` moves the history across
  nine tables.
* Delete removes one row, and it needs the **whole** primary key. `DELETE FROM
  card_state WHERE card_id = ?` without the `user_id` is one keystroke from
  deleting every account's row for that card.

## Accounts and ownership

Two forms rather than raw rows, because both have rules the generic editor does
not know: hashing a password, carrying `units.owner` across a rename,
deactivating instead of deleting. See [Accounts](accounts.md).
