"""The `fit` command line. Thin: parse arguments, open the database, call into the modules, emit."""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, Optional

import toon_format
import typer

from fit import corrections, db, exercises, notelog, notes, output, plan, record, resolve, setup, stats, view, vocab
from fit.changes import undo as undo_last
from fit.changes import writing
from fit.errors import FitError

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False, add_completion=False,
                  help="Log workouts and cardio, and track progress. Built to be driven by an agent: "
                       "output is TOON when not at a terminal (--toon / --table to force).")
person_app = typer.Typer(no_args_is_help=True, help="People who train.")
gym_app = typer.Typer(no_args_is_help=True, help="Gyms.")
equipment_app = typer.Typer(no_args_is_help=True, help="Equipment at a gym.")
cardio_app = typer.Typer(no_args_is_help=True, help="Runs, rides and structured cardio workouts.")
set_app = typer.Typer(no_args_is_help=True, help="Log one set at a time (for people; agents use `fit log`).")
symptom_app = typer.Typer(no_args_is_help=True, help="Pain, clicking, soreness during a session.")
measure_app = typer.Typer(no_args_is_help=True, help="Body weight and other measurements.")
exercise_app = typer.Typer(no_args_is_help=True, help="The exercise catalog.")
plan_app = typer.Typer(no_args_is_help=True, help="Training plans: days of exercise slots that change over time.")
session_app = typer.Typer(no_args_is_help=True, help="Whole sessions: delete one to re-log it.")
vocab_app = typer.Typer(no_args_is_help=True, help="Lists that can grow: body parts, symptom kinds, muscles, equipment kinds...")
notes_app = typer.Typer(no_args_is_help=True, help="Apple Notes on the Mac: where gym templates go and filled notes come from.")
for sub, name in [(person_app, "person"), (gym_app, "gym"), (equipment_app, "equipment"), (cardio_app, "cardio"),
                  (set_app, "set"), (symptom_app, "symptom"), (measure_app, "measure"), (exercise_app, "exercise"),
                  (plan_app, "plan"), (notes_app, "notes"),
                  (vocab_app, "vocab"), (session_app, "session")]:
    app.add_typer(sub, name=name)

_db_path: Path | None = None

SessionId = Annotated[Optional[str], typer.Option("--id", help="Session id, when a day has more than one (see fit show).")]


Person = Annotated[Optional[str], typer.Option("--person", "-p", help="Whose (default: the only person, or $FIT_PERSON).")]
Day = Annotated[Optional[str], typer.Option("--date", "-d", help="YYYY-MM-DD (default: today).")]


@app.callback()
def main(
    db_path: Annotated[Optional[Path], typer.Option("--db", help="Database file (default: $FIT_DB or ~/.local/share/fit/fit.db).")] = None,
    toon: Annotated[bool, typer.Option("--toon", help="Force TOON output.")] = False,
    table: Annotated[bool, typer.Option("--table", help="Force table output.")] = False,
):
    global _db_path
    _db_path = db_path or db.default_path()
    output.set_mode("toon" if toon else "table" if table else None)


# ── plumbing ─────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    assert _db_path is not None
    if not _db_path.exists():
        raise FitError("not_initialized", f"no database at {_db_path}", ["fit init"])
    return db.connect(_db_path)


def _run(work) -> None:
    try:
        output.emit(work())
    except FitError as e:
        output.emit_error(e.as_dict())
        raise typer.Exit(1)
    except sqlite3.IntegrityError as e:
        output.emit_error({"error": "constraint", "message": str(e), "suggestions": []})
        raise typer.Exit(1)


def _read(conn_work):
    return lambda: conn_work(_connect())


def _write(command: str, work):
    """work(conn, writer) -> result; runs as one change set (one undo)."""
    def go():
        conn = _connect()
        with writing(conn, command, output.actor()) as w:
            return work(conn, w)
    return go


def _document(source: str) -> dict:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    try:
        doc = json.loads(text) if text.lstrip().startswith("{") else toon_format.decode(text)
    except Exception as e:  # the TOON decoder raises several types
        raise FitError("bad_document", f"couldn't read the document: {e}") from None
    if not isinstance(doc, dict):
        raise FitError("bad_document", "the document must be an object (key: value lines)")
    return doc


# ── setup ────────────────────────────────────────────────────────────────────

@app.command()
def init():
    """Create the database with the built-in exercise catalog.

    At a terminal it also asks who's training and sets up a gym."""
    def go():
        assert _db_path is not None
        if _db_path.exists():
            raise FitError("already_initialized", f"a database already exists at {_db_path}")
        conn = db.create(_db_path)
        result = {"database": str(_db_path),
                  "exercises": conn.execute("SELECT COUNT(*) FROM exercise").fetchone()[0]}
        if sys.stdin.isatty() and output.mode() == "table":
            _wizard(conn)
        return result
    _run(go)


def _wizard(conn: sqlite3.Connection) -> None:
    typer.echo("Who's training? Leave the name blank when done.")
    while name := typer.prompt("  name", default="", show_default=False).strip():
        sex = typer.prompt("  sex (female/male, blank to skip)", default="", show_default=False).strip() or None
        year = typer.prompt("  birth year (blank to skip)", default="", show_default=False).strip()
        with writing(conn, "person add", "person") as w:
            setup.add_person(conn, w, name, sex, int(year) if year else None)
    gym = typer.prompt("Gym name (blank to skip)", default="", show_default=False).strip()
    if not gym:
        return
    with writing(conn, "gym add", "person") as w:
        setup.add_gym(conn, w, gym)
    kinds = [r[0] for r in conn.execute("SELECT id FROM equipment_kind ORDER BY id")]
    typer.echo(f"Equipment kinds: {', '.join(kinds)}. Leave the kind blank when done.")
    while kind := typer.prompt("  kind", default="", show_default=False).strip():
        name = typer.prompt("  name", default=kind.replace("_", " ").capitalize())
        loads = typer.prompt("  weights (e.g. 5-30/5, 100, blank for none)", default="", show_default=False)
        with writing(conn, "equipment add", "person") as w:
            setup.add_equipment(conn, w, gym=gym, name=name, kind=kind, loads=loads or None, unit="lb", multiplier=1.0)


@person_app.command("add")
def person_add(name: str,
               sex: Annotated[Optional[str], typer.Option(help="female or male.")] = None,
               birth_year: Annotated[Optional[int], typer.Option(help="e.g. 1990.")] = None):
    """Add a person."""
    _run(_write("person add", lambda conn, w: setup.add_person(conn, w, name, sex, birth_year)))


@gym_app.command("add")
def gym_add(name: str):
    """Add a gym."""
    _run(_write("gym add", lambda conn, w: setup.add_gym(conn, w, name)))


@gym_app.command("show")
def gym_show(name: Annotated[Optional[str], typer.Argument()] = None):
    """A gym's equipment and available weights."""
    _run(_read(lambda conn: setup.show_gym(conn, name)))


@equipment_app.command("add")
def equipment_add(
    name: str,
    kind: Annotated[str, typer.Option(help="Equipment kind, e.g. dumbbells, cable_station.")],
    gym: Annotated[Optional[str], typer.Option(help="Gym (default: the only one).")] = None,
    loads: Annotated[Optional[str], typer.Option(help="Weights it can be set to: 5-30/5, 100, or 10,15,20.")] = None,
    unit: Annotated[str, typer.Option(help="lb or kg.")] = "lb",
    multiplier: Annotated[float, typer.Option(help="Pulley ratio, e.g. 2 for a 2:1 leg press.")] = 1.0,
):
    """Add equipment to a gym."""
    _run(_write("equipment add", lambda conn, w: setup.add_equipment(
        conn, w, gym=gym, name=name, kind=kind, loads=loads, unit=unit, multiplier=multiplier)))


@equipment_app.command("edit")
def equipment_edit(
    name: str,
    gym: Annotated[Optional[str], typer.Option(help="Gym (default: the only one).")] = None,
    add_loads: Annotated[Optional[str], typer.Option(help="Weights to add: 35-50/5, or 32.5,37.5.")] = None,
    remove_loads: Annotated[Optional[str], typer.Option(help="Weights to remove.")] = None,
    multiplier: Annotated[Optional[float], typer.Option(help="New pulley ratio (also changes past totals).")] = None,
    rename: Annotated[Optional[str], typer.Option()] = None,
):
    """Change a piece of equipment, e.g. the gym got heavier dumbbells."""
    _run(_write("equipment edit", lambda conn, w: setup.edit_equipment(
        conn, w, gym=gym, name=name, add_loads=add_loads, remove_loads=remove_loads, multiplier=multiplier,
        rename=rename)))


@exercise_app.command("alias")
def exercise_alias(exercise: str, alias: str):
    """Add another name for an exercise (how it's written in your notes)."""
    def work(conn, w):
        ex = resolve.exercise(conn, exercise)
        if conn.execute("SELECT 1 FROM exercise_alias WHERE alias = ?", (alias,)).fetchone():
            raise FitError("alias_exists", f"{alias!r} is already an alias")
        w.insert("exercise_alias", {"alias": alias, "exercise_id": ex["id"]})
        return {"exercise": ex["name"], "alias": alias}
    _run(_write("exercise alias", work))


# ── logging ──────────────────────────────────────────────────────────────────

@app.command()
def log(source: Annotated[str, typer.Argument(help="A TOON (or JSON) session document, or - for stdin.")] = "-"):
    """Log a whole strength session in one write (agents)."""  # replaced below by the format reference
    def go():
        doc = _document(source)
        conn = _connect()
        with writing(conn, "log", output.actor()) as w:
            session_id = record.log_strength(conn, w, doc)
        return view.session(conn, session_id)
    _run(go)


log.__doc__ = "Log a whole strength session from one TOON document (agents).\n\n" + (record.__doc__ or "").strip()


@cardio_app.command("log")
def cardio_log(source: Annotated[str, typer.Argument(help="A TOON (or JSON) cardio document, or - for stdin.")] = "-"):
    """Log a run or ride, with optional splits or interval segments."""
    def go():
        doc = _document(source)
        conn = _connect()
        with writing(conn, "cardio log", output.actor()) as w:
            session_id = record.log_cardio(conn, w, doc)
        return view.session(conn, session_id)
    _run(go)


@set_app.command("add")
def set_add(
    exercise: str,
    load: Annotated[str, typer.Argument(help="Weight per implement, or BW.")],
    reps: Annotated[Optional[int], typer.Argument()] = None,
    person: Person = None, date: Day = None,
    seconds: Annotated[Optional[int], typer.Option(help="For timed exercises.")] = None,
    rp: Annotated[Optional[int], typer.Option("--rp", help="Reps after failing and a short rest.")] = None,
    failure: Annotated[bool, typer.Option(help="Taken to failure.")] = False,
    side: Annotated[Optional[str], typer.Option(help="left or right, when sides differ.")] = None,
    gym: Annotated[Optional[str], typer.Option()] = None,
    equipment: Annotated[Optional[str], typer.Option()] = None,
):
    """Add one set to today's (or --date's) session."""
    _run(_write("set add", lambda conn, w: record.add_set(
        conn, w, person=person, date=date, exercise=exercise, load=load, reps=reps, seconds=seconds,
        rest_pause_reps=rp, failure=failure, side=side, gym=gym, equipment=equipment)))


@app.command()
def skip(exercise: str,
         reason: Annotated[str, typer.Argument(help="pain, time, equipment, fatigue or other.")],
         day: Annotated[str, typer.Option(help="Plan day label, e.g. B.")],
         person: Person = None, date: Day = None):
    """Record that a planned exercise was skipped."""
    _run(_write("skip", lambda conn, w: record.skip(conn, w, person=person, date=date, exercise=exercise,
                                                     reason=reason, day=day)))


@symptom_app.command("add")
def symptom_add(body_part: str, kind: str,
                severity: Annotated[str, typer.Argument(help="mild, moderate or severe.")],
                side: Annotated[Optional[str], typer.Option(help="left, right or both (knees, shoulders...).")] = None,
                exercise: Annotated[Optional[str], typer.Option(help="The exercise it happened on.")] = None,
                person: Person = None, date: Day = None):
    """Record pain, clicking, soreness or tightness during a session."""
    _run(_write("symptom add", lambda conn, w: record.add_symptom(
        conn, w, person=person, date=date, body_part=body_part, kind=kind, severity=severity, side=side,
        exercise=exercise)))


@measure_app.command("add")
def measure_add(kind: Annotated[str, typer.Argument(help="bodyweight or height.")], value: float,
                unit: Annotated[Optional[str], typer.Option(help="lb/kg or in/cm (default lb / in).")] = None,
                person: Person = None, date: Day = None):
    """Record a measurement, e.g. `fit measure add bodyweight 172`."""
    _run(_write("measure add", lambda conn, w: record.add_measurement(
        conn, w, person=person, kind=kind, value=value, unit=unit, date=date)))


# ── reading and undo ─────────────────────────────────────────────────────────

@app.command()
def show(date: Annotated[Optional[str], typer.Argument(help="YYYY-MM-DD (default: the latest day logged).")] = None,
         person: Person = None,
         sets: Annotated[bool, typer.Option("--sets", help="Include every set as a row.")] = False):
    """Everything logged on one day."""
    _run(_read(lambda conn: view.show(conn, person, record.parse_date(date) if date else None, sets)))


@app.command()
def undo():
    """Reverse the most recent write (one command = one write)."""
    _run(_read(lambda conn: undo_last(conn, output.actor())))


# ── plans and templates ──────────────────────────────────────────────────────

@plan_app.command("load")
def plan_load(source: Annotated[str, typer.Argument(help="A TOON (or JSON) plan document, or - for stdin.")] = "-",
              replace: Annotated[bool, typer.Option(help="End the current plan the day before this one starts.")] = False):
    """Create a plan from a document."""
    def go():
        doc = _document(source)
        conn = _connect()
        with writing(conn, "plan load", output.actor()) as w:
            return plan.load(conn, w, doc, replace)
    _run(go)


plan_load.__doc__ = "Create a plan from one TOON document.\n\n" + (plan.__doc__ or "").strip()


@plan_app.command("show")
def plan_show(person: Person = None,
              on: Annotated[Optional[str], typer.Option("--on", help="The plan as it was on this date.")] = None):
    """The current plan (or the plan on a past date)."""
    _run(_read(lambda conn: plan.show(conn, person, on_text=on)))


@plan_app.command("swap")
def plan_swap(day: str, slot: int, exercise: str,
              start_load: Annotated[Optional[float], typer.Option(help="Starting weight per implement.")] = None,
              person: Person = None, date: Day = None):
    """Replace the exercise in a slot from --date on. The old one stays in the plan's history."""
    _run(_write("plan swap", lambda conn, w: plan.swap(conn, w, person=person, day=day, position=slot,
                                                        exercise=exercise, start_load=start_load, on_text=date)))


@plan_app.command("edit")
def plan_edit(
    day: str, slot: int, person: Person = None, date: Day = None,
    sets: Annotated[Optional[int], typer.Option()] = None,
    rep_min: Annotated[Optional[int], typer.Option()] = None,
    rep_max: Annotated[Optional[int], typer.Option()] = None,
    seconds_min: Annotated[Optional[int], typer.Option()] = None,
    seconds_max: Annotated[Optional[int], typer.Option()] = None,
    start_load: Annotated[Optional[float], typer.Option()] = None,
    implements: Annotated[Optional[int], typer.Option()] = None,
    tempo: Annotated[Optional[str], typer.Option(help="e.g. 3-1-1-0")] = None,
    target_rir: Annotated[Optional[int], typer.Option(help="Stop this many reps short.")] = None,
    progression: Annotated[Optional[str], typer.Option(help="double, linear or none.")] = None,
    load_cap: Annotated[Optional[float], typer.Option(help="Never go above this weight.")] = None,
):
    """Change a slot's sets, reps, tempo... in place."""
    changes = {k: v for k, v in dict(sets=sets, rep_min=rep_min, rep_max=rep_max, seconds_min=seconds_min,
                                     seconds_max=seconds_max, start_load=start_load, implements=implements,
                                     tempo=tempo, target_rir=target_rir, progression=progression,
                                     load_cap=load_cap).items() if v is not None}
    _run(_write("plan edit", lambda conn, w: plan.edit(conn, w, person=person, day=day, position=slot,
                                                        changes=changes, on_text=date)))


@plan_app.command("add-day")
def plan_add_day(label: str, name: Annotated[Optional[str], typer.Option(help="Short label, e.g. 'Push'.")] = None,
                 person: Person = None, date: Day = None):
    """Add a day to the current plan."""
    _run(_write("plan add-day", lambda conn, w: plan.add_day(conn, w, person=person, label=label, name=name,
                                                              on_text=date)))


@plan_app.command("add-slot")
def plan_add_slot(
    day: str, exercise: str,
    sets: Annotated[int, typer.Option()],
    rep_min: Annotated[Optional[int], typer.Option()] = None,
    rep_max: Annotated[Optional[int], typer.Option()] = None,
    seconds_min: Annotated[Optional[int], typer.Option()] = None,
    seconds_max: Annotated[Optional[int], typer.Option()] = None,
    start_load: Annotated[Optional[float], typer.Option()] = None,
    implements: Annotated[Optional[int], typer.Option()] = None,
    tempo: Annotated[Optional[str], typer.Option()] = None,
    target_rir: Annotated[Optional[int], typer.Option()] = None,
    progression: Annotated[Optional[str], typer.Option(help="double, linear or none.")] = None,
    load_cap: Annotated[Optional[float], typer.Option()] = None,
    slot: Annotated[Optional[int], typer.Option(help="Position (default: after the last slot).")] = None,
    person: Person = None, date: Day = None,
):
    """Add an exercise to a plan day from --date on."""
    values = {k: v for k, v in dict(sets=sets, rep_min=rep_min, rep_max=rep_max, seconds_min=seconds_min,
                                    seconds_max=seconds_max, start_load=start_load, implements=implements, tempo=tempo,
                                    target_rir=target_rir, progression=progression, load_cap=load_cap).items()
              if v is not None}
    _run(_write("plan add-slot", lambda conn, w: plan.add_slot(conn, w, person=person, day=day, exercise=exercise,
                                                                values=values, position=slot, on_text=date)))


@plan_app.command("drop-slot")
def plan_drop_slot(day: str, slot: int, person: Person = None, date: Day = None):
    """Stop doing a slot from --date on (it stays in the plan's history)."""
    _run(_write("plan drop-slot", lambda conn, w: plan.drop_slot(conn, w, person=person, day=day, position=slot,
                                                                  on_text=date)))


@app.command("next")
def next_(person: Person = None,
          day: Annotated[Optional[str], typer.Option(help="Plan day (default: the one done longest ago).")] = None,
          date: Day = None,
          to_notes: Annotated[bool, typer.Option("--to-notes", help="Also create the note in Apple Notes (Gym folder).")] = False,
          replace: Annotated[bool, typer.Option("--replace", help="With --to-notes: rewrite an existing note of the same "
                                                              "name (its old text is archived first).")] = False):
    """The gym template for the next session, with computed target weights.

    Each exercise gets its target weight and blank reps. If an exercise is maxed at the heaviest
    weight available, it's swapped in the plan for the next harder exercise (undoable)."""
    def go():
        conn = _connect()
        result = plan.next_session(conn, lambda: writing(conn, "next", output.actor()),
                                   person=person, day=day, on_text=date)
        if to_notes:
            result["note"] = notes.push(result["template"], _archive_dir(), replace=replace)
        if output.mode() == "table":
            return result["template"] + ("" if not result["swapped"] else
                                         "\n\n" + "\n".join(f"swapped: {s['from']} -> {s['to']}" for s in result["swapped"]))
        return result
    _run(go)


def _archive_dir() -> Path:
    assert _db_path is not None
    return _db_path.parent / "notes"


@notes_app.command("pull")
def notes_pull(title: str, folder: Annotated[str, typer.Option(help="Notes folder.")] = notes.FOLDER):
    """Read a filled-in note as plain text, keeping an unedited copy next to the database.

    Log it with source_ref set to the archived path."""
    _run(lambda: notes.pull(title, _archive_dir(), folder))


@notes_app.command("list")
def notes_list(folder: Annotated[str, typer.Option(help="Notes folder.")] = notes.FOLDER):
    """Note titles in the Gym folder."""
    _run(lambda: {"folder": folder, "notes": notes.titles(folder)})


# ── reading ──────────────────────────────────────────────────────────────────

Since = Annotated[Optional[str], typer.Option("--since", help="YYYY-MM-DD.")]
On = Annotated[Optional[str], typer.Option("--on", help="As of this date (default: today).")]


@app.command()
def history(exercise: str, person: Person = None, since: Since = None):
    """Every session of one exercise: weights by set, volume, estimated max (mean and low-high)."""
    _run(_read(lambda conn: stats.history(conn, person, exercise, since)))


@app.command()
def e1rm(exercise: str, person: Person = None):
    """Estimated one-rep max per session by all seven formulas, with their spread."""
    _run(_read(lambda conn: stats.e1rm(conn, person, exercise)))


@app.command()
def bests(person: Person = None, exercise: Annotated[Optional[str], typer.Option()] = None):
    """Personal bests per exercise: heaviest weight (and most reps at it), best estimated max, best volume."""
    _run(_read(lambda conn: stats.bests(conn, person, exercise)))


@app.command()
def muscles(person: Person = None, weeks: Annotated[int, typer.Option(help="How many weeks back.")] = 4, on: On = None):
    """Weekly working sets and volume per muscle (primary = 1 set, secondary = ½)."""
    _run(_read(lambda conn: stats.muscles(conn, person, weeks, on)))


@app.command()
def consistency(person: Person = None, weeks: Annotated[int, typer.Option(help="How many weeks to list.")] = 8,
                on: On = None):
    """Sessions per week and the current and longest streak of weeks with lifting."""
    _run(_read(lambda conn: stats.consistency(conn, person, weeks, on)))


@app.command()
def capped(person: Person = None):
    """Lifts whose last session was at the equipment's heaviest weight with the top of the rep range."""
    _run(_read(lambda conn: stats.capped(conn, person)))


@app.command("exercise-age")
def exercise_age(person: Person = None, on: On = None):
    """How long each exercise in the current plan has been in use, counted from the log."""
    _run(_read(lambda conn: stats.exercise_age(conn, person, on)))


@cardio_app.command("edit")
def cardio_edit(date: Annotated[Optional[str], typer.Argument(help="YYYY-MM-DD (default: today).")] = None,
                person: Person = None, session_id: SessionId = None,
                type: Annotated[Optional[str], typer.Option(help="Workout type, e.g. long, tempo; 'none' clears it.")] = None,
                activity: Annotated[Optional[str], typer.Option(help="run, mountain_bike...")] = None,
                distance: Annotated[Optional[float], typer.Option()] = None,
                unit: Annotated[Optional[str], typer.Option(help="mi, km or m.")] = None,
                moving_time: Annotated[Optional[str], typer.Option(help="mm:ss or h:mm:ss.")] = None):
    """Fix a cardio session's type, activity, distance or moving time."""
    changes = {k: v for k, v in dict(type=type, activity=activity, distance=distance, unit=unit,
                                     moving_time=moving_time).items() if v is not None}
    _run(_write("cardio edit", lambda conn, w: corrections.edit_cardio(
        conn, w, corrections.find_session(conn, person=person, date=date, kind="cardio", session_id=session_id), changes)))


@cardio_app.command("list")
def cardio_list(person: Person = None, since: Since = None):
    """Cardio sessions with distance, time and pace, plus miles per week."""
    _run(_read(lambda conn: stats.cardio(conn, person, since)))


@cardio_app.command("show")
def cardio_show(date: Annotated[Optional[str], typer.Argument(help="YYYY-MM-DD (default: the latest cardio day).")] = None,
                person: Person = None):
    """One day's cardio in full: totals, then each segment with its pace."""
    _run(_read(lambda conn: view.show(conn, person, record.parse_date(date) if date else None, kind="cardio")))


@app.command()
def body(person: Person = None, kind: Annotated[Optional[str], typer.Option(help="bodyweight or height.")] = None):
    """Measurements over time."""
    _run(_read(lambda conn: stats.body(conn, person, kind)))


@person_app.command("list")
def person_list():
    """Everyone set up."""
    _run(_read(lambda conn: {"people": [dict(r) for r in conn.execute(
        "SELECT name, sex, birth_year FROM person WHERE deleted_at IS NULL ORDER BY name")]}))


@gym_app.command("list")
def gym_list():
    """All gyms."""
    _run(_read(lambda conn: {"gyms": [r[0] for r in conn.execute("SELECT name FROM location ORDER BY name")]}))


# ── the catalog ──────────────────────────────────────────────────────────────

@exercise_app.command("find")
def exercise_find(
    muscle: Annotated[Optional[str], typer.Option(help="Trains this muscle (as a primary mover unless --any).")] = None,
    any_role: Annotated[bool, typer.Option("--any", help="Count secondary muscles too.")] = False,
    pattern: Annotated[Optional[str], typer.Option(help="Movement pattern, e.g. hinge.")] = None,
    gym: Annotated[Optional[str], typer.Option(help="Only exercises this gym's equipment allows.")] = None,
    laterality: Annotated[Optional[str], typer.Option(help="bilateral or unilateral.")] = None,
):
    """Search the catalog by muscle, pattern and what a gym's equipment allows."""
    _run(_read(lambda conn: exercises.find(conn, muscle=muscle, any_role=any_role, pattern=pattern, gym=gym,
                                           laterality=laterality)))


@exercise_app.command("list")
def exercise_list():
    """Every exercise in the catalog."""
    _run(_read(lambda conn: exercises.find(conn, muscle=None, any_role=False, pattern=None, gym=None, laterality=None)))


@exercise_app.command("show")
def exercise_show(name: str):
    """One exercise: muscles, equipment needs, aliases, harder and easier versions."""
    _run(_read(lambda conn: exercises.show(conn, name)))


@exercise_app.command("add")
def exercise_add(
    name: str,
    pattern: Annotated[str, typer.Option(help="Movement pattern, e.g. hinge, horizontal_pull.")],
    laterality: Annotated[str, typer.Option(help="bilateral or unilateral.")],
    measure: Annotated[str, typer.Option(help="reps or time.")],
    implements: Annotated[int, typer.Option(help="Implements held: 0, 1 or 2.")],
    bodyweight_fraction: Annotated[float, typer.Option(help="Share of body weight moved; 0 if not counted.")],
    muscles: Annotated[str, typer.Option(help="e.g. 'chest=1,triceps=0.5'.")],
    needs: Annotated[Optional[list[str]], typer.Option(help="Equipment kinds, one option per slot; 'a|b' = either.")] = None,
    no_equipment: Annotated[bool, typer.Option("--no-equipment", help="Needs no equipment.")] = False,
    alias: Annotated[Optional[list[str]], typer.Option(help="Other names; repeatable.")] = None,
):
    """Add an exercise. Every field is required."""
    _run(_write("exercise add", lambda conn, w: exercises.add(
        conn, w, name=name, pattern=pattern, laterality=laterality, measure=measure, implements=implements,
        bodyweight_fraction=bodyweight_fraction, muscles=muscles, needs=needs or [], no_equipment=no_equipment,
        aliases=alias or [])))


@exercise_app.command("edit")
def exercise_edit(
    name: str,
    rename: Annotated[Optional[str], typer.Option(help="New name; the old one stays as an alias.")] = None,
    pattern: Annotated[Optional[str], typer.Option()] = None,
    laterality: Annotated[Optional[str], typer.Option(help="bilateral or unilateral.")] = None,
    measure: Annotated[Optional[str], typer.Option(help="reps or time.")] = None,
    implements: Annotated[Optional[int], typer.Option()] = None,
    bodyweight_fraction: Annotated[Optional[float], typer.Option()] = None,
    muscles: Annotated[Optional[str], typer.Option(help="Replaces the muscle map, e.g. 'lats=1,upper_back=1'.")] = None,
    needs: Annotated[Optional[list[str]], typer.Option(help="Replaces equipment needs; one per slot, 'a|b' = either.")] = None,
    no_equipment: Annotated[bool, typer.Option("--no-equipment", help="Replace needs with none.")] = False,
    alias: Annotated[Optional[list[str]], typer.Option(help="Add another name; repeatable.")] = None,
    remove_alias: Annotated[Optional[list[str]], typer.Option(help="Remove a name; repeatable.")] = None,
):
    """Change an exercise, built-in or your own."""
    fields = {k: v for k, v in dict(movement_pattern=pattern, laterality=laterality, measure=measure,
                                    default_implements=implements, bodyweight_fraction=bodyweight_fraction).items()
              if v is not None}
    _run(_write("exercise edit", lambda conn, w: exercises.edit(
        conn, w, name=name, rename=rename, fields=fields, muscles=muscles, needs=needs or [], no_equipment=no_equipment,
        add_aliases=alias or [], remove_aliases=remove_alias or [])))


@exercise_app.command("link")
def exercise_link(source: str, target: str,
                  kind: Annotated[str, typer.Option(help="harder, easier or substitute.")] = "harder",
                  start_ratio: Annotated[Optional[float], typer.Option(
                      help="Where the target starts, as a share of the total load on the source.")] = None):
    """Link two exercises, e.g. what to move to when one is maxed out."""
    _run(_write("exercise link", lambda conn, w: exercises.link(conn, w, source=source, target=target, kind=kind,
                                                                 start_ratio=start_ratio)))


@app.command()
def schema():
    """What the data means, the vocabularies in use, and the input formats. Start here as an agent."""
    from fit import describe
    _run(_read(describe.schema))


@vocab_app.command("list")
def vocab_list(name: Annotated[Optional[str], typer.Argument(help="One vocabulary (default: all).")] = None):
    """Show the vocabularies that can grow."""
    _run(_read(lambda conn: vocab.list_(conn, name)))


@vocab_app.command("add")
def vocab_add(
    name: Annotated[str, typer.Argument(help=", ".join(vocab.VOCABS))],
    value: Annotated[str, typer.Argument(help="lowercase_with_underscores, e.g. foot.")],
    paired: Annotated[Optional[bool], typer.Option("--paired/--unpaired", help="body_part: has a left and right.")] = None,
    region: Annotated[Optional[str], typer.Option(help="muscle: upper, lower or core.")] = None,
    mechanism: Annotated[Optional[str], typer.Option(help="equipment_kind: dumbbell, stack, fixed, loadable or none.")] = None,
    dimension: Annotated[Optional[str], typer.Option(help="measurement_kind: mass, length or rate.")] = None,
):
    """Add to a vocabulary, e.g. `fit vocab add body_part foot --paired`."""
    extra = {"body_part": paired, "muscle": region, "equipment_kind": mechanism, "measurement_kind": dimension}.get(name)
    _run(_write("vocab add", lambda conn, w: vocab.add(conn, w, name, value, extra)))


# ── corrections ──────────────────────────────────────────────────────────────

@session_app.command("delete")
def session_delete(date: Annotated[Optional[str], typer.Argument(help="YYYY-MM-DD (default: today).")] = None,
                   person: Person = None,
                   kind: Annotated[Optional[str], typer.Option(help="strength or cardio.")] = None,
                   session_id: SessionId = None):
    """Delete a session and everything in it, e.g. to re-log it correctly. Undoable right after."""
    _run(_write("session delete", lambda conn, w: corrections.delete_session(
        conn, w, corrections.find_session(conn, person=person, date=date, kind=kind, session_id=session_id))))


@set_app.command("edit")
def set_edit(
    exercise: str, set_no: int, person: Person = None, date: Day = None, session_id: SessionId = None,
    side: Annotated[Optional[str], typer.Option(help="left or right, for sets logged per side.")] = None,
    load: Annotated[Optional[str], typer.Option(help="Weight per implement, or BW.")] = None,
    reps: Annotated[Optional[int], typer.Option()] = None,
    seconds: Annotated[Optional[int], typer.Option()] = None,
    rp: Annotated[Optional[int], typer.Option("--rp", help="Rest-pause reps (0 to clear).")] = None,
    kind: Annotated[Optional[str], typer.Option(help="working, warmup, backoff or test.")] = None,
    failure: Annotated[Optional[bool], typer.Option("--failure/--no-failure")] = None,
    implements: Annotated[Optional[int], typer.Option()] = None,
):
    """Fix one logged set in place, e.g. `fit set edit "DB RDL" 2 --date 2026-10-05 --reps 11`."""
    changes = {k: v for k, v in dict(load=load, reps=reps, seconds=seconds, rest_pause_reps=rp, kind=kind,
                                     to_failure=failure, implements=implements).items() if v is not None}
    _run(_write("set edit", lambda conn, w: corrections.edit_set(
        conn, w, corrections.find_session(conn, person=person, date=date, kind="strength", session_id=session_id),
        exercise=exercise, set_no=set_no, side=side, changes=changes)))


@set_app.command("delete")
def set_delete(exercise: str, set_no: int, person: Person = None, date: Day = None, session_id: SessionId = None,
               side: Annotated[Optional[str], typer.Option(help="left or right, for sets logged per side.")] = None):
    """Remove one logged set (removing the last one removes the exercise from the session)."""
    _run(_write("set delete", lambda conn, w: corrections.delete_set(
        conn, w, corrections.find_session(conn, person=person, date=date, kind="strength", session_id=session_id),
        exercise=exercise, set_no=set_no, side=side)))


@notes_app.command("log")
def notes_log(title: Annotated[Optional[str], typer.Argument(help="The note's title, e.g. 'Wed Oct 7 · Sam · B'.")] = None,
              file: Annotated[Optional[Path], typer.Option(help="Read the note from a text file instead of Notes.")] = None,
              date: Annotated[Optional[str], typer.Option(help="Override the date read from the header.")] = None,
              started_at: Annotated[Optional[str], typer.Option(help="e.g. 19:30 or 7:30pm.")] = None,
              folder: Annotated[str, typer.Option(help="Notes folder.")] = notes.FOLDER):
    """Log a filled-in gym note in the exact template format (plain reps only; anything else goes to an agent)."""
    from datetime import date as Date

    def go():
        if (title is None) == (file is None):
            raise FitError("source_required", "give a note title or --file, not both")
        if file is not None:
            text, ref = file.read_text(), str(file)
        else:
            pulled = notes.pull(title, _archive_dir(), folder)
            text, ref = pulled["text"], pulled["archived"]
        doc, blank = notelog.parse(text, Date.today(), date)
        doc |= {"source": "import", "source_ref": ref}
        if started_at:
            doc["started_at"] = started_at
        conn = _connect()
        with writing(conn, "notes log", output.actor()) as w:
            session_id = record.log_strength(conn, w, doc)
        return view.session(conn, session_id) | ({"not_logged_no_reps": blank} if blank else {})
    _run(go)


notes_log.__doc__ = "Log a filled-in gym note in the exact template format.\n\n" + (notelog.__doc__ or "").strip()
