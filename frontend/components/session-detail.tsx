"use client";
import { SectionHeader, StatusDot, Loading, ErrorState, leverLabel } from "@/components/ui";
import { GateChecklist } from "@/components/audit";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

// Shared session-detail rendering, reused by both /rail/sessions/[id] and
// /merchant/transactions/[id]. Session detail is the same underlying record
// regardless of who's looking at it — what used to differ was that a
// merchant-side session id always linked to the RAIL route, silently
// swapping the whole sidebar into the Rail role mid-click. That read as the
// merchant link "redirecting to the rail page" (a real reported bug) rather
// than opening a detail view. Each role now has its own route rendering this
// same component, so the sidebar/role context never jumps unexpectedly.
export function SessionDetailView({ id }: { id: string }) {
  const { data: s, loading, error, reload } = useData(() => api.getSession(id), [id]);

  if (loading) return <Loading rows={6} />;
  if (error || !s) return <ErrorState error={error} retry={reload} />;

  const offerRows: { label: string; value: React.ReactNode; tone?: "jade" | "crimson" }[] = s.offer
    ? [
        { label: "SKU", value: s.offer.base_sku },
        { label: "Lever", value: leverLabel(s.offer.lever) },
        { label: "Total", value: api.paise(s.offer.total_paise) },
        { label: "Margin", value: api.pct(s.offer.margin_bps) },
        { label: "Added cost", value: api.paise(s.offer.added_cost_paise) },
        { label: "Within bounds", value: s.offer.within_bounds ? "yes" : "no", tone: s.offer.within_bounds ? "jade" : "crimson" },
      ]
    : [];

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-9">
      <div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusDot status={s.status} />
          {s.outcome_reason && <span className="mono text-2xs text-ink3">{s.outcome_reason}</span>}
        </div>
        <div className="mono mt-1.5 break-all text-2xs text-ink3">{s.session_id}</div>
      </div>

      {s.offer && (
        <div>
          <SectionHeader>Offer</SectionHeader>
          <div className="stagger flex flex-col">
            {offerRows.map((r) => (
              <div key={r.label} className="flex items-center justify-between border-b border-line py-2.5">
                <span className="text-[13px] text-ink3">{r.label}</span>
                <span className={`mono text-[13px] ${r.tone === "jade" ? "text-jade" : r.tone === "crimson" ? "text-crimson" : "text-ink"}`}>{r.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <SectionHeader hint="TRD §7">Gate</SectionHeader>
        <div className="mt-1">
          <GateChecklist events={s.gate_events} />
        </div>
      </div>

      {s.receipt && (
        <div>
          <SectionHeader>Receipt</SectionHeader>
          <div className="mono mt-2.5 break-all text-2xs text-ink3">head {s.receipt.chain_head_hash}</div>
        </div>
      )}
    </div>
  );
}
