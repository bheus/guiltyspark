import type { AnomalyGroup, IgnorePayload, Incident } from "../api/types";

// Builds the /api/anomalies/ignore payload for a single fingerprint, tolerating
// a missing incident (ported from ignorePayload in the vanilla app.js).
export function ignorePayload(
  fingerprint: string,
  incident?: Incident,
): IgnorePayload {
  return {
    fingerprint,
    service: incident?.service || "",
    level: incident?.level || "",
    count: incident?.count || 0,
    sample: incident?.samples?.[0] || "",
  };
}

// Every sample line across a group's member incidents, for pattern suggestion.
export function groupSamples(group: AnomalyGroup): string[] {
  const lines: string[] = [];
  for (const member of group.members || []) {
    for (const line of member.samples || []) lines.push(line);
  }
  return lines;
}
