"use client";
import { useState } from "react";
import { SectionHeader, Button, Loading, ErrorState, EmptyState, ReasonChip, leverLabel, Drawer, Modal } from "@/components/ui";
import { useData } from "@/lib/useData";
import * as api from "@/lib/api";

const CATEGORIES = ["running_shoes", "training_shoes", "apparel", "socks", "accessories"];
const GRID = "grid-cols-[2.2fr_1.3fr_1fr_1fr_0.8fr_1.2fr]";

export default function ProductsPage() {
  const { data, loading, error, reload } = useData(api.getCatalog);
  const [open, setOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [sku, setSku] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-end justify-between gap-4">
        <p className="max-w-xl text-[13px] text-ink2">
          Your SKUs, and the bounded offers the engine can legally make on each.
        </p>
        <div className="flex gap-2.5">
          <Button variant="secondary" onClick={() => setImporting(true)}>Import CSV</Button>
          <Button variant="primary" onClick={() => setOpen(true)}>Onboard SKU</Button>
        </div>
      </div>

      <div>
        <SectionHeader hint={data ? `${data.count} SKUs` : undefined}>Catalog</SectionHeader>
        <div className="mt-3.5">
          {loading ? <Loading rows={6} /> : error ? <ErrorState error={error} retry={reload} /> :
            !data || data.products.length === 0 ? (
              <EmptyState title="No products" hint="Onboard a SKU to preview its offer envelope." />
            ) : (
              <div className="overflow-x-auto">
                <div className="min-w-[720px]">
                  <div className={`grid ${GRID} pb-2.5 text-[11px] font-semibold uppercase tracking-wide text-ink3`}>
                    <span>SKU</span><span>Category</span><span className="text-right">Price</span>
                    <span className="text-right">Margin</span><span className="text-right">Return</span><span></span>
                  </div>
                  <div className="border-t border-ink" />
                  <div className="stagger">
                    {data.products.map((p) => {
                      const price = p.effective_price_paise != null ? p.effective_price_paise : p.list_price_paise;
                      const margin = price > 0 ? Math.round(((price - p.cost_paise) / price) * 10000) : null;
                      return (
                        <div key={p.sku} className={`grid ${GRID} items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]`}>
                          <div>
                            <div className="text-[13px] text-ink">{p.title}</div>
                            <div className="mono text-[11px] text-ink3">{p.sku}{p.promo_code ? ` · ${p.promo_code}` : ""}</div>
                          </div>
                          <span className="text-[13px] text-ink2">{p.category.replace(/_/g, " ")}</span>
                          <span className="mono text-right text-[13px] tnum">{api.paise(price)}</span>
                          <span className="mono text-right text-[13px] tnum text-ink2">{api.pct(margin)}</span>
                          <span className="mono text-right text-[13px] tnum text-ink2">{p.return_days}d</span>
                          <div className="text-right">
                            <button onClick={() => setSku(p.sku)}
                              className="rounded-md border border-line2 px-2.5 py-1 text-[12px] text-ink2 transition-colors duration-150 hover:bg-ink/5 hover:text-ink">
                              Offer envelope
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
        </div>
      </div>

      {sku && <EnvelopeDrawer sku={sku} onClose={() => setSku(null)} />}
      {open && <OnboardModal onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
      {importing && <ImportCsvModal onClose={() => setImporting(false)} onDone={() => reload()} />}
    </div>
  );
}

function EnvelopeList({ envelope }: { envelope: api.EnvelopeEntry[] }) {
  return (
    <div className="stagger flex flex-col">
      {envelope.map((e, i) => (
        <div key={i} className="border-b border-line py-3.5 last:border-0">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-ink">{e.label}</span>
            <ReasonChip tone={e.reachable ? "jade" : "ochre"}>{e.reachable ? leverLabel(e.lever) : "no offer"}</ReasonChip>
          </div>
          <div className="mono mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-ink3">
            <span>ceiling {api.paise(e.intent.max_price_paise)}</span>
            <span>&ge;{e.intent.min_return_days}d</span>
            {e.reachable
              ? <span className="text-jade">&rarr; {api.paise(e.total_paise)} &middot; {e.return_days}d &middot; {api.pct(e.margin_bps)}</span>
              : <span className="text-ochre">&rarr; {e.reason}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function EnvelopeDrawer({ sku, onClose }: { sku: string; onClose: () => void }) {
  const { data, loading, error } = useData(() => api.getEnvelope(sku), [sku]);
  return (
    <Drawer title="Offer envelope" sub={sku} onClose={onClose}>
      <p className="mb-4 text-[13px] text-ink2">
        Every legal move the engine could make on this SKU across representative buyer intents — and where it must decline.
      </p>
      {loading && <div className="text-[13px] text-ink2">Computing envelope…</div>}
      {error ? <div className="text-[13px] text-crimson">Couldn&apos;t compute the envelope.</div> : null}
      {data && <EnvelopeList envelope={data.envelope} />}
    </Drawer>
  );
}

function OnboardModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({
    sku: "", title: "", category: "running_shoes", price: "", cost: "",
    stock: "20", return_days: "14", shipping_days: "2", promo: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await api.onboardProduct({
        sku: f.sku, title: f.title, category: f.category,
        list_price_paise: Math.round(parseFloat(f.price) * 100), cost_paise: Math.round(parseFloat(f.cost) * 100),
        stock: parseInt(f.stock) || 20, return_days: parseInt(f.return_days), shipping_days: parseInt(f.shipping_days),
        promo_code: f.promo || null,
      });
      onDone();
    } catch (e) {
      setErr(e instanceof api.ApiError ? e.body : String(e));
    } finally {
      setBusy(false);
    }
  }

  const inputCls = "mt-1 w-full rounded-[6px] border border-line2 bg-bg px-3 py-2 text-[13px] text-ink outline-none transition-colors focus:border-accent";
  const F = (k: keyof typeof f, label: string, props: object = {}) => (
    <label className="block">
      <span className="text-[11px] text-ink3">{label}</span>
      <input value={f[k]} onChange={set(k)} {...props} className={inputCls} />
    </label>
  );

  return (
    <Modal title="Onboard a SKU" sub="Add a product; the engine will preview the offers it can legally make on it." onClose={onClose}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {F("sku", "SKU")}
        {F("title", "Title")}
        <label className="block">
          <span className="text-[11px] text-ink3">Category</span>
          <select value={f.category} onChange={set("category")} className={inputCls}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        {F("promo", "Promo code (optional)")}
        {F("price", "List price (₹)", { type: "number" })}
        {F("cost", "Cost (₹)", { type: "number" })}
        {F("return_days", "Return days", { type: "number" })}
        {F("shipping_days", "Shipping days", { type: "number" })}
        {F("stock", "Stock", { type: "number" })}
      </div>
      {err && <div className="mono mt-3 text-[11px] text-crimson">{err}</div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={busy}>{busy ? "Adding…" : "Onboard"}</Button>
      </div>
    </Modal>
  );
}

function ImportCsvModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<api.CsvImportResult | null>(null);

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    setText(await file.text());
    setResult(null); setErr(null);
  }

  async function downloadTemplate() {
    try {
      const t = await api.getImportTemplate();
      const url = URL.createObjectURL(new Blob([t.content], { type: "text/csv" }));
      const a = document.createElement("a"); a.href = url; a.download = t.filename; a.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  }

  async function submit() {
    if (!text.trim()) { setErr("Choose a CSV file first."); return; }
    setBusy(true); setErr(null); setResult(null);
    try {
      const r = await api.importCatalogCsv(text);
      setResult(r);
      if (r.created + r.updated > 0) onDone();      // refresh the catalog table immediately
    } catch (e) {
      setErr(e instanceof api.ApiError ? e.body : String(e));
    } finally { setBusy(false); }
  }

  return (
    <Modal title="Import SKUs from CSV" sub="Bulk add or update products. Prices are in rupees; existing SKUs are updated in place." onClose={onClose}>
      <button onClick={downloadTemplate} className="mb-3 text-[12px] text-accent underline underline-offset-2">Download template</button>

      <label className="flex cursor-pointer items-center justify-center gap-2 rounded-card border border-dashed border-line2 bg-base/40 px-4 py-6 text-[13px] text-ink2 transition-colors hover:border-accent/50 hover:text-ink">
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M12 16V4m0 0L8 8m4-4l4 4M4 20h16" strokeLinecap="round" strokeLinejoin="round" /></svg>
        {filename || "Choose a .csv file"}
        <input type="file" accept=".csv,text/csv" onChange={pick} className="hidden" />
      </label>

      {err && <div className="mt-3 font-mono text-[11px] text-crimson">{err}</div>}

      {result && (
        <div className="mt-4 rounded-card border border-line bg-base/40 p-3">
          <div className="flex flex-wrap gap-2 text-[12px]">
            <ReasonChip tone="jade">{result.created} created</ReasonChip>
            <ReasonChip tone="ochre">{result.updated} updated</ReasonChip>
            {result.rows_failed > 0 && <ReasonChip tone="crimson">{result.rows_failed} failed</ReasonChip>}
          </div>
          {result.errors.length > 0 && (
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto font-mono text-[11px] text-crimson">
              {result.errors.map((e, i) => (
                <li key={i}>line {e.line}{e.sku ? ` (${e.sku})` : ""}: {e.error}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mt-5 flex gap-2.5">
        <Button variant="primary" onClick={submit} disabled={busy || !text}>{busy ? "Importing…" : "Import"}</Button>
        <Button variant="ghost" onClick={onClose}>{result ? "Done" : "Cancel"}</Button>
      </div>
    </Modal>
  );
}
