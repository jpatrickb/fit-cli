"""Apple Notes on the Mac (A8): write gym templates into a Notes folder (they sync to the phone),
and read filled-in notes back as plain text. Optional and Mac-only; uses AppleScript via osascript.
An existing note is only rewritten with --replace, and its old text is archived first."""

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

_REPLACE = """
on run argv
  set theTitle to item 1 of argv
  set theBody to item 2 of argv
  set folderName to item 3 of argv
  tell application "Notes"
    set found to (notes of folder folderName of default account whose name is theTitle)
    if (count of found) is not 1 then return "MANY"
    set body of item 1 of found to theBody
    return "OK"
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


def push(text: str, archive_dir: Path, folder: str = FOLDER, replace: bool = False) -> dict:
    """Create the note, or with replace=True rewrite an existing one after archiving its text."""
    title = text.splitlines()[0]
    out = _osascript(_PUSH, title, _html(text), folder)
    if out != "EXISTS":
        return {"note": title, "folder": folder, "replaced": False}
    if not replace:
        raise FitError("note_exists", f"a note named {title!r} is already in {folder}",
                       ["fit next --to-notes --replace (keeps a copy of the old text)", f"fit notes pull {title!r}"])
    old = pull(title, archive_dir, folder, suffix="-replaced")  # never lose what was typed into it
    if _osascript(_REPLACE, title, _html(text), folder) != "OK":
        raise FitError("note_many", f"more than one note named {title!r} in {folder}; fix that in Notes first")
    return {"note": title, "folder": folder, "replaced": True, "old_text_archived": old["archived"]}


def titles(folder: str = FOLDER) -> list[str]:
    return [t for t in _osascript(_LIST, folder).splitlines() if t]


def pull(title: str, archive_dir: Path, folder: str = FOLDER, suffix: str = "") -> dict:
    out = _osascript(_PULL, title, folder)
    if out in ("NOFOLDER", "MISSING", "MANY"):
        from fit.errors import closest
        reasons = {"NOFOLDER": f"there's no {folder!r} folder in Notes",
                   "MISSING": f"no note named {title!r} in {folder}",
                   "MANY": f"more than one note named {title!r} in {folder}"}
        raise FitError(f"note_{out.lower()}", reasons[out], closest(title, titles(folder)) if out == "MISSING" else [])
    archive_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w]+", "-", title).strip("-").lower() + suffix
    path = archive_dir / f"{stem}.txt"
    n = 1
    while suffix and path.exists():  # keep every replaced version
        n += 1
        path = archive_dir / f"{stem}-{n}.txt"
    path.write_text(out + "\n")
    return {"note": title, "archived": str(path), "text": out}
