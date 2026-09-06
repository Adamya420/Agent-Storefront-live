"use client";
import { useEffect } from "react";
import Link from "next/link";
import { HeroMetric, StatRail, SectionHeader, StatusDot, Loading, ErrorState, EmptyState, leverLabel } from "@/components/ui";
import { AreaTrend, Funnel, CompositionBars } from "@/components/charts";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

// A checkout paid via payment link lands the session at
// OPEN/PENDING_PAYMENT until something polls the capture — see
// /console/sessions/reconcile. Home has no server push, so it fast-polls
// that endpoint itself and silently refreshes the metrics/chart/session
// list the moment a payment converts, instead of requiring a detour
// through Transactions (which was the only page doing this before).
const RECONCILE_POLL_MS = 2000;

export default function MerchantHome() {
  const ov = useData(api.getOverview);
  const an = useData(api.getAnalytics);
  const tx = useData(() => api.getSessions());

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const r = await api.reconcileSessions();
        if (!cancelled && r.converted > 0) {
          ov.reload({ silent: true });
          an.reload({ silent: true });
          tx.reload({ silent: true });
        }
      } catch {
        /* transient — next tick retries */
      } finally {
        inFlight = false;
      }
    };
    tick();
    const id = setInterval(tick, RECONCILE_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Wait for all three fetches before painting anything — gating on ov/an
  // alone let the hero/chart/funnel/composition render while sessions (tx)
  // was still in flight, so "Recent sessions" visibly popped in late.
  if (ov.loading || an.loading || tx.loading) return <Loading rows={6} />;
  if (ov.error) return <ErrorState error={ov.error} retry={ov.reload} />;
  if (an.error) return <ErrorState error={an.error} retry={an.reload} />;
  const m = ov.data!;
  const a = an.data;

  const series = (a?.recovered_over_time || []).map((p) => ({
    value: Math.round(p.cumulative_recovered_paise / 100),
    label: new Date(p.ts).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  }));
  const leverEntries = Object.entries(m.lever_mix);
  const leverTotal = leverEntries.reduce((s, [, v]) => s + v, 0) || 1;
  const leverColors: Record<string, "jade" | "ochre" | "crimson" | "ink3"> = { NONE: "ink3", RETURN_EXTENSION: "jade", DISCOUNT: "ochre", BUNDLE: "crimson", SHIPPING_UPGRADE: "jade", MULTI: "ochre" };
  const composition = leverEntries.map(([k, v]) => ({ name: leverLabel(k), pct: Math.round((v / leverTotal) * 100), tone: leverColors[k] || "ink3" }));

  return (
    <div className="flex flex-col gap-11">
      <div className="grid gap-14 lg:grid-cols-[2fr_1fr]">
        <div>
          <HeroMetric label="Recovered revenue · last 30 days" value={api.paise(m.recovered_revenue_paise)}
            delta={`+${m.engine_delta} sales vs. baseline`}
            sub="Sales the deterministic offer engine recovered inside the authorized band that the passive baseline abandons." />
          <div className="mt-5">
            {series.length > 1
              ? <AreaTrend data={series} tone="jade" fmt={(v) => `₹${v.toLocaleString("en-IN")}`} />
              : <EmptyState title="Not enough sales yet" hint="Run a few buyer sessions to see the trend build." />}
          </div>
        </div>
        <StatRail items={[
          { label: "Conversion rate", value: api.pct(m.conversion_rate_bps) },
          { label: "Gross converted", value: api.paise(m.gross_converted_paise) },
          { label: "Denied at gate", value: m.denied, tone: m.denied ? "crimson" : "ink" },
          { label: "Sessions seen", value: m.sessions },
        ]} />
      </div>

      <div>
        <SectionHeader hint="sessions → recovered">Agent traffic</SectionHeader>
        <div className="mt-5">
          {a ? (
            <Funnel steps={[
              { label: "Sessions", value: a.funnel.sessions, tone: "ink3" },
              { label: "Offered", value: a.funnel.offered, tone: "ochre" },
              { label: "Converted", value: a.funnel.converted, tone: "jade" },
              { label: "Recovered", value: a.funnel.recovered, tone: "jade" },
            ]} />
          ) : <Loading rows={2} />}
        </div>
      </div>

      <div className="grid gap-14 lg:grid-cols-[1fr_2fr]">
        <div>
          <SectionHeader>How sales were saved</SectionHeader>
          <div className="mt-5">
            {composition.length ? <CompositionBars data={composition} /> : <EmptyState title="No conversions yet" />}
          </div>
        </div>
        <div>
          <SectionHeader>Recent sessions</SectionHeader>
          <div className="mt-3.5">
            {tx.loading ? <Loading rows={5} /> : !tx.data || tx.data.sessions.length === 0
              ? <EmptyState title="No transactions yet" />
              : (
                <>
                  <div className="grid grid-cols-[2fr_1fr_1.3fr_1fr_1fr] pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                    <span>Session</span><span>Status</span><span>Lever</span><span className="text-right">Amount</span><span className="text-right">Margin</span>
                  </div>
                  <div className="border-t border-ink" />
                  <div className="stagger">
                    {tx.data.sessions.slice(0, 6).map((s) => (
                      <div key={s.session_id} className="grid grid-cols-[2fr_1fr_1.3fr_1fr_1fr] items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]">
                        <Link href={`/merchant/transactions/${s.session_id}`} className="mono text-[13px] text-ink hover:text-accent">{s.session_id.slice(0, 12)}…</Link>
                        <StatusDot status={s.status} />
                        <span className="text-[13px] text-ink2">{leverLabel(s.lever)}</span>
                        <span className="mono text-right text-[13px] tnum">{s.total_paise ? api.paise(s.total_paise) : "—"}</span>
                        <span className="mono text-right text-[13px] tnum text-ink3">{api.pct(s.margin_bps)}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
          </div>
        </div>
      </div>
    </div>
  );
}
