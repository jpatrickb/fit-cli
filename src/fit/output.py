"""Tables for a person at a terminal, TOON for an agent (A23)."""

import sys

import toon_format
from rich.console import Console
from rich.table import Table

_mode: str | None = None  # 'toon' | 'table'; None = decide from whether stdout is a terminal


def set_mode(mode: str | None) -> None:
    global _mode
    _mode = mode


def mode() -> str:
    return _mode or ("table" if sys.stdout.isatty() else "toon")


def actor() -> str:
    """Who is writing, for the change log: a terminal means a person."""
    import os
    return os.environ.get("FIT_ACTOR") or ("person" if sys.stdin.isatty() and sys.stdout.isatty() else "agent")


def uniform(rows: list[dict]) -> list[dict]:
    """Same keys on every row, so TOON can use its compact table form."""
    keys = list(dict.fromkeys(k for r in rows for k in r))
    return [{k: r.get(k) for k in keys} for r in rows]


def _normalize(data):
    if isinstance(data, list):
        items = [_normalize(d) for d in data]
        return uniform(items) if items and all(isinstance(i, dict) for i in items) else items
    if isinstance(data, dict):
        return {k: _normalize(v) for k, v in data.items()}
    return data


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, bool):
        return "yes" if v else ""
    return str(v)


def _render(console: Console, data, title: str | None = None) -> None:
    if isinstance(data, list):
        if not data:
            console.print(f"[dim]{title + ': ' if title else ''}none[/dim]")
            return
        nested = any(isinstance(v, (list, dict)) for row in data if isinstance(row, dict) for v in row.values())
        if isinstance(data[0], dict) and nested:  # one section per item, e.g. sessions
            for i, row in enumerate(data):
                console.rule(f"{title} {i + 1}" if title else None, style="dim")
                _render(console, row)
                console.print()
            return
        if isinstance(data[0], dict):
            table = Table(title=title, title_justify="left", show_edge=False, header_style="bold")
            for k in data[0]:
                table.add_column(k, overflow="fold")
            for row in data:
                table.add_row(*(_fmt(v) for v in row.values()))
            console.print(table)
        else:
            console.print(f"{title}: " if title else "", ", ".join(_fmt(v) for v in data))
        return
    if isinstance(data, dict):
        scalars = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
        if scalars:
            width = max(len(k) for k in scalars)
            for k, v in scalars.items():
                console.print(f"[bold]{k.ljust(width)}[/bold]  {_fmt(v)}")
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                console.print()
                _render(console, v, title=k)
        return
    console.print(_fmt(data))


def emit(data) -> None:
    data = _normalize(data)
    if mode() == "toon":
        print(toon_format.encode(data))
    else:
        _render(Console(), data)


def emit_error(err: dict) -> None:
    if mode() == "toon":
        print(toon_format.encode(err), file=sys.stderr)
    else:
        console = Console(stderr=True)
        console.print(f"[red bold]error[/red bold] ({err['error']}): {err['message']}")
        if err.get("suggestions"):
            console.print("did you mean: " + ", ".join(err["suggestions"]))
