"""The vocabularies that are meant to grow (D19, D36): adding "foot" or "numbness" is an insert,
done through the CLI like every other write. Fixed vocabularies (sides, severities, skip reasons,
progression rules) are code, not data, and aren't listed here."""

import re
import sqlite3

from fit.changes import Writer
from fit.errors import FitError, closest

# name -> (table, extra column, allowed values for it or None if the column is a yes/no flag)
VOCABS: dict[str, tuple[str, str | None, list[str] | None]] = {
    "body_part": ("body_part", "paired", None),
    "symptom_kind": ("symptom_kind", None, None),
    "muscle": ("muscle", "region", ["upper", "lower", "core"]),
    "equipment_kind": ("equipment_kind", "load_mechanism", ["dumbbell", "stack", "fixed", "loadable", "none"]),
    "measurement_kind": ("measurement_kind", "dimension", ["mass", "length", "rate"]),
    "cardio_activity": ("cardio_activity", None, None),
    "cardio_workout_type": ("cardio_workout_type", None, None),
}


def _vocab(name: str) -> tuple[str, str | None, list[str] | None]:
    if name not in VOCABS:
        raise FitError("unknown_vocabulary", f"{name!r} isn't a vocabulary that can grow", closest(name, list(VOCABS)) or list(VOCABS))
    return VOCABS[name]


def list_(conn: sqlite3.Connection, name: str | None) -> dict:
    names = [name] if name else list(VOCABS)
    out = {}
    for n in names:
        table, extra, _ = _vocab(n)
        cols = "id" + (f", {extra}" if extra else "")
        rows = conn.execute(f"SELECT {cols} FROM {table} ORDER BY id").fetchall()
        out[n] = [dict(r) for r in rows] if extra else [r[0] for r in rows]
    return out


def add(conn: sqlite3.Connection, w: Writer, name: str, value: str, extra_value) -> dict:
    table, extra, allowed = _vocab(name)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise FitError("bad_value", f"{value!r}: use lowercase words joined by underscores, e.g. lower_back")
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (value,)).fetchone():
        raise FitError("already_exists", f"{value!r} is already a {name.replace('_', ' ')}")
    row: dict[str, object] = {"id": value}
    if extra:
        if extra_value is None:
            options = allowed or ["yes", "no"]
            raise FitError("missing_field", f"a {name.replace('_', ' ')} needs --{extra.replace('_', '-')}", options)
        if allowed is not None and extra_value not in allowed:
            raise FitError(f"bad_{extra}", f"{extra_value!r} is not a valid {extra}", closest(str(extra_value), allowed) or allowed)
        row[extra] = int(extra_value) if allowed is None else extra_value
    w.insert(table, row)
    return {"vocabulary": name, "added": value, **({extra: row[extra]} if extra else {})}
