"use client";
import { SectionHeader, StatRail, ReasonChip, Loading, ErrorState, EmptyState, Pagination } from "@/components/ui";
import { useData } from "@/lib/useData";
import { usePagination } from "@/lib/pagination";
import * as api from "@/lib/api";

export default function GateLogPage() {
  const { data: g, loading, error, reload } = useData(api.getGateLog);
  const { page, setPage, totalPages, pageItems, from, to, total } = usePagination(g?.denies ?? [], 25);
  if (loading) return <Loading rows={6} />;
  if (error || !g) return <ErrorState error={error} retry={reload} />;

  const reasons = Object.entries(g.by_reason).sort((a, b) => b[1] - a[1]);

  return (
    <div className="flex flex-col gap-11">
      <div>
        <p className="max-w-2xl text-[13px] text-ink2">
          Every settlement the gate refused, with the exact check that fired. Nothing here moved money — the boundary held.
        </p>
        <div className="mt-5">
          <StatRail items={[
            { label: "Total denied", value: g.count, tone: g.count ? "crimson" : "ink" },
            ...reasons.slice(0, 3).map(([r, c]) => ({ label: r, value: c, tone: "crimson" as const })),
          ]} />
        </div>
      </div>

      <div>
        <SectionHeader hint="most recent first">Refusals</SectionHeader>
        <div className="mt-3.5">
          {g.denies.length === 0 ? (
            <EmptyState title="Nothing refused" hint="No out-of-bounds settlement attempted." />
          ) : (
            <div className="overflow-x-auto">
              <div className="min-w-[620px]">
                <div className="grid grid-cols-[1.6fr_1.4fr_1fr_1fr] pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                  <span>Session</span>
                  <span>Reason</span>
                  <span>Stage</span>
                  <span className="text-right">When</span>
                </div>
                <div className="border-t border-ink" />
                <div className="stagger">
                  {pageItems.map((d, i) => (
                    <div key={i} className="grid grid-cols-[1.6fr_1.4fr_1fr_1fr] items-center border-b border-line py-2.5 transition-colors duration-150 hover:bg-ink/[0.025]">
                      <span className="mono text-[13px] text-ink">{d.session_id.slice(0, 12)}…</span>
                      <span><ReasonChip tone="crimson">{d.reason_code}</ReasonChip></span>
                      <span className="text-[13px] text-ink2">{d.type}</span>
                      <span className="mono text-right text-[12px] tnum text-ink3">{d.ts ? new Date(d.ts).toLocaleTimeString() : "—"}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
          {g.denies.length > 0 && <Pagination page={page} totalPages={totalPages} onPageChange={setPage} from={from} to={to} total={total} />}
        </div>
      </div>
    </div>
  );
}
