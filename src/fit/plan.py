"""Plans: loading, showing and changing them (A10-A14), and `fit next`, which turns a plan day plus
the log into a gym template with computed target weights (A5-A7).

Plan document (TOON or JSON):

    person: Sam
    name: Block 2
    gym: Home gym               # gives start_load its unit; exercises are checked against it
    starts_on: 2026-10-05
    days[1]:
      - label: A
        name: Push & Quad
        slots[2]{exercise,sets,rep_min,rep_max,start_load}:
          Machine Chest Press,3,6,10,80
          B-Stance RDL,3,8,12,25

Slot fields: exercise, sets, rep_min, rep_max (both empty = as many as possible), seconds_min,
seconds_max, start_load (per implement), implements, tempo, target_rir, progression
(double | linear | none), load_cap.
"""

import sqlite3
from datetime import date as Date
from datetime import timedelta

from fit import resolve
from fit.changes import Writer
from fit.errors import FitError, closest
from fit.record import _check_fields, _int, parse_date

PLAN_FIELDS = {"person", "name", "gym", "starts_on", "days"}
DAY_FIELDS = {"label", "name", "slots"}
SLOT_FIELDS = {"exercise", "sets", "rep_min", "rep_max", "seconds_min", "seconds_max", "start_load", "implements",
               "tempo", "target_rir", "progression", "load_cap"}
EDITABLE = SLOT_FIELDS - {"exercise"}


def _day_before(day: str) -> str:
    return (Date.fromisoformat(day) - timedelta(days=1)).isoformat()


def current_program(conn: sqlite3.Connection, person_id: str, on: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM program WHERE person_id = ? AND deleted_at IS NULL AND starts_on <= ? "
        "AND (ends_on IS NULL OR ends_on >= ?) ORDER BY starts_on DESC LIMIT 1", (person_id, on, on)).fetchone()
    if row is None:
        raise FitError("no_plan", f"no plan on {on}", ["fit plan load"])
    return row


def active_slots(conn: sqlite3.Connection, day_id: str, on: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pr.*, e.name AS exercise, e.laterality, e.movement_pattern, e.measure, e.default_implements "
        "FROM prescription pr JOIN exercise e ON e.id = pr.exercise_id "
        "WHERE pr.program_day_id = ? AND pr.active_from <= ? AND (pr.active_until IS NULL OR pr.active_until >= ?) "
        "ORDER BY pr.position", (day_id, on, on)).fetchall()


def _slot_values(conn: sqlite3.Connection, slot: dict, where: str) -> dict:
    _check_fields(slot, SLOT_FIELDS, where)
    values = {k: slot.get(k) for k in SLOT_FIELDS - {"exercise", "progression"} if slot.get(k) is not None}
    for k in ("sets", "rep_min", "rep_max", "seconds_min", "seconds_max", "implements", "target_rir"):
        if k in values:
            values[k] = _int(values[k], k)
    if "sets" not in values:
        raise FitError("missing_field", f"{where}: 'sets' is required")
    if slot.get("progression"):
        values["progression_id"] = resolve.lookup(conn, "progression_rule", slot["progression"], "progression rule")
    return values


def _check_doable(conn: sqlite3.Connection, ex: sqlite3.Row, loc: sqlite3.Row | None) -> None:
    """Every equipment slot the exercise needs must be covered at the plan's gym."""
    if loc is None:
        return
    missing = conn.execute(
        "SELECT need.slot, group_concat(need.equipment_kind_id, ' or ') FROM exercise_equipment need "
        "WHERE need.exercise_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM exercise_equipment alt JOIN equipment have ON have.kind_id = alt.equipment_kind_id "
        "  WHERE alt.exercise_id = need.exercise_id AND alt.slot = need.slot AND have.location_id = ?) "
        "GROUP BY need.slot", (ex["id"], loc["id"])).fetchall()
    if missing:
        raise FitError("not_doable_here", f"{ex['name']} needs {', '.join(m[1] for m in missing)}, "
                                          f"which {loc['name']} doesn't have")


def load(conn: sqlite3.Connection, w: Writer, doc: dict, replace: bool) -> dict:
    _check_fields(doc, PLAN_FIELDS, "plan")
    who = resolve.person(conn, doc.get("person"))
    starts = parse_date(doc.get("starts_on"))
    loc = resolve.location(conn, doc.get("gym"))
    if not doc.get("name"):
        raise FitError("missing_field", "'name' is required")
    current = conn.execute("SELECT * FROM program WHERE person_id = ? AND ends_on IS NULL AND deleted_at IS NULL",
                           (who["id"],)).fetchone()
    if current is not None and not replace:
        raise FitError("plan_exists", f"{who['name']} already has a current plan ({current['name']})",
                       ["fit plan load --replace (ends the current one the day before)", "fit plan swap"])
    if conn.execute("SELECT 1 FROM program WHERE person_id = ? AND name = ? COLLATE NOCASE AND deleted_at IS NULL",
                    (who["id"], doc["name"])).fetchone():
        raise FitError("plan_name_taken", f"{who['name']} already has a plan named {doc['name']!r}")
    if current is not None:
        if current["starts_on"] >= starts:
            raise FitError("bad_date", f"the new plan must start after {current['starts_on']}")
        w.update("program", {"id": current["id"]}, {"ends_on": _day_before(starts)})
    program = w.insert("program", {"person_id": who["id"], "name": doc["name"], "starts_on": starts,
                                   "location_id": loc["id"] if loc else None})
    for d in doc.get("days") or []:
        _check_fields(d, DAY_FIELDS, f"day {d.get('label')!r}")
        if not d.get("label"):
            raise FitError("missing_field", "every day needs a 'label'")
        day = w.insert("program_day", {"program_id": program["id"], "label": str(d["label"]), "name": d.get("name")})
        seen: set[str] = set()
        for pos, slot in enumerate(d.get("slots") or [], 1):
            where = f"day {d['label']} slot {pos}"
            ex = resolve.exercise(conn, slot.get("exercise") or "")
            if ex["id"] in seen:
                raise FitError("already_in_day", f"{ex['name']} appears twice on day {d['label']}")
            seen.add(ex["id"])
            _check_doable(conn, ex, loc)
            w.insert("prescription", {"program_day_id": day["id"], "position": pos, "exercise_id": ex["id"],
                                      "active_from": starts, **_slot_values(conn, slot, where)})
    return show(conn, who["name"], on_text=starts)


def show(conn: sqlite3.Connection, person: str | None, on_text: str | None) -> dict:
    who = resolve.person(conn, person)
    on = parse_date(on_text)
    program = current_program(conn, who["id"], on)
    loc = conn.execute("SELECT name FROM location WHERE id = ?", (program["location_id"],)).fetchone()
    days = []
    for d in conn.execute("SELECT * FROM program_day WHERE program_id = ? ORDER BY label", (program["id"],)):
        slots = [{"slot": s["position"], "exercise": s["exercise"], "sets": s["sets"], "reps": _reps(s),
                  "start_load": s["start_load"], "tempo": s["tempo"], "progression": s["progression_id"],
                  "since": s["active_from"]} for s in active_slots(conn, d["id"], on)]
        days.append({"day": d["label"], "name": d["name"], "slots": slots})
    return {"person": who["name"], "plan": program["name"], "gym": loc[0] if loc else None,
            "since": program["starts_on"], "days": days}


def _reps(s: sqlite3.Row) -> str:
    if s["seconds_min"] is not None:
        return f"{s['seconds_min']}–{s['seconds_max']} s"
    if s["rep_min"] is None:
        return "max"
    return f"{s['rep_min']}–{s['rep_max']}" if s["rep_min"] != s["rep_max"] else str(s["rep_min"])


def _slot(conn: sqlite3.Connection, person: str | None, day: str, position: int, on: str):
    who = resolve.person(conn, person)
    pd = resolve.program_day(conn, who["id"], day, on)
    for s in active_slots(conn, pd["id"], on):
        if s["position"] == position:
            return who, pd, s
    raise FitError("unknown_slot", f"no slot {position} on day {day}",
                   [f"{s['position']}: {s['exercise']}" for s in active_slots(conn, pd["id"], on)])


def swap(conn: sqlite3.Connection, w: Writer, *, person: str | None, day: str, position: int, exercise: str,
         start_load: float | None, on_text: str | None) -> dict:
    """End a slot's exercise and start another at the same position (D53: only swaps make versions)."""
    on = parse_date(on_text)
    who, pd, old = _slot(conn, person, day, position, on)
    ex = resolve.exercise(conn, exercise)
    program = conn.execute("SELECT * FROM program WHERE id = ?", (pd["program_id"],)).fetchone()
    loc = conn.execute("SELECT * FROM location WHERE id = ?", (program["location_id"],)).fetchone()
    _check_doable(conn, ex, loc)
    return _do_swap(w, old, ex, start_load, on) | {"person": who["name"], "day": day}


def _do_swap(w: Writer, old: sqlite3.Row, ex: sqlite3.Row, start_load: float | None, on: str) -> dict:
    if old["active_from"] == on:  # swapped again the same day: replace instead of an empty version
        w.update("prescription", {"id": old["id"]}, {"exercise_id": ex["id"], "start_load": start_load})
    else:
        w.update("prescription", {"id": old["id"]}, {"active_until": _day_before(on)})
        keep = {k: old[k] for k in ("program_day_id", "position", "sets", "rep_min", "rep_max", "seconds_min",
                                    "seconds_max", "implements", "tempo", "target_rir", "progression_id", "load_cap")}
        w.insert("prescription", keep | {"exercise_id": ex["id"], "start_load": start_load, "active_from": on})
    return {"slot": old["position"], "from": old["exercise"], "to": ex["name"], "start_load": start_load, "from_date": on}


def edit(conn: sqlite3.Connection, w: Writer, *, person: str | None, day: str, position: int, changes: dict,
         on_text: str | None) -> dict:
    """Change sets, reps, tempo... in place (D53); the change log keeps the old values."""
    on = parse_date(on_text)
    _, _, slot = _slot(conn, person, day, position, on)
    for k in changes:
        if k not in EDITABLE:
            raise FitError("unknown_field", f"can't edit {k!r}", closest(k, sorted(EDITABLE)))
    values = _slot_values(conn, {"sets": slot["sets"], **changes}, f"slot {position}")
    values = {k: v for k, v in values.items() if k in changes or (k == "progression_id" and "progression" in changes)}
    w.update("prescription", {"id": slot["id"]}, values)
    return {"day": day, "slot": position, "exercise": slot["exercise"], "changed": sorted(changes)}



def _program_for(conn: sqlite3.Connection, person: str | None, on: str):
    who = resolve.person(conn, person)
    program = current_program(conn, who["id"], on)
    loc = conn.execute("SELECT * FROM location WHERE id = ?", (program["location_id"],)).fetchone()
    return who, program, loc


def add_day(conn: sqlite3.Connection, w: Writer, *, person: str | None, label: str, name: str | None,
            on_text: str | None) -> dict:
    on = parse_date(on_text)
    who, program, _ = _program_for(conn, person, on)
    if conn.execute("SELECT 1 FROM program_day WHERE program_id = ? AND label = ?", (program["id"], label)).fetchone():
        raise FitError("day_exists", f"{program['name']} already has a day {label!r}")
    w.insert("program_day", {"program_id": program["id"], "label": label, "name": name})
    return {"person": who["name"], "plan": program["name"], "day": label, "name": name}


def add_slot(conn: sqlite3.Connection, w: Writer, *, person: str | None, day: str, exercise: str, values: dict,
             position: int | None, on_text: str | None) -> dict:
    """A new slot from --date on, at the end of the day unless a free position is given."""
    on = parse_date(on_text)
    who, program, loc = _program_for(conn, person, on)
    pd = resolve.program_day(conn, who["id"], day, on)
    ex = resolve.exercise(conn, exercise)
    _check_doable(conn, ex, loc)
    current = active_slots(conn, pd["id"], on)
    if any(s["exercise_id"] == ex["id"] for s in current):
        raise FitError("already_in_day", f"{ex['name']} is already on day {day}")
    taken = {s["position"] for s in current}
    if position is None:
        position = max(taken, default=0) + 1
    elif position in taken:
        raise FitError("slot_taken", f"slot {position} on day {day} is in use", [str(max(taken) + 1)])
    w.insert("prescription", {"program_day_id": pd["id"], "position": position, "exercise_id": ex["id"],
                              "active_from": on, **_slot_values(conn, values, f"day {day} slot {position}")})
    return {"person": who["name"], "day": day, "slot": position, "exercise": ex["name"], "from_date": on}


def drop_slot(conn: sqlite3.Connection, w: Writer, *, person: str | None, day: str, position: int,
              on_text: str | None) -> dict:
    """Stop doing a slot from --date on; it stays in the plan's history."""
    on = parse_date(on_text)
    who, _, slot = _slot(conn, person, day, position, on)
    if slot["active_from"] == on:  # added today and dropped today: it never really existed
        w.delete("prescription", {"id": slot["id"]})
    else:
        w.update("prescription", {"id": slot["id"]}, {"active_until": _day_before(on)})
    return {"person": who["name"], "day": day, "slot": position, "dropped": slot["exercise"], "from_date": on}


# ── fit next ─────────────────────────────────────────────────────────────────

def _loads(conn: sqlite3.Connection, equipment_id: str | None) -> list[float]:
    if equipment_id is None:
        return []
    return [r[0] for r in conn.execute("SELECT load FROM equipment_load WHERE equipment_id = ? ORDER BY load",
                                       (equipment_id,))]


def _last(conn: sqlite3.Connection, person_id: str, exercise_id: str, before: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM v_exercise_session WHERE person_id = ? AND exercise_id = ? AND date < ? "
                        "ORDER BY date DESC LIMIT 1", (person_id, exercise_id, before)).fetchone()


def _pain(conn: sqlite3.Connection, session_exercise_id: str) -> bool:
    return conn.execute("SELECT 1 FROM symptom WHERE session_exercise_id = ? AND deleted_at IS NULL "
                        "AND kind_id = 'pain' AND severity IN ('moderate', 'severe')", (session_exercise_id,)).fetchone() is not None


def target(conn: sqlite3.Connection, person_id: str, slot: sqlite3.Row, equipment_id: str | None, on: str):
    """(target load, why, capped) for one slot, from the last time this exercise was done."""
    loads = _loads(conn, equipment_id)
    last = _last(conn, person_id, slot["exercise_id"], on)
    cap = slot["load_cap"]

    def capped_at(x):
        return min(x, cap) if cap is not None and x is not None else x

    if last is None:
        return slot["start_load"], "first time: plan's start weight" if slot["start_load"] else "first time", False
    top = last["top_load"]
    seen = f"last {last['date']}: {last['sets_by_load']}"
    if top is None:
        return None, f"{seen}; body weight", False
    if _pain(conn, last["session_exercise_id"]):
        return top, f"{seen}; held, pain logged", False
    if last["min_reps_at_top"] is None:  # timed sets: nothing to progress on by weight
        return top, f"{seen}; same weight", False
    rule = slot["progression_id"]
    up = next((x for x in loads if x > top), None)
    down = next((x for x in reversed(loads) if x < top), None)
    if rule == "none" or (rule == "double" and slot["rep_max"] is None):
        return top, f"{seen}; held", False
    if rule == "linear" or last["min_reps_at_top"] >= slot["rep_max"]:
        if cap is not None and top >= cap:
            return cap, f"{seen}; at the plan's cap", False
        if up is None:
            return top, f"{seen}; maxed at the heaviest weight", bool(loads)
        return capped_at(up), f"{seen}; up a step", False
    # Down only when the *first* set at the top weight missed the range: a short last set is fatigue,
    # a short first set means the weight is too heavy.
    if slot["rep_min"] is not None and last["first_reps_at_top"] < slot["rep_min"] and down is not None:
        return down, f"{seen}; down a step", False
    if _stalled(conn, person_id, slot["exercise_id"], on):
        return top, f"{seen}; same weight; stalled: no progress in 3 sessions", False
    return top, f"{seen}; same weight", False


def _stalled(conn: sqlite3.Connection, person_id: str, exercise_id: str, before: str, sessions: int = 3) -> bool:
    """No improvement in the last 3 sessions: same top weight, and neither later session beat the
    first one's total reps at that weight."""
    rows = conn.execute("SELECT top_load, reps_at_top FROM v_exercise_session WHERE person_id = ? AND exercise_id = ? "
                        "AND date < ? ORDER BY date DESC LIMIT ?", (person_id, exercise_id, before, sessions)).fetchall()
    if len(rows) < sessions or len({r["top_load"] for r in rows}) != 1 or any(r["reps_at_top"] is None for r in rows):
        return False
    first = rows[-1]["reps_at_top"]
    return all(r["reps_at_top"] <= first for r in rows[:-1])


def _harder(conn: sqlite3.Connection, exercise_id: str, loc_id: str | None):
    """Next harder exercise doable at the gym, with its start_ratio."""
    for row in conn.execute("SELECT e.*, p.start_ratio FROM exercise_progression p JOIN exercise e ON e.id = p.to_exercise_id "
                            "WHERE p.from_exercise_id = ? AND p.kind = 'harder' AND e.deleted_at IS NULL ORDER BY p.rowid",
                            (exercise_id,)):
        try:
            loc = conn.execute("SELECT * FROM location WHERE id = ?", (loc_id,)).fetchone() if loc_id else None
            _check_doable(conn, row, loc)
            return row
        except FitError:
            continue
    return None


def _start_load(conn, harder, old_top: float, old_implements: int, equipment_id: str | None) -> float | None:
    """D51: share of the old total load, to the nearest available weight; else one step below."""
    loads = _loads(conn, equipment_id)
    if harder["start_ratio"] == 0 or not loads:
        return None
    total = old_top * max(old_implements, 1)
    if harder["start_ratio"] is not None:
        raw = harder["start_ratio"] * total / max(harder["default_implements"], 1)
        return min(loads, key=lambda x: (abs(x - raw), x))
    below = [x for x in loads if x < old_top]
    return below[-1] if below else loads[0]


def _note(slot: sqlite3.Row, target_load: float | None, implements: int) -> str:
    load = "BW" if target_load is None else f"{target_load:g}" + (f"x{implements}" if implements == 2 else "")
    extras = []
    if slot["sets"] != 3:
        extras.append(f"{slot['sets']} sets")
    if slot["laterality"] == "unilateral":
        extras.append("each " + ("leg" if slot["movement_pattern"] in ("squat", "lunge", "hinge", "hip_extension",
                                                                      "knee_flexion", "calf") else
                                 "side" if slot["movement_pattern"] == "core" else "arm"))
    if slot["tempo"]:
        extras.append(f"tempo {slot['tempo']}")
    if slot["target_rir"] is not None:
        extras.append(f"stop {slot['target_rir']} short")
    if slot["measure"] == "time" and slot["seconds_min"] is not None:
        extras.append(f"{slot['seconds_min']}–{slot['seconds_max']} s")
    return f"{slot['exercise']}{' ' + load if load else ''}{' (' + ', '.join(extras) + ')' if extras else ''}:"


def suggest_day(conn: sqlite3.Connection, person_id: str, program_id: str) -> str:
    """D30: the day done longest ago (never done counts as longest)."""
    rows = conn.execute(
        "SELECT d.label, (SELECT MAX(s.date) FROM session s JOIN program_day d2 ON d2.id = s.program_day_id "
        "  WHERE s.person_id = ? AND d2.label = d.label AND s.deleted_at IS NULL) AS last "
        "FROM program_day d WHERE d.program_id = ? ORDER BY last IS NOT NULL, last, d.label",
        (person_id, program_id)).fetchall()
    if not rows:
        raise FitError("empty_plan", "the plan has no days")
    return rows[0]["label"]


def next_session(conn: sqlite3.Connection, w_factory, *, person: str | None, day: str | None, on_text: str | None) -> dict:
    """Build the template. Swaps capped exercises in the plan (A7), which is a write; otherwise read-only."""
    on = parse_date(on_text)
    who = resolve.person(conn, person)
    program = current_program(conn, who["id"], on)
    label = day or suggest_day(conn, who["id"], program["id"])
    pd = resolve.program_day(conn, who["id"], label, on)
    loc = conn.execute("SELECT * FROM location WHERE id = ?", (program["location_id"],)).fetchone()

    plan, swaps = [], []
    for slot in active_slots(conn, pd["id"], on):
        ex = conn.execute("SELECT * FROM exercise WHERE id = ?", (slot["exercise_id"],)).fetchone()
        equipment = resolve.equipment(conn, ex, loc, None)
        eq_id = equipment["id"] if equipment else None
        load, why, capped = target(conn, who["id"], slot, eq_id, on)
        if capped:
            harder = _harder(conn, slot["exercise_id"], program["location_id"])
            if harder is not None:
                h_equipment = resolve.equipment(conn, harder, loc, None)
                start = _start_load(conn, harder, load, slot["implements"] or ex["default_implements"],
                                    h_equipment["id"] if h_equipment else None)
                swaps.append((slot, harder, start, why))
                continue
            why += "; no harder exercise available here"
        plan.append((slot, load, why))

    if swaps:
        with w_factory() as w:
            for slot, harder, start, why in swaps:
                _do_swap(w, slot, harder, start, on)
        # re-read the swapped slots so the template reflects the plan as it now stands
        new = {s["position"]: s for s in active_slots(conn, pd["id"], on)}
        for slot, harder, start, why in swaps:
            plan.append((new[slot["position"]], start, f"{why}; swapped {slot['exercise']} -> {harder['name']}"))
        plan.sort(key=lambda p: p[0]["position"])

    when = Date.fromisoformat(on)
    header = f"{when:%a %b} {when.day} · {who['name']} · {label}"
    lines, rows = [header], []
    for slot, load, why in plan:
        implements = slot["implements"] if slot["implements"] is not None else slot["default_implements"]
        lines.append(_note(slot, load, implements))
        rows.append({"slot": slot["position"], "exercise": slot["exercise"], "target": load, "why": why})
    return {"template": "\n".join(lines), "day": label, "targets": rows,
            "swapped": [{"slot": s["position"], "from": s["exercise"], "to": h["name"], "start_load": st}
                        for s, h, st, _ in swaps]}
