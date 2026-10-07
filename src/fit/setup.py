"""People, gyms and equipment."""

import re
import sqlite3

from fit import resolve
from fit.changes import Writer
from fit.errors import FitError, closest

SEXES = ["female", "male"]


def parse_loads(spec: str | None) -> list[float]:
    """'5-30/5' -> 5,10..30. '100' -> 100. '10,15,20' and mixes like '5-30/5,35,40' work too (A20)."""
    if not spec:
        return []
    loads: set[float] = set()
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"([\d.]+)-([\d.]+)/([\d.]+)", part)
        if m:
            lo, hi, step = (float(x) for x in m.groups())
            if step <= 0 or hi < lo:
                raise FitError("bad_loads", f"{part!r}: range must be low-high/step with step > 0")
            n = 0
            while lo + n * step <= hi + 1e-9:
                loads.add(round(lo + n * step, 3))
                n += 1
        else:
            try:
                loads.add(float(part))
            except ValueError:
                raise FitError("bad_loads", f"{part!r} is not a weight or a range like 5-30/5") from None
    return sorted(loads)


def add_person(conn: sqlite3.Connection, w: Writer, name: str, sex: str | None, birth_year: int | None) -> dict:
    if conn.execute("SELECT 1 FROM person WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL", (name,)).fetchone():
        raise FitError("person_exists", f"{name!r} is already set up")
    if sex is not None and sex not in SEXES:
        raise FitError("bad_sex", f"{sex!r} is not one of {SEXES}", SEXES)
    row = w.insert("person", {"name": name, "sex": sex, "birth_year": birth_year})
    return {"person": row["name"], "sex": row["sex"], "birth_year": row["birth_year"]}


def add_gym(conn: sqlite3.Connection, w: Writer, name: str) -> dict:
    if conn.execute("SELECT 1 FROM location WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise FitError("gym_exists", f"{name!r} is already set up")
    w.insert("location", {"name": name})
    return {"gym": name}


def add_equipment(conn: sqlite3.Connection, w: Writer, *, gym: str | None, name: str, kind: str,
                  loads: str | None, unit: str, multiplier: float) -> dict:
    loc = resolve.location(conn, gym)
    if loc is None:
        raise FitError("gym_required", "more than one gym (or none); say which with --gym",
                       [r[0] for r in conn.execute("SELECT name FROM location")])
    kinds = {r[0]: r[1] for r in conn.execute("SELECT id, load_mechanism FROM equipment_kind")}
    if kind not in kinds:
        raise FitError("unknown_equipment_kind", f"{kind!r} is not a known equipment kind", closest(kind, list(kinds)))
    if unit not in ("lb", "kg"):
        raise FitError("bad_unit", f"{unit!r}: equipment is in lb or kg", ["lb", "kg"])
    values = parse_loads(loads)
    if values and kinds[kind] == "none":
        raise FitError("loads_not_allowed", f"{kind} doesn't hold weight")
    row = w.insert("equipment", {"location_id": loc["id"], "kind_id": kind, "name": name, "unit": unit,
                                 "load_multiplier": multiplier})
    for v in values:
        w.insert("equipment_load", {"equipment_id": row["id"], "load": v})
    return {"gym": loc["name"], "equipment": name, "kind": kind, "loads": _summarize(values), "unit": unit,
            "multiplier": multiplier}


def _summarize(loads: list[float]) -> str | None:
    if not loads:
        return None
    if len(loads) > 2:
        steps = {round(b - a, 3) for a, b in zip(loads, loads[1:])}
        if len(steps) == 1:
            return f"{loads[0]:g}-{loads[-1]:g}/{steps.pop():g}"
    return ",".join(f"{x:g}" for x in loads)


def show_gym(conn: sqlite3.Connection, name: str | None) -> dict:
    loc = resolve.location(conn, name)
    if loc is None:
        raise FitError("gym_required", "say which gym", [r[0] for r in conn.execute("SELECT name FROM location")])
    rows = conn.execute("SELECT * FROM equipment WHERE location_id = ? ORDER BY name", (loc["id"],)).fetchall()
    out = []
    for r in rows:
        loads = [x[0] for x in conn.execute("SELECT load FROM equipment_load WHERE equipment_id = ? ORDER BY load",
                                            (r["id"],))]
        out.append({"equipment": r["name"], "kind": r["kind_id"], "loads": _summarize(loads),
                    "unit": r["unit"], "multiplier": r["load_multiplier"]})
    return {"gym": loc["name"], "equipment": out}


def edit_equipment(conn: sqlite3.Connection, w: Writer, *, gym: str | None, name: str, add_loads: str | None,
                   remove_loads: str | None, multiplier: float | None, rename: str | None) -> dict:
    """Change a piece of equipment: e.g. the gym gets heavier dumbbells (A21). Lifts in the plan simply
    keep progressing; capping is calculated. A new multiplier also changes how past sets are totalled
    (store what you read, derive the rest)."""
    loc = resolve.location(conn, gym)
    if loc is None:
        raise FitError("gym_required", "say which gym with --gym", [r[0] for r in conn.execute("SELECT name FROM location")])
    eq = resolve.equipment_named(conn, loc, name)
    have = {r[0] for r in conn.execute("SELECT load FROM equipment_load WHERE equipment_id = ?", (eq["id"],))}
    adding, removing = parse_loads(add_loads), parse_loads(remove_loads)
    mechanism = conn.execute("SELECT load_mechanism FROM equipment_kind WHERE id = ?", (eq["kind_id"],)).fetchone()[0]
    if adding and mechanism == "none":
        raise FitError("loads_not_allowed", f"{eq['kind_id']} doesn't hold weight")
    if missing := [x for x in removing if x not in have]:
        raise FitError("load_not_found", f"{eq['name']} has no {', '.join(f'{x:g}' for x in missing)}",
                       [_summarize(sorted(have)) or "no weights"])
    if not (adding or removing or multiplier is not None or rename):
        raise FitError("nothing_to_change", "give at least one change", ["fit equipment edit --help"])
    for x in adding:
        if x not in have:
            w.insert("equipment_load", {"equipment_id": eq["id"], "load": x})
    for x in removing:
        w.delete("equipment_load", {"equipment_id": eq["id"], "load": x})
    values = {k: v for k, v in dict(load_multiplier=multiplier, name=rename).items() if v is not None}
    if values:
        w.update("equipment", {"id": eq["id"]}, values)
    loads = [r[0] for r in conn.execute("SELECT load FROM equipment_load WHERE equipment_id = ? ORDER BY load", (eq["id"],))]
    return {"gym": loc["name"], "equipment": rename or eq["name"], "loads": _summarize(loads), "unit": eq["unit"],
            "multiplier": multiplier if multiplier is not None else eq["load_multiplier"]}
