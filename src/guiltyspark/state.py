from __future__ import annotations

import sqlite3
from pathlib import Path


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def get_cursor_ns(self, target_id: str = "default") -> int | None:
        with self._connect() as db:
            row = db.execute(
                "select value from cursors where key = ?", (f"loki_ns:{target_id}",)
            ).fetchone()
            if row is None and target_id == "default":
                row = db.execute("select value from cursors where key = 'loki_ns'").fetchone()
            return int(row[0]) if row else None

    def set_cursor_ns(self, value: int, target_id: str = "default") -> None:
        with self._connect() as db:
            db.execute(
                "insert into cursors(key, value) values(?, ?) "
                "on conflict(key) do update set value = excluded.value",
                (f"loki_ns:{target_id}", str(value)),
            )

    def has_target_finding(self, target_id: str, fingerprint: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "select 1 from target_findings where target_id = ? and fingerprint = ?",
                (target_id, fingerprint),
            ).fetchone()
            return row is not None

    def record_target_finding(self, target_id: str, fingerprint: str, title: str) -> None:
        with self._connect() as db:
            db.execute(
                "insert or ignore into target_findings(target_id, fingerprint, title) values (?, ?, ?)",
                (target_id, fingerprint, title),
            )

    def record_remediation(
        self,
        target_id: str,
        fingerprint: str,
        status: str,
        details: str = "",
        branch: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into remediations(target_id, fingerprint, status, details, branch, pr_url) "
                "values (?, ?, ?, ?, ?, ?)",
                (target_id, fingerprint, status, details, branch, pr_url),
            )

    def has_completed_remediation(self, target_id: str, fingerprint: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "select 1 from remediations "
                "where target_id = ? and fingerprint = ? and status in ('validated', 'pr-opened') "
                "limit 1",
                (target_id, fingerprint),
            ).fetchone()
            return row is not None

    def enqueue_remediation_job(
        self, target_id: str, fingerprint: str, payload: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into remediation_jobs(target_id, fingerprint, payload) values (?, ?, ?) "
                "on conflict(target_id, fingerprint) do update set payload = excluded.payload",
                (target_id, fingerprint, payload),
            )

    def pending_remediation_jobs(
        self, target_id: str, include_validated: bool = False
    ) -> list[tuple[str, str]]:
        statuses = "('pending', 'failed', 'validated')" if include_validated else "('pending', 'failed')"
        with self._connect() as db:
            rows = db.execute(
                f"select fingerprint, payload from remediation_jobs "
                f"where target_id = ? and status in {statuses} order by updated_at",
                (target_id,),
            ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def update_remediation_job(
        self, target_id: str, fingerprint: str, status: str, error: str = ""
    ) -> None:
        with self._connect() as db:
            db.execute(
                "update remediation_jobs set status = ?, attempts = attempts + 1, "
                "last_error = ?, updated_at = current_timestamp "
                "where target_id = ? and fingerprint = ?",
                (status, error, target_id, fingerprint),
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
            db.execute(
                "create table if not exists target_findings("
                "target_id text not null, "
                "fingerprint text not null, "
                "title text not null, "
                "created_at text not null default current_timestamp, "
                "primary key(target_id, fingerprint))"
            )
            db.execute(
                "create table if not exists remediations("
                "id integer primary key autoincrement, "
                "target_id text not null, "
                "fingerprint text not null, "
                "status text not null, "
                "details text not null default '', "
                "branch text, "
                "pr_url text, "
                "created_at text not null default current_timestamp)"
            )
            db.execute(
                "create table if not exists remediation_jobs("
                "target_id text not null, "
                "fingerprint text not null, "
                "payload text not null, "
                "status text not null default 'pending', "
                "attempts integer not null default 0, "
                "last_error text not null default '', "
                "created_at text not null default current_timestamp, "
                "updated_at text not null default current_timestamp, "
                "primary key(target_id, fingerprint))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
