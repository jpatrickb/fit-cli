"""`fit notes log`: log a filled-in gym note that's still in the exact format `fit next` writes (A46).

Strict on purpose. Only the plain case is understood:

    Wed Oct 7 · Sam · B
    Low Pulley Seated Row 90: 12 11 10
    Bulgarian Split Squat 20x2 (each leg): 10 10 9
    DB Reverse Lunge BW (each leg): 12 12
    Face Pull 30 (2 sets):                <- no reps: not done, nothing logged

Anything else (rest-pause `10+2`, `L12 R10`, `skip`, a changed weight, a symptom, extra lines) is
refused with the line named, so it goes to an agent instead of being guessed at.
"""

import re
from datetime import date as Date

from fit.errors import FitError

HEADER = re.compile(r"^(?P<dow>[A-Z][a-z]{2}) (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}) · (?P<person>.+) · (?P<label>\S+)$")
LINE = re.compile(r"^(?P<name>.+?)(?: (?P<load>BW|\d+(?:\.\d+)?)(?P<pair>x2)?)?(?: \((?P<extras>[^)]*)\))?:(?P<reps>.*)$")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _date(header: re.Match, today: Date) -> str:
    """The header has no year: take the latest date on or before tomorrow whose weekday matches."""
    month, day = MONTHS.get(header["mon"]), int(header["day"])
    if month is None:
        raise FitError("bad_note", f"can't read the month in {header.group(0)!r}")
    for year in (today.year + 1, today.year, today.year - 1):
        try:
            d = Date(year, month, day)
        except ValueError:
            continue
        if d <= Date.fromordinal(today.toordinal() + 1) and d.strftime("%a") == header["dow"]:
            return d.isoformat()
    raise FitError("bad_note", f"{header.group(0)!r}: no recent {header['dow']} {header['mon']} {day}; pass --date")


def parse(text: str, today: Date, on: str | None = None) -> tuple[dict, list[str]]:
    """(session document for record.log_strength, exercises left blank)."""
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise FitError("bad_note", "the note is empty")
    header = HEADER.match(lines[0])
    if header is None:
        raise FitError("not_a_template", f"first line {lines[0]!r} isn't a fit template header",
                       ["Wed Oct 7 · Sam · B", "or hand the note to an agent"])
    doc = {"person": header["person"], "date": on or _date(header, today), "day": header["label"], "exercises": []}
    blank = []
    for line in lines[1:]:
        m = LINE.match(line)
        if m is None:
            raise FitError("needs_an_agent", f"can't read {line!r}", ["hand this note to an agent"])
        reps = m["reps"].strip()
        if not reps:
            blank.append(m["name"])
            continue
        if not re.fullmatch(r"\d+(?:\s+\d+)*", reps):
            raise FitError("needs_an_agent", f"{line!r}: only plain reps like '12 11 10' are read here",
                           ["hand this note to an agent"])
        load = None if m["load"] in (None, "BW") else float(m["load"])
        if m["load"] is None:
            raise FitError("needs_an_agent", f"{line!r} has no weight", ["hand this note to an agent"])
        item: dict = {"exercise": m["name"], "sets": [{"load": load, "reps": int(r)} for r in reps.split()]}
        if m["pair"]:
            item["implements"] = 2
        doc["exercises"].append(item)
    if not doc["exercises"]:
        raise FitError("nothing_to_log", "no exercise has reps filled in")
    return doc, blank
