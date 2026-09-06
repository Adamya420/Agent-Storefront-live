"use client";
import { SectionHeader, StatRail, Loading, ErrorState, EmptyState, leverLabel } from "@/components/ui";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

export default function ReportsPage() {
  const ov = useData(api.getOverview);
  const an = useData(api.getAnalytics);
  if (ov.loading || an.loading) return <Loading rows={6} />;
  if (ov.error || !ov.data) return <ErrorState error={ov.error} retry={ov.reload} />;
  if (an.error || !an.data) return <ErrorState error={an.error} retry={an.reload} />;
  const m = ov.data, a = an.data;

  function exportCsv() {
    const rows = [["lever", "sales", "revenue_paise", "avg_margin_bps"],
      ...a!.lever_effectiveness.map((l) => [l.lever, l.count, l.revenue_paise, l.avg_margin_bps])];
    const csv = rows.map((r) => r.join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url; link.download = "acg-report.csv"; link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-col gap-11">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <p className="max-w-xl text-[13px] text-ink2">A summary of engine performance over all recorded sessions.</p>
        <button onClick={exportCsv}
          className="self-start rounded-pill border border-line2 px-4 py-2 text-[13px] font-medium text-ink2 transition-colors duration-150 hover:bg-ink/5 hover:text-ink sm:self-auto">
          Export CSV
        </button>
      </div>

      <StatRail items={[
        { label: "Recovered revenue", value: api.paise(m.recovered_revenue_paise), tone: "jade" },
        { label: "Gross converted", value: api.paise(m.gross_converted_paise) },
        { label: "Conversion rate", value: api.pct(m.conversion_rate_bps) },
        { label: "Denied at gate", value: m.denied, tone: m.denied ? "crimson" : "ink" },
      ]} />

      <div>
        <SectionHeader hint="all-time">Lever effectiveness</SectionHeader>
        <div className="mt-3.5">
          {a.lever_effectiveness.length === 0 ? <EmptyState title="No conversions yet" /> : (
            <>
              <div className="grid grid-cols-[2fr_1fr_1fr_1fr] pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                <span>Lever</span><span className="text-right">Sales</span><span className="text-right">Revenue</span><span className="text-right">Avg margin</span>
              </div>
              <div className="border-t border-ink" />
              <div className="stagger">
                {a.lever_effectiveness.map((l) => (
                  <div key={l.lever} className="grid grid-cols-[2fr_1fr_1fr_1fr] items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]">
                    <span className="text-[13px] text-ink2">{leverLabel(l.lever)}</span>
                    <span className="mono text-right text-[13px] tnum">{l.count}</span>
                    <span className="mono text-right text-[13px] tnum">{api.paise(l.revenue_paise)}</span>
                    <span className="mono text-right text-[13px] tnum text-jade">{api.pct(l.avg_margin_bps)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
