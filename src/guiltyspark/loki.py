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
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/loki/api/v1/query_range?{params}",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Loki query failed with HTTP {exc.code}: {body}") from exc

        if payload.get("status") != "success":
            raise RuntimeError(f"Loki query failed: {payload}")

        events: list[LogEvent] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = {str(k): str(v) for k, v in stream.get("stream", {}).items()}
            for ts, line in stream.get("values", []):
                events.append(LogEvent(ts_ns=int(ts), labels=labels, line=str(line)))

        events.sort(key=lambda event: event.ts_ns)
        return events

    def label_values(self, label: str, start_ns: int, end_ns: int) -> list[str]:
        """Return the sorted values seen for a label within the time window."""
        params = urllib.parse.urlencode({"start": str(start_ns), "end": str(end_ns)})
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/loki/api/v1/label/"
            f"{urllib.parse.quote(label, safe='')}/values?{params}",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Loki label query failed with HTTP {exc.code}: {body}") from exc

        if payload.get("status") != "success":
            raise RuntimeError(f"Loki label query failed: {payload}")

        return sorted(str(value) for value in payload.get("data") or [])

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.basic_auth:
            encoded = base64.b64encode(self.basic_auth.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        return headers
