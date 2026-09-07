"use client";
import { useEffect, useState } from "react";
import { SectionHeader, ReasonChip, Button, Loading, ErrorState, EmptyState, leverLabel } from "@/components/ui";
import { Slider } from "@/components/charts";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

export default function SettingsPage() {
  const { data, loading, error, reload } = useData(api.getPolicy);
  const [d, setD] = useState<api.Policy | null>(null);
  const [preview, setPreview] = useState<api.EnvelopeEntry[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<number | null>(null);
  useEffect(() => { if (data) setD(data); }, [data]);

  // live envelope preview as sliders move (debounced), draft band — no save
  useEffect(() => {
    if (!d) return;
    const t = setTimeout(async () => {
      try {
        const r = await api.previewBand({
          margin_floor_bps: d.margin_floor_bps, discount_budget_bps: d.discount_budget_bps,
          return_band_max_days: d.return_band_max_days, shipping_upgrade_max_cost_paise: d.shipping_upgrade_max_cost_paise,
          bundle_enabled: d.bundle_enabled,
        });
        setPreview(r.envelope);
      } catch { /* ignore */ }
    }, 250);
    return () => clearTimeout(t);
  }, [d]);

  if (loading) return <Loading rows={5} />;
  if (error || !d) return <ErrorState error={error} retry={reload} />;

  const set = (k: keyof api.Policy) => (v: number) => setD({ ...d, [k]: v });
  async function save() {
    setSaving(true);
    try {
      const r = await api.putPolicy({ margin_floor_bps: d!.margin_floor_bps, discount_budget_bps: d!.discount_budget_bps,
        return_band_max_days: d!.return_band_max_days, shipping_upgrade_max_cost_paise: d!.shipping_upgrade_max_cost_paise, bundle_enabled: d!.bundle_enabled });
      setSaved(r.version); reload();
    } finally { setSaving(false); }
  }

  const reachable = preview.filter((e) => e.reachable).length;

  return (
    <div className="grid gap-14 lg:grid-cols-[3fr_2fr]">
      <div>
        <SectionHeader hint="the bounds the engine operates within">Merchant band</SectionHeader>
        <div className="mt-7 flex flex-col gap-7">
          <Slider label="Margin floor" hint="offers may never resolve below this" suffix="bps" min={0} max={4000} step={50}
            value={d.margin_floor_bps} onChange={set("margin_floor_bps")} />
          <Slider label="Discount budget" hint="total across standing promo + engine + coupon" suffix="bps" min={0} max={3000} step={50}
            value={d.discount_budget_bps} onChange={set("discount_budget_bps")} />
          <Slider label="Return band — furthest extension" hint="the engine can extend returns up to this" suffix="days" min={7} max={45} step={1}
            value={d.return_band_max_days} onChange={set("return_band_max_days")} />
          <Slider label="Shipping upgrade cap" hint="most the engine will spend to expedite" suffix="paise" min={0} max={30000} step={500}
            value={d.shipping_upgrade_max_cost_paise} onChange={set("shipping_upgrade_max_cost_paise")} />
          <label className="flex cursor-pointer items-center justify-between">
            <span>
              <span className="block text-[13px] text-ink">Bundle offers</span>
              <span className="block text-2xs text-ink3">let the engine propose an accretive add-on bundle</span>
            </span>
            <button type="button" role="switch" aria-checked={d.bundle_enabled}
              onClick={() => setD({ ...d, bundle_enabled: !d.bundle_enabled })}
              className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${d.bundle_enabled ? "bg-jade" : "bg-line2"}`}>
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${d.bundle_enabled ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
          </label>
        </div>
        <div className="mt-7 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save band"}</Button>
          {saved && <span className="mono text-2xs text-jade">saved · config v{saved}</span>}
          <span className="mono ml-auto text-2xs text-ink3">promo stacking {d.allow_promo_stacking ? "on" : "off"}</span>
        </div>
      </div>

      <div>
        <SectionHeader hint={preview.length ? `${reachable} of ${preview.length} reachable` : undefined}>Live preview</SectionHeader>
        <div className="mt-3.5 max-h-[460px] overflow-y-auto">
          {preview.length === 0 ? (
            <EmptyState title="Computing envelope…" hint="Representative intents will appear as the band settles." />
          ) : (
            <div className="stagger">
              {preview.map((e, i) => (
                <div key={i} className="flex items-center justify-between gap-3 border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] text-ink">{e.label}</div>
                    <div className="mono mt-0.5 text-2xs text-ink3">
                      {e.reachable ? <span className="text-jade">{api.paise(e.total_paise)} · {api.pct(e.margin_bps)}</span> : <span className="text-ink3">{e.reason}</span>}
                    </div>
                  </div>
                  <ReasonChip tone={e.reachable ? "jade" : "ochre"}>{e.reachable ? leverLabel(e.lever) : "no offer"}</ReasonChip>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
