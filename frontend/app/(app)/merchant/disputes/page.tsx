"use client";
import { useState } from "react";
import { SectionHeader, Button, ReasonChip } from "@/components/ui";
import { AuditTimeline, GateChecks11 } from "@/components/audit";
import * as api from "@/lib/api";

// Request-and-reveal: a merchant doesn't sit on the buyer's provenance. When a
// dispute/complaint is raised, they request the trail for that transaction and the
// verified, tamper-evident chain is reconstructed — merchant-side record only.
export default function DisputesPage() {
  const [id, setId] = useState("");
  const [trail, setTrail] = useState<api.AuditTrail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function request() {
    const sid = id.trim();
    if (!sid) return;
    setBusy(true); setErr(null); setTrail(null);
    try { setTrail(await api.getAuditTrail(sid)); }
    catch (e) { setErr(e instanceof api.ApiError ? (e.status === 404 ? "No such transaction." : e.body) : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-8">
      <p className="text-[13px] text-ink2">
        Raise a dispute against a transaction to retrieve its tamper-evident audit trail — every step from
        mandate to settlement, with the gate decision that authorized it. The buyer&apos;s private reasoning is never included.
      </p>

      <div>
        <SectionHeader>Request audit trail</SectionHeader>
        <div className="mt-4 flex gap-2">
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && request()}
            placeholder="Transaction / session id"
            className="mono flex-1 rounded-[6px] border border-line2 bg-bg px-3 py-2 text-[13px] text-ink placeholder:text-ink3 outline-none transition-colors focus:border-accent"
          />
          <Button variant="primary" onClick={request} disabled={busy}>{busy ? "Retrieving…" : "Request trail"}</Button>
        </div>
        {err && <div className="mono mt-2 text-[11px] text-crimson">{err}</div>}
      </div>

      {trail && (
        <div>
          <SectionHeader hint={trail.session_id.slice(0, 18) + "…"}>Transaction</SectionHeader>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <ReasonChip tone={trail.chain_verified ? "jade" : "crimson"}>
              {trail.chain_verified ? "Chain verified" : `Tampered @ seq ${trail.first_bad_seq}`}
            </ReasonChip>
            {trail.receipt_present && (
              <ReasonChip tone={trail.receipt_verified ? "jade" : "crimson"}>
                {trail.receipt_verified ? "Receipt verified" : "Receipt mismatch"}
              </ReasonChip>
            )}
          </div>
          <div className="mt-6">
            <SectionHeader hint="short-circuits on first failure">Gate checks</SectionHeader>
            <div className="mt-3">
              <GateChecks11 steps={trail.steps} />
            </div>
          </div>
          <div className="mt-6">
            <SectionHeader>Audit trail</SectionHeader>
            <div className="mt-4">
              <AuditTimeline trail={trail} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
