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

// Fetches on mount and every `intervalMs`, re-running when `deps` change. The
// previous `data` is retained during a refetch and on error, so the UI never
// flickers to empty on the 60s tick — components stay mounted and keep their
// local state (open cards, in-progress edits). This is the core of the
// state-preserving render model that replaced innerHTML wholesale-replacement.
//
// `intervalMs` may be a function of the latest data, letting a caller poll
// faster while the server reports work in flight. Changing the interval only
// reschedules the timer; it never triggers an extra fetch.
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

  const delay = typeof intervalMs === "function" ? intervalMs(data) : intervalMs;

  // Fetch on mount and whenever the query itself changes.
  useEffect(() => {
    refetch().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // Keep the timer separate so a change of `delay` reschedules without
  // refetching — otherwise every interval change would burn an extra request.
  useEffect(() => {
    const id = window.setInterval(() => {
      refetch().catch(() => {});
    }, delay);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delay, ...deps]);

  return { data, error, loading: settledKey !== key, refetch };
}
