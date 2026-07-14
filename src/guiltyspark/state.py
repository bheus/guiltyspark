from __future__ import annotations

import json
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

    # -- semantic issue registry ------------------------------------------

    def issue_for_fingerprint(self, target_id: str, fingerprint: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "select issue_key from issue_members "
                "where target_id = ? and fingerprint = ?",
                (target_id, fingerprint),
            ).fetchone()
        return row[0] if row else None

    def record_issue_member(
        self, target_id: str, fingerprint: str, issue_key: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into issue_members(target_id, fingerprint, issue_key) "
                "values (?, ?, ?) on conflict(target_id, fingerprint) do nothing",
                (target_id, fingerprint, issue_key),
            )

    def create_issue(
        self,
        target_id: str,
        issue_key: str,
        title: str,
        service: str,
        anchor_fingerprint: str,
        anchor_sample: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into remediation_issues"
                "(target_id, issue_key, title, service, anchor_fingerprint, anchor_sample) "
                "values (?, ?, ?, ?, ?, ?) "
                "on conflict(target_id, issue_key) do nothing",
                (target_id, issue_key, title, service, anchor_fingerprint, anchor_sample),
            )

    def active_issues(
        self, target_id: str, within_seconds: int, limit: int = 40
    ) -> list[dict]:
        """Issues first seen within the window, newest first, for match context."""
        with self._connect() as db:
            rows = db.execute(
                "select issue_key, title, service, anchor_fingerprint, anchor_sample "
                "from remediation_issues "
                "where target_id = ? "
                "and created_at >= datetime('now', ?) "
                "order by created_at desc limit ?",
                (target_id, f"-{int(within_seconds)} seconds", limit),
            ).fetchall()
        return [
            {
                "issue_key": row[0],
                "title": row[1],
                "service": row[2],
                "anchor_fingerprint": row[3],
                "anchor_sample": row[4],
            }
            for row in rows
        ]

    def issue_last_pr(self, target_id: str, issue_key: str) -> dict | None:
        """Most recent PR-bearing remediation among the issue's member fingerprints."""
        with self._connect() as db:
            row = db.execute(
                "select r.pr_url, r.status, r.created_at from remediations r "
                "join issue_members m "
                "  on m.target_id = r.target_id and m.fingerprint = r.fingerprint "
                "where r.target_id = ? and m.issue_key = ? "
                "and r.pr_url is not null and r.pr_url != '' "
                "order by r.id desc limit 1",
                (target_id, issue_key),
            ).fetchone()
        if row is None:
            return None
        return {"pr_url": row[0], "status": row[1], "created_at": row[2]}

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

    def count_remediations(self) -> int:
        with self._connect() as db:
            return int(db.execute("select count(*) from remediations").fetchone()[0])

    def recent_remediations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "select target_id, fingerprint, status, branch, pr_url, created_at "
                "from remediations order by id desc limit ? offset ?",
                (limit, max(0, offset)),
            ).fetchall()
        return [
            {
                "target_id": row[0],
                "fingerprint": row[1],
                "status": row[2],
                "branch": row[3],
                "pr_url": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def dashboard_counts(self) -> dict[str, int]:
        with self._connect() as db:
            findings = db.execute("select count(*) from findings").fetchone()[0]
            target_findings = db.execute("select count(*) from target_findings").fetchone()[0]
            remediations = db.execute("select count(*) from remediations").fetchone()[0]
            prs_opened = db.execute(
                "select count(*) from remediations where status = 'pr-opened'"
            ).fetchone()[0]
        return {
            "findings": int(findings) + int(target_findings),
            "remediations": int(remediations),
            "prs_opened": int(prs_opened),
        }

    # --- targets (DB-backed, editable from the dashboard) ---------------

    def list_target_payloads(self) -> list[dict]:
        """Return stored target payloads, ordered by id."""
        with self._connect() as db:
            rows = db.execute(
                "select payload from targets order by id"
            ).fetchall()
        payloads: list[dict] = []
        for (raw,) in rows:
            try:
                payloads.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return payloads

    def count_targets(self) -> int:
        with self._connect() as db:
            return int(db.execute("select count(*) from targets").fetchone()[0])

    def upsert_target(self, target_id: str, payload: dict) -> None:
        with self._connect() as db:
            db.execute(
                "insert into targets(id, payload, updated_at) values (?, ?, current_timestamp) "
                "on conflict(id) do update set payload = excluded.payload, "
                "updated_at = current_timestamp",
                (target_id, json.dumps(payload, sort_keys=True)),
            )

    def delete_target(self, target_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("delete from targets where id = ?", (target_id,))
            return cursor.rowcount > 0

    def seed_targets_if_empty(self, payloads: list[dict]) -> bool:
        """Seed the targets table from env/file once, guarded so a later web
        deletion is never undone by a restart. Returns True if seeding ran."""
        with self._connect() as db:
            already = db.execute(
                "select 1 from cursors where key = 'targets_seeded'"
            ).fetchone()
            if already is not None:
                return False
            for payload in payloads:
                target_id = str(payload.get("id", "")).strip()
                if not target_id:
                    continue
                db.execute(
                    "insert or ignore into targets(id, payload) values (?, ?)",
                    (target_id, json.dumps(payload, sort_keys=True)),
                )
            db.execute(
                "insert or ignore into cursors(key, value) values ('targets_seeded', '1')"
            )
        return True

    # --- ignored anomalies (noise the operator has silenced) ------------

    def ignore_anomaly(
        self,
        fingerprint: str,
        note: str = "",
        service: str = "",
        level: str = "",
        sample: str = "",
        count: int = 0,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "insert into ignored_anomalies"
                "(fingerprint, note, service, level, sample, count) "
                "values (?, ?, ?, ?, ?, ?) "
                "on conflict(fingerprint) do update set "
                "note = excluded.note, service = excluded.service, "
                "level = excluded.level, sample = excluded.sample, "
                "count = excluded.count",
                (fingerprint, note, service, level, sample, int(count or 0)),
            )

    def ignore_anomalies(self, anomalies: list[dict]) -> int:
        """Silence many anomalies in one transaction. Returns the count applied."""
        rows = [
            (
                str(item.get("fingerprint", "")).strip(),
                str(item.get("note", "")).strip(),
                str(item.get("service", "")).strip(),
                str(item.get("level", "")).strip(),
                str(item.get("sample", "")).strip(),
                int(item.get("count") or 0),
            )
            for item in anomalies
        ]
        rows = [row for row in rows if row[0]]
        if not rows:
            return 0
        with self._connect() as db:
            db.executemany(
                "insert into ignored_anomalies"
                "(fingerprint, note, service, level, sample, count) "
                "values (?, ?, ?, ?, ?, ?) "
                "on conflict(fingerprint) do update set "
                "note = excluded.note, service = excluded.service, "
                "level = excluded.level, sample = excluded.sample, "
                "count = excluded.count",
                rows,
            )
        return len(rows)

    def set_ignored_note(self, fingerprint: str, note: str) -> bool:
        """Update only the triage note of an already-silenced anomaly."""
        with self._connect() as db:
            cursor = db.execute(
                "update ignored_anomalies set note = ? where fingerprint = ?",
                (note, fingerprint),
            )
            return cursor.rowcount > 0

    def unignore_anomaly(self, fingerprint: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "delete from ignored_anomalies where fingerprint = ?", (fingerprint,)
            )
            return cursor.rowcount > 0

    def ignored_fingerprints(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute("select fingerprint from ignored_anomalies").fetchall()
        return {str(row[0]) for row in rows}

    def list_ignored_anomalies(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "select fingerprint, note, service, level, sample, count, created_at "
                "from ignored_anomalies order by created_at desc"
            ).fetchall()
        return [
            {
                "fingerprint": row[0],
                "note": row[1],
                "service": row[2],
                "level": row[3],
                "sample": row[4],
                "count": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    # --- pattern silence rules ------------------------------------------

    def add_ignore_rule(
        self, service: str, pattern: str, note: str = "", title: str = ""
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "insert into ignore_rules(service, pattern, note, title) values (?, ?, ?, ?)",
                (service, pattern, note, title),
            )
            return int(cursor.lastrowid)

    def list_ignore_rules(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "select id, service, pattern, note, title, created_at "
                "from ignore_rules order by created_at desc"
            ).fetchall()
        return [
            {
                "id": row[0],
                "service": row[1],
                "pattern": row[2],
                "note": row[3],
                "title": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def delete_ignore_rule(self, rule_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "delete from ignore_rules where id = ?", (rule_id,)
            )
            return cursor.rowcount > 0

    def set_ignore_rule_metadata(self, rule_id: int, title: str, note: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "update ignore_rules set title = ?, note = ? where id = ?",
                (title, note, rule_id),
            )
            return cursor.rowcount > 0

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
            db.execute(
                "create table if not exists targets("
                "id text primary key, "
                "payload text not null, "
                "updated_at text not null default current_timestamp)"
            )
            db.execute(
                "create table if not exists ignored_anomalies("
                "fingerprint text primary key, "
                "note text not null default '', "
                "service text not null default '', "
                "level text not null default '', "
                "sample text not null default '', "
                "count integer not null default 0, "
                "created_at text not null default current_timestamp)"
            )
            # Migrate DBs created before triage context was captured.
            existing = {
                row[1] for row in db.execute("pragma table_info(ignored_anomalies)")
            }
            for column, ddl in (
                ("service", "text not null default ''"),
                ("level", "text not null default ''"),
                ("sample", "text not null default ''"),
                ("count", "integer not null default 0"),
            ):
                if column not in existing:
                    db.execute(
                        f"alter table ignored_anomalies add column {column} {ddl}"
                    )
            db.execute(
                "create table if not exists ignore_rules("
                "id integer primary key autoincrement, "
                "service text not null default '', "
                "pattern text not null, "
                "note text not null default '', "
                "title text not null default '', "
                "created_at text not null default current_timestamp)"
            )
            rule_columns = {
                row[1] for row in db.execute("pragma table_info(ignore_rules)")
            }
            if "title" not in rule_columns:
                db.execute(
                    "alter table ignore_rules add column title text not null default ''"
                )
            # A logical "issue" is a Codex-assigned semantic cluster that many
            # distinct fingerprints can map to, so remediation dedups on the
            # underlying malfunction rather than on exact log wording.
            db.execute(
                "create table if not exists remediation_issues("
                "target_id text not null, "
                "issue_key text not null, "
                "title text not null default '', "
                "service text not null default '', "
                "anchor_fingerprint text not null default '', "
                "anchor_sample text not null default '', "
                "created_at text not null default current_timestamp, "
                "primary key(target_id, issue_key))"
            )
            db.execute(
                "create table if not exists issue_members("
                "target_id text not null, "
                "fingerprint text not null, "
                "issue_key text not null, "
                "created_at text not null default current_timestamp, "
                "primary key(target_id, fingerprint))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
