// Typed client for the ACG console API. Every type mirrors a real backend
// response shape (backend/console/routes.py). The client talks to same-origin
// /api/* which next.config.js proxies to the FastAPI backend.
//
// This file is a standalone copy (not an import) of the original console's
// lib/api.ts — the backend contract doesn't change with the redesign, so the
// typed client is reused verbatim rather than reinvented.

const BASE = "/api";

// Demo console key (merchant/rail). Stored in the browser session after unlock and
// sent as a header; the value never lives in the shipped code — the backend holds it.
export const CONSOLE_KEY_STORAGE = "asf_console_key";
export function getConsoleKey(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(CONSOLE_KEY_STORAGE) || "";
}
export function setConsoleKey(v: string) {
  if (typeof window !== "undefined") window.sessionStorage.setItem(CONSOLE_KEY_STORAGE, v);
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const key = getConsoleKey();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(key ? { "X-Console-Key": key } : {}),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText, path);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, public body: string, public path: string) {
    super(`${status} on ${path}`);
  }
}

export const paise = (p?: number | null) => {
  if (p == null) return "—";
  const sign = p < 0 ? "-" : "";
  const abs = Math.abs(p) / 100;
  return `${sign}₹${abs.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
export const pct = (bps?: number | null) => (bps == null ? "—" : `${(bps / 100).toFixed(2)}%`);

// ---- MERCHANT: overview ----
export interface Overview {
  sessions: number; converted: number; abandoned: number; denied: number;
  recovered_sales: number; recovered_revenue_paise: number; gross_converted_paise: number;
  conversion_rate_bps: number; baseline_converted: number; engine_delta: number;
  lever_mix: Record<string, number>; deny_reasons: Record<string, number>;
}
export const getOverview = () => req<Overview>("/console/overview");

// ---- RAIL: sessions + gate log ----
export interface SessionRow {
  session_id: string; status: string; agent_type: string; lever: string;
  total_paise: number; outcome_reason: string | null; margin_bps: number | null;
  started_at: string | null;
}
export const getSessions = (status?: string) =>
  req<{ sessions: SessionRow[]; count: number }>(`/console/sessions${status ? `?status=${status}` : ""}`);

export interface SessionDetail {
  session_id: string; status: string; agent_type: string; outcome_reason: string | null;
  offer: null | { base_sku: string; lever: string; total_paise: number | null; margin_bps: number;
    within_bounds: boolean; added_cost_paise: number; transformation: Record<string, unknown> };
  gate_events: { seq: number; type: string; decision: string; reason_code: string | null }[];
  receipt: null | { chain: Record<string, unknown>; chain_head_hash: string };
}
export const getSession = (id: string) => req<SessionDetail>(`/console/sessions/${id}`);

export interface GateLog {
  denies: { session_id: string; type: string; reason_code: string; ts: string | null }[];
  count: number; by_reason: Record<string, number>;
}
export const getGateLog = () => req<GateLog>("/console/gate-log");

// ---- MERCHANT: audit trail ----
export interface TrailStep {
  seq: number; action: string; actor: string; detail: Record<string, unknown>;
  hash: string; prev_hash: string;
}
export interface AuditTrail {
  session_id: string; chain_verified: boolean; first_bad_seq: number | null;
  ledger_head_hash: string; receipt_present: boolean; receipt_verified: boolean;
  receipt_head_hash: string | null; steps: TrailStep[];
}
export const getAuditTrail = (id: string) => req<AuditTrail>(`/console/audit-trail/${id}`);

// ---- MERCHANT: catalog + onboarding + envelope ----
export interface Product {
  sku: string; title: string; category: string; list_price_paise: number; cost_paise: number;
  stock: number; return_days: number; shipping_days: number;
  effective_price_paise: number | null; promo_code: string | null;
}
export const getCatalog = () => req<{ products: Product[]; count: number }>("/console/catalog");
export const onboardProduct = (p: NewProduct) =>
  req<{ ok: boolean; sku: string }>("/console/catalog", { method: "POST", body: JSON.stringify(p) });

export interface NewProduct {
  sku: string; title: string; category: string; list_price_paise: number; cost_paise: number;
  stock?: number; return_days: number; shipping_days: number;
  effective_price_paise?: number | null; promo_code?: string | null; description?: string;
}

export interface EnvelopeEntry {
  label: string; intent: { max_price_paise: number; min_return_days: number; max_delivery_days: number };
  lever: string; reachable: boolean; total_paise: number | null; return_days: number | null;
  delivery_days: number | null; margin_bps: number | null; cost_paise: number | null; reason: string;
}
export const getEnvelope = (sku: string) =>
  req<{ sku: string; envelope: EnvelopeEntry[] }>(`/console/catalog/${sku}/envelope`);

// ---- MERCHANT: policy ----
export interface Policy {
  margin_floor_bps: number; discount_budget_bps: number; return_band_min_days: number;
  return_band_max_days: number; shipping_upgrade_allowed: boolean;
  shipping_upgrade_max_cost_paise: number; bundle_enabled: boolean;
  bundle_max_addon_categories: string[]; allow_promo_stacking: boolean;
}
export const getPolicy = () => req<Policy>("/console/policy");
export const putPolicy = (patch: Partial<Policy>) =>
  req<{ ok: boolean; version: number }>("/console/policy", { method: "PUT", body: JSON.stringify(patch) });

// ---- BUYER lens (case 3): extract -> confirm -> run -> poll ----
export interface Intent {
  category: string; max_price_paise: number; min_return_days: number;
  max_delivery_days: number; quantity: number; tolerate: string[];
}
export interface ExtractResult { intent: Intent; narration: string; source: string; confirm_required: boolean; }
export const buyerExtract = (text: string) =>
  req<ExtractResult>("/console/buyer/chat/extract", { method: "POST", body: JSON.stringify({ text }) });

export interface Turn {
  kind: "search" | "offer" | "reasoning" | "decision" | "no_offer" | "blocked" | "settlement" | "receipt" | "info";
  role: "agent"; text: string;
  offer?: { base_sku: string; lever_type: string; total_paise: number; return_days: number; shipping_days: number; unit_price_paise: number };
  decision?: string; reason?: string; status?: string; payment_link?: string; payment_id?: string;
}
export interface RunResult { session_id: string; turns: Turn[]; next: "poll" | "done"; payment_link?: string; }
export const buyerRun = (intent: Intent) =>
  req<RunResult>("/console/buyer/chat/run", { method: "POST", body: JSON.stringify({ intent }) });

export interface PollResult { status: string; turns: Turn[]; next: "poll" | "done"; }
export const buyerPoll = (session_id: string) =>
  req<PollResult>("/console/buyer/chat/poll", { method: "POST", body: JSON.stringify({ session_id }) });

// ---- analytics (merchant insights) ----
export interface Analytics {
  funnel: { sessions: number; offered: number; converted: number; recovered: number };
  lever_effectiveness: { lever: string; count: number; revenue_paise: number; avg_margin_bps: number }[];
  margin_histogram: { floor_bps: number; count: number }[];
  recovered_over_time: { ts: string; cumulative_recovered_paise: number; amount_paise: number; recovered: boolean }[];
}
export const getAnalytics = () => req<Analytics>("/console/analytics");
export const reconcileSessions = () => req<{ reconciled: number; converted: number }>("/console/sessions/reconcile", { method: "POST" });

// merge-aware extract (conversational edit)
export const buyerExtractMerge = (text: string, prior?: Intent) =>
  req<ExtractResult>("/console/buyer/chat/extract", { method: "POST", body: JSON.stringify({ text, prior }) });

// draft-band envelope preview (live slider feedback)
export interface BandPreview { margin_floor_bps: number; discount_budget_bps: number; return_band_max_days: number; shipping_upgrade_max_cost_paise: number; bundle_enabled?: boolean; sku?: string | null; }
export const previewBand = (b: BandPreview) =>
  req<{ sku: string | null; envelope: EnvelopeEntry[] }>("/console/policy/preview", { method: "POST", body: JSON.stringify(b) });

// ---- bulk CSV catalogue import ----
export interface CsvImportResult { created: number; updated: number; rows_ok: number; rows_failed: number; errors: { line: number; sku: string; error: string }[]; }
export const importCatalogCsv = (csv: string) =>
  req<CsvImportResult>("/console/catalog/import", { method: "POST", body: JSON.stringify({ csv }) });
export const getImportTemplate = () =>
  req<{ filename: string; content: string }>("/console/catalog/import/template");

export const authCheck = () => req<{ ok: boolean }>("/console/auth/check");
