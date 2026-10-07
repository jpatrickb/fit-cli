# Workout Tracker — App

What the `fit` CLI does. Data design is in `schema.md`. How we (people and agents) work with it day
to day is in `CLAUDE.local.md`, not here: this file only covers behavior the app itself has to have.

Status: **everything specced is built** (2026-10-07). Next: day-to-day use.
Stack: Python 3.14 (uv project, package `fit`), SQLite, Typer, `toon-format`.

| Milestone | Commands | State |
| :-- | :-- | :-- |
| 1. Setup and logging | `init`, `person add`, `gym add/show`, `equipment add`, `exercise alias`, `log`, `cardio log`, `set add`, `skip`, `symptom add`, `measure add`, `show`, `undo` | built, tested |
| 2. Plans and templates | `plan load/show/swap/edit`, `next`; `next --to-notes`, `notes pull` | built, tested |
| 3. Reading and catalog | `history`, `e1rm`, `bests`, `muscles`, `consistency`, `capped`, `exercise-age`, `cardio list`, `body`, `person/gym list`, `exercise find/list/show/add/link` | built, tested |
| 4. Polish | `schema`, help text, agent guide, README | built, tested |
| 5. Gaps found in review | `plan add-day/add-slot/drop-slot`, `cardio show`, stall flag, `exercise edit`, `equipment edit`, `vocab list/add` | built, tested |

| File | What it is |
| :-- | :-- |
| `docs/schema.md` | Data design, decision log (D1–D50), what's deliberately not modelled. |
| `docs/app.md` | This file: CLI behavior and decisions (A-numbers). |
| `src/fit/schema.sql` | The schema itself (tables + analytics views). Source of truth. |
| `scripts/seed.py` | Builds `data/fitness.db` with a made-up person, gym and generated six weeks of history. |
| `scripts/queries.sql` | The questions the schema must answer, as runnable SQL. |
| `data/fitness.db` | Generated; safe to delete. Not tracked. |

```
uv run scripts/seed.py && sqlite3 data/fitness.db < scripts/queries.sql
```

## Shape of the app

```
      gym (phone)                    Mac
 ┌───────────────────┐   iCloud   ┌──────────────────────────────────────────┐
 │ Notes: template,  │ ◄────────► │ fit next  ──► writes the template note   │
 │ reps filled in    │            │ agent ◄── fit notes pull (raw text)      │
 └───────────────────┘            │   │ translates note / screenshot         │
 Strava screenshots ─────────────►│   ▼                                      │
                                  │ fit log … (structured input) ──► SQLite  │
                                  │ fit undo · fit show · fit progress …     │
                                  └──────────────────────────────────────────┘
```

The CLI takes **structured input only**. Turning a messy note or a Strava screenshot into
structure is the agent's job, which means the app needs no note parser.

## Decisions

| # | Area | Decision |
| :-- | :-- | :-- |
| A1 | Platform | **Local only.** No iOS app, nothing hosted, no accounts. Runs on the Mac; the phone side is Apple's own apps (Notes). |
| A2 | Writes | **Every write goes through the CLI**, whether a person or an agent runs it. Saves without a confirmation step. |
| A3 | Writes | `fit undo` reverses the **most recent** write only, exactly (via the change log, D49). |
| A4 | Input | Commands take **structured input** (flags / JSON), designed to be driven by an agent. No free-form note parsing in the app. *(Proposed; no objection so far.)* |
| A5 | Templates | `fit next` builds a gym template per person: each exercise with its **target weight**, reps blank. **No previous numbers shown.** Plan instructions inline: set count when not 3, `each leg/arm`, tempo, "stop 2 short". |
| A6 | Templates | Targets are **computed when the template is generated**: look up the last session of each exercise; if every set at the top weight hit the top of the range, go up one equipment step. Rules come from the plan slot (D45). |
| A7 | Templates | **If the weight is already at the equipment's max** and was maxed, the template uses a **different exercise**: the next `harder` progression (D20 graph) that can be done at the plan's location. **This updates the plan**: the old slot ends, a new one starts at the same position (D7 versioning). The start weight comes from the progression link's `start_ratio` (D51), else one step below. Because it writes, `fit next` goes through the change log and can be undone. |
| A8 | Notes | `fit next` can **write the template straight into Apple Notes** (via AppleScript on the Mac; syncs to the phone). `fit notes pull` reads a filled-in note back as raw text for the agent, and archives it unedited under `logs/` so the original is kept. |
| A9 | Templates | One template per person, for whoever is going (one or both). |
| A10 | Plans | **Plans live in the database only.** The markdown files in `plans/` retire once the CLI can hold and show plans (kept for history). People or agents write plans; the CLI helps them. |
| A11 | Plans | **Exercise finder:** search the catalog by muscle (primary or any), movement pattern and what the location's equipment allows (`queries.sql` #10). |
| A12 | Plans | **Plans evolve; they don't get replaced wholesale.** One current program per person; exercises are swapped inside it when capped (A7) or when they've gone stale. The app reports **how long each exercise has been in use**, counted from the log rather than from when the slot started (Block 1 → 2 shouldn't reset lat pulldowns to 0 weeks). Nudging is the agent's job. |
| A13 | Plans | **No balance warnings** from the app; it reports weekly sets/volume per muscle and the agent does the warning. |
| A14 | Plans | Starting weights: from history when the exercise has some, else from a progression link (D51), else set by hand. |
| A15 | Progress | **The agent does the analysis.** The CLI exposes the data and calculated views; it doesn't produce reports, summaries or insights. Progress is seen by asking the agent, or as terminal tables. Terminal charts maybe later; **no HTML dashboard** for now. |
| A16 | Progress | Things to be able to answer (from the data, via the agent): per-exercise weight/reps and estimated-max trends, weekly sets and volume per muscle, personal bests, consistency (sessions per week, streaks), cardio mileage, pace and interval comparisons, body weight. **Not** side-by-side comparisons of the two of us. |
| A17 | Progress | **Only on request.** No automatic post-session summaries or weekly reviews. |
| A18 | Progress | **No "expected progress" comparisons** (beginner gain rates, strength standards). Skipped as too hard for now; sex and birth year stay on `person` for later. |
| A19 | Catalog | **Adding an exercise requires every field**: pattern, laterality, measure, implements, body-weight share, at least one muscle, and equipment needs or an explicit "no equipment". The agent fills them in; the CLI refuses anything incomplete. |
| A20 | Gyms | **Quick gym setup**: equipment kinds plus a weight range and step (e.g. dumbbells `5-50/5`), which the CLI expands into the list of available weights. Equipment with no known weights simply skips capping logic. |
| A21 | Gyms | When a gym gets heavier equipment, exercises **in the current plan** just keep progressing (capping is calculated). Exercises not in the plan aren't revisited; no "can progress again" alerts. |
| A22 | Catalog | **Aliases stay**, so name lookups are consistent ("is *rdl* a known exercise?"). **Built-in exercises are editable directly**; catalog updates only add, never overwrite. |
| A23 | Agents | **Output: tables for a person in a terminal, TOON for an agent** (auto-detected when output isn't a terminal; `--toon` / `--table` override). Agent input is TOON too. Library: `toon-format` (official, 1.1.0; round-trip verified on a sample workout). Outputs keep rows uniform (same fields every row, empty when unset) so TOON can use its compact table form. |
| A24 | Agents | **Named commands only; no SQL access.** When something can't be answered with the existing commands, add a command for it. |
| A25 | Writes | **Two ways to write:** one TOON document per session for agents (one write, one undo), and small per-thing commands for people (add a set, skip an exercise…), each its own undoable write. |
| A26 | Agents | **Errors are structured and include suggestions** ("unknown exercise 'rdl'; did you mean DB RDL, B-Stance RDL?"). The CLI never guesses. |
| A27 | Agents | Agents learn the CLI from thorough `--help` plus a `fit schema` command describing the data, **and** a short section in `CLAUDE.md`. |
| A28 | Install | **Database in the standard data folder** (`~/.local/share/fit/fit.db`), overridable with `FIT_DB`. The seed script keeps using `data/` for testing. |
| A29 | Install | **This repo is the app.** The personal material (`plans/`, `logs/`, `reference/`) was never committed and was **deleted on 2026-10-07** once its workouts were in the database; copies live outside the repo. `CLAUDE.local.md` stays as a git-ignored working agreement. |
| A30 | Install | Others install with **`uv tool install`** from the git repo. No PyPI for now. |
| A31 | Install | **`fit init`**: create the database, load the built-in catalog and equipment kinds, ask who's training (name, optional sex and birth year), quick gym setup (A20). Apple Notes is an optional, Mac-only extra. |
| A32 | People | People are named by **first name**. |
| A33 | Templates | **Drop a weight only when the first set at the top weight misses the range** (2026-10-07). A short last set is expected fatigue. |
| A34 | Notes | Templates go into a **Gym** folder in Notes; an existing note is never overwritten or edited. `fit notes pull` keeps an unedited copy in a `notes/` folder next to the database (replacing the `logs/` idea in A8, which was personal to this repo). |
| A35 | Progress | `exercise-age` counts back from the latest session until a gap of more than 3 weeks; anything older is a separate stint. |
| A36 | Templates | **Stall**: the last 3 sessions at the same top weight with no more reps at it than the first of them. Reported in `fit next`'s reason; the weight holds. |
| A37 | Plans | **An exercise appears at most once per plan day**, so a logged exercise always maps to one slot. Slots are added at the end unless a free position is given; dropping a slot ends it (history kept). |
| A38 | Catalog | `fit exercise edit` changes any field; muscles and equipment needs are replaced whole. **Renaming keeps the old name as an alias**, so old notes still match. |
| A39 | Gyms | `fit equipment edit` adds or removes weights, renames, or changes the pulley ratio. A new ratio also changes past totals, by design (store what you read, derive the rest). |
| A40 | Vocab | **Growing vocabularies get a command**, `fit vocab add`, since every write goes through the CLI. Fixed ones (sides, severities, skip reasons, progression rules) are code. |
| A41 | Install | **Table changes ship as migrations** (`db.MIGRATIONS`, `PRAGMA user_version`): the affected tables are rebuilt from `schema.sql` on open, after the file is copied to `fit.db.pre-v<N>.bak`. View changes need no migration; they're rebuilt automatically. |
| A42 | Corrections | Undo only reaches the most recent write, so older data has its own commands: **`fit session delete`** (the whole session and everything in it; its source_ref is freed for re-logging) and **`fit set edit` / `fit set delete`** (one set in place; deleting an exercise's last set removes the exercise and its symptoms). Both are ordinary writes, so undoable right after. |
| A43 | Corrections | A day with more than one session (lifting and a run) needs `--kind` or `--id`; `fit show` now includes each session's id. |

## Progression rules (double progression, per plan slot)

| Last session of this exercise | Next template |
| :-- | :-- |
| Every set at the top weight reached `rep_max`, heavier weight exists | Next `equipment_load` up |
| Same, but already at the heaviest weight | Swap to a harder exercise (A7) |
| The **first** set at the top weight below `rep_min` (later sets falling short is fatigue) | One step down |
| Otherwise | Same weight |
| Moderate or severe pain logged on it | Same weight at most; flag it |
| No improvement in 3 sessions (same top weight, no more reps at it than 3 sessions ago) | Same weight, flagged as stalled |

## Commands

`fit <command> --help` has the details; `fit schema` explains the data for agents.

```
# setup
fit init                                         database + built-in catalog (wizard at a terminal)
fit person add|list · fit gym add|list|show · fit equipment add|edit
fit vocab list|add                               grow body parts, symptom kinds, muscles, equipment kinds...

# plans and templates
fit plan load|show|swap|edit|add-day|add-slot|drop-slot
fit next [--person] [--day] [--date] [--to-notes]   template with computed targets; may swap a maxed lift (A7)
fit notes pull <title> · fit notes list          filled notes back as text (Mac)

# logging (every write is one undoable change set)
fit log -                                        a whole strength session as one TOON document (agents)
fit cardio log -                                 a run or ride, with optional segments
fit set add · fit skip · fit symptom add · fit measure add     one piece at a time (people)
fit undo                                         reverse the most recent write
fit session delete [DATE] · fit set edit|delete <exercise> <set>   correct older sessions

# reading
fit show [DATE] [--sets] · fit cardio show [DATE] · fit cardio list
fit history <exercise> · fit e1rm <exercise> · fit bests · fit muscles · fit consistency
fit capped · fit exercise-age · fit body
fit exercise find|list|show|add|edit|link|alias
fit schema
```

## Sample data

The seed script, checks, tests and docs use a made-up person ("Sam") and gym. Real training data
lives only in a user's own database, never in the repo.
