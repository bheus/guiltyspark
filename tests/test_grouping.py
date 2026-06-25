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
