"""Build data/fitness.db with a made-up person, gym and six weeks of generated training history,
to check that the schema holds realistic data and answers real questions.

The history is synthetic: double progression simulated per exercise, plus a baseline session,
a mid-block exercise swap, warmup / rest-pause / test sets, symptoms and runs with segments.
No real person's data belongs here.

    uv run scripts/seed.py && sqlite3 data/fitness.db < scripts/queries.sql
"""

import uuid
from datetime import date, timedelta
from pathlib import Path

from fit import catalog
from fit import db as fitdb

ROOT = Path(__file__).parent.parent
DB = ROOT / "data" / "fitness.db"
GYM = "Home gym"


def new_id() -> str:
    return str(uuid.uuid7())


# ── the gym ──────────────────────────────────────────────────────────────────
STACK = [float(x) for x in range(10, 201, 10)]
EQUIPMENT = {  # key: (name, kind, loads, multiplier)
    "db": ("Dumbbells", "dumbbells", [float(x) for x in range(5, 31, 5)], 1.0),
    "ez": ("EZ-curl bar", "ez_bar", [100.0], 1.0),
    "bench": ("Adjustable bench", "adjustable_bench", [], 1.0),
    "lat": ("Lat pulldown station", "lat_pulldown_station", STACK, 1.0),
    "cable": ("Cable station", "cable_station", STACK, 1.0),
    "chest": ("Chest press machine", "chest_press_machine", STACK, 1.0),
    "legpress": ("Leg press machine", "leg_press_machine", STACK, 2.0),
}
USED = {  # catalog exercise -> the equipment it's done on here (None = body weight)
    "db_bench": "db", "incline_db": "db", "chest_press": "chest", "ohp": "db", "lat_raise": "db",
    "face_pull": "cable", "pulldown": "lat", "lp_row": "cable", "sa_db_row": "db", "sa_cable_row": "cable",
    "db_curl": "db", "pushdown": "cable", "goblet_box": "db", "leg_press": "legpress", "rev_lunge": "db",
    "bss": "db", "db_rdl": "db", "bstance_rdl": "db", "db_hip_thrust": "db", "lying_ham_curl": "db",
    "woodchop": "cable", "pallof": "cable",
}
NOTE_ALIASES = {"Seated Row": "lp_row"}  # how this person's notes spell things

# ── plans ────────────────────────────────────────────────────────────────────
BLOCK1_START, SWAP_ON, BLOCK2_START = date(2026, 3, 9), date(2026, 3, 23), date(2026, 4, 20)
# day -> (name, [(exercise, sets, rep_min, rep_max, start_load)]); C2 swaps from lying ham curl to hip thrust
BLOCK1 = {
    "A": ("Push & Quad", [("db_bench", 3, 8, 10, 25), ("leg_press", 3, 10, 12, 70), ("pulldown", 3, 10, 12, 70),
                          ("db_rdl", 3, 10, 12, 30), ("lat_raise", 3, 12, 15, 10)]),
    "B": ("Hinge & Pull", [("lp_row", 3, 10, 12, 50), ("rev_lunge", 3, 8, 10, 25), ("ohp", 3, 8, 10, 15),
                           ("db_curl", 3, 10, 12, 15), ("pushdown", 3, 12, 15, 50)]),
    "C": ("Incline & Hypertrophy", [("incline_db", 3, 8, 10, 25), ("lying_ham_curl", 3, 12, 15, 15),
                                    ("chest_press", 3, 10, 12, 50), ("sa_db_row", 3, 10, 12, 30),
                                    ("woodchop", 3, 12, 12, 40)]),
}
SWAP = ("C", 2, "db_hip_thrust", 15)
BLOCK2 = {
    "A": ("Push & Quad", [("chest_press", 3, 6, 10, 80), ("leg_press", 3, 10, 12, 110), ("pulldown", 3, 8, 12, 110),
                          ("bstance_rdl", 3, 8, 12, 25), ("lat_raise", 3, 12, 15, 15)]),
    "B": ("Hinge & Pull", [("lp_row", 3, 8, 12, 80), ("bss", 3, 8, 10, 20), ("ohp", 3, 8, 12, 25),
                           ("db_curl", 3, 8, 12, 25), ("face_pull", 2, 12, 15, 30)]),
}
SECOND_PERSON = {
    "A": ("Full Body A", [("chest_press", 3, 8, 12, 20), ("leg_press", 3, 10, 12, 50), ("db_rdl", 3, 10, 12, 10)]),
}


def progression(start: float, rep_min: int, rep_max: int, loads: list[float], sessions: int):
    """Simulated double progression: reps climb by one a session; at the top of the range the weight
    goes up a step (when there is one). The 2nd and 5th sessions' last set falls one short and nothing
    advances. Yields (load, [reps per set]) per session, for three sets."""
    load, reps = start, rep_min
    for n in range(sessions):
        short = n % 3 == 1
        yield load, [reps, reps, reps - 1 if short else reps]
        if short:
            continue
        heavier = [x for x in loads if x > load]
        if reps >= rep_max and heavier:
            load, reps = heavier[0], rep_min
        elif reps < rep_max:
            reps += 1


def build() -> None:
    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    db = fitdb.create(DB)  # schema + built-in catalog, same as `fit init`
    db.execute("BEGIN")

    loc = new_id()
    db.execute("INSERT INTO location (id, name) VALUES (?, ?)", (loc, GYM))
    eq = {}
    for key, (name, kind, loads, mult) in EQUIPMENT.items():
        eq[key] = new_id()
        db.execute("INSERT INTO equipment (id, location_id, kind_id, name, load_multiplier) VALUES (?,?,?,?,?)",
                   (eq[key], loc, kind, name, mult))
        db.executemany("INSERT INTO equipment_load VALUES (?,?)", [(eq[key], x) for x in loads])
    ex = {key: catalog.builtin_id(key) for key in catalog.EXERCISES}
    db.executemany("INSERT INTO exercise_alias VALUES (?,?)", [(a, ex[k]) for a, k in NOTE_ALIASES.items()])

    people = {"Sam": ("male", 1995, 165, 70), "Alex": ("female", 1997, 130, 66)}
    pid = {}
    for name, (sex, year, weight, height) in people.items():
        pid[name] = new_id()
        db.execute("INSERT INTO person (id, name, sex, birth_year) VALUES (?,?,?,?)", (pid[name], name, sex, year))
        db.executemany("INSERT INTO measurement (id, person_id, measured_on, kind_id, value, unit) VALUES (?,?,?,?,?,?)",
                       [(new_id(), pid[name], "2026-03-06", "bodyweight", weight, "lb"),
                        (new_id(), pid[name], "2026-03-06", "height", height, "in")])

    def program(person, name, starts, ends, days):
        prog = new_id()
        db.execute("INSERT INTO program (id, person_id, name, location_id, starts_on, ends_on) VALUES (?,?,?,?,?,?)",
                   (prog, person, name, loc, starts.isoformat(), ends.isoformat() if ends else None))
        slots = {}
        for label, (day_name, rx) in days.items():
            day = new_id()
            db.execute("INSERT INTO program_day (id, program_id, label, name) VALUES (?,?,?,?)", (day, prog, label, day_name))
            for pos, (key, sets, lo, hi, load) in enumerate(rx, 1):
                rid = new_id()
                db.execute("INSERT INTO prescription (id, program_day_id, position, exercise_id, sets, rep_min, rep_max, "
                           "start_load, active_from) VALUES (?,?,?,?,?,?,?,?,?)",
                           (rid, day, pos, ex[key], sets, lo, hi, load, starts.isoformat()))
                slots[(label, pos)] = (rid, day)
        return slots

    b1 = program(pid["Sam"], "Block 1", BLOCK1_START, BLOCK2_START - timedelta(days=1), BLOCK1)
    label, pos, new_key, new_load = SWAP
    old_rid, day_id = b1[(label, pos)]
    db.execute("UPDATE prescription SET active_until = ? WHERE id = ?", ((SWAP_ON - timedelta(days=1)).isoformat(), old_rid))
    swap_rid = new_id()
    db.execute("INSERT INTO prescription (id, program_day_id, position, exercise_id, sets, rep_min, rep_max, start_load, "
               "active_from) VALUES (?,?,?,?,3,12,15,?,?)", (swap_rid, day_id, pos, ex[new_key], new_load, SWAP_ON.isoformat()))
    program(pid["Sam"], "Block 2", BLOCK2_START, None, BLOCK2)
    program(pid["Alex"], "Block 1", BLOCK2_START, None, SECOND_PERSON)

    def session(person, day: date, program_day=None, kind="strength", ref=None):
        sid = new_id()
        db.execute("INSERT INTO session (id, person_id, kind, date, started_at, location_id, program_day_id, source, "
                   "source_ref) VALUES (?,?,?,?,?,?,?,'import',?)",
                   (sid, person, kind, day.isoformat(), f"{day.isoformat()}T18:00:00", loc if kind == "strength" else None,
                    program_day, ref))
        return sid

    def exercise(sid, pos, key, rid, sets, symptom=None):
        """sets: [(load, reps, kind, rest_pause_reps)]"""
        se = new_id()
        db.execute("INSERT INTO session_exercise (id, session_id, position, exercise_id, prescription_id, equipment_id) "
                   "VALUES (?,?,?,?,?,?)", (se, sid, pos, ex[key], rid, eq.get(USED.get(key, ""))))
        e = catalog.EXERCISES[key]
        side = "each" if e.laterality == "unilateral" else "both"
        for n, (load, reps, kind, rp) in enumerate(sets, 1):
            db.execute("INSERT INTO exercise_set (id, session_exercise_id, set_no, kind, side, load, implements, reps, "
                       "rest_pause_reps, to_failure) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (new_id(), se, n, kind, side, load, e.implements, reps, rp, 1 if rp else 0))
        if symptom:
            part, sym_side, kind, severity = symptom
            db.execute("INSERT INTO symptom (id, session_id, session_exercise_id, body_part_id, side, kind_id, severity) "
                       "VALUES (?,?,?,?,?,?,?)", (new_id(), sid, se, part, sym_side, kind, severity))

    # Baseline: off-plan, a warmup set, mixed weights, a symptom
    base = session(pid["Sam"], date(2026, 3, 6), ref="sample-notes.md:1")
    exercise(base, 1, "db_bench", None, [(20, 10, "warmup", None), (25, 10, "working", None), (25, 10, "working", None)])
    exercise(base, 2, "goblet_box", None, [(20, 10, "working", None)] * 3, symptom=("knee", "right", "clicking", "mild"))
    exercise(base, 3, "pulldown", None, [(60, 10, "working", None), (60, 10, "working", None), (70, 10, "working", None)])
    exercise(base, 4, "lat_raise", None, [(15, 12, "working", None), (10, 10, "working", None), (10, 10, "working", None)])

    # Six weeks of Block 1: A on Monday, B on Wednesday, C on Friday
    day_ids = {label: b1[(label, 1)][1] for label in BLOCK1}
    streams = {(label, pos): progression(load, lo, hi, EQUIPMENT[USED[key]][2], 6)
               for label, (_, rx) in BLOCK1.items() for pos, (key, _, lo, hi, load) in enumerate(rx, 1)}
    swap_stream = progression(new_load, 12, 15, EQUIPMENT["db"][2], 6)
    line = 2
    for week in range(6):
        for offset, label in ((0, "A"), (2, "B"), (4, "C")):
            day = BLOCK1_START + timedelta(days=7 * week + offset)
            sid = session(pid["Sam"], day, day_ids[label], ref=f"sample-notes.md:{line}")
            line += 1
            for pos, (key, *_rest) in enumerate(BLOCK1[label][1], 1):
                rid, stream = b1[(label, pos)][0], streams[(label, pos)]
                if (label, pos) == SWAP[:2] and day >= SWAP_ON:
                    key, rid, stream = new_key, swap_rid, swap_stream
                load, reps = next(stream)
                sets = [(load, r, "working", None) for r in reps]
                if label == "A" and pos == 1 and week == 3:      # kept going after failing: 10 + 2
                    sets[-1] = (load, reps[-1], "working", 2)
                if label == "A" and pos == 2 and week == 2:      # a one-off heavier try
                    sets.append((load + 50, 3, "test", None))
                exercise(sid, pos, key, rid, sets)

    # Runs: an easy one with a symptom, and intervals with segments
    run = session(pid["Sam"], date(2026, 3, 10), kind="cardio", ref="sample-strava-1")
    db.execute("INSERT INTO cardio_detail VALUES (?, 'run', 'easy', 3, 'mi', 1680)", (run,))
    db.execute("INSERT INTO symptom (id, session_id, body_part_id, side, kind_id, severity) VALUES (?,?,?,?,?,?)",
               (new_id(), run, "knee", "both", "soreness", "mild"))
    intervals = session(pid["Sam"], date(2026, 3, 17), kind="cardio", ref="sample-strava-2")
    db.execute("INSERT INTO cardio_detail VALUES (?, 'run', 'intervals', 4, 'mi', 1900)", (intervals,))
    reps = [x for t in (170, 168, 172, 165) for x in (("work", 800, "m", t), ("recovery", 400, "m", None))]
    segments = [("warmup", 1, "mi", 540), *reps[:-1], ("cooldown", 1, "mi", 560)]
    for n, (role, dist, unit, secs) in enumerate(segments, 1):
        db.execute("INSERT INTO cardio_segment (id, session_id, seq, role, distance, distance_unit, seconds) "
                   "VALUES (?,?,?,?,?,?,?)", (new_id(), intervals, n, role, dist, unit, secs))

    db.execute("COMMIT")
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("exercise", "prescription", "session", "session_exercise", "exercise_set", "symptom")}
    print(f"built {DB.name}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    build()
