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
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
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

  useEffect(() => {
    refetch().catch(() => {});
    const id = window.setInterval(() => {
      refetch().catch(() => {});
    }, intervalMs);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, refetch };
}
