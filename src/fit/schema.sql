-- Workout tracker schema, draft v0.1 (2026-10-05). Design notes: docs/schema.md.
--
-- Conventions
--   ids        TEXT UUIDv7 (time-ordered; safe to generate on a phone offline and merge later)
--   dates      TEXT 'YYYY-MM-DD' (local calendar date of the workout)
--   times      TEXT ISO-8601 with offset, e.g. '2026-10-05T18:30:00-05:00'
--   weights    REAL pounds, exactly as read off the equipment (per dumbbell, pin number x 10, ...)
--   audit      created_at / updated_at / deleted_at on everything a person edits; rows are never
--              hard-deleted, so a sync can tell "deleted" from "never existed"

PRAGMA foreign_keys = ON;

-- ───────────────────────────── people ─────────────────────────────

CREATE TABLE person (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    -- Optional; used for strength standards and plan design. Height is a measurement, not a column.
    sex         TEXT CHECK (sex IN ('female', 'male')),
    birth_year  INTEGER CHECK (birth_year BETWEEN 1900 AND 2100),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at  TEXT
);
CREATE UNIQUE INDEX person_name ON person (name) WHERE deleted_at IS NULL;

-- What can be measured. A lookup table so new kinds are an insert. Starts with body weight and height.
CREATE TABLE measurement_kind (
    id         TEXT PRIMARY KEY,    -- 'bodyweight', 'height'
    dimension  TEXT NOT NULL CHECK (dimension IN ('mass', 'length', 'rate'))
);

-- Body weight and other measurements over time, logged whenever (rarely is fine). Body weight
-- also feeds the effective load of bodyweight exercises: the latest value on or before the
-- session date is used (see v_set).
CREATE TABLE measurement (
    id           TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL REFERENCES person(id),
    measured_on  TEXT NOT NULL,
    kind_id      TEXT NOT NULL REFERENCES measurement_kind(id),
    value        REAL NOT NULL CHECK (value > 0),
    unit         TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg', 'in', 'cm', 'bpm')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at   TEXT
);

-- The unit has to fit the kind: body weight in lb or kg, never in cm.
CREATE TRIGGER measurement_unit_matches_kind
BEFORE INSERT ON measurement
WHEN NEW.unit NOT IN (
    SELECT u.value FROM measurement_kind k, json_each(CASE k.dimension
        WHEN 'mass' THEN '["lb","kg"]' WHEN 'length' THEN '["in","cm"]' ELSE '["bpm"]' END) u
    WHERE k.id = NEW.kind_id)
BEGIN
    SELECT RAISE(ABORT, 'unit does not match the measurement kind');
END;
CREATE INDEX measurement_person_kind_date ON measurement (person_id, kind_id, measured_on);

-- ───────────────────────────── places & equipment ─────────────────────────────

CREATE TABLE location (
    id     TEXT PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE
);

-- Generic kinds of equipment, shipped with the app like the exercise catalog: 'dumbbells',
-- 'adjustable_bench', 'cable_station', 'leg_press_machine'. Exercises name the kinds they need;
-- a location's equipment says which kinds it has. load_mechanism says how the weight is chosen.
CREATE TABLE equipment_kind (
    id              TEXT PRIMARY KEY,
    load_mechanism  TEXT NOT NULL CHECK (load_mechanism IN
                      ('dumbbell', 'stack', 'fixed', 'loadable', 'none'))   -- none: benches, bars to hang from
);

-- One real piece of equipment at a location: "the leg press", "the dumbbell rack".
CREATE TABLE equipment (
    id               TEXT PRIMARY KEY,
    location_id      TEXT NOT NULL REFERENCES location(id),
    kind_id          TEXT NOT NULL REFERENCES equipment_kind(id),
    name             TEXT NOT NULL,
    -- Every weight on this equipment is in this unit (what the plates/dumbbells say).
    unit             TEXT NOT NULL DEFAULT 'lb' CHECK (unit IN ('lb', 'kg')),
    -- Converts the number you read into force actually moved (a 2:1 leg press -> 2.0).
    load_multiplier  REAL NOT NULL DEFAULT 1.0 CHECK (load_multiplier > 0),
    UNIQUE (location_id, name)
);

-- The discrete loads this equipment can be set to, in equipment.unit. One shape for every case:
-- dumbbells 5..30, a stack 10..200, a fixed bar with one row. "Next step up" = next row.
-- Loadable bars (bar + plate combinations) are deferred; list combinations by hand for now.
CREATE TABLE equipment_load (
    equipment_id  TEXT NOT NULL REFERENCES equipment(id),
    load          REAL NOT NULL CHECK (load > 0),
    PRIMARY KEY (equipment_id, load)
);

-- ───────────────────────────── exercise catalog ─────────────────────────────

-- An exercise is a movement plus how it's loaded. Change the movement or the kind of load
-- (dumbbells, a bar, a cable) and it's a different exercise (DB RDL, B-Stance RDL, EZ-Bar RDL);
-- doing it on a different machine of the same kind isn't. Change only the execution (tempo) and it's
-- the same exercise with a modifier on the set. Flat vs incline press are different exercises; the
-- exact bench angle isn't tracked. Names are generic ("Machine Chest Press"), never a brand: the
-- user's own machine is equipment data.
-- Every field is required, with no defaults to fall back on: the catalog never holds a half-
-- described exercise. The CLI also requires at least one muscle, and either equipment needs or an
-- explicit "no equipment" (no exercise_equipment rows), when an exercise is added.
CREATE TABLE exercise (
    id                    TEXT PRIMARY KEY,     -- builtin rows have fixed ids, stable across installs
    name                  TEXT NOT NULL,
    -- builtin: ships with the app's starter catalog. user: added locally. Built-ins can be edited
    -- like any other row; catalog updates only ever add new built-ins, never overwrite existing ones.
    origin                TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('builtin', 'user')),
    movement_pattern      TEXT NOT NULL CHECK (movement_pattern IN
                            ('horizontal_push', 'vertical_push', 'horizontal_pull', 'vertical_pull',
                             'squat', 'lunge', 'hinge', 'hip_extension', 'knee_flexion',
                             'shoulder_isolation', 'arm_isolation', 'core', 'calf')),
    -- unilateral: one side at a time; reps are logged per side.
    laterality            TEXT NOT NULL CHECK (laterality IN ('bilateral', 'unilateral')),
    -- How a set is measured: by reps (most things) or by time (planks).
    measure               TEXT NOT NULL CHECK (measure IN ('reps', 'time')),
    -- How many implements are normally held (2 for a DB pair, 1 for goblet / single-arm / stack).
    default_implements    INTEGER NOT NULL CHECK (default_implements IN (0, 1, 2)),
    -- Rough share of body weight moved (push-up ~0.64). 0 = body weight not counted.
    bodyweight_fraction   REAL NOT NULL CHECK (bodyweight_fraction BETWEEN 0 AND 1),
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at            TEXT
);
CREATE UNIQUE INDEX exercise_name ON exercise (name) WHERE deleted_at IS NULL;

-- What an exercise needs. Rows with the same `slot` are alternatives (any one will do); different
-- slots are all required. Bulgarian split squat: slot 1 dumbbells, slot 2 flat OR adjustable bench.
-- Lat pulldown: slot 1 lat_pulldown_station OR cable_station. No rows = bodyweight only.
CREATE TABLE exercise_equipment (
    exercise_id        TEXT NOT NULL REFERENCES exercise(id),
    slot               INTEGER NOT NULL,
    equipment_kind_id  TEXT NOT NULL REFERENCES equipment_kind(id),
    PRIMARY KEY (exercise_id, slot, equipment_kind_id)
);

-- Every spelling seen in notes, so the importer maps "DB RDL" and "Dumbbell Romanian Deadlift"
-- to the same exercise.
CREATE TABLE exercise_alias (
    alias        TEXT PRIMARY KEY COLLATE NOCASE,
    exercise_id  TEXT NOT NULL REFERENCES exercise(id)
);

CREATE TABLE muscle (
    id     TEXT PRIMARY KEY,           -- 'chest', 'lats', 'quads', ...
    region TEXT NOT NULL CHECK (region IN ('upper', 'lower', 'core'))
);

-- Which muscles an exercise trains, and how much a working set counts toward each
-- (1.0 primary, 0.5 secondary is the usual convention for weekly-sets analytics).
CREATE TABLE exercise_muscle (
    exercise_id  TEXT NOT NULL REFERENCES exercise(id),
    muscle_id    TEXT NOT NULL REFERENCES muscle(id),
    set_credit   REAL NOT NULL CHECK (set_credit > 0 AND set_credit <= 1),
    PRIMARY KEY (exercise_id, muscle_id)
);

-- Directed graph of "do this when that one is capped / mastered".
CREATE TABLE exercise_progression (
    from_exercise_id  TEXT NOT NULL REFERENCES exercise(id),
    to_exercise_id    TEXT NOT NULL REFERENCES exercise(id),
    kind              TEXT NOT NULL CHECK (kind IN ('harder', 'easier', 'substitute')),
    -- Where to start the new exercise, as a share of the total load you were at on the old one
    -- (per-implement weight x implements), rounded to the nearest weight the equipment has. A ratio,
    -- not a weight, so the shipped catalog works in lb or kg. 0 = body weight only. NULL = no
    -- default; fall back to one step below.
    -- DB RDL 2x30 -> B-Stance RDL at 0.83 = 50 total = 2x25.
    start_ratio       REAL CHECK (start_ratio >= 0),
    PRIMARY KEY (from_exercise_id, to_exercise_id),
    CHECK (from_exercise_id <> to_exercise_id)
);

-- ───────────────────────────── plans (what we intend) ─────────────────────────────

-- How a slot decides when to add weight. The logic is code; this names it, so different plans and
-- goals can use different rules. 'double': hold the weight until every set at the top weight
-- reaches rep_max, then go up one step. 'linear': up one step every session. 'none': hold.
CREATE TABLE progression_rule (
    id  TEXT PRIMARY KEY
);

-- A strength plan. Runs aren't planned here (see docs/schema.md, piece 9).
CREATE TABLE program (
    id           TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL REFERENCES person(id),
    name         TEXT NOT NULL,         -- 'Block 2'
    -- The gym it's written for: gives start_load its unit and lets the app check that every
    -- exercise can be done there. NULL = not tied to a place.
    location_id  TEXT REFERENCES location(id),
    starts_on    TEXT NOT NULL,
    ends_on      TEXT,              -- set when it's actually replaced, never planned ahead
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at  TEXT
);
CREATE UNIQUE INDEX program_person_name ON program (person_id, name) WHERE deleted_at IS NULL;
-- A program is long-lived: exercises get swapped inside it as they cap out or get stale, rather
-- than starting a new program. One open-ended program per person at a time.
CREATE UNIQUE INDEX program_one_current ON program (person_id) WHERE ends_on IS NULL AND deleted_at IS NULL;

-- A day has no schedule: no weekday, no fixed order. Any day can be done on any date, in any
-- order. "What's next" is derived from the log (the day done longest ago), never stored.
CREATE TABLE program_day (
    id          TEXT PRIMARY KEY,
    program_id  TEXT NOT NULL REFERENCES program(id),
    label       TEXT NOT NULL,          -- 'A'
    name        TEXT,                   -- short label, 'Push & Quad'
    UNIQUE (program_id, label)
);

-- One planned exercise slot. Swapping the exercise ends the old row (active_until) and starts a
-- new one at the same position, so "what did I do in this slot, and when" stays answerable. Other
-- edits (sets, reps, tempo) update the row in place; the change log keeps the old values.
-- start_load is set by hand, or computed from history / a progression link when one exists; it
-- only matters for the first session, after which targets come from the log.
CREATE TABLE prescription (
    id               TEXT PRIMARY KEY,
    program_day_id   TEXT NOT NULL REFERENCES program_day(id),
    position         INTEGER NOT NULL,
    exercise_id      TEXT NOT NULL REFERENCES exercise(id),
    sets             INTEGER NOT NULL,
    rep_min          INTEGER,           -- NULL for "as many as possible"
    rep_max          INTEGER,
    seconds_min      INTEGER,           -- time-measured exercises
    seconds_max      INTEGER,
    start_load       REAL,              -- per implement, in the unit of the program location's equipment
    implements       INTEGER,           -- override exercise.default_implements
    tempo            TEXT,              -- 'ecc-pause-con-top' seconds, e.g. '3-1-1-0'
    target_rir       INTEGER,           -- instruction only ("stop 2 short"); RIR isn't logged
    progression_id   TEXT NOT NULL DEFAULT 'double' REFERENCES progression_rule(id),
    load_cap         REAL,              -- stop adding weight here, whatever the rule says
    active_from      TEXT NOT NULL,
    active_until     TEXT,              -- inclusive; NULL = still current
    CHECK (rep_min IS NULL OR rep_max IS NULL OR rep_min <= rep_max),
    CHECK (active_until IS NULL OR active_from <= active_until),
    UNIQUE (program_day_id, position, active_from)
);

-- ───────────────────────────── logs (what actually happened) ─────────────────────────────

-- One person, one lifting session or one cardio session. Two people training together = two unlinked sessions.
-- A workout that didn't happen has no row.
CREATE TABLE session (
    id              TEXT PRIMARY KEY,
    person_id       TEXT NOT NULL REFERENCES person(id),
    kind            TEXT NOT NULL CHECK (kind IN ('strength', 'cardio')),
    date            TEXT NOT NULL,
    started_at      TEXT,
    location_id     TEXT REFERENCES location(id),
    program_day_id  TEXT REFERENCES program_day(id),    -- NULL = off-plan
    -- How the record got here. source_ref points back at the original (a note's file:line, a
    -- Strava activity id), which makes re-importing the same thing a no-op instead of a duplicate.
    source          TEXT NOT NULL CHECK (source IN ('manual', 'import', 'strava')),
    source_ref      TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at      TEXT
);
CREATE UNIQUE INDEX session_source_ref ON session (source, source_ref) WHERE deleted_at IS NULL;
CREATE INDEX session_person_date ON session (person_id, date);

-- An exercise within a session: done (with sets below it) or skipped (no sets). One row per exercise
-- per session; all its sets hang off it, including weight changes mid-exercise.
-- Linking to the prescription is how plan-vs-actual works: same prescription, different
-- exercise_id = a substitution.
CREATE TABLE session_exercise (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES session(id),
    position         INTEGER,           -- order performed; NULL only for a skip
    exercise_id      TEXT NOT NULL REFERENCES exercise(id),
    prescription_id  TEXT REFERENCES prescription(id),
    equipment_id     TEXT REFERENCES equipment(id),     -- what it was done on; NULL = bodyweight
    status           TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('done', 'skipped')),
    skip_reason      TEXT CHECK (skip_reason IN ('pain', 'time', 'equipment', 'fatigue', 'other')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at       TEXT,
    CHECK (status = 'skipped' OR position IS NOT NULL),
    -- only planned work can be skipped, and a skip always says why; a done row has no reason
    CHECK ((status = 'done' AND skip_reason IS NULL)
        OR (status = 'skipped' AND skip_reason IS NOT NULL AND prescription_id IS NOT NULL))
);
CREATE UNIQUE INDEX session_exercise_once ON session_exercise (session_id, exercise_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX session_exercise_position ON session_exercise (session_id, position) WHERE deleted_at IS NULL;
CREATE INDEX session_exercise_exercise ON session_exercise (exercise_id);

CREATE TABLE exercise_set (
    id                   TEXT PRIMARY KEY,
    session_exercise_id  TEXT NOT NULL REFERENCES session_exercise(id),
    set_no               INTEGER NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'working'
                           -- test: a one-off heavier try ("80 x 2"); like warmups, ignored by progression
                           CHECK (kind IN ('warmup', 'working', 'backoff', 'test')),
    -- both: bilateral. each: same reps done on each side. left/right: sides logged separately.
    side                 TEXT NOT NULL DEFAULT 'both'
                           CHECK (side IN ('both', 'each', 'left', 'right')),
    load                 REAL,          -- per implement, as read, in equipment.unit. NULL = bodyweight only.
    implements           INTEGER NOT NULL DEFAULT 1 CHECK (implements IN (0, 1, 2)),
    reps                 INTEGER CHECK (reps >= 0),        -- clean reps, up to the first failure
    -- Reps squeezed out after failing and a short rest, still part of this set ("10, fail, +2").
    -- Count toward volume; progression and e1RM use `reps` only.
    rest_pause_reps      INTEGER CHECK (rest_pause_reps > 0),
    seconds              INTEGER CHECK (seconds >= 0),
    tempo                TEXT,          -- overrides prescription.tempo
    to_failure           INTEGER NOT NULL DEFAULT 0 CHECK (to_failure IN (0, 1)),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at           TEXT,
    CHECK (reps IS NOT NULL OR seconds IS NOT NULL),    -- an unlogged set is no row, not a zero
    CHECK (rest_pause_reps IS NULL OR (reps IS NOT NULL AND to_failure = 1))  -- extra reps imply a failure
);
CREATE UNIQUE INDEX exercise_set_no ON exercise_set (session_exercise_id, set_no, side) WHERE deleted_at IS NULL;

-- A skipped exercise has no sets.
CREATE TRIGGER exercise_set_not_on_skipped
BEFORE INSERT ON exercise_set
WHEN (SELECT status FROM session_exercise WHERE id = NEW.session_exercise_id) = 'skipped'
BEGIN
    SELECT RAISE(ABORT, 'cannot log sets on a skipped exercise');
END;

-- Vocabularies expected to grow are lookup tables, not CHECK lists: adding "numbness" or "foot"
-- later is an INSERT, not a table rebuild.
CREATE TABLE body_part (
    id      TEXT PRIMARY KEY,     -- 'knee', 'lower_back'
    paired  INTEGER NOT NULL CHECK (paired IN (0, 1))   -- has a left and a right
);
CREATE TABLE symptom_kind (
    id  TEXT PRIMARY KEY          -- 'pain', 'clicking'
);

-- Something that went wrong during an exercise or a run. Feeling fine is the default and isn't
-- logged. Exists for the pain rule: moderate-or-worse pain on an exercise holds its weight.
CREATE TABLE symptom (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES session(id),
    session_exercise_id  TEXT REFERENCES session_exercise(id),   -- NULL = the session as a whole (a run)
    body_part_id         TEXT NOT NULL REFERENCES body_part(id),
    side                 TEXT CHECK (side IN ('left', 'right', 'both')),  -- NULL for unpaired parts
    kind_id              TEXT NOT NULL REFERENCES symptom_kind(id),
    severity             TEXT NOT NULL CHECK (severity IN ('mild', 'moderate', 'severe')),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at           TEXT
);

-- Paired parts (knee) need a side; unpaired parts (lower back) can't have one.
CREATE TRIGGER symptom_side_matches_body_part
BEFORE INSERT ON symptom
WHEN (SELECT paired FROM body_part WHERE id = NEW.body_part_id) <> (NEW.side IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'side is required for paired body parts and not allowed otherwise');
END;

-- ───────────────────────────── cardio ─────────────────────────────
-- Runs, rides, and structured workouts Strava can't represent (4x800, fartlek, sprints).
-- Entered by hand or from a Strava screenshot; Strava's activity id goes in session.source_ref.

CREATE TABLE cardio_activity (
    id  TEXT PRIMARY KEY          -- 'run', 'mountain_bike'
);

-- What kind of workout it was. Mostly for runs; NULL when it was just a run or ride.
CREATE TABLE cardio_workout_type (
    id  TEXT PRIMARY KEY          -- 'easy', 'long', 'tempo', 'fartlek', 'intervals', 'strides', 'sprints'
);

-- One row per cardio session: the totals, as Strava or the watch reports them. Totals are
-- authoritative; segments below may cover only part of the session (e.g. just the timed reps).
CREATE TABLE cardio_detail (
    session_id       TEXT PRIMARY KEY REFERENCES session(id),
    activity_id      TEXT NOT NULL REFERENCES cardio_activity(id),
    workout_type_id  TEXT REFERENCES cardio_workout_type(id),
    distance         REAL CHECK (distance > 0),
    distance_unit    TEXT CHECK (distance_unit IN ('mi', 'km', 'm')),
    moving_seconds   INTEGER CHECK (moving_seconds > 0),
    CHECK ((distance IS NULL) = (distance_unit IS NULL))
);

-- The pieces of a cardio session, in order. The same shape covers:
--   mile splits      steady 1 mi, steady 1 mi, steady 0.3 mi
--   4x800            warmup 1 mi | work 800 m, recovery 400 m (x4) | cooldown 1 mi
--   fartlek          work 60 s, recovery 60 s, ...   (time only, no distance)
--   sprints          work 100 m, recovery 90 s, ...
-- Each segment has a distance, a time, or both; pace is derived.
CREATE TABLE cardio_segment (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES session(id),
    seq            INTEGER NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('warmup', 'work', 'recovery', 'cooldown', 'steady')),
    distance       REAL CHECK (distance > 0),
    distance_unit  TEXT CHECK (distance_unit IN ('mi', 'km', 'm')),
    seconds        REAL CHECK (seconds > 0),        -- REAL: sprint times have tenths
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at     TEXT,
    CHECK (distance IS NOT NULL OR seconds IS NOT NULL),
    CHECK ((distance IS NULL) = (distance_unit IS NULL))
);
CREATE UNIQUE INDEX cardio_segment_seq ON cardio_segment (session_id, seq) WHERE deleted_at IS NULL;

-- ───────────────────────────── change log ─────────────────────────────
-- Every write goes through the CLI (person or agent) and is recorded here, which is what makes
-- `fit undo` exact and gives an audit trail of everything an agent has changed. Rows with a
-- deleted_at are never hard-deleted (a delete is an update setting it); setup tables without one
-- (equipment, plan slots, lookups) are.
-- `fit undo` reverses the most recent change set that isn't an undo and hasn't been undone:
-- updated rows get their before-values back; inserted rows get deleted_at, or are removed if the
-- table has none. Uniqueness only counts live rows, so undone data can be logged again.
CREATE TABLE change_set (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    command     TEXT NOT NULL,          -- the CLI command name: 'log', 'edit', 'undo'
    actor       TEXT NOT NULL CHECK (actor IN ('person', 'agent')),
    undoes_id   TEXT REFERENCES change_set(id),     -- set on an undo: the change set it reversed
    CHECK ((command = 'undo') = (undoes_id IS NOT NULL))
);
CREATE UNIQUE INDEX change_set_undone_once ON change_set (undoes_id);

CREATE TABLE change (
    change_set_id  TEXT NOT NULL REFERENCES change_set(id),
    seq            INTEGER NOT NULL,
    table_name     TEXT NOT NULL,
    row_key        TEXT NOT NULL CHECK (json_valid(row_key)),   -- primary key as JSON: {"id": "…"}
    op             TEXT NOT NULL CHECK (op IN ('insert', 'update', 'delete')),   -- delete: setup tables only
    before_json    TEXT CHECK (before_json IS NULL OR json_valid(before_json)),   -- NULL for an insert
    after_json     TEXT CHECK (after_json IS NULL OR json_valid(after_json)),     -- NULL for a delete
    PRIMARY KEY (change_set_id, seq),
    CHECK ((op = 'insert') = (before_json IS NULL)),
    CHECK ((op = 'delete') = (after_json IS NULL))
);
CREATE INDEX change_row ON change (table_name, row_key);

-- ───────────────────────────── derived (never stored) ─────────────────────────────

-- Every live set with its effective load and volume. All analytics build on this.
CREATE VIEW v_set AS
SELECT
    st.id                                   AS set_id,
    s.id                                    AS session_id,
    s.person_id,
    s.date,
    se.id                                   AS session_exercise_id,
    se.prescription_id,
    e.id                                    AS exercise_id,
    e.name                                  AS exercise,
    st.set_no,
    st.kind,
    st.side,
    st.load,
    eq.unit,
    st.implements,
    st.reps,
    st.rest_pause_reps,
    st.to_failure,
    COALESCE(st.tempo, p.tempo)             AS tempo,     -- the set's, else the plan's
    st.seconds,
    se.equipment_id,
    -- external load actually moved: per-implement x count x pulley ratio, plus body weight share
    -- always reported in lb, whatever the equipment's unit
    COALESCE(st.load, 0) * MAX(st.implements, 1) * COALESCE(eq.load_multiplier, 1.0)
      * CASE eq.unit WHEN 'kg' THEN 2.20462 ELSE 1.0 END
      + COALESCE(e.bodyweight_fraction * (
            SELECT m.value * CASE m.unit WHEN 'kg' THEN 2.20462 ELSE 1.0 END FROM measurement m
            WHERE m.person_id = s.person_id AND m.kind_id = 'bodyweight'
              AND m.measured_on <= s.date AND m.deleted_at IS NULL
            ORDER BY m.measured_on DESC LIMIT 1), 0)                AS effective_lb,
    CASE st.side WHEN 'each' THEN 2 ELSE 1 END                     AS side_factor
FROM exercise_set st
JOIN session_exercise se ON se.id = st.session_exercise_id
JOIN session s           ON s.id = se.session_id
JOIN exercise e          ON e.id = se.exercise_id
LEFT JOIN equipment eq   ON eq.id = se.equipment_id
LEFT JOIN prescription p ON p.id = se.prescription_id
WHERE st.deleted_at IS NULL AND se.deleted_at IS NULL AND s.deleted_at IS NULL;

-- One row per exercise per session: the facts progression rules work from. The rules themselves
-- (double progression, load caps, ...) live in code and vary by plan; this view just reports.
CREATE VIEW v_exercise_session AS
WITH w AS (
    SELECT v.*, MAX(v.load) OVER (PARTITION BY v.session_exercise_id) AS top_load
    FROM v_set v WHERE v.kind = 'working'
),
by_load AS (    -- "15×10,10" per distinct weight, in the order the weights were first used
    SELECT session_exercise_id, load, MIN(set_no) AS first_set,
           CASE WHEN load IS NULL THEN 'BW' ELSE printf('%g', load) END
             || '×' || group_concat(reps, ',' ORDER BY set_no, side) AS part
    FROM w GROUP BY session_exercise_id, load
)
SELECT
    w.person_id,
    w.date,
    w.session_id,
    w.session_exercise_id,
    w.prescription_id,
    w.exercise_id,
    w.exercise,
    w.equipment_id,
    -- Weights done at different tempos aren't comparable; progress queries should compare like with like.
    MAX(w.tempo)                                        AS tempo,
    MAX(w.unit)                                         AS unit,
    COUNT(*)                                            AS working_sets,
    (SELECT group_concat(b.part, ' · ' ORDER BY b.first_set) FROM by_load b
      WHERE b.session_exercise_id = w.session_exercise_id)          AS sets_by_load,  -- '15×10,10 · 20×10'
    MAX(w.top_load)                                     AS top_load,         -- in unit
    SUM(w.load IS w.top_load)                           AS sets_at_top,
    MIN(CASE WHEN w.load IS w.top_load THEN w.reps END) AS min_reps_at_top,  -- clean reps
    SUM(CASE WHEN w.load IS w.top_load THEN w.reps END) AS reps_at_top,      -- clean reps, all sets at top
    (SELECT w2.reps FROM w w2 WHERE w2.session_exercise_id = w.session_exercise_id
       AND w2.load IS w2.top_load ORDER BY w2.set_no, w2.reps LIMIT 1)            AS first_reps_at_top,
    SUM(w.reps)                                         AS total_reps,       -- clean reps only
    SUM(COALESCE(w.rest_pause_reps, 0))                 AS rest_pause_reps,
    SUM(w.effective_lb * (w.reps + COALESCE(w.rest_pause_reps, 0)) * w.side_factor) AS volume_lb
FROM w
GROUP BY w.session_exercise_id;

-- Estimated one-rep max for every working set by seven published formulas, so they can be
-- compared rather than trusting one. Per implement, in the equipment's unit, clean reps only.
-- All of them degrade past ~10-12 reps, so sets above 12 get no estimate.
CREATE VIEW v_e1rm_set AS
SELECT v.set_id, v.session_exercise_id, v.person_id, v.date, v.exercise_id, v.exercise, v.unit,
       v.load, v.reps,
       v.load * (1 + v.reps / 30.0)                              AS epley,
       v.load * 36.0 / (37 - v.reps)                             AS brzycki,
       v.load * pow(v.reps, 0.10)                                AS lombardi,
       v.load * (1 + 0.025 * v.reps)                             AS oconner,
       100 * v.load / (52.2 + 41.9 * exp(-0.055 * v.reps))       AS mayhew,
       100 * v.load / (48.8 + 53.8 * exp(-0.075 * v.reps))       AS wathan,
       100 * v.load / (101.3 - 2.67123 * v.reps)                 AS lander
FROM v_set v
WHERE v.kind = 'working' AND v.load IS NOT NULL AND v.reps BETWEEN 1 AND 12;

-- Best estimate per formula per session, plus how much the formulas disagree.
CREATE VIEW v_e1rm_session AS
WITH best AS (
    SELECT session_exercise_id, person_id, date, exercise_id, exercise, unit,
           MAX(epley) AS epley, MAX(brzycki) AS brzycki, MAX(lombardi) AS lombardi,
           MAX(oconner) AS oconner, MAX(mayhew) AS mayhew, MAX(wathan) AS wathan, MAX(lander) AS lander
    FROM v_e1rm_set GROUP BY session_exercise_id
)
SELECT *,
       (epley + brzycki + lombardi + oconner + mayhew + wathan + lander) / 7.0  AS mean,
       MIN(epley, brzycki, lombardi, oconner, mayhew, wathan, lander)          AS low,
       MAX(epley, brzycki, lombardi, oconner, mayhew, wathan, lander)          AS high
FROM best;

-- Exercises whose most recent session was at the equipment's highest load with the top of the
-- rep range reached on every set at that load: double progression has nowhere left to go.
CREATE VIEW v_capped AS
WITH latest AS (
    SELECT es.*, p.rep_max,
           ROW_NUMBER() OVER (PARTITION BY es.person_id, es.exercise_id ORDER BY es.date DESC) AS rn
    FROM v_exercise_session es
    LEFT JOIN prescription p ON p.id = es.prescription_id
)
SELECT l.person_id, l.exercise, l.date, l.top_load, l.unit, l.sets_by_load, l.rep_max,
       (SELECT MAX(load) FROM equipment_load el WHERE el.equipment_id = l.equipment_id) AS max_load
FROM latest l
WHERE l.rn = 1
  AND l.top_load >= (SELECT MAX(load) FROM equipment_load el WHERE el.equipment_id = l.equipment_id)
  AND l.min_reps_at_top >= l.rep_max;

-- Per muscle per ISO week (Mon-Sun): working sets and volume, both weighted by set_credit.
CREATE VIEW v_weekly_muscle AS
SELECT v.person_id,
       strftime('%G-W%V', v.date)   AS week,
       em.muscle_id                 AS muscle,
       -- a unilateral set done on each side is still one set for that muscle
       SUM(em.set_credit)           AS sets,
       SUM(em.set_credit * v.effective_lb * (v.reps + COALESCE(v.rest_pause_reps, 0)) * v.side_factor)
                                    AS volume_lb
FROM v_set v
JOIN exercise_muscle em ON em.exercise_id = v.exercise_id
WHERE v.kind = 'working'
GROUP BY v.person_id, week, em.muscle_id;

-- Distances in miles, whatever they were entered in.
CREATE VIEW v_cardio_segment AS
SELECT cs.*,
       cs.distance * CASE cs.distance_unit WHEN 'km' THEN 0.621371 WHEN 'm' THEN 0.000621371 ELSE 1.0 END
                                                AS distance_mi,
       cs.seconds / (cs.distance * CASE cs.distance_unit WHEN 'km' THEN 0.621371
                                   WHEN 'm' THEN 0.000621371 ELSE 1.0 END) AS sec_per_mi
FROM cardio_segment cs
WHERE cs.deleted_at IS NULL;

CREATE VIEW v_cardio AS
SELECT s.id AS session_id, s.person_id, s.date, cd.activity_id, cd.workout_type_id,
       cd.distance * CASE cd.distance_unit WHEN 'km' THEN 0.621371 WHEN 'm' THEN 0.000621371 ELSE 1.0 END
                                                AS distance_mi,
       cd.moving_seconds,
       cd.moving_seconds / (cd.distance * CASE cd.distance_unit WHEN 'km' THEN 0.621371
                            WHEN 'm' THEN 0.000621371 ELSE 1.0 END) AS sec_per_mi
FROM session s
JOIN cardio_detail cd ON cd.session_id = s.id
WHERE s.deleted_at IS NULL;
