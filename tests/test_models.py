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

    def test_declared_json_level_wins_over_keyword_scan(self):
        # A JSON logger reporting a clean run: "(0 errors)" in the message must
        # not outrank the line's own "level": "INFO".
        line = (
            '{"timestamp": "2026-07-29T05:02:55", "level": "INFO", '
            '"logger": "abraham.news_fetcher", "message": "News fetch complete: '
            '339 new articles, 1513 duplicates skipped (0 errors)"}'
        )
        assert _event(line).level == "info"

    def test_declared_json_error_is_still_an_error(self):
        line = '{"level": "ERROR", "message": "upstream refused"}'
        assert _event(line).level == "error"

    def test_json_level_variants_are_read(self):
        # Key spelling and spacing vary by formatter.
        assert _event('{"levelname":"INFO","msg":"error saving"}').level == "info"
        assert _event('{"severity" : "warning", "msg": "error saving"}').level == "warning"
        assert _event('{"level": "critical", "msg": "down"}').level == "error"

    def test_json_level_inside_a_word_is_not_a_declaration(self):
        assert _event('{"sublevel": "info", "msg": "error occurred"}').level == "error"
