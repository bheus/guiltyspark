from pathlib import Path
from types import SimpleNamespace

import pytest

import guiltyspark.codex as codex


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        codex_workdir=tmp_path,
        codex_path="codex",
        codex_home=tmp_path,
        codex_timeout_seconds=10,
        analysis_model_name=None,
        github_token_env="GITHUB_TOKEN",
    )


def _result(returncode: int, message: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stderr=message, stdout="")


def test_503_retries_are_bounded_and_recover(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) < 3:
            return _result(1, 'worker quit: HTTP 503 circuit_open')
        Path(command[-2]).write_text('{"ok": true}', encoding="utf-8")
        return _result(0)

    monkeypatch.setattr(codex.subprocess, "run", run)
    monkeypatch.setattr(codex.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(codex.time, "sleep", lambda _delay: None)

    assert codex.execute_codex(_settings(tmp_path), "prompt") == '{"ok": true}'
    assert len(calls) == 3
    assert codex.transport_readiness() == {"ready": True, "degraded": False, "error": None}


def test_non_503_client_error_remains_terminal(monkeypatch, tmp_path):
    calls = []

    def run(_command, **_kwargs):
        calls.append(True)
        return _result(1, "HTTP 401: unauthorized")

    monkeypatch.setattr(codex.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="HTTP 401"):
        codex.execute_codex(_settings(tmp_path), "prompt")
    assert len(calls) == 1


def test_exhausted_transient_retries_report_degraded_readiness(monkeypatch, tmp_path):
    calls = []
    delays = []

    def run(_command, **_kwargs):
        calls.append(True)
        return _result(
            1,
            "worker quit with fatal: Transport channel closed, when Client "
            '(HttpRequest(HttpRequest("http/request failed")))',
        )

    monkeypatch.setattr(codex.subprocess, "run", run)
    monkeypatch.setattr(codex.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(codex.time, "sleep", delays.append)

    with pytest.raises(RuntimeError, match="Transport channel closed"):
        codex.execute_codex(_settings(tmp_path), "prompt")
    assert len(calls) == 4
    assert delays == [0, 0, 0]
    readiness = codex.transport_readiness()
    assert readiness["ready"] is False
    assert readiness["degraded"] is True
    assert "Transport channel closed" in readiness["error"]


def test_transport_retries_use_increasing_jittered_backoff(monkeypatch, tmp_path):
    delays = []

    monkeypatch.setattr(
        codex.subprocess,
        "run",
        lambda _command, **_kwargs: _result(1, "error sending request"),
    )
    monkeypatch.setattr(codex.random, "uniform", lambda _low, high: high)
    monkeypatch.setattr(codex.time, "sleep", delays.append)

    with pytest.raises(RuntimeError, match="error sending request"):
        codex.execute_codex(_settings(tmp_path), "prompt")

    assert delays == [1.0, 2.0, 4.0]
