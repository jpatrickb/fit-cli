"""Apple Notes on the Mac (A8): write gym templates into a Notes folder (they sync to the phone),
and read filled-in notes back as plain text. Optional and Mac-only; uses AppleScript via osascript.
Never edits or deletes an existing note."""

import html
import re
import subprocess
import sys
from pathlib import Path

from fit.errors import FitError

FOLDER = "Gym"

_PUSH = """
on run argv
  set theTitle to item 1 of argv
  set theBody to item 2 of argv
  set folderName to item 3 of argv
  tell application "Notes"
    set acct to default account
    if not (exists folder folderName of acct) then make new folder at acct with properties {name:folderName}
    set f to folder folderName of acct
    if (count of (notes of f whose name is theTitle)) > 0 then return "EXISTS"
    set n to make new note at f with properties {body:theBody}
    return id of n
  end tell
end run
"""

_PULL = """
on run argv
  set theTitle to item 1 of argv
  set folderName to item 2 of argv
  tell application "Notes"
    set acct to default account
    if not (exists folder folderName of acct) then return "NOFOLDER"
    set found to (notes of folder folderName of acct whose name is theTitle)
    if (count of found) is 0 then return "MISSING"
    if (count of found) > 1 then return "MANY"
    return plaintext of item 1 of found
  end tell
end run
"""

_LIST = """
on run argv
  set folderName to item 1 of argv
  tell application "Notes"
    set acct to default account
    if not (exists folder folderName of acct) then return ""
    set out to ""
    repeat with n in (notes of folder folderName of acct)
      set out to out & (name of n) & linefeed
    end repeat
    return out
  end tell
end run
"""


def _osascript(script: str, *args: str) -> str:
    if sys.platform != "darwin":
        raise FitError("notes_unavailable", "Apple Notes integration only works on a Mac")
    try:
        result = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise FitError("notes_timeout", "Notes didn't respond; is a permission prompt waiting?") from None
    if result.returncode != 0:
        if "-1743" in result.stderr or "Not authorized" in result.stderr:
            raise FitError("notes_not_allowed", "macOS blocked access to Notes",
                           ["System Settings → Privacy & Security → Automation → allow your terminal to control Notes"])
        raise FitError("notes_failed", result.stderr.strip() or "osascript failed")
    return result.stdout.rstrip("\n")


def _html(text: str) -> str:
    """First line becomes the note's title (Notes names a note after its first line)."""
    lines = text.splitlines()
    head = f"<div><b>{html.escape(lines[0])}</b></div>"
    return head + "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in lines[1:])


def push(text: str, folder: str = FOLDER) -> dict:
    title = text.splitlines()[0]
    out = _osascript(_PUSH, title, _html(text), folder)
    if out == "EXISTS":
        raise FitError("note_exists", f"a note named {title!r} is already in {folder}; it won't be overwritten",
                       [f"fit notes pull {title!r}"])
    return {"note": title, "folder": folder}


def titles(folder: str = FOLDER) -> list[str]:
    return [t for t in _osascript(_LIST, folder).splitlines() if t]


def pull(title: str, archive_dir: Path, folder: str = FOLDER) -> dict:
    out = _osascript(_PULL, title, folder)
    if out in ("NOFOLDER", "MISSING", "MANY"):
        from fit.errors import closest
        reasons = {"NOFOLDER": f"there's no {folder!r} folder in Notes",
                   "MISSING": f"no note named {title!r} in {folder}",
                   "MANY": f"more than one note named {title!r} in {folder}"}
        raise FitError(f"note_{out.lower()}", reasons[out], closest(title, titles(folder)) if out == "MISSING" else [])
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / (re.sub(r"[^\w]+", "-", title).strip("-").lower() + ".txt")
    path.write_text(out + "\n")
    return {"note": title, "archived": str(path), "text": out}
