import { useCallback, useEffect, useRef, useState } from "react";

export interface Polling<T> {
  data: T | null;
  error: Error | null;
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
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refetch = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
      return result;
    } catch (err) {
      setError(err as Error);
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

  return { data, error, refetch };
}
