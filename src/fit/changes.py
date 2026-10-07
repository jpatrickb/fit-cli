"""Every write goes through a Writer, which records it in the change log (D49) so `fit undo` can
reverse it exactly. One Writer = one change set = one transaction = one undo."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from functools import cache

from fit.errors import FitError

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def new_id() -> str:
    return str(uuid.uuid7())


@cache
def _columns(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[str]]:
    """(all columns, primary-key columns in key order)."""
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    cols = [r["name"] for r in info]
    pk = [r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])]
    return cols, pk


def _where(key: dict) -> tuple[str, list]:
    return " AND ".join(f"{k} = ?" for k in key), list(key.values())


class Writer:
    def __init__(self, conn: sqlite3.Connection, command: str, actor: str, undoes_id: str | None = None):
        self.conn = conn
        self.id = new_id()
        self._seq = 0
        conn.execute("INSERT INTO change_set (id, command, actor, undoes_id) VALUES (?,?,?,?)",
                     (self.id, command, actor, undoes_id))

    def _row(self, table: str, key: dict) -> dict | None:
        where, args = _where(key)
        row = self.conn.execute(f"SELECT * FROM {table} WHERE {where}", args).fetchone()
        return dict(row) if row else None

    def _record(self, table: str, key: dict, op: str, before: dict | None, after: dict | None) -> None:
        self._seq += 1
        self.conn.execute(
            "INSERT INTO change (change_set_id, seq, table_name, row_key, op, before_json, after_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (self.id, self._seq, table, json.dumps(key), op,
             None if before is None else json.dumps(before), None if after is None else json.dumps(after)))

    def insert(self, table: str, values: dict) -> dict:
        _, pk = _columns(self.conn, table)
        values = dict(values)
        if pk == ["id"] and "id" not in values:
            values["id"] = new_id()
        names = list(values)
        self.conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                          [values[n] for n in names])
        key = {k: values[k] for k in pk}
        after = self._row(table, key)
        assert after is not None
        self._record(table, key, "insert", None, after)
        return after

    def update(self, table: str, key: dict, values: dict) -> dict:
        cols, _ = _columns(self.conn, table)
        before = self._row(table, key)
        if before is None:
            raise FitError("not_found", f"no {table} row {json.dumps(key)}")
        sets = [f"{k} = ?" for k in values]
        args = list(values.values())
        if "updated_at" in cols and "updated_at" not in values:
            sets.append(f"updated_at = {NOW}")
        where, wargs = _where(key)
        self.conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {where}", args + wargs)
        after = self._row(table, key)
        assert after is not None
        self._record(table, key, "update", before, after)
        return after

    def delete(self, table: str, key: dict) -> None:
        """Remove a row: soft-delete where the table supports it, hard-delete otherwise (D50)."""
        cols, _ = _columns(self.conn, table)
        if "deleted_at" in cols:
            now = self.conn.execute(f"SELECT {NOW}").fetchone()[0]
            self.update(table, key, {"deleted_at": now})
            return
        before = self._row(table, key)
        where, args = _where(key)
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", args)
        self._record(table, key, "delete", before, None)


@contextmanager
def writing(conn: sqlite3.Connection, command: str, actor: str, undoes_id: str | None = None):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield Writer(conn, command, actor, undoes_id)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def undo(conn: sqlite3.Connection, actor: str) -> dict:
    """Reverse the most recent change set that isn't an undo and hasn't been undone (A3)."""
    target = conn.execute(
        "SELECT * FROM change_set c WHERE c.command <> 'undo' "
        "AND NOT EXISTS (SELECT 1 FROM change_set u WHERE u.undoes_id = c.id) "
        "ORDER BY c.rowid DESC LIMIT 1").fetchone()
    if target is None:
        raise FitError("nothing_to_undo", "there are no writes left to undo")
    changes = conn.execute("SELECT * FROM change WHERE change_set_id = ? ORDER BY seq DESC",
                           (target["id"],)).fetchall()
    with writing(conn, "undo", actor, undoes_id=target["id"]) as w:
        for c in changes:
            key = json.loads(c["row_key"])
            if c["op"] == "insert":
                w.delete(c["table_name"], key)
            elif c["op"] == "update":
                before = json.loads(c["before_json"])
                after = json.loads(c["after_json"])
                restore = {k: v for k, v in before.items() if after.get(k) != v and k != "updated_at"}
                if restore:
                    w.update(c["table_name"], key, restore)
            else:  # delete
                w.insert(c["table_name"], json.loads(c["before_json"]))
    return {"undid": target["id"], "command": target["command"], "at": target["at"], "rows": len(changes)}
