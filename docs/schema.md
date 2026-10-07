# Data Schema — Design Notes (draft v0.1, 2026-10-05)

`src/fit/schema.sql` is the source of truth; this file explains why it looks the way it does and
lists what is still undecided. `scripts/seed.py` loads a generated sample history into it, and
`scripts/queries.sql` checks that it answers the questions we care about.

## Principles

1. **Store what was observed; derive everything else.** A set stores "30 lb dumbbell, two of them,
   12 reps", never "60 lb" or "720 lb volume". Totals, e1RM, pulley ratios and body-weight shares
   are computed in views, so a wrong assumption (e.g. the leg press ratio) is fixed in one place and
   the history corrects itself.
2. **Plan and log are separate.** The plan (`program → program_day → prescription`) is what we
   intend; the log (`session → session_exercise → exercise_set`) is what happened. They're linked,
   never merged, so plan-vs-actual and substitutions are queryable.
3. **Missing is not zero.** An unlogged set has no row. Unknown values are NULL. Every imported record
   points back to its original (`session.source_ref`), so reconstructed data is traceable.
4. **History is immutable in meaning.** Changing a plan never rewrites what the plan *was*:
   prescriptions have `active_from` / `active_until`.
5. **No free-text fields.** Agents (and people) fill them with junk. When something is worth
   recording, it gets a structured column or a fixed vocabulary; until then it isn't stored.
6. **Installable by anyone.** Nothing about any one person's gym, name or exercises is hard-coded:
   equipment, locations and people are data, so someone else can install it and set up their own.
7. **Ready for a phone app later.** UUIDv7 ids, timestamps on every edited row, soft deletes. That's
   cheap now and very expensive to retrofit once two devices are writing data.

## Shape

```
person ─┬─ measurement (body weight over time)
        ├─ program ── program_day ── prescription ──► exercise
        └─ session ─┬─ session_exercise ──► exercise, prescription?, equipment?
                    │        └─ exercise_set
                    ├─ symptom (knee, shoulders… optionally tied to one exercise)
                    ├─ cardio_detail (kind = 'cardio': activity, workout type, totals)
                    └─ cardio_segment (splits, reps, recoveries, in order)

change_set ── change (before/after per row, for undo and audit)

exercise ─┬─ exercise_alias       (spellings seen in notes)
          ├─ exercise_muscle ──► muscle   (set credit for volume analytics)
          ├─ exercise_progression         (harder / easier / substitute graph)
          └─ exercise_equipment ──► equipment_kind   (what it needs)

location ── equipment ──► equipment_kind
               └─ equipment_load (the discrete weights it can be set to, in equipment.unit)
```

## How the awkward cases are handled

| Case from real notes | Representation |
| :-- | :-- |
| `[30x2]`, `[2x30lb]`, `30x2 [Weight]` | `load = 30, implements = 2` on lb equipment |
| `[7 plates]` | `load = 70`. The plate-to-lb conversion happens once, at import |
| Leg press 2:1 pulley | `equipment.load_multiplier = 2.0`, applied in `v_set` (confirmed) |
| `10 each arm` / `each side` | One row, `side = 'each'`. Split into `left` / `right` rows only if the sides differ |
| Push-ups, bodyweight hip thrusts | `load` NULL or added load only; `exercise.bodyweight_fraction` × latest body weight in views |
| Side plank, 30 s | `seconds = 30`, `reps` NULL; `exercise.measure = 'time'` |
| `(Warmup)` set, or a 40 → 60 ramp | `kind = 'warmup'`, excluded from progression and volume |
| Tempo incline press | Same exercise as incline DB press; `tempo = '3-1-1-0'` on the prescription or set |
| Ham curl → hip thrust mid-block | Old prescription gets `active_until`, new one starts at the same position |
| Baseline day, off-plan workout | `session.program_day_id` NULL |
| Same note imported twice | Rejected by `UNIQUE (source, source_ref)` |
| A session reconstructed from another source | `source = 'import'`; `source_ref` names the original |
| Clicking in the right knee | `symptom`: knee, right, clicking, mild. "Felt great" isn't logged |
| A 2 mi run with sore knees | `session.kind = 'cardio'` + `cardio_detail` (run, 2 mi) + `symptom` (knee, both, soreness, mild) |
| 4x800 with 400 jog | `cardio_segment` rows: warmup, work 800 m, recovery 400 m … cooldown; pace per rep is derived |
| Fartlek, 1 min on / 1 min off | Segments with time only, no distance |
| Dumbbells cap at 30 | `equipment_load` rows 5…30; `v_capped` finds lifts at the top load *and* the top of the rep range |

## What the seed run showed

- **Every check in `scripts/queries.sql` answers correctly** (progress, capped lifts, e1RM, weekly
  sets per muscle, plan-vs-actual, symptoms, the plan, availability, next day, exercise finder,
  slot age). On real history, `v_capped` independently flagged the same lifts a person had picked out
  by hand as needing a harder variation.
- **Constraints earned their keep:** the case-insensitive alias key caught a duplicate spelling.
- **Bug found and fixed:** a set done on each side was counted as two sets per muscle.
- **Fixed later:** "top load × reps" misread mixed-load sessions (a hip thrust session at 15/15/20 showed as
  20 × 10/10/10). Now summarized by weight: `15×10,10 · 20×10` (D44).

## Decisions made

| # | Date | Piece | Decision |
| :-- | :-- | :-- | :-- |
| D1 | 2026-10-05 | Set | Load is stored **per implement** plus a count (`30 × 2`), never as a total. |
| D2 | 2026-10-05 | Set | Warmups are rarely done; `kind = 'warmup'` stays available but defaults to `working`, and the importer doesn't guess warmups. |
| D3 | 2026-10-05 | Set | **RIR/RPE dropped.** It won't be logged at the gym. `to_failure` is the only effort signal. |
| D4 | 2026-10-05 | Set | `side = 'each'` by default; separate `left` / `right` rows are kept for when a side falls short. |
| D5 | 2026-10-05 | Set | **Rest-pause reps** ("10, fail, rest, +2") go in `rest_pause_reps` on the same row. They count toward volume; progression and e1RM use the clean `reps` only. Requires `to_failure = 1`. |
| D6 | 2026-10-06 | Exercise in session | **Skips are recorded:** `status = 'skipped'` with a required `skip_reason` (`pain`, `time`, `equipment`, `fatigue`, `other`). Only planned exercises can be skipped, and a skipped exercise can't have sets. |
| D7 | 2026-10-06 | Exercise in session | One row per exercise per session (`UNIQUE (session_id, exercise_id)`); every set, including a mid-exercise weight change, hangs off it, ordered by `set_no`. **`position` kept** (briefly removed, then restored as possibly useful); NULL only for a skip. |
| D8 | 2026-10-06 | Exercise in session | `equipment_id` override kept for training elsewhere, since others will use this too. |
| D9 | 2026-10-06 | Everywhere | **No free-text notes.** Removed from sets and session exercises; the remaining `notes` columns go as their pieces come up. |
| D10 | 2026-10-06 | Session | `started_at` kept. **No end time or duration**: the watch and Strava already track that. |
| D11 | 2026-10-06 | Session | `title` and `notes` dropped; a session is named by its plan day. |
| D12 | 2026-10-06 | Session | **Missed workouts aren't recorded.** No row = didn't happen. |
| D13 | 2026-10-06 | Session | People training together are **not linked**; two independent sessions. |
| D14 | 2026-10-06 | Session | `source` is generic (`manual`, `import`, `strava`). `source_ref` points to the original (file:line, Strava id); `UNIQUE (source, source_ref)` makes re-imports no-ops. Kept tentatively; its value is mostly dedupe. |
| D15 | 2026-10-06 | Symptom | **No "felt fine" entries.** Fine is the default; only problems are logged. |
| D16 | 2026-10-06 | Symptom | Body part is a fixed list (`knee`, `shoulder`, `wrist`, `elbow`, `hip`, `ankle`, `lower_back`, `neck`) plus `side` (`left`/`right`/`both`), required for paired parts and forbidden otherwise. Free-text description dropped. |
| D17 | 2026-10-06 | Symptom | Severity is `mild` / `moderate` / `severe`, required. The pain rule triggers at moderate. |
| D18 | 2026-10-06 | Symptom | Symptoms are logged **during** an exercise or run only; no next-day soreness. |
| D19 | 2026-10-06 | Symptom | Body parts and symptom kinds are **lookup tables**, so growing either list is an insert, not a migration. |
| D20 | 2026-10-06 | Exercise | **What's an exercise:** a new exercise when the movement or the equipment kind changes (DB RDL ≠ B-Stance RDL ≠ EZ-Bar RDL); the same exercise with a modifier when only execution changes (tempo). Flat vs incline are separate exercises; exact bench angle isn't tracked. Views carry the effective tempo so progress compares like with like. |
| D21 | 2026-10-06 | Exercise | **Starter catalog ships with the app** (`origin = 'builtin'`, fixed ids stable across installs); users add their own locally (`origin = 'user'`). No multi-user approval flow until/unless this goes public. |
| D22 | 2026-10-06 | Exercise | Names are generic ("Machine Chest Press"); the brand belongs to the user's equipment. Brand spellings live on as aliases. |
| D23 | 2026-10-06 | Exercise | **Muscles and weekly sets per muscle are important**; the muscle mapping stays. |
| D24 | 2026-10-06 | Exercise | `bodyweight_fraction` kept. `cues` dropped. |
| D25 | 2026-10-06 | Equipment | **Each piece of equipment has a unit** (`lb` default, or `kg`); loads are stored in it (`load`, not `load_lb`). Calculated totals report in lb. |
| D26 | 2026-10-06 | Equipment | Pulley ratios are facts to confirm once, not flags: `verified` dropped. |
| D27 | 2026-10-06 | Equipment | Loadable bars (plate combinations) deferred; list combinations by hand if needed. |
| D28 | 2026-10-06 | Equipment | **Equipment is a full inventory per location.** Generic `equipment_kind` catalog (ships with the app); `exercise_equipment` says what an exercise needs (same slot = alternatives, different slots = all required). "What can I do here?" is a query. `exercise.default_equipment_id` removed; `session_exercise.equipment_id` records what was actually used. Refines D20: the *kind of load* defines an exercise, not the specific machine. |
| D29 | 2026-10-06 | Equipment | Location and equipment `notes` dropped. |
| D30 | 2026-10-06 | Plan | **Days have no schedule.** No weekday, no fixed order; any day can be done any date. The app suggests the day done longest ago, derived from the log, and you can always pick another. |
| D31 | 2026-10-06 | Plan | **Week-specific changes aren't modelled** (her 2-set first week, lighter weeks). Revisit with an override table only if it becomes a real need. |
| D32 | 2026-10-06 | Plan | Program `goal` and `notes`, and prescription `notes`, dropped. The short day name ("Push & Quad") stays as a label. |
| D33 | 2026-10-06 | Plan | `target_rir` kept as an **instruction** in the plan only (e.g. push-ups: stop 2 short). |
| D34 | 2026-10-06 | Plan | A program can be tied to a location (`program.location_id`), which gives `start_load` its unit and lets plans be checked against available equipment. Programs are strength-only; runs are handled separately. |
| D35 | 2026-10-06 | Person | Body weight is logged **occasionally**; the latest value on or before a session is used for bodyweight exercises. |
| D36 | 2026-10-06 | Person | Measurement kind and unit are separate (`kind_id = 'bodyweight'`, `unit = 'lb'` default). Kinds are a lookup table; starts with body weight only. Unit must fit the kind (enforced). |
| D37 | 2026-10-06 | Person | `notes` dropped from person and measurement. **Nutrition is out of scope.** |
| D38 | 2026-10-06 | Person | Height is a **measurement** (`height`, in/cm), not a column; it's context for plan design, not used in any calculation. Person gains optional `sex` and `birth_year` for strength standards and plan design. |
| D39 | 2026-10-06 | Person | Birth year is recorded when known. |
| D40 | 2026-10-06 | Cardio | **Cardio is in**, so lifting and cardio can be queried together. `session.kind` is `strength` or `cardio`; activity (`run`, `mountain_bike`) and workout type (`easy`, `tempo`, `fartlek`, `intervals`, `strides`, `sprints`…) are lookup tables. |
| D41 | 2026-10-06 | Cardio | Entered **by hand or from a Strava screenshot**; no Strava API. Strava's activity id, when known, is `session.source_ref`. |
| D42 | 2026-10-06 | Cardio | Totals per session: distance + unit (`mi`/`km`/`m`) and moving time. Dropped: elapsed time, average HR, elevation, the separate Strava id column. |
| D43 | 2026-10-06 | Cardio | **Segments** (`cardio_segment`) cover splits and structured workouts with one shape: ordered rows with a role (`warmup`/`work`/`recovery`/`cooldown`/`steady`), distance and/or time. Totals stay authoritative; segments can be partial. Pace is derived. |
| D44 | 2026-10-06 | Calculated | Sessions are **summarized by weight** in order of use (`15×10,10 · 20×10`). Progression looks only at sets at the heaviest weight (`sets_at_top`, `min_reps_at_top`). |
| D45 | 2026-10-06 | Calculated | **Progression rules are per plan slot** (`prescription.progression_id` → `progression_rule` lookup: `double`, `linear`, `none`) with an optional `load_cap`. Views report facts; rule logic lives in code. Double progression is what *my* plan uses, not a global rule. |
| D46 | 2026-10-06 | Calculated | **Estimated max uses seven formulas** (Epley, Brzycki, Lombardi, O'Conner, Mayhew, Wathan, Lander) with mean and low–high spread, per set and per session. Sets over 12 reps get no estimate. |
| D47 | 2026-10-06 | Calculated | Volume, weekly sets per muscle **and volume per muscle** are all wanted (`v_weekly_muscle`). |
| D48 | 2026-10-06 | Calculated | Strength and cardio **aren't combined** in any view; they meet in queries. |
| D49 | 2026-10-06 | Change log | **Every write goes through the CLI** (person or agent) and is recorded in `change_set` / `change` (before and after values per row). `fit undo` reverses the **most recent** change set only. Doubles as an audit trail of agent edits. |
| D50 | 2026-10-06 | Change log | **Uniqueness counts only live rows** (partial unique indexes `WHERE deleted_at IS NULL`), so undone data can be re-logged. Setup tables without `deleted_at` are hard-deleted on undo. |
| D51 | 2026-10-06 | Exercise | `exercise_progression.start_ratio`: where a harder exercise starts, as a share of the **total** load on the old one, rounded to the **nearest** available weight. A ratio, not a weight, so the shipped catalog works in lb or kg; 0 = body weight. Reproduces the hand-picked Block 2 starts (2×25, 2×20, 40). |
| D52 | 2026-10-07 | Plan | **One current program per person** (partial unique index). `ends_on` is set when a program is actually replaced, never planned ahead. |
| D53 | 2026-10-07 | Plan | **Only exercise swaps create a new slot version**; other edits (sets, reps, tempo) update in place, with old values kept by the change log. |
| D54 | 2026-10-07 | Exercise | **No defaults on exercise fields**; `bodyweight_fraction` is required (0 = not counted). Built-ins are editable; catalog updates only insert new rows. |
| D55 | 2026-10-07 | Set | New set kind **`test`**: a one-off heavier try (e.g. 80 × 2 after two sets at 50). Like warmups, ignored by progression, volume and summaries; shown as `80×2(t)`. |

## Open questions

None open from the piece-by-piece review (2026-10-05/06); every earlier open question is in the
log above. Next: walk through the app's entry points and outputs (`docs/app.md`), which may
reopen pieces of this.

## Not modelled (on purpose)

| Thing | Why not | Would be |
| :-- | :-- | :-- |
| Nutrition | Out of scope; tracked elsewhere (D37) | — |
| RIR / RPE | Won't be logged (D3) | a column on `exercise_set` |
| Next-day soreness | Symptoms are during-activity only (D18) | a timing column on `symptom` |
| Week-specific plan changes | Not complex enough yet (D31) | a `prescription_override` table |
| Loadable bar + plate combinations | Not used yet (D27) | a plate inventory per location |
| Cardio plans (planned 4x800 paces) | Plans are strength-only (D34) | cardio prescriptions |
| Elevation, heart rate | Dropped from cardio (D42) | columns on `cardio_detail` |
| Rest times, supersets, sleep, photos | Not logged today | added tables or columns |

Each of these is an addition, not a change to existing tables.
