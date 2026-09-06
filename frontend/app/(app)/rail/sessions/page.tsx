"use client";
import { useState } from "react";
import Link from "next/link";
import { StatusDot, Loading, ErrorState, EmptyState, Pagination, leverLabel } from "@/components/ui";
import { useData } from "@/lib/useData";
import { usePagination } from "@/lib/pagination";
import * as api from "@/lib/api";

const FILTERS = ["", "CONVERTED", "ABANDONED", "DENIED"];

export default function RailSessions() {
  const [filter, setFilter] = useState("");
  const { data, loading, error, reload } = useData(() => api.getSessions(filter || undefined), [filter]);
  const { page, setPage, totalPages, pageItems, from, to, total } = usePagination(data?.sessions ?? [], 25, filter);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-5 border-b border-line">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`border-b-2 pb-2.5 text-[13px] transition-colors duration-150 ${
              filter === f ? "border-ink font-semibold text-ink" : "border-transparent text-ink3 hover:text-ink2"
            }`}
          >
            {f || "All"}
          </button>
        ))}
      </div>

      <div>
        {loading ? (
          <Loading rows={6} />
        ) : error ? (
          <ErrorState error={error} retry={reload} />
        ) : !data || data.sessions.length === 0 ? (
          <EmptyState title="No sessions yet" hint="Agent traffic will appear here as sessions run." />
        ) : (
          <div className="overflow-x-auto">
            <div className="min-w-[760px]">
              <div className="grid grid-cols-[2fr_1.6fr_1.2fr_1fr_1fr] pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                <span>Session</span>
                <span>Status</span>
                <span>Lever</span>
                <span className="text-right">Amount</span>
                <span className="text-right">Margin</span>
              </div>
              <div className="border-t border-ink" />
              <div className="stagger">
                {pageItems.map((s) => (
                  <div
                    key={s.session_id}
                    className="grid grid-cols-[2fr_1.6fr_1.2fr_1fr_1fr] items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]"
                  >
                    <div>
                      <Link href={`/rail/sessions/${s.session_id}`} className="mono text-[13px] text-ink transition-colors duration-150 hover:text-accent">
                        {s.session_id.slice(0, 14)}…
                      </Link>
                      <div className="mono text-2xs text-ink3">{s.agent_type}</div>
                    </div>
                    <div>
                      <StatusDot status={s.status} />
                      {s.outcome_reason && s.status !== "CONVERTED" && (
                        <div className="mono mt-0.5 text-2xs text-ink3">{s.outcome_reason}</div>
                      )}
                    </div>
                    <span className="text-[13px] text-ink2">{leverLabel(s.lever)}</span>
                    <span className="mono text-right text-[13px] tnum">{s.total_paise ? api.paise(s.total_paise) : "—"}</span>
                    <span className="mono text-right text-[13px] tnum text-ink3">{api.pct(s.margin_bps)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
        {data && data.sessions.length > 0 && <Pagination page={page} totalPages={totalPages} onPageChange={setPage} from={from} to={to} total={total} />}
      </div>
    </div>
  );
}
