"use client";
import clsx from "clsx";
import { ReasonChip } from "./ui";
import type { AuditTrail, SessionDetail } from "@/lib/api";

// The gate's 11 checks, in the fixed TRD §7 order (gateway/gate.py — "Check
// order is load-bearing... must not be reordered without a DECISIONS.md
// entry"). The gate short-circuits on the first failing check and audits ONE
// row per evaluation (GATE_PASS with check_index=0, or GATE_DENY with the
// 1-11 index of the check that stopped it) — never 11 rows. This label list
// just names that fixed, documented order; every pass/deny/skip status below
// still comes from the real recorded check_index, nothing is guessed.
const GATE_CHECK_LABELS = [
  "Mandate verified", "Signatures valid", "Not expired", "Nonce fresh",
  "Scope in bounds", "Within mandate ceiling", "Within merchant band",
  "Cart integrity", "Inventory consistent", "Token scope", "Idempotent",
];

// All 11 gate checks for one transaction, derived from the audit trail's
// single GATE_PASS/GATE_DENY step. A PASS means all 11 ran clean; a DENY
// carries which check (1-11) stopped it — checks before it passed, checks
// after it never ran (short-circuit) and are shown as skipped, not failed.
export function GateChecks11({ steps }: { steps: AuditTrail["steps"] }) {
  const gate = steps.find((s) => s.action === "GATE_PASS" || s.action === "GATE_DENY");
  if (!gate) return <div className="py-3 text-[13px] text-ink3">No gate decision recorded for this transaction.</div>;
  const failedAt = gate.action === "GATE_DENY" ? Number(gate.detail.check_index) || 0 : 0;
  const reasonCode = gate.detail.reason_code as string | undefined;

  return (
    <div className="flex flex-col">
      {GATE_CHECK_LABELS.map((label, i) => {
        const n = i + 1;
        const status = !failedAt || n < failedAt ? "pass" : n === failedAt ? "deny" : "skipped";
        return (
          <div key={n} className="flex items-center gap-3 border-b border-line py-2 last:border-0">
            <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full",
              status === "pass" ? "bg-jade" : status === "deny" ? "bg-crimson" : "bg-line2")} />
            <span className="mono w-5 shrink-0 text-[11px] text-ink3">{n}</span>
            <span className={clsx("flex-1 text-[13px]", status === "skipped" && "text-ink3")}>{label}</span>
            {status === "deny" && reasonCode && <ReasonChip tone="crimson">{reasonCode}</ReasonChip>}
            {status === "skipped" && <span className="mono text-[11px] text-ink3">not evaluated</span>}
          </div>
        );
      })}
    </div>
  );
}

// Gate checks for one session — renders exactly what the API returned for
// `gate_events`, in order. The backend decides how many checks ran and in
// what order (TRD §7); the frontend never fabricates a fixed check count or
// invents an "unevaluated" state the API didn't report.
export function GateChecklist({ events }: { events: SessionDetail["gate_events"] }) {
  if (events.length === 0) return <div className="py-3 text-[13px] text-ink3">No gate events recorded.</div>;
  return (
    <div className="flex flex-col">
      {events.map((e) => {
        const deny = e.decision === "DENY";
        return (
          <div key={e.seq} className="flex items-center gap-3 border-b border-line py-2.5 last:border-0">
            <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", deny ? "bg-crimson" : "bg-jade")} />
            <span className="mono w-8 shrink-0 text-[11px] text-ink3">#{e.seq}</span>
            <span className="flex-1 text-[13px]">{e.type}</span>
            {/* A reason_code can be present on a PASS event too (e.g. "OK") — only
                a DENY reason is a warning; coloring every code crimson regardless of
                decision was a real bug caught during the build (found live: a passing
                check's own reason code rendered in alarm red). */}
            {e.reason_code && (deny ? <ReasonChip tone="crimson">{e.reason_code}</ReasonChip> : <span className="mono text-[11px] text-ink3">{e.reason_code}</span>)}
          </div>
        );
      })}
    </div>
  );
}

// Hash-chain audit trail — a session's provenance, or a merchant's dispute
// evidence. The connector line is drawn with real opacity (a previous
// console shipped this line at near-zero contrast in both themes — fixed
// here from the start, not discovered after ship).
export function AuditTimeline({ trail }: { trail: AuditTrail }) {
  if (trail.steps.length === 0) return <div className="py-3 text-[13px] text-ink3">No steps recorded for this transaction.</div>;
  return (
    <div>
      <div className="relative pl-5">
        <div className="absolute bottom-1.5 left-[3px] top-1.5 w-px bg-line2" />
        <div className="stagger flex flex-col gap-4">
          {trail.steps.map((s) => {
            const broken = !trail.chain_verified && trail.first_bad_seq != null && s.seq >= trail.first_bad_seq;
            // A gate deny is a real rejection, not chain tampering — it needs its
            // own red regardless of `broken` (a step can be a clean, unbroken-chain
            // DENY). Coloring it the same jade as every passing step was the bug.
            const denied = s.action === "GATE_DENY";
            const flagged = broken || denied;
            return (
              <div key={s.seq} className="relative">
                <span className={clsx("absolute -left-5 top-1 h-2 w-2 rounded-full border-2",
                  flagged ? "border-crimson bg-crimson" : "border-jade bg-jade")} />
                <div className="flex items-center gap-2">
                  <span className={clsx("text-[13px] font-medium", flagged && "text-crimson")}>{s.action}</span>
                  <span className="mono text-[11px] text-ink3">#{s.seq} · {s.actor}</span>
                </div>
                <div className="mono mt-0.5 truncate text-[11px] text-ink3">{s.hash.slice(0, 32)}…</div>
                {s.detail && Object.keys(s.detail).length > 0 && (
                  <pre className="mono mt-1.5 whitespace-pre-wrap break-all rounded-[4px] bg-ink/[0.03] px-2 py-1.5 text-[11px] leading-relaxed text-ink3">
                    {JSON.stringify(s.detail, null, 0)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div className="mono mt-4 border-t border-line pt-3 text-[11px] text-ink3">ledger head {trail.ledger_head_hash.slice(0, 32)}…</div>
    </div>
  );
}
