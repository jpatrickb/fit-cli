"""Reading sessions back: `fit show`."""

import sqlite3
from itertools import groupby

from fit import resolve
from fit.errors import FitError


def clock(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    seconds = round(seconds, 1)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    sec = f"{s:04.1f}" if s % 1 else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{sec}" if h else f"{int(m)}:{sec}"


def pace(sec_per_mi: float | None) -> str | None:
    return f"{clock(round(sec_per_mi))}/mi" if sec_per_mi else None


def _rep(s: sqlite3.Row) -> str:
    text = f"{s['seconds']}s" if s["reps"] is None else str(s["reps"])
    if s["rest_pause_reps"]:
        text += f"+{s['rest_pause_reps']}"
    elif s["to_failure"]:
        text += "F"
    if s["kind"] != "working":
        text += f"({s['kind'][0]})"
    return text


def sets_text(sets: list[sqlite3.Row]) -> str:
    """'90×12,11 · 100×10+2'. L/R sets read 'L12/R10'; F = to failure; (w) = warmup."""
    parts = []
    for load, group in groupby(sorted(sets, key=lambda s: (s["set_no"], s["side"])), key=lambda s: s["load"]):
        by_set = groupby(group, key=lambda s: s["set_no"])
        reps = []
        for _, same_set in by_set:
            same_set = list(same_set)
            if len(same_set) > 1 or same_set[0]["side"] in ("left", "right"):
                reps.append("/".join(f"{s['side'][0].upper()}{_rep(s)}" for s in same_set))
            else:
                reps.append(_rep(same_set[0]))
        parts.append(f"{'BW' if load is None else f'{load:g}'}×{','.join(reps)}")
    return " · ".join(parts)


def _strength(conn: sqlite3.Connection, session: sqlite3.Row, detail: bool) -> dict:
    out = {}
    if session["program_day_id"]:
        out["day"] = conn.execute("SELECT label FROM program_day WHERE id = ?", (session["program_day_id"],)).fetchone()[0]
    rows = conn.execute(
        "SELECT se.*, e.name AS exercise, eq.name AS equipment, eq.unit FROM session_exercise se "
        "JOIN exercise e ON e.id = se.exercise_id LEFT JOIN equipment eq ON eq.id = se.equipment_id "
        "WHERE se.session_id = ? AND se.deleted_at IS NULL ORDER BY se.position IS NULL, se.position",
        (session["id"],)).fetchall()
    exercises = []
    for r in rows:
        sets = conn.execute("SELECT * FROM exercise_set WHERE session_exercise_id = ? AND deleted_at IS NULL "
                            "ORDER BY set_no, side", (r["id"],)).fetchall()
        volume = conn.execute("SELECT volume_lb FROM v_exercise_session WHERE session_exercise_id = ?",
                              (r["id"],)).fetchone()
        item = {"exercise": r["exercise"], "equipment": r["equipment"],
                "sets": sets_text(sets) if r["status"] == "done" else f"skipped: {r['skip_reason']}",
                "unit": r["unit"] if r["status"] == "done" else None,
                "volume_lb": round(volume[0]) if volume and volume[0] is not None else None}
        if detail:
            item["set_rows"] = [{k: s[k] for k in ("set_no", "kind", "side", "load", "implements", "reps",
                                                    "rest_pause_reps", "seconds", "tempo", "to_failure")} for s in sets]
        exercises.append(item)
    out["exercises"] = exercises
    return out


def _cardio(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    c = conn.execute("SELECT cd.*, v.sec_per_mi FROM cardio_detail cd JOIN v_cardio v ON v.session_id = cd.session_id "
                     "WHERE cd.session_id = ?", (session["id"],)).fetchone()
    out = {"activity": c["activity_id"], "type": c["workout_type_id"],
           "distance": f"{c['distance']:g} {c['distance_unit']}" if c["distance"] is not None else None,
           "moving_time": clock(c["moving_seconds"]),
           "pace": pace(c["sec_per_mi"])}
    segs = conn.execute("SELECT * FROM v_cardio_segment WHERE session_id = ? ORDER BY seq", (session["id"],)).fetchall()
    out["segments"] = [{"seq": s["seq"], "role": s["role"],
                        "distance": f"{s['distance']:g} {s['distance_unit']}" if s["distance"] is not None else None,
                        "time": clock(s["seconds"]),
                        "pace": pace(s["sec_per_mi"])} for s in segs]
    return out


def session(conn: sqlite3.Connection, session_id: str, detail: bool = False) -> dict:
    s = conn.execute("SELECT s.*, p.name AS person FROM session s JOIN person p ON p.id = s.person_id "
                     "WHERE s.id = ?", (session_id,)).fetchone()
    out = {"id": s["id"], "person": s["person"], "date": s["date"], "kind": s["kind"],
           "started_at": s["started_at"][11:16] if s["started_at"] else None,
           "source": s["source"] + (f" {s['source_ref']}" if s["source_ref"] else "")}
    out |= _strength(conn, s, detail) if s["kind"] == "strength" else _cardio(conn, s)
    symptoms = conn.execute(
        "SELECT sy.*, e.name AS exercise FROM symptom sy LEFT JOIN session_exercise se ON se.id = sy.session_exercise_id "
        "LEFT JOIN exercise e ON e.id = se.exercise_id WHERE sy.session_id = ? AND sy.deleted_at IS NULL",
        (session_id,)).fetchall()
    if symptoms:
        out["symptoms"] = [{"exercise": y["exercise"], "body_part": y["body_part_id"], "side": y["side"],
                            "kind": y["kind_id"], "severity": y["severity"]} for y in symptoms]
    return out


def show(conn: sqlite3.Connection, person: str | None, date: str | None, detail: bool = False,
         kind: str | None = None) -> dict:
    people = [resolve.person(conn, person)["id"]] if person else \
        [r[0] for r in conn.execute("SELECT id FROM person WHERE deleted_at IS NULL")]
    marks = ",".join("?" * len(people))
    only = " AND s.kind = ?" if kind else ""
    extra = [kind] if kind else []
    if date is None:
        row = conn.execute(f"SELECT MAX(date) FROM session s WHERE person_id IN ({marks}) AND deleted_at IS NULL{only}",
                           [*people, *extra]).fetchone()
        date = row[0]
        if date is None:
            raise FitError("no_sessions", "nothing logged yet")
    ids = [r[0] for r in conn.execute(
        f"SELECT s.id FROM session s JOIN person p ON p.id = s.person_id WHERE s.person_id IN ({marks}) "
        f"AND s.date = ? AND s.deleted_at IS NULL{only} ORDER BY p.name, s.started_at, s.created_at",
        [*people, date, *extra])]
    if not ids:
        raise FitError("no_sessions", f"nothing logged on {date}")
    return {"sessions": [session(conn, i, detail) for i in ids]}
