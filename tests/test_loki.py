from __future__ import annotations

from guiltyspark.loki import LokiClient
from guiltyspark.models import LogEvent


class FakeLoki(LokiClient):
    """A client whose pages come from a scripted list instead of HTTP."""

    def __init__(self, pages: list[list[LogEvent]]) -> None:
        super().__init__(base_url="http://loki.invalid:3100")
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "calls", [])

    def query_range(self, query, start_ns, end_ns, limit):
        self.calls.append({"start_ns": start_ns, "end_ns": end_ns, "limit": limit})
        if not self.pages:
            return []
        return self.pages.pop(0)[:limit]


def _events(start: int, count: int) -> list[LogEvent]:
    return [
        LogEvent(ts_ns=start + i, labels={"container": "abraham"}, line=f"error {i}")
        for i in range(count)
    ]


def test_short_page_ends_the_walk_in_one_request():
    client = FakeLoki([_events(10, 3)])

    events, truncated = client.query_window(
        query='{job=~".+"}', start_ns=0, end_ns=1000, page_limit=5, max_events=100
    )

    assert len(events) == 3
    assert truncated is False
    assert len(client.calls) == 1


def test_full_page_resumes_after_the_last_event():
    client = FakeLoki([_events(10, 5), _events(30, 2)])

    events, truncated = client.query_window(
        query='{job=~".+"}', start_ns=0, end_ns=1000, page_limit=5, max_events=100
    )

    # The whole window is returned, not just the oldest page.
    assert [event.ts_ns for event in events] == [10, 11, 12, 13, 14, 30, 31]
    assert truncated is False
    # Second request resumes one nanosecond past the last event of page one.
    assert [call["start_ns"] for call in client.calls] == [0, 15]


def test_max_events_caps_the_walk_and_reports_truncation():
    client = FakeLoki([_events(10, 5), _events(30, 5), _events(50, 5)])

    events, truncated = client.query_window(
        query='{job=~".+"}', start_ns=0, end_ns=1000, page_limit=5, max_events=8
    )

    assert len(events) == 8
    assert truncated is True
    # The final request asks only for the events still under the cap.
    assert [call["limit"] for call in client.calls] == [5, 3]


def test_exhausted_window_is_not_reported_as_truncated():
    client = FakeLoki([_events(10, 5), []])

    events, truncated = client.query_window(
        query='{job=~".+"}', start_ns=0, end_ns=1000, page_limit=5, max_events=100
    )

    assert len(events) == 5
    assert truncated is False
