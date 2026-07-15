from __future__ import annotations

from guiltyspark.models import LogEvent


def _event(line: str, **labels) -> LogEvent:
    return LogEvent(ts_ns=1, labels=labels, line=line)


class TestLogEventLevel:
    def test_label_wins_over_line_contents(self):
        assert _event("everything is fine", level="error").level == "error"

    def test_declared_logfmt_level_wins_over_keyword_scan(self):
        # Loki logs the queries it serves at info, quoting the query text. A
        # query hunting for "error" must not match its own echo.
        line = (
            'level=info ts=2026-07-15T15:32:09Z caller=metrics.go:159 '
            'component=querier query="{job=~\\".+\\"} |~ \\"(?i)(error|fatal)\\""'
        )
        assert _event(line).level == "info"

    def test_declared_error_is_still_an_error(self):
        assert _event('level=error msg="upstream refused"').level == "error"

    def test_keyword_scan_still_applies_without_a_declaration(self):
        assert _event("Traceback (most recent call last):").level == "error"
        assert _event("kernel panic - not syncing").level == "fatal"
        assert _event("connection established").level == "info"

    def test_severity_synonyms_normalize_to_known_levels(self):
        assert _event("x", level="PANIC").level == "fatal"
        assert _event("x", level="critical").level == "error"
        assert _event("x", level="warn").level == "warning"
        assert _event("x", level="debug").level == "info"
        assert _event('lvl=panic msg="down"').level == "fatal"

    def test_quoted_and_uppercase_declarations_are_read(self):
        assert _event('level="INFO" msg="error saving"').level == "info"

    def test_level_inside_a_word_is_not_a_declaration(self):
        # `sublevel=info` is not this line declaring itself info.
        assert _event("sublevel=info error occurred").level == "error"
