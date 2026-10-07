"""`fit schema`: what the data means, for an agent starting cold (A27). Concepts and conventions are
fixed text; vocabularies are read live from the database, since people can add to them."""

import sqlite3

from fit import plan, record
from fit.exercises import PATTERNS

CONCEPTS = [
    ("person", "Someone who trains. Most commands take --person; it can be left out when there's only one."),
    ("gym / equipment", "Equipment at a gym has a kind, a unit (lb/kg) and the weights it can be set to. "
                        "A pulley ratio (multiplier) converts what you read into force moved."),
    ("exercise", "A movement plus how it's loaded. Built-ins ship with fit; users add their own. Aliases map other "
                 "names onto it. It lists the equipment kinds it needs and the muscles it trains."),
    ("plan", "One current plan per person: days (A, B, C) of ordered slots. No schedule: any day, any date. "
             "Swapping an exercise ends its slot and starts a new one, so past plans stay answerable."),
    ("session", "One person's workout or cardio on a date. Strength sessions hold exercises (done or skipped) with "
                "sets; cardio sessions hold totals and optional segments (splits, reps, recoveries)."),
    ("set", "Weight per implement as read (30 for two 30s) with an implement count, reps or seconds, side "
            "(both, each, left, right), optional rest-pause reps after failure. Kind is working, warmup, backoff or "
            "test (a one-off heavier try); only working sets count. Volume counts rest-pause reps; progression and "
            "estimated max don't."),
    ("symptom", "Pain, clicking... during an exercise or run: body part, side (for paired parts), kind, severity "
                "(mild, moderate, severe). Moderate or severe pain holds that exercise's weight."),
    ("change log", "Every write is one change set; `fit undo` reverses the most recent one exactly."),
]

CONVENTIONS = [
    "Output is TOON when not at a terminal; --toon / --table force it. Errors go to stderr as error, message, "
    "suggestions, with exit code 1. Never guess past an error.",
    "Weights are stored as read, per implement, in the equipment's unit. Volume and calculated totals are in lb.",
    "Dates are YYYY-MM-DD; times HH:MM (local). Durations mm:ss or h:mm:ss.",
    "No free text anywhere: anything worth recording has a field or a fixed vocabulary. Missing a body part, "
    "symptom kind, muscle or equipment kind? `fit vocab add` it rather than forcing the nearest one.",
    "Targets (fit next): up a step when every set at the top weight reached the top of the range; down a step only "
    "when the first set at the top weight missed the bottom; held after moderate pain. A lift maxed at the heaviest "
    "weight is swapped in the plan for the next harder exercise doable at the gym.",
    "Analysis is the agent's job: the commands report facts (history, e1rm, bests, muscles, consistency, capped, "
    "exercise-age, cardio list, body). If a question can't be answered with them, a command is missing.",
]


def schema(conn: sqlite3.Connection) -> dict:
    def ids(table: str) -> list[str]:
        return [r[0] for r in conn.execute(f"SELECT id FROM {table} ORDER BY id")]

    return {
        "concepts": [{"name": n, "meaning": m} for n, m in CONCEPTS],
        "conventions": CONVENTIONS,
        "vocabularies": {
            "equipment_kinds": ids("equipment_kind"),
            "muscles": ids("muscle"),
            "movement_patterns": PATTERNS,
            "body_parts": ids("body_part"),
            "symptom_kinds": ids("symptom_kind"),
            "severities": record.SEVERITIES,
            "skip_reasons": record.SKIP_REASONS,
            "cardio_activities": ids("cardio_activity"),
            "cardio_workout_types": ids("cardio_workout_type"),
            "segment_roles": record.SEGMENT_ROLES,
            "distance_units": record.DISTANCE_UNITS,
            "measurement_kinds": ids("measurement_kind"),
            "progression_rules": ids("progression_rule"),
        },
        "documents": {"session (fit log, fit cardio log)": (record.__doc__ or "").strip(),
                      "plan (fit plan load)": (plan.__doc__ or "").strip()},
    }
