"use client";
import { useState } from "react";
import clsx from "clsx";
import { SectionHeader, ReasonChip, Loading, ErrorState, EmptyState, Pagination } from "@/components/ui";
import { AuditTimeline } from "@/components/audit";
import { useData } from "@/lib/useData";
import { usePagination } from "@/lib/pagination";
import * as api from "@/lib/api";

// The rail's legitimate provenance explorer: pick a session, reconstruct and verify
// its tamper-evident chain against the global ledger.
export default function ProvenancePage() {
  const list = useData(() => api.getSessions());
  const [sel, setSel] = useState<string | null>(null);
  const trail = useData(() => (sel ? api.getAuditTrail(sel) : Promise.resolve(null as unknown as api.AuditTrail)), [sel]);
  const { page, setPage, totalPages, pageItems, from, to, total } = usePagination(list.data?.sessions ?? [], 25);

  return (
    <div className="grid gap-10 lg:grid-cols-5">
      <div className="lg:col-span-2">
        <SectionHeader hint="pick one to verify">Sessions</SectionHeader>
        <div className="mt-2">
          {list.loading ? (
            <Loading rows={6} />
          ) : list.error ? (
            <ErrorState error={list.error} retry={list.reload} />
          ) : !list.data || list.data.sessions.length === 0 ? (
            <EmptyState title="No sessions" />
          ) : (
            <>
            <div className="stagger flex flex-col">
              {pageItems.map((s) => {
                const on = sel === s.session_id;
                const dot = s.status === "CONVERTED" ? "bg-jade" : s.status === "DENIED" ? "bg-crimson" : "bg-ink3";
                return (
                  <button
                    key={s.session_id}
                    onClick={() => setSel(s.session_id)}
                    className={clsx(
                      "flex items-center gap-2.5 border-b border-line border-l-2 px-3 py-2.5 text-left transition-colors duration-150",
                      on ? "border-accent bg-gradient-to-r from-accent-bg to-transparent" : "border-transparent hover:bg-ink/[0.025]"
                    )}
                  >
                    <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
                    <span className={clsx("mono text-[13px]", on ? "font-semibold text-ink" : "text-ink2")}>{s.session_id.slice(0, 14)}…</span>
                    <span className="mono ml-auto text-2xs text-ink3">{s.status}</span>
                  </button>
                );
              })}
            </div>
            <Pagination page={page} totalPages={totalPages} onPageChange={setPage} from={from} to={to} total={total} />
            </>
          )}
        </div>
      </div>

      <div className="lg:col-span-3">
        {!sel ? (
          <div className="pt-8">
            <EmptyState title="Select a session" hint="Its provenance chain will be reconstructed and verified here." />
          </div>
        ) : trail.loading ? (
          <Loading rows={6} />
        ) : trail.error || !trail.data ? (
          <ErrorState error={trail.error} retry={trail.reload} />
        ) : (
          <div>
            <SectionHeader hint={`${sel.slice(0, 16)}…`}>Provenance chain</SectionHeader>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <ReasonChip tone={trail.data.chain_verified ? "jade" : "crimson"}>
                {trail.data.chain_verified ? "Chain verified" : `Broken @ seq ${trail.data.first_bad_seq}`}
              </ReasonChip>
              {trail.data.receipt_present && (
                <ReasonChip tone={trail.data.receipt_verified ? "jade" : "crimson"}>
                  {trail.data.receipt_verified ? "Receipt verified" : "Receipt mismatch"}
                </ReasonChip>
              )}
              <span className="mono ml-auto text-2xs text-ink3">{trail.data.steps.length} steps</span>
            </div>
            <div className="mt-5">
              <AuditTimeline trail={trail.data} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
