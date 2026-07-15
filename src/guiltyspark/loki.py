from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from guiltyspark.models import LogEvent


@dataclass(frozen=True)
class LokiClient:
    base_url: str
    bearer_token: str | None = None
    basic_auth: str | None = None
    timeout_seconds: int = 30

    def query_range(self, query: str, start_ns: int, end_ns: int, limit: int) -> list[LogEvent]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": str(start_ns),
                "end": str(end_ns),
                "limit": str(limit),
                "direction": "forward",
            }
        )
        payload = self._get(f"/loki/api/v1/query_range?{params}", "Loki query failed")

        events: list[LogEvent] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = {str(k): str(v) for k, v in stream.get("stream", {}).items()}
            for ts, line in stream.get("values", []):
                events.append(LogEvent(ts_ns=int(ts), labels=labels, line=str(line)))

        events.sort(key=lambda event: event.ts_ns)
        return events

    def query_window(
        self,
        query: str,
        start_ns: int,
        end_ns: int,
        page_limit: int,
        max_events: int,
    ) -> tuple[list[LogEvent], bool]:
        """Page through a whole time window rather than taking one capped slice.

        `query_range` asks Loki for `limit` matches in forward order, so a busy
        window comes back as the oldest matches only. Resume from the last event
        of each page until the window is exhausted, and report whether
        `max_events` cut the walk short.
        """
        events: list[LogEvent] = []
        cursor_ns = start_ns
        while cursor_ns < end_ns and len(events) < max_events:
            request_limit = min(page_limit, max_events - len(events))
            page = self.query_range(
                query=query,
                start_ns=cursor_ns,
                end_ns=end_ns,
                limit=request_limit,
            )
            events.extend(page)
            if len(page) < request_limit:
                return events, False
            # Events sharing the last page's final nanosecond are given up here;
            # without the skip the next page would repeat the page just read.
            cursor_ns = page[-1].ts_ns + 1
        return events, cursor_ns < end_ns

    def count_over_window(self, query: str, start_ns: int, end_ns: int) -> int:
        """Total lines matching `query` in the window, counted by Loki itself.

        An instant metric query, so the count is exact and its cost does not
        scale with the volume being counted — unlike counting fetched lines,
        which is bounded by whatever page limit the caller could afford.
        """
        seconds = max(1, (end_ns - start_ns) // 1_000_000_000)
        params = urllib.parse.urlencode(
            {
                "query": f"sum(count_over_time({query} [{seconds}s]))",
                "time": str(end_ns),
            }
        )
        payload = self._get(f"/loki/api/v1/query?{params}", "Loki count query failed")
        result = payload.get("data", {}).get("result") or []
        if not result:
            return 0
        return int(float(result[0]["value"][1]))

    def label_values(self, label: str, start_ns: int, end_ns: int) -> list[str]:
        """Return the sorted values seen for a label within the time window."""
        params = urllib.parse.urlencode({"start": str(start_ns), "end": str(end_ns)})
        payload = self._get(
            f"/loki/api/v1/label/{urllib.parse.quote(label, safe='')}/values?{params}",
            "Loki label query failed",
        )
        return sorted(str(value) for value in payload.get("data") or [])

    def _get(self, path: str, error_prefix: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}", headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{error_prefix} with HTTP {exc.code}: {body}") from exc

        if payload.get("status") != "success":
            raise RuntimeError(f"{error_prefix}: {payload}")
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.basic_auth:
            encoded = base64.b64encode(self.basic_auth.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        return headers
