"""Correcting logged data that isn't the most recent write (undo only reaches that one):
delete a whole session to re-log it, or fix one set in place. Every change goes through the
change log like any other write, so a correction can itself be undone right after."""

import sqlite3

from fit import resolve
from fit.changes import Writer
from fit.errors import FitError
from fit.record import _check_load, _choice, _int, _load, parse_date


def find_session(conn: sqlite3.Connection, *, person: str | None, date: str | None, kind: str | None,
                 session_id: str | None) -> sqlite3.Row:
    """One session by id, or the only one for a person on a date (optionally of a kind)."""
    if session_id:
        row = conn.execute("SELECT * FROM session WHERE id = ? AND deleted_at IS NULL", (session_id,)).fetchone()
        if row is None:
            raise FitError("unknown_session", f"no session {session_id!r}")
        return row
    who = resolve.person(conn, person)
    day = parse_date(date)
    rows = conn.execute("SELECT * FROM session WHERE person_id = ? AND date = ? AND deleted_at IS NULL"
                        + (" AND kind = ?" if kind else "") + " ORDER BY started_at, created_at",
                        (who["id"], day, *([kind] if kind else []))).fetchall()
    if not rows:
        raise FitError("no_session", f"nothing logged for {who['name']} on {day}")
    if len(rows) > 1:
        raise FitError("ambiguous_session", f"{len(rows)} sessions for {who['name']} on {day}; pass --id",
                       [f"{r['id']} ({r['kind']}{', ' + r['started_at'][11:16] if r['started_at'] else ''})" for r in rows])
    return rows[0]


def delete_session(conn: sqlite3.Connection, w: Writer, session: sqlite3.Row) -> dict:
    """Remove a session and everything under it. Its source_ref is freed, so the note can be logged again."""
    sid = session["id"]
    exercises = [r[0] for r in conn.execute("SELECT id FROM session_exercise WHERE session_id = ? AND deleted_at IS NULL", (sid,))]
    for se in exercises:
        for (set_id,) in conn.execute("SELECT id FROM exercise_set WHERE session_exercise_id = ? AND deleted_at IS NULL",
                                      (se,)).fetchall():
            w.delete("exercise_set", {"id": set_id})
    for (sym,) in conn.execute("SELECT id FROM symptom WHERE session_id = ? AND deleted_at IS NULL", (sid,)).fetchall():
        w.delete("symptom", {"id": sym})
    for se in exercises:
        w.delete("session_exercise", {"id": se})
    for (seg,) in conn.execute("SELECT id FROM cardio_segment WHERE session_id = ? AND deleted_at IS NULL", (sid,)).fetchall():
        w.delete("cardio_segment", {"id": seg})
    if conn.execute("SELECT 1 FROM cardio_detail WHERE session_id = ?", (sid,)).fetchone():
        w.delete("cardio_detail", {"session_id": sid})
    w.delete("session", {"id": sid})
    who = conn.execute("SELECT name FROM person WHERE id = ?", (session["person_id"],)).fetchone()[0]
    return {"deleted": sid, "person": who, "date": session["date"], "kind": session["kind"]}


def _set_rows(conn: sqlite3.Connection, session: sqlite3.Row, exercise: str, set_no: int, side: str | None):
    ex = resolve.exercise(conn, exercise)
    se = conn.execute("SELECT * FROM session_exercise WHERE session_id = ? AND exercise_id = ? AND deleted_at IS NULL",
                      (session["id"], ex["id"])).fetchone()
    if se is None:
        logged = [r[0] for r in conn.execute("SELECT e.name FROM session_exercise se JOIN exercise e ON e.id = se.exercise_id "
                                             "WHERE se.session_id = ? AND se.deleted_at IS NULL", (session["id"],))]
        raise FitError("exercise_not_logged", f"{ex['name']} isn't in that session", logged)
    rows = conn.execute("SELECT * FROM exercise_set WHERE session_exercise_id = ? AND set_no = ? AND deleted_at IS NULL"
                        + (" AND side = ?" if side else ""), (se["id"], set_no, *([side] if side else []))).fetchall()
    if not rows:
        numbers = [str(r[0]) for r in conn.execute("SELECT DISTINCT set_no FROM exercise_set WHERE session_exercise_id = ? "
                                                   "AND deleted_at IS NULL ORDER BY set_no", (se["id"],))]
        raise FitError("unknown_set", f"{ex['name']} has no set {set_no}" + (f" ({side})" if side else ""), numbers)
    if len(rows) > 1:
        raise FitError("side_required", f"set {set_no} was logged per side; pass --side", ["left", "right"])
    return ex, se, rows[0]


def edit_set(conn: sqlite3.Connection, w: Writer, session: sqlite3.Row, *, exercise: str, set_no: int, side: str | None,
             changes: dict) -> dict:
    ex, se, row = _set_rows(conn, session, exercise, set_no, side)
    if not changes:
        raise FitError("nothing_to_change", "give at least one change", ["fit set edit --help"])
    values: dict = {}
    if "load" in changes:
        values["load"] = _load(changes["load"])
        equipment = conn.execute("SELECT * FROM equipment WHERE id = ?", (se["equipment_id"],)).fetchone()
        _check_load(conn, equipment, values["load"], ex["name"])
    for k in ("reps", "seconds", "implements"):
        if k in changes:
            values[k] = _int(changes[k], k)
    if "rest_pause_reps" in changes:  # 0 clears them
        values["rest_pause_reps"] = _int(changes["rest_pause_reps"], "rest_pause_reps") or None
    if "kind" in changes:
        values["kind"] = _choice(changes["kind"], ["working", "warmup", "backoff", "test"], "set kind")
    if "to_failure" in changes:
        values["to_failure"] = 1 if changes["to_failure"] else 0
    if values.get("rest_pause_reps"):
        values["to_failure"] = 1  # extra reps after failing imply the failure
    w.update("exercise_set", {"id": row["id"]}, values)
    return {"exercise": ex["name"], "set": set_no, "side": row["side"], "changed": sorted(values)}


def delete_set(conn: sqlite3.Connection, w: Writer, session: sqlite3.Row, *, exercise: str, set_no: int,
               side: str | None) -> dict:
    """Remove one set. Removing an exercise's last set removes the exercise from the session too."""
    ex, se, row = _set_rows(conn, session, exercise, set_no, side)
    w.delete("exercise_set", {"id": row["id"]})
    left = conn.execute("SELECT COUNT(*) FROM exercise_set WHERE session_exercise_id = ? AND deleted_at IS NULL",
                        (se["id"],)).fetchone()[0]
    if left == 0:
        for (sym,) in conn.execute("SELECT id FROM symptom WHERE session_exercise_id = ? AND deleted_at IS NULL",
                                   (se["id"],)).fetchall():
            w.delete("symptom", {"id": sym})
        w.delete("session_exercise", {"id": se["id"]})
    return {"exercise": ex["name"], "deleted_set": set_no, "side": row["side"], "exercise_removed": left == 0}


def edit_cardio(conn: sqlite3.Connection, w: Writer, session: sqlite3.Row, changes: dict) -> dict:
    """Fix a cardio session's totals or tags in place: activity, workout type, distance, moving time."""
    if session["kind"] != "cardio":
        raise FitError("not_cardio", "that session isn't cardio")
    if not changes:
        raise FitError("nothing_to_change", "give at least one change", ["fit cardio edit --help"])
    from fit.record import DISTANCE_UNITS, parse_duration
    values: dict = {}
    if "activity" in changes:
        values["activity_id"] = resolve.lookup(conn, "cardio_activity", changes["activity"], "activity")
    if "type" in changes:  # "none" clears it
        values["workout_type_id"] = None if changes["type"] == "none" else \
            resolve.lookup(conn, "cardio_workout_type", changes["type"], "workout type")
    if "distance" in changes:
        values["distance"] = changes["distance"]
        current = conn.execute("SELECT distance_unit FROM cardio_detail WHERE session_id = ?", (session["id"],)).fetchone()[0]
        values["distance_unit"] = _choice(changes.get("unit") or current or "mi", DISTANCE_UNITS, "distance unit")
    elif "unit" in changes:
        values["distance_unit"] = _choice(changes["unit"], DISTANCE_UNITS, "distance unit")
    if "moving_time" in changes:
        seconds = parse_duration(changes["moving_time"])
        values["moving_seconds"] = round(seconds) if seconds is not None else None
    w.update("cardio_detail", {"session_id": session["id"]}, values)
    return {"session": session["id"], "date": session["date"], "changed": sorted(values)}
