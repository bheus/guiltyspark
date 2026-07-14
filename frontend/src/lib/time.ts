// Timestamp helpers ported verbatim from the vanilla app.js. SQLite's
// `current_timestamp` yields a naive UTC string ("2026-07-13 16:09:21") with no
// zone marker; `new Date()` would read that as local time and shift it by the
// viewer's offset. Stamp such values as UTC so every timestamp resolves to the
// same instant, then let toLocaleString render it in the browser's own timezone.

export function toUtcDate(iso: string): Date {
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasZone ? iso : iso.replace(" ", "T") + "Z");
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return toUtcDate(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtNs(ns: number): string {
  return fmtTime(new Date(ns / 1e6).toISOString());
}
