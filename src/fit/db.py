"""Where the database lives, opening it, and creating it."""

import os
import re
import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path

from fit import catalog

SCHEMA_VERSION = 2

# Table changes need a migration (SQLite can't alter a CHECK in place); view changes don't (_sync_views).
# version -> tables to rebuild from their current definition in schema.sql
MIGRATIONS: dict[int, list[str]] = {
    2: ["exercise_set"],  # set kind 'test' (D55)
}


def default_path() -> Path:
    """$FIT_DB, else the standard data folder (A28)."""
    if env := os.environ.get("FIT_DB"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "fit" / "fit.db"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # transactions are managed explicitly
    conn.row_factory = sqlite3.Row
    _migrate(conn, path)
    conn.execute("PRAGMA foreign_keys = ON")
    _sync_views(conn)
    return conn


def _statements(sql: str, table: str) -> tuple[str, list[str]]:
    """A table's CREATE TABLE plus the indexes and triggers defined on it, from schema.sql."""
    create = re.search(rf"CREATE TABLE {table} \(.*?\n\);", sql, re.S)
    assert create, table
    extras = re.findall(rf"CREATE (?:UNIQUE )?INDEX \w+ ON {table} .*?;", sql)
    extras += re.findall(rf"CREATE TRIGGER \w+\s+\w+ \w+ ON {table}\b.*?END;", sql, re.S)
    return create.group(0), extras


def _migrate(conn: sqlite3.Connection, path: Path) -> None:
    """Bring an older database up to SCHEMA_VERSION, after copying it to <name>.pre-v<N>.bak."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [v for v in sorted(MIGRATIONS) if v > version]
    if not pending:
        return
    shutil.copy2(path, path.with_name(f"{path.name}.pre-v{pending[-1]}.bak"))
    sql = _schema()
    conn.execute("PRAGMA foreign_keys = OFF")  # must be outside the transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        for name in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")]:
            conn.execute(f"DROP VIEW {name}")  # rebuilt by _sync_views
        for v in pending:
            for table in MIGRATIONS[v]:
                create, extras = _statements(sql, table)
                cols = ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
                conn.execute(create.replace(f"CREATE TABLE {table} (", f"CREATE TABLE {table}_new (", 1))
                conn.execute(f"INSERT INTO {table}_new ({cols}) SELECT {cols} FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
                for stmt in extras:
                    conn.execute(stmt)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("migration broke a foreign key")
        conn.execute(f"PRAGMA user_version = {pending[-1]}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _schema() -> str:
    return files("fit").joinpath("schema.sql").read_text()


def _view_statements(sql: str) -> dict[str, str]:
    """CREATE VIEW statements in schema.sql, by view name, exactly as SQLite stores them."""
    out = {}
    for m in re.finditer(r"CREATE VIEW (\w+) AS.*?;(?=\n\s*(?:--|CREATE|$))", sql, re.S):
        out[m.group(1)] = m.group(0).rstrip(";")
    return out


def _sync_views(conn: sqlite3.Connection) -> None:
    """Views hold no data, so when the code's definitions differ from the database's, rebuild them all.
    This is how calculation changes reach existing databases without a migration."""
    wanted = _view_statements(_schema())
    have = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'view'")}
    if have == wanted:
        return
    conn.execute("BEGIN")
    for name in have:
        conn.execute(f"DROP VIEW {name}")
    for sql in wanted.values():
        conn.execute(sql)
    conn.execute("COMMIT")


def create(path: Path) -> sqlite3.Connection:
    """Create a new database with the schema and the built-in catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript(_schema())
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("BEGIN")
    catalog.load(raw)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    raw.execute("COMMIT")
    raw.close()
    return connect(path)
