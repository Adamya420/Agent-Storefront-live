"use client";
import { SectionHeader, ReasonChip, Loading, ErrorState, EmptyState, Pagination } from "@/components/ui";
import { useData } from "@/lib/useData";
import { usePagination } from "@/lib/pagination";
import * as api from "@/lib/api";

// Honest and lean: we don't warehouse raw webhook payloads. Settlement is confirmed
// by signature-verified webhook OR reconciling poll (they converge on one capture).
// Each converted session is one confirmed settlement event.
export default function WebhooksPage() {
  const { data, loading, error, reload } = useData(() => api.getSessions("CONVERTED"));
  const { page, setPage, totalPages, pageItems, from, to, total } = usePagination(data?.sessions ?? [], 25);
  // The console calls the backend same-origin via the /api proxy (next.config.js
  // rewrites /api/* to the server-side ACG_API_BASE). There is no public base URL
  // to read at runtime, so show the proxy path rather than a misleading fallback.
  const base = "/api → server-configured (ACG_API_BASE)";

  return (
    <div className="flex flex-col gap-11">
      <div>
        <SectionHeader>Endpoint</SectionHeader>
        <div className="stagger mt-3 flex flex-col">
          <div className="flex flex-col gap-1 border-b border-line py-3 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-[13px] text-ink2">Route</span>
            <span className="mono text-[13px] text-ink">POST /webhooks/razorpay</span>
          </div>
          <div className="flex flex-col items-start gap-1 border-b border-line py-3 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-[13px] text-ink2">Signature</span>
            <ReasonChip tone="jade">HMAC verified</ReasonChip>
          </div>
          <div className="flex flex-col gap-1 border-b border-line py-3 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-[13px] text-ink2">Reconciliation</span>
            <span className="text-[13px] text-ink3 sm:text-right">webhook + poll converge on one capture (idempotent)</span>
          </div>
          <div className="flex flex-col gap-1 py-3 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-[13px] text-ink2">Base</span>
            <span className="mono text-2xs text-ink3 sm:text-right">{base}</span>
          </div>
        </div>
      </div>

      <div>
        <SectionHeader hint="signature-verified captures">Settlement events</SectionHeader>
        <div className="mt-3">
          {loading ? <Loading rows={4} /> : error ? <ErrorState error={error} retry={reload} /> :
            !data || data.sessions.length === 0 ? <EmptyState title="No settlement events yet" hint="Captured payments appear here once a link is paid." /> : (
              <>
                <div className="stagger flex flex-col">
                  {pageItems.map((s) => (
                    <div key={s.session_id} className="flex items-center gap-3 border-b border-line py-3 text-[13px] transition-colors duration-150 hover:bg-ink/[0.025]">
                      <span className="h-2 w-2 shrink-0 rounded-full bg-jade" />
                      <span className="mono shrink-0 text-xs text-ink">payment.captured</span>
                      <span className="mono truncate text-2xs text-ink3">{s.session_id.slice(0, 12)}…</span>
                      <span className="mono ml-auto shrink-0 tnum text-ink">{api.paise(s.total_paise)}</span>
                    </div>
                  ))}
                </div>
                <Pagination page={page} totalPages={totalPages} onPageChange={setPage} from={from} to={to} total={total} />
              </>
            )}
        </div>
      </div>
    </div>
  );
}
