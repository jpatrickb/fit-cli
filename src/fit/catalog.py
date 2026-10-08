"""The built-in catalog that ships with fit and is loaded by `fit init`.

Built-in exercises get fixed ids (uuid5 of their key), so they're the same on every install and
future catalog updates can add to an existing database without clashing (D21). Updates only ever
add rows; they never overwrite a built-in someone has edited (D54).
"""

import uuid
from dataclasses import dataclass, field

NAMESPACE = uuid.UUID("525072d9-2a27-43db-8187-121678b32d38")


def builtin_id(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"exercise:{key}"))


# equipment kind -> how its weight is chosen
EQUIPMENT_KINDS = {
    "dumbbells": "dumbbell",
    "ez_bar": "fixed",
    "barbell": "loadable",
    "flat_bench": "none",
    "adjustable_bench": "none",
    "pull_up_bar": "none",
    "cable_rope": "none",            # rope attachment for a cable station
    "cable_station": "stack",
    "lat_pulldown_station": "stack",
    "chest_press_machine": "stack",
    "leg_press_machine": "stack",
}

MUSCLES = {
    "chest": "upper", "front_delts": "upper", "side_delts": "upper", "rear_delts": "upper",
    "triceps": "upper", "biceps": "upper", "lats": "upper", "upper_back": "upper",
    "quads": "lower", "hamstrings": "lower", "glutes": "lower", "calves": "lower",
    "abs": "core", "obliques": "core",
}

BODY_PARTS = {  # part -> has a left and right
    "knee": 1, "shoulder": 1, "wrist": 1, "elbow": 1, "hip": 1, "ankle": 1,
    "lower_back": 0, "neck": 0,
}

SYMPTOM_KINDS = ["pain", "clicking", "soreness", "tightness"]
MEASUREMENT_KINDS = {"bodyweight": "mass", "height": "length"}
CARDIO_ACTIVITIES = ["run", "mountain_bike"]
CARDIO_WORKOUT_TYPES = ["easy", "long", "tempo", "fartlek", "intervals", "strides", "sprints", "hills", "race"]
PROGRESSION_RULES = ["double", "linear", "none"]

ANY_BENCH = ["flat_bench", "adjustable_bench"]


@dataclass(frozen=True)
class Exercise:
    name: str
    pattern: str
    laterality: str
    measure: str
    implements: int
    bodyweight_fraction: float
    muscles: dict[str, float]
    requires: list[list[str]]  # slots; kinds within a slot are alternatives
    aliases: list[str] = field(default_factory=list)


def _ex(name, pattern, laterality, implements, muscles, requires, aliases=(), measure="reps", bw=0.0):
    return Exercise(name, pattern, laterality, measure, implements, bw, muscles, requires, list(aliases))


PUSH = {"chest": 1, "triceps": .5, "front_delts": .5}
ROW = {"lats": 1, "upper_back": .5, "biceps": .5}
HINGE = {"hamstrings": 1, "glutes": 1}
BRIDGE = {"glutes": 1, "hamstrings": .5}

EXERCISES: dict[str, Exercise] = {
    "db_bench": _ex("DB Bench Press", "horizontal_push", "bilateral", 2, PUSH,
                    [["dumbbells"], ANY_BENCH], ["Dumbbell Bench Press"]),
    "incline_db": _ex("Incline DB Press", "horizontal_push", "bilateral", 2, PUSH,
                      [["dumbbells"], ["adjustable_bench"]], ["Incline DB Bench Press", "Incline Dumbbell Press"]),
    "chest_press": _ex("Machine Chest Press", "horizontal_push", "bilateral", 1, PUSH,
                       [["chest_press_machine"]], ["Chest Press"]),
    "pushup_db": _ex("Push-Up on DB Handles", "horizontal_push", "bilateral", 0, PUSH,
                     [["dumbbells"]], bw=.64),
    "incline_pushup": _ex("Incline Push-Up", "horizontal_push", "bilateral", 0, PUSH, [ANY_BENCH], bw=.45),
    "ohp": _ex("Standing DB Overhead Press", "vertical_push", "bilateral", 2, {"front_delts": 1, "triceps": .5},
               [["dumbbells"]], ["DB Overhead Press", "Dumbbell Shoulder Press"]),
    "lat_raise": _ex("DB Lateral Raise", "shoulder_isolation", "bilateral", 2, {"side_delts": 1},
                     [["dumbbells"]], ["DB Lateral Raises", "Lateral Raise"]),
    "face_pull": _ex("Face Pull", "shoulder_isolation", "bilateral", 1, {"rear_delts": 1, "upper_back": .5},
                     [["cable_station"], ["cable_rope"]]),
    "rear_delt_fly": _ex("Chest-Supported DB Rear Delt Fly", "shoulder_isolation", "bilateral", 2,
                         {"rear_delts": 1, "upper_back": .5}, [["dumbbells"], ["adjustable_bench"]],
                         ["Rear Delt Fly", "Incline Rear Delt Fly", "Chest-Supported Reverse Fly"]),
    "cable_rear_delt_fly": _ex("Cable Rear Delt Fly", "shoulder_isolation", "unilateral", 1,
                               {"rear_delts": 1, "upper_back": .5}, [["cable_station"]], ["Cable Reverse Fly"]),
    "pulldown": _ex("Lat Pulldown", "vertical_pull", "bilateral", 1, {"lats": 1, "biceps": .5, "upper_back": .5},
                    [["lat_pulldown_station", "cable_station"]]),
    "lp_row": _ex("Low Pulley Seated Row", "horizontal_pull", "bilateral", 1,
                  {"upper_back": 1, "lats": .5, "biceps": .5}, [["cable_station"]], ["Seated Cable Row"]),
    "sa_db_row": _ex("Single-Arm DB Row", "horizontal_pull", "unilateral", 1, ROW,
                     [["dumbbells"], ANY_BENCH], ["Single Arm DB Row", "One-Arm Dumbbell Row"]),
    "sa_cable_row": _ex("Single-Arm Cable Row", "horizontal_pull", "unilateral", 1, ROW, [["cable_station"]]),
    "db_curl": _ex("DB Curl", "arm_isolation", "bilateral", 2, {"biceps": 1},
                   [["dumbbells"]], ["DB Bicep Curls", "Dumbbell Curl"]),
    "pushdown": _ex("Tricep Pushdown", "arm_isolation", "bilateral", 1, {"triceps": 1},
                    [["cable_station"]], ["Tricep Pushdowns", "Cable Pushdown"]),
    "goblet_box": _ex("Goblet Box Squat", "squat", "bilateral", 1, {"quads": 1, "glutes": .5},
                      [["dumbbells"], ANY_BENCH]),
    "leg_press": _ex("Machine Leg Press", "squat", "bilateral", 1, {"quads": 1, "glutes": .5},
                     [["leg_press_machine"]], ["Leg Press"]),
    "rev_lunge": _ex("DB Reverse Lunge", "lunge", "unilateral", 2, {"quads": 1, "glutes": 1},
                     [["dumbbells"]], ["DB Reverse Lunges", "Reverse Lunge"]),
    "bss": _ex("Bulgarian Split Squat", "lunge", "unilateral", 2, {"quads": 1, "glutes": 1},
               [["dumbbells"], ANY_BENCH]),
    "db_rdl": _ex("DB RDL", "hinge", "bilateral", 2, HINGE,
                  [["dumbbells"]], ["Dumbbell Romanian Deadlift", "DB Romanian Deadlift"]),
    "bstance_rdl": _ex("B-Stance RDL", "hinge", "unilateral", 2, HINGE, [["dumbbells"]], ["Kickstand RDL"]),
    "sl_rdl": _ex("Single-Leg RDL", "hinge", "unilateral", 2, HINGE, [["dumbbells"]]),
    "ez_rdl": _ex("EZ-Bar RDL", "hinge", "bilateral", 1, HINGE, [["ez_bar"]]),
    "db_hip_thrust": _ex("DB Hip Thrust", "hip_extension", "bilateral", 1, BRIDGE,
                         [["dumbbells"], ANY_BENCH], ["Dumbbell Hip Thrust"]),
    "sl_hip_thrust": _ex("Single-Leg Hip Thrust", "hip_extension", "unilateral", 0, BRIDGE, [ANY_BENCH]),
    "glute_bridge": _ex("Glute Bridge", "hip_extension", "bilateral", 0, BRIDGE, []),
    "lying_ham_curl": _ex("DB Lying Hamstring Curl", "knee_flexion", "bilateral", 1, {"hamstrings": 1},
                          [["dumbbells"], ANY_BENCH], ["Dumbbell Lying Hamstring Curls"]),
    "woodchop": _ex("Cable Woodchopper", "core", "unilateral", 1, {"obliques": 1},
                    [["cable_station"]], ["Woodchopper", "Wood Chopper"]),
    "pallof": _ex("Pallof Press", "core", "unilateral", 1, {"obliques": 1, "abs": .5}, [["cable_station"]]),
    "side_plank": _ex("Side Plank", "core", "unilateral", 0, {"obliques": 1}, [], measure="time"),
    "sl_calf": _ex("Single-Leg Calf Raise", "calf", "unilateral", 0, {"calves": 1}, []),
}

# (from, to, kind, start_ratio): start_ratio is a share of the total load on the old exercise (D51)
PROGRESSIONS = [
    ("db_rdl", "bstance_rdl", "harder", 0.83),
    ("bstance_rdl", "sl_rdl", "harder", 0.67),
    ("bstance_rdl", "ez_rdl", "harder", 1.67),
    ("rev_lunge", "bss", "harder", 0.67),
    ("db_hip_thrust", "sl_hip_thrust", "harder", 0.0),
    ("sa_db_row", "sa_cable_row", "harder", 1.33),
    ("glute_bridge", "db_hip_thrust", "harder", None),
    ("incline_pushup", "pushup_db", "harder", None),
    ("db_bench", "chest_press", "substitute", None),
    ("goblet_box", "leg_press", "substitute", None),
    ("face_pull", "rear_delt_fly", "substitute", None),
    ("face_pull", "cable_rear_delt_fly", "substitute", None),
]


def load(conn) -> None:
    """Insert the whole catalog into an empty database (part of `fit init`, not undoable)."""
    sync(conn)


def missing(conn) -> bool:
    """Is anything in this catalog absent from the database? Cheap enough to check on every open."""
    have = {r[0] for r in conn.execute("SELECT id FROM exercise WHERE origin = 'builtin'")}
    if any(builtin_id(k) not in have for k in EXERCISES):
        return True
    for table, ids in _LOOKUPS.items():
        present = {r[0] for r in conn.execute(f"SELECT id FROM {table}")}
        if not set(ids()) <= present:
            return True
    pairs = {(r[0], r[1]) for r in conn.execute("SELECT from_exercise_id, to_exercise_id FROM exercise_progression")}
    return any((builtin_id(a), builtin_id(b)) not in pairs for a, b, _, _ in PROGRESSIONS)


def sync(conn) -> None:
    """Add whatever in the catalog the database doesn't have yet (D21). Only ever adds: an existing
    built-in a user has edited, or a vocabulary entry, is never touched (D54). Not undoable, like init."""
    conn.executemany("INSERT OR IGNORE INTO equipment_kind VALUES (?,?)", EQUIPMENT_KINDS.items())
    conn.executemany("INSERT OR IGNORE INTO muscle VALUES (?,?)", MUSCLES.items())
    conn.executemany("INSERT OR IGNORE INTO body_part VALUES (?,?)", BODY_PARTS.items())
    conn.executemany("INSERT OR IGNORE INTO symptom_kind VALUES (?)", [(k,) for k in SYMPTOM_KINDS])
    conn.executemany("INSERT OR IGNORE INTO measurement_kind VALUES (?,?)", MEASUREMENT_KINDS.items())
    conn.executemany("INSERT OR IGNORE INTO cardio_activity VALUES (?)", [(a,) for a in CARDIO_ACTIVITIES])
    conn.executemany("INSERT OR IGNORE INTO cardio_workout_type VALUES (?)", [(t,) for t in CARDIO_WORKOUT_TYPES])
    conn.executemany("INSERT OR IGNORE INTO progression_rule VALUES (?)", [(r,) for r in PROGRESSION_RULES])
    existing = {r[0] for r in conn.execute("SELECT id FROM exercise")}
    for key, e in EXERCISES.items():
        eid = builtin_id(key)
        if eid in existing:
            continue
        conn.execute(
            "INSERT INTO exercise (id, name, origin, movement_pattern, laterality, measure, "
            "default_implements, bodyweight_fraction) VALUES (?,?,'builtin',?,?,?,?,?)",
            (eid, e.name, e.pattern, e.laterality, e.measure, e.implements, e.bodyweight_fraction))
        conn.executemany("INSERT INTO exercise_muscle VALUES (?,?,?)",
                         [(eid, m, c) for m, c in e.muscles.items()])
        conn.executemany("INSERT INTO exercise_equipment VALUES (?,?,?)",
                         [(eid, n, k) for n, kinds in enumerate(e.requires, 1) for k in kinds])
        conn.executemany("INSERT OR IGNORE INTO exercise_alias VALUES (?,?)",  # a user alias may already own a name
                         [(a, eid) for a in {a.lower(): a for a in (e.name, *e.aliases)}.values()])
    present = {r[0] for r in conn.execute("SELECT id FROM exercise")}
    conn.executemany(
        "INSERT OR IGNORE INTO exercise_progression (from_exercise_id, to_exercise_id, kind, start_ratio) VALUES (?,?,?,?)",
        [(builtin_id(a), builtin_id(b), k, r) for a, b, k, r in PROGRESSIONS
         if builtin_id(a) in present and builtin_id(b) in present])


_LOOKUPS = {
    "equipment_kind": lambda: EQUIPMENT_KINDS, "muscle": lambda: MUSCLES, "body_part": lambda: BODY_PARTS,
    "symptom_kind": lambda: SYMPTOM_KINDS, "measurement_kind": lambda: MEASUREMENT_KINDS,
    "cardio_activity": lambda: CARDIO_ACTIVITIES, "cardio_workout_type": lambda: CARDIO_WORKOUT_TYPES,
    "progression_rule": lambda: PROGRESSION_RULES,
}
