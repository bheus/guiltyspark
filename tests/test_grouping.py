import unittest

from guiltyspark.grouping import group_incidents, is_signal
from guiltyspark.models import LogEvent, fingerprint_for, normalize_line


def event(line: str, ts: int = 1, labels: dict[str, str] | None = None) -> LogEvent:
    return LogEvent(ts_ns=ts, labels=labels or {"app": "paperless"}, line=line)


class GroupingTests(unittest.TestCase):
    def test_normalize_line_removes_noisy_numbers_and_ids(self) -> None:
        left = normalize_line("request 123 failed for user abcdef123456")
        right = normalize_line("request 456 failed for user fedcba999999")
        self.assertEqual(left, right)

    def test_fingerprint_groups_same_error_shape(self) -> None:
        self.assertEqual(fingerprint_for(event("job 123 failed")), fingerprint_for(event("job 456 failed")))

    def test_fingerprint_is_stable_across_embedded_timestamps(self) -> None:
        """A timestamp in the message body must not change the fingerprint.

        Regression: the `T` separator is a word character, so `\\b\\d+\\b` left the
        day-of-month and hour intact ("25t13"). Every hour produced a fresh
        fingerprint, so finding dedup never fired — one wedged database yielded
        25 duplicate findings and 25 wasted Codex analyses in a day.
        """
        def line(stamp: str) -> str:
            return (
                '{"timestamp": "%s", "level": "WARNING", "logger": "abraham.trading_app", '
                '"line": 476, "message": "Failed to write heartbeat: database is locked"}'
                % stamp
            )

        stamps = [
            "2026-07-25T13:00:23",      # the original
            "2026-07-25T19:04:11",      # a later hour
            "2026-07-28T04:59:02",      # a later day
            "2026-07-28T04:59:02.123",  # sub-second precision
            "2026-07-28T04:59:02Z",     # UTC suffix
            "2026-07-28 04:59:02",      # space separator
        ]
        fingerprints = {fingerprint_for(event(line(s))) for s in stamps}
        self.assertEqual(len(fingerprints), 1, fingerprints)

    def test_fingerprint_still_separates_genuinely_different_errors(self) -> None:
        """Collapsing timestamps must not collapse distinct messages."""
        stamp = "2026-07-25T13:00:23"
        locked = fingerprint_for(event(f'{stamp} failed to write heartbeat: database is locked'))
        benchmark = fingerprint_for(event(f'{stamp} unable to sync benchmark history: not null'))
        self.assertNotEqual(locked, benchmark)

    def test_is_signal_detects_error_terms_without_level_label(self) -> None:
        self.assertTrue(is_signal(event("upstream connection refused")))
        self.assertFalse(is_signal(event("started worker successfully")))

    def test_group_incidents_keeps_repeated_warning(self) -> None:
        events = [
            event("retry 1 failed for upstream", ts=1),
            event("retry 2 failed for upstream", ts=2),
            event("started worker successfully", ts=3),
        ]
        incidents = group_incidents(events, min_events=2)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].count, 2)
        self.assertEqual(incidents[0].service, "paperless")
