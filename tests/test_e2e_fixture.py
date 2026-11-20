"""The e2e seeds produce the counts the browser tests assert on."""

from dataclasses import replace
from pathlib import Path

import pytest

from guiltyspark.config import Settings
from guiltyspark.dashboard import DashboardService
from tests.e2e import seeds


def _service(tmp_path: Path, state: str) -> DashboardService:
    settings = replace(
        Settings.from_env(),
        state_path=tmp_path / "state.sqlite3",
        findings_path=tmp_path / "findings.jsonl",
        codex_workdir=tmp_path,
        codex_home=tmp_path,
    )
    service = DashboardService(settings)
    seeds.seed(state, service.state, settings.findings_path)
    return service


@pytest.mark.parametrize(("state", "expected"), [("busy", seeds.BUSY_COUNTS), ("empty", seeds.EMPTY_COUNTS)])
def test_seeded_counts(tmp_path, state, expected):
    assert _service(tmp_path, state).overview()["counts"] == expected


def test_busy_findings_span_more_than_one_page(tmp_path):
    findings = _service(tmp_path, "busy").findings(limit=10)
    assert findings["total"] == seeds.FINDINGS
    assert findings["total"] > 10


def test_busy_has_two_targets(tmp_path):
    assert [t["id"] for t in _service(tmp_path, "busy").overview()["targets"]] == ["checkout", "ledger"]


def test_remediation_ages_are_frozen(tmp_path):
    created = [r["created_at"] for r in _service(tmp_path, "busy").state.recent_remediations()]
    assert created == ["2026-06-15 08:20:00", "2026-06-15 09:50:00", "2026-06-15 11:20:00"]
