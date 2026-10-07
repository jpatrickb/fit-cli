import pytest
import toon_format
from typer.testing import CliRunner

from fit import _hoist_global_options, db
from fit.changes import writing
from fit.cli import app
from fit.setup import parse_loads

runner = CliRunner()

SESSION = """\
date: 2026-10-07
started_at: 6:30pm
source: import
source_ref: note-1
exercises[3]:
  - exercise: Seated Cable Row
    sets[3]{load,reps}:
      90,12
      90,11
      100,10
  - exercise: Bulgarian Split Squat
    sets[2]:
      - load: 20
        reps: 10
      - load: 20
        reps: 9
        rest_pause_reps: 2
  - exercise: Single-Arm Cable Row
    sets[2]{load,reps,left,right}:
      40,12,null,null
      40,null,12,10
symptoms[1]{exercise,body_part,side,kind,severity}:
  Bulgarian Split Squat,knee,right,clicking,mild
"""


class Fit:
    """Runs fit with --toon against one database and decodes the result."""

    def __init__(self, path):
        self.path = path

    def _run(self, args, input):
        return runner.invoke(app, ["--toon", *args], input=input)

    def __call__(self, *args, input=None):
        result = self._run(args, input)
        assert result.exit_code == 0, result.output
        return toon_format.decode(result.stdout) if result.stdout.strip() else None

    def error(self, *args, input=None):
        result = self._run(args, input)
        assert result.exit_code == 1, result.output
        return toon_format.decode(result.stderr)


@pytest.fixture
def fit(tmp_path, monkeypatch):
    """A fresh database with one person and a small gym."""
    monkeypatch.setenv("FIT_DB", str(tmp_path / "fit.db"))
    monkeypatch.delenv("FIT_PERSON", raising=False)
    f = Fit(tmp_path / "fit.db")
    f("init")
    f("person", "add", "Me", "--sex", "male", "--birth-year", "1995")
    f("gym", "add", "Home gym")
    f("equipment", "add", "Dumbbells", "--kind", "dumbbells", "--loads", "5-30/5")
    f("equipment", "add", "Cable station", "--kind", "cable_station", "--loads", "10-200/10")
    f("equipment", "add", "Bench", "--kind", "adjustable_bench")
    return f


def test_init_loads_catalog_and_refuses_twice(fit):
    conn = db.connect(fit.path)
    assert conn.execute("SELECT COUNT(*) FROM exercise WHERE origin = 'builtin'").fetchone()[0] == 30
    assert fit.error("init")["error"] == "already_initialized"


def test_log_and_show(fit):
    out = fit("log", "-", input=SESSION)
    rows = {e["exercise"]: e for e in out["exercises"]}
    assert rows["Low Pulley Seated Row"]["sets"] == "90×12,11 · 100×10"
    assert rows["Low Pulley Seated Row"]["equipment"] == "Cable station"
    assert rows["Bulgarian Split Squat"]["sets"] == "20×10,9+2"
    # 20 lb x 2 dumbbells x (10 + 9 + 2 rest-pause) reps x 2 legs
    assert rows["Bulgarian Split Squat"]["volume_lb"] == 20 * 2 * 21 * 2
    assert rows["Single-Arm Cable Row"]["sets"] == "40×12,L12/R10"
    assert out["symptoms"][0]["side"] == "right"
    assert out["started_at"] == "18:30"
    assert fit("show")["sessions"][0]["date"] == "2026-10-07"


def test_rest_pause_implies_failure(fit):
    fit("log", "-", input=SESSION)
    conn = db.connect(fit.path)
    row = conn.execute("SELECT to_failure FROM exercise_set WHERE rest_pause_reps = 2").fetchone()
    assert row[0] == 1


@pytest.mark.parametrize("doc, code, suggestion", [
    ("exercises[1]:\n  - exercise: rdl\n    sets[1]{load,reps}:\n      30,12\n", "unknown_exercise", "DB RDL"),
    ("exercises[1]:\n  - exercise: DB RDL\n    sets[1]{load,reps}:\n      32.5,12\n", "load_not_available", "30"),
    ("exercises[1]:\n  - exercise: DB RDL\n    sets[1]{lod,reps}:\n      30,12\n", "unknown_field", "load"),
    ("exercises[1]:\n  - exercise: Face Pull\n    skip: time\n", "skip_needs_plan", None),
])
def test_errors_are_structured_and_nothing_is_written(fit, doc, code, suggestion):
    err = fit.error("log", "-", input="date: 2026-10-07\n" + doc)
    assert err["error"] == code
    if suggestion:
        assert suggestion in err["suggestions"]
    assert db.connect(fit.path).execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0


def test_undo_reverses_latest_write_only_and_allows_relogging(fit):
    fit("log", "-", input=SESSION)
    assert fit.error("log", "-", input=SESSION)["error"] == "already_logged"
    fit("measure", "add", "bodyweight", "172")

    assert fit("undo")["command"] == "measure add"
    assert fit("undo")["command"] == "log"
    conn = db.connect(fit.path)
    assert conn.execute("SELECT COUNT(*) FROM session WHERE deleted_at IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM exercise_set WHERE deleted_at IS NULL").fetchone()[0] == 0

    fit("log", "-", input=SESSION)  # same source_ref is fine once the old one is undone
    assert conn.execute("SELECT COUNT(*) FROM session WHERE deleted_at IS NULL").fetchone()[0] == 1


def test_undo_restores_hard_deleted_setup_rows_and_runs_out(fit):
    for _ in range(5):  # equipment x3, gym, person (init itself is not undoable)
        fit("undo")
    conn = db.connect(fit.path)
    assert conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM equipment_load").fetchone()[0] == 0
    assert fit.error("undo")["error"] == "nothing_to_undo"


def test_cardio_intervals(fit):
    out = fit("cardio", "log", "-", input="""\
date: 2026-10-08
activity: run
type: intervals
distance: 4
moving_time: "31:40"
segments[3]{role,distance,unit,time}:
  warmup,1,mi,"9:05"
  work,800,m,"2:50"
  work,800,m,"2:48.5"
""")
    assert out["distance"] == "4 mi"
    assert out["pace"] == "7:55/mi"
    assert [s["pace"] for s in out["segments"]] == ["9:05/mi", "5:42/mi", "5:39/mi"]


def test_one_set_at_a_time(fit):
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-09")
    fit("set", "add", "DB Curl", "25", "10", "--rp", "2", "--date", "2026-10-09")
    fit("symptom", "add", "shoulder", "pain", "moderate", "--side", "left", "--exercise", "DB Curl",
        "--date", "2026-10-09")
    session = fit("show", "2026-10-09")["sessions"][0]
    assert session["exercises"][0]["sets"] == "25×12,10+2"
    assert fit.error("symptom", "add", "knee", "pain", "mild", "--date", "2026-10-09")["error"] == "side_required"


def test_plan_links_and_skips(fit):
    conn = db.connect(fit.path)
    person = conn.execute("SELECT id FROM person").fetchone()[0]
    face_pull = conn.execute("SELECT id FROM exercise WHERE name = 'Face Pull'").fetchone()[0]
    row = conn.execute("SELECT id FROM exercise WHERE name = 'Low Pulley Seated Row'").fetchone()[0]
    with writing(conn, "plan", "person") as w:
        program = w.insert("program", {"person_id": person, "name": "Block 2", "starts_on": "2026-10-05"})
        day = w.insert("program_day", {"program_id": program["id"], "label": "B"})
        for pos, ex in [(1, row), (2, face_pull)]:
            w.insert("prescription", {"program_day_id": day["id"], "position": pos, "exercise_id": ex, "sets": 3,
                                      "rep_min": 8, "rep_max": 12, "active_from": "2026-10-05"})
    fit("log", "-", input="""\
date: 2026-10-07
day: B
exercises[2]:
  - exercise: Seated Cable Row
    sets[1]{load,reps}:
      90,12
  - exercise: Face Pull
    skip: time
""")
    linked = conn.execute("SELECT e.name, se.status, se.skip_reason, se.position, se.prescription_id IS NOT NULL "
                          "FROM session_exercise se JOIN exercise e ON e.id = se.exercise_id ORDER BY e.name").fetchall()
    assert [tuple(r) for r in linked] == [("Face Pull", "skipped", "time", None, 1),
                                          ("Low Pulley Seated Row", "done", None, 1, 1)]


def test_parse_loads():
    assert parse_loads("5-30/5") == [5, 10, 15, 20, 25, 30]
    assert parse_loads("100") == [100]
    assert parse_loads("5-15/5,35,40") == [5, 10, 15, 35, 40]
    assert parse_loads(None) == []


def test_global_options_anywhere():
    assert _hoist_global_options(["gym", "show", "--table"]) == ["--table", "gym", "show"]
    assert _hoist_global_options(["show", "--db", "x.db", "--toon"]) == ["--db", "x.db", "--toon", "show"]


PLAN = """\
name: Block 2
gym: Home gym
starts_on: 2026-10-05
days[1]:
  - label: A
    name: Hinge
    slots[3]{exercise,sets,rep_min,rep_max,start_load}:
      DB RDL,3,10,12,20
      DB Curl,3,8,12,25
      Face Pull,2,12,15,30
"""


def _rdl_history(fit, date, load, reps):
    rows = "\n".join(f"      {load},{r}" for r in reps)
    fit("log", "-", input=f"date: {date}\nday: A\nexercises[1]:\n  - exercise: DB RDL\n"
                          f"    sets[{len(reps)}]{{load,reps}}:\n{rows}\n")


def test_plan_load_show_and_checks(fit):
    shown = fit("plan", "load", "-", input=PLAN)
    assert [s["exercise"] for s in shown["days"][0]["slots"]] == ["DB RDL", "DB Curl", "Face Pull"]
    assert fit.error("plan", "load", "-", input=PLAN)["error"] == "plan_exists"
    leg_press = PLAN.replace("Face Pull,2,12,15,30", "Machine Leg Press,3,10,12,100").replace("2026-10-05", "2026-10-12")
    assert fit.error("plan", "load", "--replace", "-", input=leg_press)["error"] == "plan_name_taken"
    leg_press = leg_press.replace("Block 2", "Block 3")
    assert fit.error("plan", "load", "--replace", "-", input=leg_press)["error"] == "not_doable_here"
    # the failed load changed nothing: Block 2 is still current and open-ended
    assert fit("plan", "show", "--on", "2026-10-20")["plan"] == "Block 2"


def test_next_targets_follow_double_progression(fit):
    fit("plan", "load", "-", input=PLAN)
    t = {r["exercise"]: r for r in fit("next", "--date", "2026-10-06")["targets"]}
    assert t["DB RDL"]["target"] == 20 and "first time" in t["DB RDL"]["why"]
    _rdl_history(fit, "2026-10-06", 20, [12, 12, 12])
    assert fit("next", "--date", "2026-10-08")["targets"][0]["target"] == 25          # maxed -> up
    _rdl_history(fit, "2026-10-08", 25, [12, 11, 10])
    assert fit("next", "--date", "2026-10-10")["targets"][0]["target"] == 25          # in range -> same
    _rdl_history(fit, "2026-10-10", 25, [10, 9, 8])
    assert fit("next", "--date", "2026-10-12")["targets"][0]["target"] == 25          # only later sets short -> same
    _rdl_history(fit, "2026-10-12", 25, [9, 9, 8])
    assert fit("next", "--date", "2026-10-14")["targets"][0]["target"] == 20          # first set short -> down


def test_next_swaps_a_capped_exercise_and_undo_restores_it(fit):
    fit("plan", "load", "-", input=PLAN)
    _rdl_history(fit, "2026-10-06", 30, [12, 12, 12])
    out = fit("next", "--date", "2026-10-08")
    assert out["swapped"] == [{"slot": 1, "from": "DB RDL", "to": "B-Stance RDL", "start_load": 25}]
    assert out["template"].splitlines()[1] == "B-Stance RDL 25x2 (each leg):"
    slots = fit("plan", "show", "--on", "2026-10-08")["days"][0]["slots"]
    assert slots[0]["exercise"] == "B-Stance RDL"
    assert fit("plan", "show", "--on", "2026-10-07")["days"][0]["slots"][0]["exercise"] == "DB RDL"  # history kept
    fit("undo")
    assert fit("plan", "show", "--on", "2026-10-08")["days"][0]["slots"][0]["exercise"] == "DB RDL"


def test_pain_holds_the_weight(fit):
    fit("plan", "load", "-", input=PLAN)
    fit("log", "-", input="date: 2026-10-06\nday: A\nexercises[1]:\n  - exercise: DB RDL\n"
                          "    sets[3]{load,reps}:\n      20,12\n      20,12\n      20,12\n"
                          "symptoms[1]{exercise,body_part,kind,severity}:\n  DB RDL,lower_back,pain,moderate\n")
    t = fit("next", "--date", "2026-10-08")["targets"][0]
    assert t["target"] == 20 and "pain" in t["why"]


def test_plan_swap_and_edit(fit):
    fit("plan", "load", "-", input=PLAN)
    fit("plan", "swap", "A", "3", "Pallof Press", "--start-load", "20", "--date", "2026-10-08")
    fit("plan", "edit", "A", "2", "--rep-max", "10", "--tempo", "3-1-1-0", "--date", "2026-10-08")
    slots = fit("plan", "show", "--on", "2026-10-08")["days"][0]["slots"]
    assert slots[2]["exercise"] == "Pallof Press" and slots[2]["since"] == "2026-10-08"
    assert slots[1]["reps"] == "8–10" and slots[1]["tempo"] == "3-1-1-0"


def test_next_suggests_the_day_done_longest_ago(fit):
    two_days = PLAN.replace("days[1]:", "days[2]:") + "  - label: B\n    slots[1]{exercise,sets,rep_min,rep_max}:\n      DB Curl,3,8,12\n"
    fit("plan", "load", "-", input=two_days)
    assert fit("next", "--date", "2026-10-06")["day"] == "A"   # neither done: first label
    _rdl_history(fit, "2026-10-06", 20, [12])
    assert fit("next", "--date", "2026-10-08")["day"] == "B"


def test_history_bests_and_e1rm(fit):
    fit("plan", "load", "-", input=PLAN)
    _rdl_history(fit, "2026-10-06", 20, [12, 12, 12])
    _rdl_history(fit, "2026-10-08", 25, [10, 9, 15])
    h = fit("history", "DB RDL")["sessions"]
    assert [s["sets"] for s in h] == ["20×12,12,12", "25×10,9,15"]
    assert h[0]["e1rm_low"] <= h[0]["e1rm"] <= h[0]["e1rm_high"]
    b = fit("bests", "--exercise", "DB RDL")["bests"][0]
    assert (b["heaviest"], b["reps_at_heaviest"], b["heaviest_date"]) == (25, 15, "2026-10-08")
    e = fit("e1rm", "DB RDL")["sessions"][1]
    assert round(e["epley"], 1) == round(25 * (1 + 10 / 30), 1)  # the 15-rep set is past the formulas' range


def test_muscles_consistency_and_capped(fit):
    fit("plan", "load", "-", input=PLAN.replace("2026-10-05", "2026-08-01"))
    _rdl_history(fit, "2026-09-28", 30, [12, 12, 12])   # week 40
    _rdl_history(fit, "2026-10-06", 30, [12, 12, 12])   # week 41
    weeks = fit("muscles", "--weeks", "1", "--on", "2026-10-07")["weeks"]
    assert {(w["week"], w["muscle"]) for w in weeks} == {("2026-W41", "hamstrings"), ("2026-W41", "glutes")}
    assert weeks[0]["sets"] == 3
    c = fit("consistency", "--on", "2026-10-07")
    assert c["lifting_week_streak"] == 2
    assert [x["exercise"] for x in fit("capped")["capped"]] == ["DB RDL"]


def test_exercise_age_counts_from_the_log(fit):
    fit("plan", "load", "-", input=PLAN.replace("2026-10-05", "2026-08-01"))
    _rdl_history(fit, "2026-09-01", 20, [12])
    _rdl_history(fit, "2026-10-06", 20, [12])        # 35-day gap: the run restarts here
    _rdl_history(fit, "2026-10-13", 20, [12])
    age = {a["exercise"]: a for a in fit("exercise-age", "--on", "2026-10-20")["exercises"]}
    assert age["DB RDL"]["in_use_since"] == "2026-10-06" and age["DB RDL"]["sessions"] == 2
    assert age["Face Pull"]["weeks"] == 0


def test_exercise_find_add_and_link(fit):
    found = [e["exercise"] for e in fit("exercise", "find", "--muscle", "hamstrings", "--gym", "Home gym")["exercises"]]
    assert "DB RDL" in found and "EZ-Bar RDL" not in found          # no EZ bar at this test gym
    assert fit.error("exercise", "add", "Cable Lateral Raise", "--pattern", "shoulder_isolation",
                     "--laterality", "unilateral", "--measure", "reps", "--implements", "1",
                     "--bodyweight-fraction", "0", "--muscles", "side_delts=1")["error"] == "needs_required"
    added = fit("exercise", "add", "Cable Lateral Raise", "--pattern", "shoulder_isolation", "--laterality", "unilateral",
                "--measure", "reps", "--implements", "1", "--bodyweight-fraction", "0", "--muscles", "side_delts=1",
                "--needs", "cable_station", "--alias", "Cable Y Raise")
    assert added["origin"] == "user" and added["aliases"] == ["Cable Y Raise"]
    fit("exercise", "link", "DB Lateral Raise", "Cable Lateral Raise", "--start-ratio", "0.5")
    links = fit("exercise", "show", "DB Lateral Raise")["progressions"]
    assert {"direction": "to", "kind": "harder", "exercise": "Cable Lateral Raise", "start_ratio": 0.5} in links


def test_cardio_list_and_body(fit):
    fit("cardio", "log", "-", input="date: 2026-10-06\nactivity: run\ndistance: 5\nunit: km\nmoving_time: \"25:00\"\n")
    fit("cardio", "log", "-", input="date: 2026-10-08\nactivity: run\ndistance: 2\nmoving_time: \"18:00\"\n")
    out = fit("cardio", "list")
    assert out["sessions"][0]["miles"] == 3.11 and out["weekly_miles"] == [{"week": "2026-W41", "miles": 5.11}]
    fit("measure", "add", "bodyweight", "77", "--unit", "kg", "--date", "2026-10-06")
    assert fit("body")["measurements"] == [{"date": "2026-10-06", "kind": "bodyweight", "value": 77, "unit": "kg"}]


def test_schema_describes_live_vocabularies(fit):
    fit("exercise", "add", "Cable Lateral Raise", "--pattern", "shoulder_isolation", "--laterality", "unilateral",
        "--measure", "reps", "--implements", "1", "--bodyweight-fraction", "0", "--muscles", "side_delts=1",
        "--needs", "cable_station")
    out = fit("schema")
    assert "knee" in out["vocabularies"]["body_parts"]
    assert {c["name"] for c in out["concepts"]} >= {"set", "plan", "change log"}
    assert "exercises[2]:" in out["documents"]["session (fit log, fit cardio log)"]


def test_stall_is_flagged(fit):
    fit("plan", "load", "-", input=PLAN)
    for d in ("2026-10-06", "2026-10-08", "2026-10-10"):
        _rdl_history(fit, d, 25, [11, 10, 10])
    assert "stalled" in fit("next", "--date", "2026-10-12")["targets"][0]["why"]
    _rdl_history(fit, "2026-10-12", 25, [11, 11, 10])                      # one more rep: progress
    assert "stalled" not in fit("next", "--date", "2026-10-14")["targets"][0]["why"]


def test_plan_add_day_add_slot_and_drop_slot(fit):
    fit("plan", "load", "-", input=PLAN)
    fit("plan", "add-day", "B", "--name", "Pull", "--date", "2026-10-06")
    fit("plan", "add-slot", "B", "Lat Pulldown", "--sets", "3", "--rep-min", "8", "--rep-max", "12",
        "--date", "2026-10-06")
    assert fit.error("plan", "add-slot", "B", "Lat Pulldown", "--sets", "3", "--date", "2026-10-06")["error"] == "already_in_day"
    assert fit.error("plan", "add-slot", "B", "Machine Leg Press", "--sets", "3", "--date", "2026-10-06")["error"] == "not_doable_here"
    added = fit("plan", "add-slot", "A", "Pallof Press", "--sets", "2", "--rep-min", "10", "--rep-max", "10",
                "--date", "2026-10-06")
    assert added["slot"] == 4
    assert fit.error("plan", "add-slot", "A", "Tricep Pushdown", "--sets", "2", "--slot", "1", "--date", "2026-10-06")["error"] == "slot_taken"
    fit("plan", "drop-slot", "A", "3", "--date", "2026-10-08")
    slots = lambda on: [s["exercise"] for s in fit("plan", "show", "--on", on)["days"][0]["slots"]]
    assert slots("2026-10-07") == ["DB RDL", "DB Curl", "Face Pull", "Pallof Press"]
    assert slots("2026-10-08") == ["DB RDL", "DB Curl", "Pallof Press"]


def test_cardio_show_only_shows_cardio(fit):
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-06")
    fit("cardio", "log", "-", input="date: 2026-10-06\nactivity: run\ndistance: 3\nmoving_time: \"27:00\"\n")
    sessions = fit("cardio", "show")["sessions"]
    assert [s["kind"] for s in sessions] == ["cardio"] and sessions[0]["pace"] == "9:00/mi"


def test_exercise_edit_and_undo(fit):
    edited = fit("exercise", "edit", "Lat Pulldown", "--muscles", "lats=1,upper_back=1", "--rename", "Pulldown",
                 "--alias", "Pull-down")
    assert edited["exercise"] == "Pulldown" and edited["muscles"] == "lats, upper_back"
    assert "Lat Pulldown" in edited["aliases"]                              # old name still finds it
    assert fit("exercise", "show", "Lat Pulldown")["exercise"] == "Pulldown"
    assert fit.error("exercise", "edit", "Pulldown", "--laterality", "both")["error"] == "bad_laterality"
    assert fit.error("exercise", "edit", "Pulldown")["error"] == "nothing_to_change"
    fit("undo")
    restored = fit("exercise", "show", "Lat Pulldown")
    assert restored["exercise"] == "Lat Pulldown" and restored["muscles"] == "lats, biceps (0.5), upper_back (0.5)"


def test_heavier_dumbbells_let_a_capped_lift_progress(fit):
    fit("plan", "load", "-", input=PLAN)
    _rdl_history(fit, "2026-10-06", 30, [12, 12, 12])
    assert [c["exercise"] for c in fit("capped")["capped"]] == ["DB RDL"]
    fit("equipment", "edit", "Dumbbells", "--add-loads", "35-50/5")
    assert fit("capped")["capped"] == []
    out = fit("next", "--date", "2026-10-08")
    assert out["swapped"] == [] and out["targets"][0]["target"] == 35
    assert fit.error("equipment", "edit", "Dumbbells", "--remove-loads", "32.5")["error"] == "load_not_found"


def test_vocabularies_grow_through_the_cli(fit):
    assert fit.error("vocab", "add", "body_part", "foot")["error"] == "missing_field"
    fit("vocab", "add", "body_part", "foot", "--paired")
    fit("vocab", "add", "symptom_kind", "numbness")
    assert {"id": "foot", "paired": 1} in fit("vocab", "list", "body_part")["body_part"]
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-06")
    fit("symptom", "add", "foot", "numbness", "mild", "--side", "left", "--date", "2026-10-06")
    assert fit.error("vocab", "add", "colour", "red")["error"] == "unknown_vocabulary"


def test_stale_views_are_rebuilt_on_open(fit):
    import sqlite3
    raw = sqlite3.connect(fit.path)
    raw.execute("DROP VIEW v_capped")
    raw.execute("CREATE VIEW v_capped AS SELECT 1 AS stale")
    raw.close()
    assert fit("capped")["capped"] == []                   # works: the view was rebuilt from the code
    stored = sqlite3.connect(fit.path).execute("SELECT sql FROM sqlite_master WHERE name = 'v_capped'").fetchone()[0]
    assert "stale" not in stored


def test_migration_from_v1_keeps_data_and_backs_up(fit):
    import sqlite3
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-06")
    # turn the database back into a version-1 one: the old set-kind CHECK
    raw = sqlite3.connect(fit.path)
    old = raw.execute("SELECT sql FROM sqlite_master WHERE name = 'exercise_set'").fetchone()[0]
    raw.executescript(f"""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE exercise_set RENAME TO exercise_set_x;
        {old.replace("'backoff', 'test'", "'backoff'").replace("-- test: a one-off", "-- was")};
        INSERT INTO exercise_set SELECT * FROM exercise_set_x;
        DROP TABLE exercise_set_x;
        PRAGMA user_version = 1;
    """)
    raw.close()
    fit("set", "add", "DB Curl", "30", "2", "--date", "2026-10-06")          # opening migrates first
    log = "date: 2026-10-07\nexercises[1]:\n  - exercise: DB Curl\n    sets[2]{load,reps,kind}:\n      25,12,working\n      30,2,test\n"
    shown = fit("log", "-", input=log)
    assert shown["exercises"][0]["sets"] == "25×12 · 30×2(t)"
    conn = db.connect(fit.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM exercise_set").fetchone()[0] == 4
    assert (fit.path.parent / "fit.db.pre-v2.bak").exists()
    # the trigger came back with the table
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'exercise_set'").fetchone()[0] == 1


def test_test_sets_are_ignored_by_progression(fit):
    fit("plan", "load", "-", input=PLAN)
    fit("log", "-", input="date: 2026-10-06\nday: A\nexercises[1]:\n  - exercise: DB RDL\n"
                          "    sets[3]{load,reps,kind}:\n      20,12,working\n      20,12,working\n      30,2,test\n")
    t = fit("next", "--date", "2026-10-08")["targets"][0]
    assert t["target"] == 25 and "20×12,12" in t["why"]


def test_delete_an_older_session_and_relog_it(fit):
    fit("log", "-", input=SESSION)                                                   # 2026-10-07, source_ref note-1
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-08")                 # a later write
    deleted = fit("session", "delete", "2026-10-07")
    assert deleted["kind"] == "strength"
    conn = db.connect(fit.path)
    assert conn.execute("SELECT COUNT(*) FROM exercise_set es JOIN session_exercise se ON se.id = es.session_exercise_id "
                        "JOIN session s ON s.id = se.session_id WHERE s.date = '2026-10-07' AND es.deleted_at IS NULL").fetchone()[0] == 0
    fit("log", "-", input=SESSION)                                                   # same note logs again
    assert fit("show", "2026-10-07")["sessions"][0]["exercises"][0]["sets"] == "90×12,11 · 100×10"
    assert fit("show", "2026-10-08")["sessions"][0]["exercises"][0]["sets"] == "25×12"   # untouched


def test_session_delete_is_undoable_and_asks_when_ambiguous(fit):
    fit("set", "add", "DB Curl", "25", "12", "--date", "2026-10-07")
    fit("cardio", "log", "-", input="date: 2026-10-07\nactivity: run\ndistance: 3\n")
    err = fit.error("session", "delete", "2026-10-07")
    assert err["error"] == "ambiguous_session" and len(err["suggestions"]) == 2
    fit("session", "delete", "2026-10-07", "--kind", "cardio")
    assert [s["kind"] for s in fit("show", "2026-10-07")["sessions"]] == ["strength"]
    fit("undo")
    assert [s["kind"] for s in fit("show", "2026-10-07")["sessions"]] == ["strength", "cardio"]


def test_set_edit_and_delete(fit):
    fit("log", "-", input=SESSION)
    fit("set", "edit", "Seated Cable Row", "2", "--date", "2026-10-07", "--reps", "12", "--rp", "2")
    fit("set", "edit", "Bulgarian Split Squat", "2", "--date", "2026-10-07", "--rp", "0")
    rows = {e["exercise"]: e["sets"] for e in fit("show", "2026-10-07")["sessions"][0]["exercises"]}
    assert rows["Low Pulley Seated Row"] == "90×12,12+2 · 100×10"
    assert rows["Bulgarian Split Squat"] == "20×10,9F"                       # rest-pause cleared; failure kept
    assert fit.error("set", "edit", "Seated Cable Row", "1", "--date", "2026-10-07", "--load", "95")["error"] == "load_not_available"
    assert fit.error("set", "edit", "Single-Arm Cable Row", "2", "--date", "2026-10-07", "--reps", "11")["error"] == "side_required"
    fit("set", "edit", "Single-Arm Cable Row", "2", "--date", "2026-10-07", "--side", "right", "--reps", "11")
    fit("set", "delete", "Bulgarian Split Squat", "1", "--date", "2026-10-07")
    out = fit("set", "delete", "Bulgarian Split Squat", "2", "--date", "2026-10-07")
    assert out["exercise_removed"] is True
    shown = fit("show", "2026-10-07")["sessions"][0]
    assert [e["exercise"] for e in shown["exercises"]] == ["Low Pulley Seated Row", "Single-Arm Cable Row"]
    assert shown["exercises"][1]["sets"] == "40×12,L12/R11"
    assert "symptoms" not in shown                                            # its knee symptom went with it
