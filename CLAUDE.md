# fit

A local workout-logging CLI (Python 3.14, uv, SQLite, Typer), built to be driven by an agent.
Design: `docs/schema.md` (data, decisions D1…) and `docs/app.md` (CLI behavior, decisions A1…).
`src/fit/schema.sql` is the source of truth for the data. `CLAUDE.local.md`, if present, holds
personal working notes and is not committed.

## Conventions

- Design reviews: **one piece at a time**, then wait for the user's thoughts.
- **No free-text fields** in the schema; propose structured fields instead.
- Record every design decision in the matching decision log (D-numbers in schema.md, A-numbers in
  app.md) with the date.
- Keep files organized; fix stale paths after moves; generated files go in git-ignored dirs.
- Every write goes through the CLI and its change log (D49); never edit the database directly.

## Using fit (for agents)

- Start with `fit schema`: concepts, conventions, vocabularies and input formats.
- Output is TOON when not at a terminal. Errors go to stderr as TOON with `error`, `message`,
  `suggestions`, exit code 1. Never guess past an error; use the suggestions or ask.
- Log a whole strength session with `fit log -` (document format: `fit log --help`), cardio with
  `fit cardio log -`. One command = one write = one `fit undo`.
- Corrections to anything older than the last write: `fit set edit` / `fit set delete` for a set,
  `fit session delete` to remove a session and re-log it.
- Plans: `fit plan load -` (format: `fit plan load --help`), `fit plan show`, `swap`, `edit`.
  `fit next` builds the gym template; it may swap a maxed exercise in the plan (undoable).
- Set `FIT_DB` to point at a scratch database when experimenting.

## Commands

```
uv run pytest                       tests (all should pass)
uv run fit --help                   the CLI
uv run scripts/seed.py && sqlite3 data/fitness.db < scripts/queries.sql   schema checks
```
