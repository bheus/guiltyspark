import { useCallback, useEffect, useRef, useState } from "react";

export interface Polling<T> {
  data: T | null;
  error: Error | null;
  // True while `data` belongs to a different query than the current `deps` —
  // on first load, and between changing the query and its result arriving.
  // Callers whose data is labelled with the query (the survey window, say)
  // must not present `data` as the answer while this is set.
  loading: boolean;
  refetch: () => Promise<T>;
}

// Fetches on mount, then waits `intervalMs` after each reply before fetching
// again, re-running when `deps` change. The previous `data` is retained during a
// refetch and on error, so the UI never flickers to empty on the 60s tick —
// components stay mounted and keep their local state (open cards, in-progress
// edits). This is the core of the state-preserving render model that replaced
// innerHTML wholesale-replacement.
//
// The interval is a gap between requests, not a fixed cadence: a request slower
// than the interval would otherwise have the next one issued on top of it. The
// anomaly survey reaches several seconds on a wide window while the pending-
// clustering interval is ~2s, so a fixed cadence stacks surveys onto Loki.
// Waiting from the reply keeps exactly one request in flight per query.
//
// `intervalMs` may be a function of the latest data, letting a caller poll
// faster while the server reports work in flight. It is read when scheduling
// the next fetch, so a change takes effect on the following cycle and never
// triggers an extra request of its own.
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number | ((data: T | null) => number),
  deps: unknown[] = [],
): Polling<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  // Which query the settled `data`/`error` describes, compared against the
  // current one to derive `loading`.
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const key = JSON.stringify(deps);
  const keyRef = useRef(key);
  keyRef.current = key;

  const refetch = useCallback(async () => {
    const requestKey = keyRef.current;
    try {
      const result = await fetcherRef.current();
      // Queries differ in cost — a 24h survey outruns a 1h one — so a slow
      // reply to a query the operator has already moved on from must not
      // overwrite the newer one it lost the race to.
      if (keyRef.current !== requestKey) return result;
      setData(result);
      setError(null);
      setSettledKey(requestKey);
      return result;
    } catch (err) {
      if (keyRef.current === requestKey) {
        setError(err as Error);
        setSettledKey(requestKey);
      }
      throw err;
    }
  }, []);

  const intervalRef = useRef(intervalMs);
  intervalRef.current = intervalMs;

  // Fetch on mount and whenever the query changes, then chain from each reply.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const cycle = async () => {
      let result: T | null = null;
      try {
        result = await refetch();
      } catch {
        // Keep polling through a failed request; `error` already carries it.
      }
      if (cancelled) return;
      const spec = intervalRef.current;
      // Derive the gap from this reply rather than from rendered state, which
      // may not have committed yet at this point in the cycle.
      const next = typeof spec === "function" ? spec(result) : spec;
      timer = window.setTimeout(cycle, next);
    };

    cycle();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading: settledKey !== key, refetch };
}
