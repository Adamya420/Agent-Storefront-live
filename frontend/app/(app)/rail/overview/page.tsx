"use client";
import { HeroMetric, StatRail, SectionHeader, Loading, ErrorState, EmptyState } from "@/components/ui";
import { CompositionBars } from "@/components/charts";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

// Deny-reason bars stay within the crimson/ochre "enforcement" family (never a
// rainbow), but a real backend can surface more distinct reason codes than the
// 3-4 semantic tones cover — cycling only those caused two unrelated reasons
// to render in the identical color (a real collision bug caught during the
// build). CSS-var opacity steps give more distinguishable shades while
// staying theme-adaptive (these vars already swap between light/dark).
const REASON_PALETTE = [
  "rgb(var(--crimson))", "rgb(var(--ochre))", "rgb(var(--crimson) / 0.55)",
  "rgb(var(--ochre) / 0.55)", "rgb(var(--ink3))", "rgb(var(--crimson) / 0.8)",
];
// tone -> explicit Tailwind class map. Tailwind's build only includes classes
// it can see as literal strings in source, so a `text-${t}` template can
// silently fail to generate the class it needs; map it explicitly instead.
const ENFORCEMENT_TONE_CLASS: Record<string, string> = {
  jade: "text-jade", ochre: "text-ochre", crimson: "text-crimson",
};

// Razorpay-side view: authorization throughput, gate enforcement, settlement.
export default function RailOverview() {
  const ov = useData(api.getOverview);
  const gl = useData(api.getGateLog);
  // Wait for BOTH fetches before painting anything — gating on ov alone let
  // the hero/enforcement numbers (ov) render while "What the gate stopped"
  // (gl) was still in flight, so that panel visibly popped in late.
  if (ov.loading || gl.loading) return <Loading rows={6} />;
  if (ov.error || !ov.data) return <ErrorState error={ov.error} retry={ov.reload} />;
  const m = ov.data;
  const passRate = m.sessions ? Math.round(((m.sessions - m.denied) / m.sessions) * 10000) : 0;

  const reasonEntries = Object.entries(gl.data?.by_reason || {}).sort((a, b) => b[1] - a[1]);
  const reasonTotal = reasonEntries.reduce((s, [, v]) => s + v, 0) || 1;
  const reasonComposition = reasonEntries.map(([k, v], i) => ({
    name: k, pct: Math.round((v / reasonTotal) * 100), tone: "crimson" as const,
    color: REASON_PALETTE[i % REASON_PALETTE.length],
  }));

  return (
    <div className="flex flex-col gap-11">
      <div className="grid gap-14 lg:grid-cols-[2fr_1fr]">
        <HeroMetric label="Gate pass rate" value={api.pct(passRate)}
          delta={`${m.denied} denied`} deltaTone={m.denied ? "crimson" : "jade"}
          sub="Share of authorization attempts the gate allowed through to settlement." />
        <StatRail items={[
          { label: "Authorizations", value: m.sessions },
          { label: "Settled volume", value: api.paise(m.gross_converted_paise) },
          { label: "Blocked", value: m.denied, tone: m.denied ? "crimson" : "ink" },
        ]} />
      </div>

      <div className="grid gap-14 lg:grid-cols-2">
        <div>
          <SectionHeader hint="by reason">What the gate stopped</SectionHeader>
          <div className="mt-5">
            {reasonComposition.length
              ? <CompositionBars data={reasonComposition} />
              : <EmptyState title="Nothing blocked" hint="No out-of-bounds settlement attempted." />}
          </div>
        </div>
        <div>
          <SectionHeader hint="the boundary in numbers">Enforcement</SectionHeader>
          <div className="stagger mt-5 grid grid-cols-4 divide-x divide-line">
            {[["Converted", m.converted, "jade"], ["Abandoned", m.abandoned, "ochre"], ["Denied", m.denied, "crimson"], ["Recovered", m.recovered_sales, "jade"]].map(([l, v, t]) => (
              <div key={l as string} className="px-3 py-1 first:pl-0 sm:px-4">
                <div className="text-[11px] uppercase tracking-wide text-ink3">{l as string}</div>
                <div className={`mono mt-1.5 text-xl font-semibold tnum sm:text-2xl ${ENFORCEMENT_TONE_CLASS[t as string]}`}>{v as number}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
