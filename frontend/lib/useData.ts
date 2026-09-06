"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// Minimal fetch hook with loading/error/refetch — every live page uses this so
// latency and failure are handled honestly, not hidden.
//
// Stale-response guard: each load carries a monotonic request id. Only the
// latest request may write state, and a load that resolves after unmount is
// ignored — without this, rapidly switching sessions on an integrity surface
// (Provenance) or SKUs (offer-envelope drawer) could paint an older response
// under a newer header.
export function useData<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const reqId = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // `silent` skips the loading/error UI churn — for background polling that
  // refreshes data behind what's already on screen without flashing a
  // skeleton or blowing away good data on a transient failure.
  const load = useCallback(async (opts?: { silent?: boolean }) => {
    const id = ++reqId.current;
    const silent = opts?.silent ?? false;
    if (!silent) { setLoading(true); setError(null); }
    try {
      const result = await fn();
      if (mounted.current && id === reqId.current) setData(result);
    } catch (e) {
      if (mounted.current && id === reqId.current && !silent) setError(e);
    } finally {
      if (mounted.current && id === reqId.current && !silent) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load };
}
