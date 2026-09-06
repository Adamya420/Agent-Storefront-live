"use client";
import { useEffect, useMemo, useState } from "react";

// Client-side pagination — the backend's list endpoints (getSessions,
// getGateLog, …) return the full array with no limit/offset/cursor params,
// so there's nothing to page server-side. This slices for display only.
// Resets to page 1 whenever the underlying list identity changes (a new
// filter, a reload) via the `resetKey` dependency.
export function usePagination<T>(items: T[], pageSize = 25, resetKey?: unknown) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const clamped = Math.min(page, totalPages);
  const pageItems = useMemo(() => {
    const start = (clamped - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, clamped, pageSize]);
  const from = items.length === 0 ? 0 : (clamped - 1) * pageSize + 1;
  const to = Math.min(clamped * pageSize, items.length);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setPage(1), [resetKey]);

  return { page: clamped, setPage, totalPages, pageItems, from, to, total: items.length };
}
