"""Turning names into rows. Every miss is a FitError with suggestions; nothing is guessed (A26)."""

import os
import sqlite3

from fit.errors import FitError, closest


def person(conn: sqlite3.Connection, name: str | None) -> sqlite3.Row:
    people = conn.execute("SELECT * FROM person WHERE deleted_at IS NULL ORDER BY name").fetchall()
    if not people:
        raise FitError("no_people", "no one is set up yet", ["fit person add <name>"])
    name = name or os.environ.get("FIT_PERSON")
    if name is None:
        if len(people) == 1:
            return people[0]
        raise FitError("person_required", "more than one person; say whose this is (--person or FIT_PERSON)",
                       [p["name"] for p in people])
    for p in people:
        if p["name"].lower() == name.lower():
            return p
    raise FitError("unknown_person", f"no person named {name!r}", closest(name, [p["name"] for p in people]))


def exercise(conn: sqlite3.Connection, text: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT e.* FROM exercise_alias a JOIN exercise e ON e.id = a.exercise_id "
        "WHERE a.alias = ? AND e.deleted_at IS NULL", (text,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM exercise WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL",
                           (text,)).fetchone()
    if row is not None:
        return row
    names = [r[0] for r in conn.execute(
        "SELECT name FROM exercise WHERE deleted_at IS NULL UNION SELECT alias FROM exercise_alias")]
    raise FitError("unknown_exercise", f"no exercise or alias named {text!r}", closest(text, names))


def location(conn: sqlite3.Connection, name: str | None) -> sqlite3.Row | None:
    rows = conn.execute("SELECT * FROM location ORDER BY name").fetchall()
    if name is None:
        return rows[0] if len(rows) == 1 else None
    for r in rows:
        if r["name"].lower() == name.lower():
            return r
    raise FitError("unknown_gym", f"no gym named {name!r}", closest(name, [r["name"] for r in rows]))


def equipment(conn: sqlite3.Connection, exercise_row: sqlite3.Row, loc: sqlite3.Row | None,
              name: str | None) -> sqlite3.Row | None:
    """The equipment an exercise was done on: named explicitly, or the one piece at the gym that
    provides its load (first matching kind in the exercise's needs). None = body weight."""
    if loc is None:
        if name:
            raise FitError("gym_required", f"equipment {name!r} given but no gym; pass --gym or set location")
        return None
    if name:
        return equipment_named(conn, loc, name)
    return conn.execute(
        "SELECT eq.* FROM exercise_equipment ee "
        "JOIN equipment_kind k ON k.id = ee.equipment_kind_id AND k.load_mechanism <> 'none' "
        "JOIN equipment eq ON eq.kind_id = k.id AND eq.location_id = ? "
        "WHERE ee.exercise_id = ? ORDER BY ee.slot, ee.rowid LIMIT 1",
        (loc["id"], exercise_row["id"])).fetchone()


def equipment_named(conn: sqlite3.Connection, loc: sqlite3.Row, name: str) -> sqlite3.Row:
    rows = conn.execute("SELECT * FROM equipment WHERE location_id = ?", (loc["id"],)).fetchall()
    for r in rows:
        if r["name"].lower() == name.lower():
            return r
    raise FitError("unknown_equipment", f"no equipment named {name!r} at {loc['name']}",
                   closest(name, [r["name"] for r in rows]))


def lookup(conn: sqlite3.Connection, table: str, value: str, what: str) -> str:
    """Validate a value against a lookup table (body parts, symptom kinds, ...)."""
    ids = [r[0] for r in conn.execute(f"SELECT id FROM {table}")]
    if value in ids:
        return value
    raise FitError(f"unknown_{what.replace(' ', '_')}", f"{value!r} is not a known {what}", closest(value, ids))


def program_day(conn: sqlite3.Connection, person_id: str, label: str, date: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT d.* FROM program_day d JOIN program p ON p.id = d.program_id "
        "WHERE p.person_id = ? AND p.deleted_at IS NULL AND d.label = ? "
        "AND p.starts_on <= ? AND (p.ends_on IS NULL OR p.ends_on >= ?) "
        "ORDER BY p.starts_on DESC LIMIT 1", (person_id, label, date, date)).fetchone()
    if row is None:
        labels = [r[0] for r in conn.execute(
            "SELECT DISTINCT d.label FROM program_day d JOIN program p ON p.id = d.program_id "
            "WHERE p.person_id = ? AND p.starts_on <= ? AND (p.ends_on IS NULL OR p.ends_on >= ?)",
            (person_id, date, date))]
        raise FitError("unknown_day", f"no plan day {label!r} on {date}", labels)
    return row
