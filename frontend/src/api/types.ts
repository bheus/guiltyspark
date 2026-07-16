// TypeScript mirrors of the JSON payloads served by the Python dashboard API.
// See src/guiltyspark/dashboard.py for the authoritative shapes.

export interface Incident {
  fingerprint: string;
  service: string;
  level: string;
  count: number;
  last_seen_ns: number;
  samples: string[];
  bucket: string;
}

export interface AnomalyGroup {
  id: string;
  title: string;
  summary: string;
  level: string;
  count: number;
  last_seen_ns: number;
  services: string[];
  fingerprints: string[];
  members: Incident[];
}

export interface TimelineBin {
  t: string;
  count: number;
}

export interface AnomaliesResponse {
  generated_at: string;
  window_minutes: number;
  containers: string[];
  total_events: number;
  truncated: boolean;
  error_events: number;
  ignored_count: number;
  incidents: Incident[];
  timeline: TimelineBin[];
  groups?: AnomalyGroup[];
  groups_pending?: boolean;
}

export interface ContainersResponse {
  generated_at: string;
  label: string;
  containers: string[];
}

export interface OverviewTarget {
  id: string;
  github_repo: string;
  mode: string;
}

export interface OverviewResponse {
  generated_at: string;
  loki_url: string;
  interval_seconds: number;
  targets: OverviewTarget[];
  counts: { findings: number; remediations: number };
}

export interface Finding {
  severity: string;
  title: string;
  created_at: string;
  summary: string;
  suspected_cause: string;
  recommended_fix: string;
  evidence: string[];
}

export interface FindingsResponse {
  findings: Finding[];
  total: number;
  limit: number;
  offset: number;
}

export interface Measure {
  status: string;
  target_id: string;
  fingerprint: string;
  created_at: string;
  pr_url?: string;
  pr_state?: string;
  merged_at?: string;
  closed_at?: string;
}

export interface RemediationsResponse {
  remediations: Measure[];
  total: number;
  limit: number;
  offset: number;
}

export interface SilencedItem {
  fingerprint: string;
  service?: string;
  level?: string;
  count?: number;
  sample?: string;
  note: string;
  created_at: string;
}

export interface SilenceRule {
  id: number;
  service?: string;
  pattern: string;
  note: string;
  title?: string;
  created_at: string;
}

export interface IgnoredResponse {
  ignored: SilencedItem[];
  rules: SilenceRule[];
}

export interface Target {
  id: string;
  mode: string;
  loki_url: string;
  github_repo: string;
  loki_query: string;
  base_branch: string;
  max_changed_files: number;
  test_commands: string[];
  allowed_paths: string[];
  expected_logs_path?: string;
  held_remediations?: number;
  release_observed?: boolean;
}

export interface TargetsResponse {
  targets: Target[];
}

export interface SuggestPatternResponse {
  pattern?: string;
  explanation?: string;
  warning?: string;
}

// Payload shape POSTed to /api/anomalies/ignore(-batch).
export interface IgnorePayload {
  fingerprint: string;
  service: string;
  level: string;
  count: number;
  sample: string;
}
