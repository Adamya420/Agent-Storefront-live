"use client";
import { SectionHeader, Loading, ErrorState, EmptyState, leverLabel } from "@/components/ui";
import { AreaTrend, Funnel } from "@/components/charts";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

export default function AnalyticsPage() {
  const { data: a, loading, error, reload } = useData(api.getAnalytics);
  if (loading) return <Loading rows={6} />;
  if (error || !a) return <ErrorState error={error} retry={reload} />;

  const series = a.recovered_over_time.map((p) => ({
    value: Math.round(p.cumulative_recovered_paise / 100),
    label: new Date(p.ts).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
  }));
  const maxHist = Math.max(1, ...a.margin_histogram.map((h) => h.count));

  return (
    <div className="flex flex-col gap-11">
      <div>
        <SectionHeader hint="cumulative">Recovered revenue</SectionHeader>
        <div className="mt-5">
          {series.length > 1
            ? <AreaTrend data={series} tone="jade" fmt={(v) => `₹${v.toLocaleString("en-IN")}`} />
            : <EmptyState title="Not enough data" hint="Run buyer sessions to populate." />}
        </div>
      </div>

      <div>
        <SectionHeader hint="sessions → recovered">Agent traffic</SectionHeader>
        <div className="mt-5">
          <Funnel steps={[
            { label: "Sessions", value: a.funnel.sessions, tone: "ink3" },
            { label: "Offered", value: a.funnel.offered, tone: "ochre" },
            { label: "Converted", value: a.funnel.converted, tone: "jade" },
            { label: "Recovered", value: a.funnel.recovered, tone: "jade" },
          ]} />
        </div>
      </div>

      <div className="grid gap-14 lg:grid-cols-[3fr_2fr]">
        <div>
          <SectionHeader hint="revenue + margin by lever">Lever effectiveness</SectionHeader>
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

        <div>
          <SectionHeader hint="realized margin on converted sales">Margin distribution</SectionHeader>
          <div className="mt-5">
            {a.margin_histogram.length === 0 ? <EmptyState title="No data" /> : (
              <div className="stagger flex flex-col gap-3">
                {a.margin_histogram.map((h) => (
                  <div key={h.floor_bps} className="flex items-center gap-3">
                    <span className="mono w-14 shrink-0 text-right text-[11px] text-ink3">{(h.floor_bps / 100).toFixed(0)}%+</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                      <div className="h-full rounded-full bg-jade transition-all duration-700 ease-swift" style={{ width: `${(h.count / maxHist) * 100}%` }} />
                    </div>
                    <span className="mono w-6 shrink-0 text-right text-[11px] text-ink3">{h.count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
