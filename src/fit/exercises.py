"""The exercise catalog: finding, showing and adding exercises (A11, A19, A22)."""

import sqlite3

from fit import resolve
from fit.changes import Writer
from fit.errors import FitError, closest

PATTERNS = ["horizontal_push", "vertical_push", "horizontal_pull", "vertical_pull", "squat", "lunge", "hinge",
            "hip_extension", "knee_flexion", "shoulder_isolation", "arm_isolation", "core", "calf"]


def _doable_sql(alias: str = "e") -> str:
    """SQL condition: every equipment slot of the exercise is covered at location :loc."""
    return (f"NOT EXISTS (SELECT 1 FROM exercise_equipment need WHERE need.exercise_id = {alias}.id AND NOT EXISTS ("
            f"SELECT 1 FROM exercise_equipment alt JOIN equipment have ON have.kind_id = alt.equipment_kind_id "
            f"WHERE alt.exercise_id = {alias}.id AND alt.slot = need.slot AND have.location_id = :loc))")


def _muscles(conn: sqlite3.Connection, exercise_id: str) -> str:
    rows = conn.execute("SELECT muscle_id, set_credit FROM exercise_muscle WHERE exercise_id = ? "
                        "ORDER BY set_credit DESC, muscle_id", (exercise_id,)).fetchall()
    return ", ".join(m + ("" if c == 1 else f" ({c:g})") for m, c in rows)


def find(conn: sqlite3.Connection, *, muscle: str | None, any_role: bool, pattern: str | None,
         gym: str | None, laterality: str | None) -> dict:
    where, params = ["e.deleted_at IS NULL"], {}
    if muscle:
        params["muscle"] = resolve.lookup(conn, "muscle", muscle, "muscle")
        credit = "" if any_role else " AND m.set_credit = 1"
        where.append(f"EXISTS (SELECT 1 FROM exercise_muscle m WHERE m.exercise_id = e.id AND m.muscle_id = :muscle{credit})")
    if pattern:
        if pattern not in PATTERNS:
            raise FitError("bad_pattern", f"{pattern!r} is not a movement pattern", closest(pattern, PATTERNS) or PATTERNS)
        where.append("e.movement_pattern = :pattern")
        params["pattern"] = pattern
    if laterality:
        where.append("e.laterality = :laterality")
        params["laterality"] = laterality
    loc = resolve.location(conn, gym)
    if loc is not None:
        where.append(_doable_sql())
        params["loc"] = loc["id"]
    rows = conn.execute(f"SELECT e.* FROM exercise e WHERE {' AND '.join(where)} ORDER BY e.name", params).fetchall()
    return {"gym": loc["name"] if loc else None, "exercises": [
        {"exercise": r["name"], "pattern": r["movement_pattern"], "laterality": r["laterality"],
         "muscles": _muscles(conn, r["id"])} for r in rows]}


def show(conn: sqlite3.Connection, name: str) -> dict:
    ex = resolve.exercise(conn, name)
    needs: dict[int, list[str]] = {}
    for slot, kind in conn.execute("SELECT slot, equipment_kind_id FROM exercise_equipment WHERE exercise_id = ? "
                                   "ORDER BY slot, rowid", (ex["id"],)):
        needs.setdefault(slot, []).append(kind)
    links = conn.execute(
        "SELECT p.kind, e.name, p.start_ratio, 'to' AS dir FROM exercise_progression p JOIN exercise e ON e.id = p.to_exercise_id "
        "WHERE p.from_exercise_id = ? UNION ALL "
        "SELECT p.kind, e.name, p.start_ratio, 'from' FROM exercise_progression p JOIN exercise e ON e.id = p.from_exercise_id "
        "WHERE p.to_exercise_id = ?", (ex["id"], ex["id"])).fetchall()
    return {
        "exercise": ex["name"], "origin": ex["origin"], "pattern": ex["movement_pattern"], "laterality": ex["laterality"],
        "measure": ex["measure"], "implements": ex["default_implements"], "bodyweight_fraction": ex["bodyweight_fraction"],
        "muscles": _muscles(conn, ex["id"]),
        "needs": " and ".join(" or ".join(k) for k in needs.values()) or "no equipment",
        "aliases": [r[0] for r in conn.execute("SELECT alias FROM exercise_alias WHERE exercise_id = ? AND alias <> ? "
                                               "ORDER BY alias", (ex["id"], ex["name"]))],
        "progressions": [{"direction": r["dir"], "kind": r["kind"], "exercise": r["name"], "start_ratio": r["start_ratio"]}
                         for r in links],
    }


def _check_values(pattern=None, laterality=None, measure=None, implements=None, bodyweight_fraction=None) -> None:
    for value, options, what in [(pattern, PATTERNS, "movement pattern"), (laterality, ["bilateral", "unilateral"], "laterality"),
                                 (measure, ["reps", "time"], "measure"), (implements, [0, 1, 2], "implements")]:
        if value is not None and value not in options:
            raise FitError(f"bad_{what.replace(' ', '_')}", f"{value!r} is not a valid {what}",
                           closest(str(value), [str(o) for o in options]) or [str(o) for o in options])
    if bodyweight_fraction is not None and not 0 <= bodyweight_fraction <= 1:
        raise FitError("bad_bodyweight_fraction", "bodyweight fraction must be between 0 and 1")


def _parse_muscles(conn: sqlite3.Connection, spec: str) -> dict[str, float]:
    """'chest=1,triceps=0.5' -> {chest: 1.0, triceps: 0.5}."""
    out = {}
    for part in spec.replace(" ", "").split(","):
        name, _, credit = part.partition("=")
        out[resolve.lookup(conn, "muscle", name, "muscle")] = float(credit or 1)
        if not 0 < out[resolve.lookup(conn, "muscle", name, "muscle")] <= 1:
            raise FitError("bad_credit", f"{name}: set credit must be between 0 and 1")
    return out


def _parse_needs(conn: sqlite3.Connection, needs: list[str]) -> list[list[str]]:
    """['dumbbells', 'flat_bench|adjustable_bench'] -> slots of alternative equipment kinds."""
    kinds = [r[0] for r in conn.execute("SELECT id FROM equipment_kind")]
    slots = []
    for slot in needs:
        alternatives = slot.split("|")
        for k in alternatives:
            if k not in kinds:
                raise FitError("unknown_equipment_kind", f"{k!r} is not an equipment kind", closest(k, kinds))
        slots.append(alternatives)
    return slots


def add(conn: sqlite3.Connection, w: Writer, *, name: str, pattern: str, laterality: str, measure: str, implements: int,
        bodyweight_fraction: float, muscles: str, needs: list[str], no_equipment: bool, aliases: list[str]) -> dict:
    """Every field is required (A19): the catalog never holds a half-described exercise."""
    if conn.execute("SELECT 1 FROM exercise_alias WHERE alias = ?", (name,)).fetchone() or \
            conn.execute("SELECT 1 FROM exercise WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL", (name,)).fetchone():
        raise FitError("exercise_exists", f"{name!r} is already an exercise or alias", ["fit exercise show " + name])
    _check_values(pattern, laterality, measure, implements, bodyweight_fraction)
    if bool(needs) == no_equipment:
        raise FitError("needs_required", "give equipment needs (--needs, repeat per slot, 'a|b' for alternatives) "
                                         "or --no-equipment, not both")
    slots = _parse_needs(conn, needs)
    credits = _parse_muscles(conn, muscles)
    row = w.insert("exercise", {"name": name, "origin": "user", "movement_pattern": pattern, "laterality": laterality,
                                "measure": measure, "default_implements": implements,
                                "bodyweight_fraction": bodyweight_fraction})
    for m, c in credits.items():
        w.insert("exercise_muscle", {"exercise_id": row["id"], "muscle_id": m, "set_credit": c})
    for n, alternatives in enumerate(slots, 1):
        for k in alternatives:
            w.insert("exercise_equipment", {"exercise_id": row["id"], "slot": n, "equipment_kind_id": k})
    for a in [name, *aliases]:
        w.insert("exercise_alias", {"alias": a, "exercise_id": row["id"]})
    return show(conn, name)


def link(conn: sqlite3.Connection, w: Writer, *, source: str, target: str, kind: str, start_ratio: float | None) -> dict:
    a, b = resolve.exercise(conn, source), resolve.exercise(conn, target)
    if kind not in ("harder", "easier", "substitute"):
        raise FitError("bad_kind", f"{kind!r}: use harder, easier or substitute", ["harder", "easier", "substitute"])
    w.insert("exercise_progression", {"from_exercise_id": a["id"], "to_exercise_id": b["id"], "kind": kind,
                                      "start_ratio": start_ratio})
    return {"from": a["name"], "to": b["name"], "kind": kind, "start_ratio": start_ratio}


def edit(conn: sqlite3.Connection, w: Writer, *, name: str, rename: str | None, fields: dict, muscles: str | None,
         needs: list[str], no_equipment: bool, add_aliases: list[str], remove_aliases: list[str]) -> dict:
    """Change any part of an exercise, built-in or not (A22). Muscles and needs are replaced whole."""
    ex = resolve.exercise(conn, name)
    if not (rename or fields or muscles is not None or needs or no_equipment or add_aliases or remove_aliases):
        raise FitError("nothing_to_change", "give at least one change", ["fit exercise edit --help"])
    _check_values(fields.get("movement_pattern"), fields.get("laterality"), fields.get("measure"),
                  fields.get("default_implements"), fields.get("bodyweight_fraction"))
    if needs and no_equipment:
        raise FitError("needs_conflict", "give --needs or --no-equipment, not both")
    if rename:
        if conn.execute("SELECT 1 FROM exercise_alias WHERE alias = ? AND exercise_id <> ?", (rename, ex["id"])).fetchone():
            raise FitError("exercise_exists", f"{rename!r} already names another exercise")
        fields["name"] = rename
    if fields:
        w.update("exercise", {"id": ex["id"]}, fields)
    if rename and not conn.execute("SELECT 1 FROM exercise_alias WHERE alias = ?", (rename,)).fetchone():
        w.insert("exercise_alias", {"alias": rename, "exercise_id": ex["id"]})  # the old name stays an alias
    if muscles is not None:
        credits = _parse_muscles(conn, muscles)
        for m in [r[0] for r in conn.execute("SELECT muscle_id FROM exercise_muscle WHERE exercise_id = ?", (ex["id"],))]:
            w.delete("exercise_muscle", {"exercise_id": ex["id"], "muscle_id": m})
        for m, c in credits.items():
            w.insert("exercise_muscle", {"exercise_id": ex["id"], "muscle_id": m, "set_credit": c})
    if needs or no_equipment:
        slots = _parse_needs(conn, needs)
        for slot, kind in conn.execute("SELECT slot, equipment_kind_id FROM exercise_equipment WHERE exercise_id = ?",
                                       (ex["id"],)).fetchall():
            w.delete("exercise_equipment", {"exercise_id": ex["id"], "slot": slot, "equipment_kind_id": kind})
        for n, alternatives in enumerate(slots, 1):
            for k in alternatives:
                w.insert("exercise_equipment", {"exercise_id": ex["id"], "slot": n, "equipment_kind_id": k})
    for a in add_aliases:
        if conn.execute("SELECT 1 FROM exercise_alias WHERE alias = ?", (a,)).fetchone():
            raise FitError("alias_exists", f"{a!r} is already an alias")
        w.insert("exercise_alias", {"alias": a, "exercise_id": ex["id"]})
    current_name = rename or ex["name"]
    for a in remove_aliases:
        row = conn.execute("SELECT alias FROM exercise_alias WHERE alias = ? AND exercise_id = ?", (a, ex["id"])).fetchone()
        if row is None or row[0].lower() == current_name.lower():
            raise FitError("bad_alias", f"{a!r} isn't a removable alias of {current_name}")
        w.delete("exercise_alias", {"alias": row[0]})
    return show(conn, current_name)
