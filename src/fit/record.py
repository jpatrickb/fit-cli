"""Writing sessions: a whole session from one document (agents), or one piece at a time (people).

Session document (TOON or JSON), strength:

    person: Sam                # optional when there's only one person
    date: 2026-10-07           # required
    started_at: "18:30"        # optional; HH:MM, 6:30pm, or ISO
    day: B                     # optional plan day label; links exercises to plan slots
    gym: Home gym              # optional when there's only one gym
    source: manual             # manual | import | strava
    source_ref: logs/x.md:12   # optional; re-logging the same ref is refused
    exercises[2]:
      - exercise: Low Pulley Seated Row   # name or alias
        equipment: Cable station          # optional; defaults to the gym's equipment for it
        slot: 1                           # optional plan position, to log a substitution
        side: each                        # optional; unilateral exercises default to each
        sets[3]{load,reps}:
          90,12
          90,11
          100,10
      - exercise: Face Pull
        skip: time                        # pain | time | equipment | fatigue | other
    symptoms[1]{exercise,body_part,side,kind,severity}:
      Bulgarian Split Squat,knee,right,clicking,mild

Set fields: load, reps, seconds, rest_pause_reps (implies to_failure), to_failure, kind
(working | warmup | backoff | test), tempo, implements. A test set is a one-off heavier try;
progression, volume and summaries ignore it, like warmups. When the sides differ on a one-side-at-a-time
exercise, give `left` and `right` reps instead of `reps` (one set, two rows).

Cardio document: person, date, started_at, activity (run | mountain_bike), type, distance,
unit (mi | km | m), moving_time ("33:00"), source, source_ref, segments[6]{role,distance,unit,time},
symptoms[1]{body_part,side,kind,severity}.
"""

import re
import sqlite3
from datetime import date as Date
from datetime import datetime

from fit import resolve
from fit.changes import Writer
from fit.errors import FitError, closest

SKIP_REASONS = ["pain", "time", "equipment", "fatigue", "other"]
SEVERITIES = ["mild", "moderate", "severe"]
SOURCES = ["manual", "import", "strava"]
SEGMENT_ROLES = ["warmup", "work", "recovery", "cooldown", "steady"]
DISTANCE_UNITS = ["mi", "km", "m"]

STRENGTH_FIELDS = {"person", "date", "started_at", "day", "gym", "source", "source_ref", "exercises", "symptoms"}
EXERCISE_FIELDS = {"exercise", "equipment", "slot", "side", "implements", "tempo", "skip", "sets"}
SET_FIELDS = {"load", "reps", "left", "right", "seconds", "rest_pause_reps", "to_failure", "kind", "side", "tempo",
              "implements"}
SYMPTOM_FIELDS = {"exercise", "body_part", "side", "kind", "severity"}
CARDIO_FIELDS = {"person", "date", "started_at", "activity", "type", "distance", "unit", "moving_time",
                 "source", "source_ref", "segments", "symptoms"}
SEGMENT_FIELDS = {"role", "distance", "unit", "time"}


# ── parsing helpers ──────────────────────────────────────────────────────────

def _check_fields(obj: dict, allowed: set[str], where: str) -> None:
    for k in obj:
        if k not in allowed:
            raise FitError("unknown_field", f"{where}: unknown field {k!r}", closest(k, sorted(allowed)))


def _choice(value, options: list[str], what: str) -> str:
    if value in options:
        return value
    raise FitError(f"bad_{what.replace(' ', '_')}", f"{value!r} is not a valid {what}", closest(str(value), options) or options)


def parse_date(value) -> str:
    if value is None:
        return Date.today().isoformat()
    try:
        return Date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise FitError("bad_date", f"{value!r} is not a date; use YYYY-MM-DD") from None


def parse_time(day: str, value) -> str | None:
    """'18:30', '6:30pm', '6:30 PM' or full ISO -> ISO with the local offset."""
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(am|pm)?", text)
    if m:
        hour, minute, half = int(m[1]), int(m[2]), m[3]
        if half == "pm" and hour < 12:
            hour += 12
        if half == "am" and hour == 12:
            hour = 0
        try:
            return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}").astimezone().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(str(value)).astimezone().isoformat()
    except ValueError:
        raise FitError("bad_time", f"{value!r} is not a time; use HH:MM, 6:30pm, or ISO") from None


def parse_duration(value) -> float | None:
    """'2:50', '33:00', '1:02:03', '170.4' or a number of seconds -> seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise FitError("bad_duration", f"{value!r} is not a duration; use mm:ss or h:mm:ss") from None
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def _int(value, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not float(value).is_integer():
        raise FitError("bad_number", f"{what} must be a whole number, got {value!r}")
    return int(value)


def _load(value) -> float | None:
    if value is None or str(value).lower() in ("bw", "bodyweight"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise FitError("bad_load", f"{value!r} is not a weight") from None


# ── shared lookups ───────────────────────────────────────────────────────────

def _check_load(conn: sqlite3.Connection, equipment: sqlite3.Row | None, load: float | None, exercise: str) -> None:
    if equipment is None or load is None:
        return
    loads = [r[0] for r in conn.execute("SELECT load FROM equipment_load WHERE equipment_id = ? ORDER BY load",
                                        (equipment["id"],))]
    if loads and load not in loads:
        nearest = sorted(loads, key=lambda x: abs(x - load))[:3]
        raise FitError("load_not_available", f"{exercise}: {equipment['name']} has no {load:g} {equipment['unit']}",
                       [f"{x:g}" for x in sorted(nearest)])


def _symptom(conn: sqlite3.Connection, w: Writer, session_id: str, se_id: str | None, s: dict) -> None:
    part = resolve.lookup(conn, "body_part", s.get("body_part"), "body part")
    paired = conn.execute("SELECT paired FROM body_part WHERE id = ?", (part,)).fetchone()[0]
    side = s.get("side")
    if paired and side not in ("left", "right", "both"):
        raise FitError("side_required", f"{part} needs a side", ["left", "right", "both"])
    if not paired and side is not None:
        raise FitError("side_not_allowed", f"{part} has no side")
    w.insert("symptom", {
        "session_id": session_id, "session_exercise_id": se_id, "body_part_id": part, "side": side,
        "kind_id": resolve.lookup(conn, "symptom_kind", s.get("kind"), "symptom kind"),
        "severity": _choice(s.get("severity"), SEVERITIES, "severity"),
    })


def _source(conn: sqlite3.Connection, doc: dict) -> tuple[str, str | None]:
    source = _choice(doc.get("source", "manual"), SOURCES, "source")
    ref = doc.get("source_ref")
    if ref is not None and conn.execute(
            "SELECT 1 FROM session WHERE source = ? AND source_ref = ? AND deleted_at IS NULL", (source, ref)).fetchone():
        raise FitError("already_logged", f"a session from {source} {ref!r} is already logged", ["fit show", "fit undo"])
    return source, ref


# ── strength ─────────────────────────────────────────────────────────────────

def _slots(conn: sqlite3.Connection, day_id: str, date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM prescription WHERE program_day_id = ? AND active_from <= ? "
        "AND (active_until IS NULL OR active_until >= ?) ORDER BY position", (day_id, date, date)).fetchall()


def _prescription(slots: list[sqlite3.Row], ex: sqlite3.Row, slot: int | None) -> str | None:
    if slot is not None:
        for s in slots:
            if s["position"] == slot:
                return s["id"]
        raise FitError("unknown_slot", f"no plan slot {slot} that day", [str(s["position"]) for s in slots])
    matches = [s["id"] for s in slots if s["exercise_id"] == ex["id"]]
    return matches[0] if len(matches) == 1 else None


def _sets(conn, w, se_id, item, ex, equipment, first_set_no: int = 1) -> None:
    default_side = item.get("side") or ("each" if ex["laterality"] == "unilateral" else "both")
    default_side = _choice(default_side, ["both", "each", "left", "right"], "side")
    for n, s in enumerate(item["sets"], first_set_no):
        _check_fields(s, SET_FIELDS, f"{ex['name']} set {n}")
        load = _load(s.get("load"))
        _check_load(conn, equipment, load, ex["name"])
        reps = _int(s.get("reps"), "reps")
        seconds = _int(parse_duration(s.get("seconds")), "seconds") if s.get("seconds") is not None else None
        sided = s.get("left") is not None or s.get("right") is not None
        if reps is None and seconds is None and not sided:
            raise FitError("set_needs_reps", f"{ex['name']} set {n}: give reps, left/right, or seconds")
        rp = _int(s.get("rest_pause_reps"), "rest_pause_reps")
        implements = _int(s.get("implements", item.get("implements")), "implements")
        if implements is None:
            implements = ex["default_implements"] or (1 if load is not None else 0)
        row = {
            "session_exercise_id": se_id, "set_no": n,
            "kind": _choice(s.get("kind", "working"), ["working", "warmup", "backoff", "test"], "set kind"),
            "side": _choice(s.get("side", default_side), ["both", "each", "left", "right"], "side"),
            "load": load, "implements": implements, "reps": reps, "rest_pause_reps": rp, "seconds": seconds,
            "tempo": s.get("tempo", item.get("tempo")),
            "to_failure": 1 if (s.get("to_failure") or rp) else 0,
        }
        if sided:
            if reps is not None or s.get("left") is None or s.get("right") is None:
                raise FitError("bad_sides", f"{ex['name']} set {n}: give both left and right, and no reps")
            for side in ("left", "right"):
                w.insert("exercise_set", row | {"side": side, "reps": _int(s[side], side)})
        else:
            w.insert("exercise_set", row)


def log_strength(conn: sqlite3.Connection, w: Writer, doc: dict) -> str:
    _check_fields(doc, STRENGTH_FIELDS, "session")
    who = resolve.person(conn, doc.get("person"))
    day = parse_date(doc.get("date") or _required("date"))
    gym = resolve.location(conn, doc.get("gym"))
    source, ref = _source(conn, doc)
    pd = resolve.program_day(conn, who["id"], str(doc["day"]), day) if doc.get("day") is not None else None
    slots = _slots(conn, pd["id"], day) if pd else []

    session = w.insert("session", {
        "person_id": who["id"], "kind": "strength", "date": day,
        "started_at": parse_time(day, doc.get("started_at")), "location_id": gym["id"] if gym else None,
        "program_day_id": pd["id"] if pd else None, "source": source, "source_ref": ref,
    })
    logged: dict[str, str] = {}  # exercise id -> session_exercise id
    position = 0
    for item in doc.get("exercises") or []:
        _check_fields(item, EXERCISE_FIELDS, f"exercise {item.get('exercise')!r}")
        ex = resolve.exercise(conn, item.get("exercise") or _required("exercise"))
        if ex["id"] in logged:
            raise FitError("duplicate_exercise", f"{ex['name']} appears twice; put all its sets in one entry")
        rx = _prescription(slots, ex, _int(item.get("slot"), "slot"))
        if item.get("skip") is not None:
            if rx is None:
                raise FitError("skip_needs_plan", f"{ex['name']}: only planned exercises can be skipped; give day (and slot)")
            se = w.insert("session_exercise", {"session_id": session["id"], "exercise_id": ex["id"],
                                               "prescription_id": rx, "status": "skipped",
                                               "skip_reason": _choice(item["skip"], SKIP_REASONS, "skip reason")})
        else:
            if not item.get("sets"):
                raise FitError("exercise_needs_sets", f"{ex['name']}: give sets, or skip with a reason")
            equipment = resolve.equipment(conn, ex, gym, item.get("equipment"))
            if not item.get("equipment") and all(_load(st.get("load")) is None for st in item["sets"]):
                equipment = None  # body weight only: no equipment was used, whatever the exercise usually takes
            position += 1
            se = w.insert("session_exercise", {"session_id": session["id"], "position": position,
                                               "exercise_id": ex["id"], "prescription_id": rx,
                                               "equipment_id": equipment["id"] if equipment else None})
            _sets(conn, w, se["id"], item, ex, equipment)
        logged[ex["id"]] = se["id"]

    for s in doc.get("symptoms") or []:
        _check_fields(s, SYMPTOM_FIELDS, "symptom")
        se_id = None
        if s.get("exercise"):
            se_id = logged.get(resolve.exercise(conn, s["exercise"])["id"])
            if se_id is None:
                raise FitError("symptom_exercise_not_logged", f"symptom on {s['exercise']!r}, which isn't in this session")
        _symptom(conn, w, session["id"], se_id, s)
    return session["id"]


def _required(field: str):
    raise FitError("missing_field", f"{field!r} is required")


# ── cardio ───────────────────────────────────────────────────────────────────

def log_cardio(conn: sqlite3.Connection, w: Writer, doc: dict) -> str:
    _check_fields(doc, CARDIO_FIELDS, "cardio session")
    who = resolve.person(conn, doc.get("person"))
    day = parse_date(doc.get("date") or _required("date"))
    source, ref = _source(conn, doc)
    activity = resolve.lookup(conn, "cardio_activity", doc.get("activity") or _required("activity"), "activity")
    wtype = resolve.lookup(conn, "cardio_workout_type", doc["type"], "workout type") if doc.get("type") else None
    distance = doc.get("distance")
    unit = doc.get("unit") if distance is not None else None
    if distance is not None:
        unit = _choice(unit or "mi", DISTANCE_UNITS, "distance unit")
    moving = parse_duration(doc.get("moving_time"))

    session = w.insert("session", {
        "person_id": who["id"], "kind": "cardio", "date": day,
        "started_at": parse_time(day, doc.get("started_at")), "source": source, "source_ref": ref,
    })
    w.insert("cardio_detail", {"session_id": session["id"], "activity_id": activity, "workout_type_id": wtype,
                               "distance": distance, "distance_unit": unit,
                               "moving_seconds": round(moving) if moving is not None else None})
    for n, seg in enumerate(doc.get("segments") or [], 1):
        _check_fields(seg, SEGMENT_FIELDS, f"segment {n}")
        seconds = parse_duration(seg.get("time"))
        if seg.get("distance") is None and seconds is None:
            raise FitError("segment_needs_distance_or_time", f"segment {n}: give a distance, a time, or both")
        w.insert("cardio_segment", {
            "session_id": session["id"], "seq": n, "role": _choice(seg.get("role", "steady"), SEGMENT_ROLES, "role"),
            "distance": seg.get("distance"),
            "distance_unit": _choice(seg.get("unit") or "mi", DISTANCE_UNITS, "distance unit")
            if seg.get("distance") is not None else None,
            "seconds": seconds,
        })
    for s in doc.get("symptoms") or []:
        _check_fields(s, SYMPTOM_FIELDS - {"exercise"}, "symptom")
        _symptom(conn, w, session["id"], None, s)
    return session["id"]


# ── one piece at a time (people) ─────────────────────────────────────────────

def _session_for(conn, w, who, day: str, gym, create: bool = True) -> sqlite3.Row | dict:
    row = conn.execute("SELECT * FROM session WHERE person_id = ? AND date = ? AND kind = 'strength' "
                       "AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1", (who["id"], day)).fetchone()
    if row is not None or not create:
        if row is None:
            raise FitError("no_session", f"no strength session for {who['name']} on {day}")
        return row
    return w.insert("session", {"person_id": who["id"], "kind": "strength", "date": day,
                                "location_id": gym["id"] if gym else None, "source": "manual"})


def add_set(conn, w, *, person, date, exercise, load, reps, seconds=None, rest_pause_reps=None, failure=False,
            side=None, gym=None, equipment=None) -> dict:
    who = resolve.person(conn, person)
    day = parse_date(date)
    loc = resolve.location(conn, gym)
    ex = resolve.exercise(conn, exercise)
    session = _session_for(conn, w, who, day, loc)
    se = conn.execute("SELECT * FROM session_exercise WHERE session_id = ? AND exercise_id = ? AND deleted_at IS NULL",
                      (session["id"], ex["id"])).fetchone()
    if se is not None and se["status"] == "skipped":
        raise FitError("exercise_skipped", f"{ex['name']} is logged as skipped that day")
    eq = resolve.equipment(conn, ex, loc, equipment)
    if se is None:
        position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM session_exercise "
                                "WHERE session_id = ? AND deleted_at IS NULL", (session["id"],)).fetchone()[0]
        se = w.insert("session_exercise", {"session_id": session["id"], "position": position, "exercise_id": ex["id"],
                                           "equipment_id": eq["id"] if eq else None})
    set_no = conn.execute("SELECT COALESCE(MAX(set_no), 0) + 1 FROM exercise_set WHERE session_exercise_id = ? "
                          "AND deleted_at IS NULL", (se["id"],)).fetchone()[0]
    one = {"load": load, "reps": reps, "seconds": seconds, "rest_pause_reps": rest_pause_reps,
           "to_failure": failure or None}
    item = {"side": side, "sets": [{k: v for k, v in one.items() if v is not None}]}
    _sets(conn, w, se["id"], item, ex, eq, first_set_no=set_no)
    return {"session_id": session["id"], "exercise": ex["name"], "set_no": set_no}


def skip(conn, w, *, person, date, exercise, reason, day) -> dict:
    who = resolve.person(conn, person)
    d = parse_date(date)
    ex = resolve.exercise(conn, exercise)
    pd = resolve.program_day(conn, who["id"], day, d)
    rx = _prescription(_slots(conn, pd["id"], d), ex, None)
    if rx is None:
        raise FitError("skip_needs_plan", f"{ex['name']} isn't in plan day {day}")
    session = _session_for(conn, w, who, d, resolve.location(conn, None))
    if session["program_day_id"] is None:
        w.update("session", {"id": session["id"]}, {"program_day_id": pd["id"]})
    w.insert("session_exercise", {"session_id": session["id"], "exercise_id": ex["id"], "prescription_id": rx,
                                  "status": "skipped", "skip_reason": _choice(reason, SKIP_REASONS, "skip reason")})
    return {"session_id": session["id"], "skipped": ex["name"], "reason": reason}


def add_symptom(conn, w, *, person, date, body_part, kind, severity, side=None, exercise=None) -> dict:
    who = resolve.person(conn, person)
    d = parse_date(date)
    session = conn.execute("SELECT * FROM session WHERE person_id = ? AND date = ? AND deleted_at IS NULL "
                           "ORDER BY created_at DESC LIMIT 1", (who["id"], d)).fetchone()
    if session is None:
        raise FitError("no_session", f"no session for {who['name']} on {d}; symptoms attach to a session")
    se_id = None
    if exercise:
        ex = resolve.exercise(conn, exercise)
        se = conn.execute("SELECT id FROM session_exercise WHERE session_id = ? AND exercise_id = ? "
                          "AND deleted_at IS NULL", (session["id"], ex["id"])).fetchone()
        if se is None:
            raise FitError("symptom_exercise_not_logged", f"{ex['name']} isn't in that session")
        se_id = se["id"]
    _symptom(conn, w, session["id"], se_id, {"body_part": body_part, "side": side, "kind": kind, "severity": severity})
    return {"session_id": session["id"], "symptom": f"{side + ' ' if side else ''}{body_part} {kind}, {severity}"}


def add_measurement(conn, w, *, person, kind, value, unit=None, date=None) -> dict:
    who = resolve.person(conn, person)
    kind = resolve.lookup(conn, "measurement_kind", kind, "measurement kind")
    dimension = conn.execute("SELECT dimension FROM measurement_kind WHERE id = ?", (kind,)).fetchone()[0]
    units = {"mass": ["lb", "kg"], "length": ["in", "cm"], "rate": ["bpm"]}[dimension]
    unit = _choice(unit or units[0], units, f"unit for {kind}")
    row = w.insert("measurement", {"person_id": who["id"], "measured_on": parse_date(date), "kind_id": kind,
                                   "value": value, "unit": unit})
    return {"person": who["name"], "kind": kind, "value": value, "unit": unit, "date": row["measured_on"]}
