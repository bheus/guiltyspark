// Thin fetch wrappers over the dashboard JSON API. Ports getJSON/sendJSON from
// the former vanilla app.js. Every backend error surfaces as a thrown Error
// carrying the server's {"error": ...} message.

import type {
  AnomaliesResponse,
  FindingsResponse,
  IgnorePayload,
  IgnoredResponse,
  OverviewResponse,
  RemediationsResponse,
  SuggestPatternResponse,
  Target,
  TargetsResponse,
} from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload as T;
}

type Method = "POST" | "DELETE";

async function sendJSON<T>(
  method: Method,
  path: string,
  body?: unknown,
): Promise<T> {
  const opts: RequestInit = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(path, opts);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload as T;
}

export const api = {
  overview: () => getJSON<OverviewResponse>("/api/overview"),
  anomalies: (minutes: number) =>
    getJSON<AnomaliesResponse>(`/api/anomalies?minutes=${minutes}`),
  ignored: () => getJSON<IgnoredResponse>("/api/anomalies/ignored"),
  findings: (limit: number, offset: number) =>
    getJSON<FindingsResponse>(`/api/findings?limit=${limit}&offset=${offset}`),
  remediations: (limit: number, offset: number) =>
    getJSON<RemediationsResponse>(
      `/api/remediations?limit=${limit}&offset=${offset}`,
    ),
  targets: () => getJSON<TargetsResponse>("/api/targets"),

  ignore: (payload: IgnorePayload) =>
    sendJSON("POST", "/api/anomalies/ignore", payload),
  ignoreBatch: (anomalies: IgnorePayload[]) =>
    sendJSON("POST", "/api/anomalies/ignore-batch", { anomalies }),
  restore: (fingerprint: string) =>
    sendJSON(
      "DELETE",
      `/api/anomalies/ignore?fingerprint=${encodeURIComponent(fingerprint)}`,
    ),
  updateNote: (fingerprint: string, note: string) =>
    sendJSON("POST", "/api/anomalies/note", { fingerprint, note }),
  suggestPattern: (service: string, samples: string[]) =>
    sendJSON<SuggestPatternResponse>("POST", "/api/anomalies/suggest-pattern", {
      service,
      samples,
    }),
  createRule: (service: string, pattern: string, title: string, note: string) =>
    sendJSON("POST", "/api/anomalies/rules", { service, pattern, title, note }),
  updateRuleMetadata: (id: number, title: string, note: string) =>
    sendJSON("POST", "/api/anomalies/rules/metadata", { id, title, note }),
  deleteRule: (id: number) =>
    sendJSON("DELETE", `/api/anomalies/rules?id=${encodeURIComponent(id)}`),

  saveTarget: (target: Target) => sendJSON("POST", "/api/targets", target),
  deleteTarget: (id: string) =>
    sendJSON("DELETE", `/api/targets?id=${encodeURIComponent(id)}`),
};
