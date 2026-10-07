"""Reading commands (A16). The CLI reports facts; the agent does the analysis (A15)."""

import sqlite3
from datetime import date as Date
from datetime import timedelta

from fit import resolve
from fit.errors import FitError
from fit.plan import active_slots, current_program
from fit.record import parse_date
from fit.view import clock, pace

FORMULAS = ("epley", "brzycki", "lombardi", "oconner", "mayhew", "wathan", "lander")


def _r(x, n=1):
    return None if x is None else round(x, n)


def history(conn: sqlite3.Connection, person: str | None, exercise: str, since: str | None) -> dict:
    who = resolve.person(conn, person)
    ex = resolve.exercise(conn, exercise)
    rows = conn.execute(
        "SELECT es.*, e.mean, e.low, e.high FROM v_exercise_session es "
        "LEFT JOIN v_e1rm_session e ON e.session_exercise_id = es.session_exercise_id "
        "WHERE es.person_id = ? AND es.exercise_id = ? AND es.date >= ? ORDER BY es.date",
        (who["id"], ex["id"], parse_date(since) if since else "0000")).fetchall()
    return {"person": who["name"], "exercise": ex["name"], "sessions": [
        {"date": r["date"], "sets": r["sets_by_load"], "unit": r["unit"], "tempo": r["tempo"],
         "volume_lb": _r(r["volume_lb"], 0), "e1rm": _r(r["mean"]), "e1rm_low": _r(r["low"]), "e1rm_high": _r(r["high"])}
        for r in rows]}


def e1rm(conn: sqlite3.Connection, person: str | None, exercise: str) -> dict:
    who = resolve.person(conn, person)
    ex = resolve.exercise(conn, exercise)
    rows = conn.execute("SELECT * FROM v_e1rm_session WHERE person_id = ? AND exercise_id = ? ORDER BY date",
                        (who["id"], ex["id"])).fetchall()
    return {"person": who["name"], "exercise": ex["name"],
            "note": "per implement, in the equipment's unit; sets over 12 reps have no estimate",
            "sessions": [{"date": r["date"], **{f: _r(r[f]) for f in FORMULAS}, "mean": _r(r["mean"]),
                          "low": _r(r["low"]), "high": _r(r["high"])} for r in rows]}


def bests(conn: sqlite3.Connection, person: str | None, exercise: str | None) -> dict:
    who = resolve.person(conn, person)
    where, args = "WHERE es.person_id = ?", [who["id"]]
    if exercise:
        where += " AND es.exercise_id = ?"
        args.append(resolve.exercise(conn, exercise)["id"])
    out = []
    for ex_id, name, unit in conn.execute(f"SELECT DISTINCT exercise_id, exercise, unit FROM v_exercise_session es "
                                          f"{where} ORDER BY exercise", args).fetchall():
        heavy = conn.execute("SELECT load, MAX(reps) AS reps, MIN(date) AS date FROM v_set WHERE person_id = ? "
                             "AND exercise_id = ? AND kind = 'working' AND load = (SELECT MAX(load) FROM v_set "
                             "WHERE person_id = ? AND exercise_id = ? AND kind = 'working')",
                             (who["id"], ex_id, who["id"], ex_id)).fetchone()
        e = conn.execute("SELECT mean, date FROM v_e1rm_session WHERE person_id = ? AND exercise_id = ? "
                         "ORDER BY mean DESC, date LIMIT 1", (who["id"], ex_id)).fetchone()
        v = conn.execute("SELECT volume_lb, date FROM v_exercise_session WHERE person_id = ? AND exercise_id = ? "
                         "ORDER BY volume_lb DESC, date LIMIT 1", (who["id"], ex_id)).fetchone()
        out.append({"exercise": name, "unit": unit, "heaviest": heavy["load"], "reps_at_heaviest": heavy["reps"],
                    "heaviest_date": heavy["date"], "best_e1rm": _r(e["mean"]) if e else None,
                    "e1rm_date": e["date"] if e else None, "best_volume_lb": _r(v["volume_lb"], 0),
                    "volume_date": v["date"]})
    return {"person": who["name"], "bests": out}


def _week(d: str) -> str:
    y, w, _ = Date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _week_start(on: Date, back: int) -> str:
    return (on - timedelta(days=on.weekday() + 7 * back)).isoformat()


def muscles(conn: sqlite3.Connection, person: str | None, weeks: int, on: str | None) -> dict:
    who = resolve.person(conn, person)
    until = parse_date(on)
    start = _week_start(Date.fromisoformat(until), weeks - 1)
    rows = conn.execute("SELECT week, muscle, sets, volume_lb FROM v_weekly_muscle WHERE person_id = ? "
                        "AND week BETWEEN ? AND ? ORDER BY week, sets DESC, muscle",
                        (who["id"], _week(start), _week(until))).fetchall()
    return {"person": who["name"], "since": start,
            "weeks": [{"week": r["week"], "muscle": r["muscle"], "sets": r["sets"], "volume_lb": _r(r["volume_lb"], 0)}
                      for r in rows]}


def consistency(conn: sqlite3.Connection, person: str | None, weeks: int, on: str | None) -> dict:
    who = resolve.person(conn, person)
    today = Date.fromisoformat(parse_date(on))
    counts: dict[str, dict[str, int]] = {}
    for d, kind in conn.execute("SELECT date, kind FROM session WHERE person_id = ? AND deleted_at IS NULL "
                                "AND date <= ?", (who["id"], today.isoformat())):
        counts.setdefault(_week(d), {"strength": 0, "cardio": 0})[kind] += 1
    if not counts:
        raise FitError("no_sessions", f"nothing logged for {who['name']}")
    # every week from the first logged one, so empty weeks show up as zeros
    first = Date.fromisoformat(conn.execute(
        "SELECT MIN(date) FROM session WHERE person_id = ? AND deleted_at IS NULL", (who["id"],)).fetchone()[0])
    all_weeks, d = [], first - timedelta(days=first.weekday())
    while d <= today:
        all_weeks.append(_week(d.isoformat()))
        d += timedelta(days=7)
    lifted = [counts.get(w, {}).get("strength", 0) > 0 for w in all_weeks]
    streak = 0
    for i, has in enumerate(reversed(lifted)):
        if has:
            streak += 1
        elif i > 0:  # the current week may still be in progress
            break
    longest = run = 0
    for has in lifted:
        run = run + 1 if has else 0
        longest = max(longest, run)
    shown = all_weeks[-weeks:]
    return {"person": who["name"], "lifting_week_streak": streak, "longest_lifting_streak": longest,
            "weeks": [{"week": w, **counts.get(w, {"strength": 0, "cardio": 0})} for w in shown]}


def capped(conn: sqlite3.Connection, person: str | None) -> dict:
    who = resolve.person(conn, person)
    rows = conn.execute("SELECT * FROM v_capped WHERE person_id = ? ORDER BY exercise", (who["id"],)).fetchall()
    return {"person": who["name"], "capped": [{"exercise": r["exercise"], "last": r["date"], "sets": r["sets_by_load"],
                                               "rep_max": r["rep_max"], "max_load": r["max_load"], "unit": r["unit"]}
                                              for r in rows]}


def exercise_age(conn: sqlite3.Connection, person: str | None, on: str | None, gap_days: int = 21) -> dict:
    """How long each exercise in the current plan has been in use, from the log (A12): back from the
    latest session to the first one with no gap longer than gap_days."""
    who = resolve.person(conn, person)
    today = Date.fromisoformat(parse_date(on))
    program = current_program(conn, who["id"], today.isoformat())
    out = []
    for d in conn.execute("SELECT * FROM program_day WHERE program_id = ? ORDER BY label", (program["id"],)).fetchall():
        for s in active_slots(conn, d["id"], today.isoformat()):
            dates = [Date.fromisoformat(r[0]) for r in conn.execute(
                "SELECT DISTINCT s.date FROM session_exercise se JOIN session s ON s.id = se.session_id "
                "WHERE s.person_id = ? AND se.exercise_id = ? AND se.status = 'done' AND se.deleted_at IS NULL "
                "AND s.deleted_at IS NULL AND s.date <= ? ORDER BY s.date DESC", (who["id"], s["exercise_id"], today.isoformat()))]
            start, sessions = None, 0
            for i, dd in enumerate(dates):
                if i and (dates[i - 1] - dd).days > gap_days:
                    break
                start, sessions = dd, sessions + 1
            out.append({"day": d["label"], "slot": s["position"], "exercise": s["exercise"],
                        "in_use_since": start.isoformat() if start else None,
                        "weeks": (today - start).days // 7 if start else 0, "sessions": sessions})
    return {"person": who["name"], "plan": program["name"], "exercises": out}


def cardio(conn: sqlite3.Connection, person: str | None, since: str | None) -> dict:
    who = resolve.person(conn, person)
    rows = conn.execute("SELECT * FROM v_cardio WHERE person_id = ? AND date >= ? ORDER BY date",
                        (who["id"], parse_date(since) if since else "0000")).fetchall()
    weekly: dict[str, float] = {}
    for r in rows:
        weekly[_week(r["date"])] = weekly.get(_week(r["date"]), 0) + (r["distance_mi"] or 0)
    return {"person": who["name"],
            "sessions": [{"date": r["date"], "activity": r["activity_id"], "type": r["workout_type_id"],
                          "miles": _r(r["distance_mi"], 2), "moving_time": clock(r["moving_seconds"]),
                          "pace": pace(r["sec_per_mi"])} for r in rows],
            "weekly_miles": [{"week": w, "miles": round(m, 2)} for w, m in weekly.items()]}


def body(conn: sqlite3.Connection, person: str | None, kind: str | None) -> dict:
    who = resolve.person(conn, person)
    where, args = "", [who["id"]]
    if kind:
        where = " AND kind_id = ?"
        args.append(resolve.lookup(conn, "measurement_kind", kind, "measurement kind"))
    rows = conn.execute(f"SELECT measured_on, kind_id, value, unit FROM measurement WHERE person_id = ? "
                        f"AND deleted_at IS NULL{where} ORDER BY kind_id, measured_on", args).fetchall()
    return {"person": who["name"], "measurements": [{"date": r[0], "kind": r[1], "value": r[2], "unit": r[3]}
                                                    for r in rows]}
