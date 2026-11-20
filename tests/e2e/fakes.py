"""One local HTTP server standing in for Loki and the GitHub API.

Anything it does not recognise is recorded, so the browser tests can fail when the
dashboard reaches for an endpoint the fixtures never meant it to touch.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.e2e import seeds

# Dashboards on these ports serve these seeds; Loki requests carry no state, so the
# fake tells them apart by the X-E2E-State header the fixture server adds.
unexpected: list[str] = []


def _events_in(state: str, start_ns: int, end_ns: int, limit: int) -> list[dict]:
    streams = []
    budget = limit
    for stream in seeds.loki_events(state):
        values = sorted(v for v in stream["values"] if start_ns <= int(v[0]) < end_ns)
        if values and budget > 0:
            streams.append({"stream": stream["stream"], "values": values[:budget]})
            budget -= len(values[:budget])
    return streams


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _send(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        state = self.headers.get("X-E2E-State", "busy")
        if url.path == "/__health":
            return self._send({"ok": True})
        if url.path == "/__unexpected":
            return self._send(unexpected)
        if url.path == "/loki/api/v1/query_range":
            streams = _events_in(state, int(query["start"]), int(query["end"]), int(query["limit"]))
            return self._send({"status": "success", "data": {"resultType": "streams", "result": streams}})
        if url.path == "/loki/api/v1/query":
            total = seeds.total_events(state)
            result = [{"metric": {}, "value": [0, str(total)]}] if total else []
            return self._send({"status": "success", "data": {"resultType": "vector", "result": result}})
        if url.path.startswith("/loki/api/v1/label/") and url.path.endswith("/values"):
            return self._send({"status": "success", "data": seeds.containers(state)})
        if url.path.startswith("/repos/") and "/pulls/" in url.path:
            return self._send({"state": "open", "merged_at": None, "closed_at": None, "html_url": ""})
        unexpected.append(f"GET {self.path}")
        self._send({"message": "Not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
