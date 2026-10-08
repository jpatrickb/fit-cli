# fit

A local CLI for logging workouts and cardio, building gym templates with computed target weights,
and tracking progress. Built to be driven by an agent: output is TOON when not at a terminal, every
write is undoable, and errors come with suggestions instead of guesses. Everything stays on your
machine in one SQLite file.

## Install

```
uv tool install git+<repo url>        # or, from a checkout: uv tool install --editable .
fit init                              # database + built-in catalog; asks who's training and sets up a gym
```

The database lives at `~/.local/share/fit/fit.db` (override with `FIT_DB` or `--db`).

## A typical day

```
fit next --person Sam --to-notes      # today's template into Apple Notes (Mac), weights filled in
  … fill in reps at the gym …
fit notes log "Wed Oct 7 · Sam · B"   # plain reps? logged straight from the note
fit notes pull "Wed Oct 7 · Sam · B"  # anything fancier: the text, for an agent to turn into
fit log - < session.toon              # one write for the whole session (format: fit log --help)
fit undo                              # if that was wrong
fit history "DB RDL" · fit bests · fit muscles · fit capped · fit consistency
```

Agents: start with `fit schema`, which explains the data, the vocabularies and the input formats.

## Development

```
uv run pytest                                                              tests
uv run scripts/seed.py && sqlite3 data/fitness.db < scripts/queries.sql   schema checks on sample history
```

| Path | What it is |
| :-- | :-- |
| `src/fit/` | The package. `schema.sql` is the source of truth for the data; `catalog.py` is the built-in catalog. |
| `docs/schema.md` | Data design and decision log (D1…). |
| `docs/app.md` | CLI behavior and decision log (A1…). |
| `tests/` | CLI tests, run against a throwaway database. |
| `scripts/` | Seed script and SQL checks over sample history. |
| `CLAUDE.md` | Conventions for working on fit. |

`CLAUDE.local.md`, if present, holds personal working notes and is git-ignored. Generated files
(`data/`, `.venv/`) are ignored too.

File names are lowercase-kebab; record every design decision in the matching decision log.
