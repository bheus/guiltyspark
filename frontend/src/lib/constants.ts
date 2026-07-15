export const REFRESH_MS = 60_000;
// While the dashboard reports `groups_pending`, anomalies are polled at this
// faster cadence so the grouped view (and the lattice covering it) resolves as
// soon as the clustering worker publishes, rather than waiting out REFRESH_MS.
export const PENDING_REFRESH_MS = 2_000;
export const PAGE_SIZE = 10;
