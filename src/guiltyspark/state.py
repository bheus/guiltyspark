from __future__ import annotations

import sqlite3
from pathlib import Path


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def get_cursor_ns(self) -> int | None:
        with self._connect() as db:
            row = db.execute("select value from cursors where key = 'loki_ns'").fetchone()
            return int(row[0]) if row else None

    def set_cursor_ns(self, value: int) -> None:
        with self._connect() as db:
            db.execute(
                "insert into cursors(key, value) values('loki_ns', ?) "
                "on conflict(key) do update set value = excluded.value",
                (str(value),),
            )

    def has_finding(self, finding_hash: str) -> bool:
        with self._connect() as db:
            row = db.execute("select 1 from findings where finding_hash = ?", (finding_hash,)).fetchone()
            return row is not None

    def record_finding(self, finding_hash: str, fingerprint: str, title: str) -> None:
        with self._connect() as db:
            db.execute(
                "insert or ignore into findings(finding_hash, fingerprint, title) values (?, ?, ?)",
                (finding_hash, fingerprint, title),
            )

    def _init(self) -> None:
        with self._connect() as db:
            db.execute("create table if not exists cursors(key text primary key, value text not null)")
            db.execute(
                "create table if not exists findings("
                "finding_hash text primary key, "
                "fingerprint text not null, "
                "title text not null, "
                "created_at text not null default current_timestamp)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
