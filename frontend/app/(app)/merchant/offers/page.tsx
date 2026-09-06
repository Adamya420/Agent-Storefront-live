"use client";
import { SectionHeader, StatusDot, Loading, ErrorState, EmptyState, Pagination, leverLabel } from "@/components/ui";
import { useData } from "@/lib/useData";
import { usePagination } from "@/lib/pagination";
import * as api from "@/lib/api";

// Offers made across sessions — what the engine actually proposed, and whether it converted.
export default function OffersPage() {
  const { data, loading, error, reload } = useData(() => api.getSessions());
  const withOffers = (data?.sessions ?? []).filter((s) => s.lever && s.lever !== "");
  const { page, setPage, totalPages, pageItems, from, to, total } = usePagination(withOffers, 25);
  if (loading) return <Loading rows={6} />;
  if (error || !data) return <ErrorState error={error} retry={reload} />;

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-xl text-[13px] text-ink2">
        Offers the engine proposed to incoming agents, and how they resolved. Every one is inside the merchant band by construction.
      </p>

      <div>
        <SectionHeader hint={`${withOffers.length} offers`}>Offers</SectionHeader>
        <div className="mt-3.5">
          {withOffers.length === 0 ? (
            <EmptyState title="No offers yet" hint="Offers appear as agents shop your catalog." />
          ) : (
            <>
              <div className="overflow-x-auto">
                <div className="min-w-[600px]">
                  <div className="grid grid-cols-[2fr_1.3fr_1.3fr_1fr_1fr] pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                    <span>Session</span>
                    <span>Lever</span>
                    <span>Outcome</span>
                    <span className="text-right">Amount</span>
                    <span className="text-right">Margin</span>
                  </div>
                  <div className="border-t border-ink" />
                  <div className="stagger">
                    {pageItems.map((s) => (
                      <div
                        key={s.session_id}
                        className="grid grid-cols-[2fr_1.3fr_1.3fr_1fr_1fr] items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]"
                      >
                        <span className="mono text-[13px] text-ink">{s.session_id.slice(0, 12)}…</span>
                        <span className={`text-[13px] ${s.lever === "NONE" ? "text-ink3" : "text-jade"}`}>{leverLabel(s.lever)}</span>
                        <StatusDot status={s.status} />
                        <span className="mono text-right text-[13px] tnum">{s.total_paise ? api.paise(s.total_paise) : "—"}</span>
                        <span className="mono text-right text-[13px] tnum text-jade">{api.pct(s.margin_bps)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <Pagination page={page} totalPages={totalPages} onPageChange={setPage} from={from} to={to} total={total} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
