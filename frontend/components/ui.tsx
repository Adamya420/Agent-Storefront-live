"use client";
import clsx from "clsx";

/* ============================================================
   Shared primitives for the Observatory redesign.
   No boxed "metric card" pattern, no Panel-with-border-everywhere.
   Hierarchy comes from type scale, whitespace, and thin dividers.

   TABLE CONVENTION (hand-roll per page, these are the shared parts):
     <SectionHeader>Recent sessions</SectionHeader>
     <div className="grid grid-cols-[2fr_1fr_1fr_1fr] pb-2.5 text-[11px] uppercase tracking-wide text-ink3 font-semibold">
       <span>Session</span><span>Status</span><span className="text-right">Amount</span><span className="text-right">Margin</span>
     </div>
     <div className="border-t border-ink" />
     <div className="stagger">
       {rows.map(r => (
         <div key={r.id} className="grid grid-cols-[2fr_1fr_1fr_1fr] items-center border-b border-line py-3 transition-colors duration-150 hover:bg-ink/[0.025]">
           ...
         </div>
       ))}
     </div>
   Numeric columns right-aligned + .mono.tnum. Session/agent ids always .mono.
   ============================================================ */

// A real heading — bold, dark, legible at a glance. The first version of this
// used the small uppercase-tracked "eyebrow" treatment for every section
// title, which read as flat/quiet everywhere at once (a real miss: the
// Stripe reference this design synthesizes from uses bold ~15-16px headings
// for "Payments", "MRR", "New customers" — only the tiny caption ABOVE a
// hero number, like "Gross volume", is that quiet). Eyebrow style is now
// reserved for exactly that one caption role (see HeroMetric's `label`).
export function SectionHeader({ children, hint, right }: { children: React.ReactNode; hint?: string; right?: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 border-t border-line pt-3">
      <h2 className="text-[15px] font-semibold tracking-tight text-ink">{children}</h2>
      {hint && <span className="mono text-[11px] text-ink3">{hint}</span>}
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

// The editorial hero number — replaces the boxed "MetricCard" pattern.
export function HeroMetric({ label, value, delta, deltaTone = "jade", sub }: {
  label: string; value: React.ReactNode; delta?: string; deltaTone?: "jade" | "crimson" | "ink3"; sub?: React.ReactNode;
}) {
  const dtone = deltaTone === "jade" ? "text-jade bg-jade-bg" : deltaTone === "crimson" ? "text-crimson bg-crimson-bg" : "text-ink3 bg-ink/5";
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-3">
        <span className="mono text-[44px] font-semibold leading-none tracking-tight tnum sm:text-[56px]">{value}</span>
        {delta && <span className={clsx("rounded-[4px] px-2 py-0.5 text-[13px] font-semibold", dtone)}>{delta}</span>}
      </div>
      {sub && <div className="mt-2.5 max-w-[480px] text-[13px] text-ink2">{sub}</div>}
    </div>
  );
}

// Compact label/value list — replaces the 4-up boxed stat grid.
export function StatRail({ items }: { items: { label: string; value: React.ReactNode; tone?: "jade" | "crimson" | "ink" }[] }) {
  return (
    <div className="stagger flex flex-col border-t border-ink">
      {items.map((s, i) => (
        <div key={i} className="flex items-baseline justify-between border-b border-line py-3.5">
          <span className="text-[13px] text-ink2">{s.label}</span>
          <span className={clsx("mono text-[20px] font-semibold tnum",
            s.tone === "jade" ? "text-jade" : s.tone === "crimson" ? "text-crimson" : "text-ink")}>{s.value}</span>
        </div>
      ))}
    </div>
  );
}

export function StatusDot({ status }: { status: string }) {
  const tone = statusTone(status);
  const dot = tone === "jade" ? "bg-jade" : tone === "ochre" ? "bg-ochre" : tone === "crimson" ? "bg-crimson" : "bg-ink3";
  return (
    <span className="flex items-center gap-1.5 text-[13px]">
      <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", dot)} />{status}
    </span>
  );
}

export function ReasonChip({ tone = "crimson", children }: { tone?: "jade" | "ochre" | "crimson"; children: React.ReactNode }) {
  const map = {
    jade: "border-jade text-jade", ochre: "border-ochre text-ochre", crimson: "border-crimson text-crimson",
  } as const;
  return <span className={clsx("mono inline-flex items-center rounded-[4px] border px-2 py-0.5 text-[11px]", map[tone])}>{children}</span>;
}

export function Button({ variant = "primary", className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const base = "rounded-[6px] px-4 py-2 text-[13px] font-semibold transition-[filter,background-color,opacity] duration-150 disabled:opacity-40 disabled:cursor-not-allowed";
  const styles = {
    primary: "bg-accent text-white hover:brightness-110",
    secondary: "border border-line2 text-ink hover:bg-ink/5 font-medium",
    ghost: "text-ink3 hover:text-ink2 font-medium px-2",
  } as const;
  return <button className={clsx(base, styles[variant], className)} {...props} />;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5 py-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded-md bg-ink/[0.04]" style={{ animationDelay: `${i * 60}ms` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="animate-fade-in px-2 py-10 text-center">
      <div className="text-[13px] font-medium text-ink2">{title}</div>
      {hint && <div className="mt-1 text-[12px] text-ink3">{hint}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="animate-fade-in px-2 py-8 text-center">
      <div className="text-[13px] font-medium text-crimson">Couldn&apos;t reach the gateway</div>
      <div className="mono mt-1 text-2xs text-ink3">{msg}</div>
      {retry && <Button variant="secondary" onClick={retry} className="mt-3">Try again</Button>}
    </div>
  );
}

// Right-side flyout — audit trails, envelope previews, session inspection.
export function Drawer({ title, sub, onClose, children, wide }: {
  title: string; sub?: string; onClose: () => void; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="animate-fade-in absolute inset-0 bg-ink/25 backdrop-blur-[2px]" onClick={onClose} />
      <div className={clsx("animate-slide-in relative flex h-full w-full flex-col border-l border-line bg-surface shadow-flyout", wide ? "max-w-lg" : "max-w-md")}>
        <header className="flex items-center gap-3 border-b border-line px-6 py-4">
          <div><h2 className="text-[14px] font-semibold">{title}</h2>{sub && <div className="mono text-[11px] text-ink3">{sub}</div>}</div>
          <button onClick={onClose} className="ml-auto rounded-md border border-line2 px-3 py-1 text-[12px] text-ink2 transition-colors hover:bg-ink/5">Close</button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

// Centered dialog — onboarding forms, confirmations.
export function Modal({ title, sub, onClose, children }: { title: string; sub?: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-40 grid place-items-center p-6">
      <div className="animate-fade-in absolute inset-0 bg-ink/30 backdrop-blur-[2px]" onClick={onClose} />
      <div className="animate-fade-up relative w-full max-w-lg rounded-card border border-line bg-surface p-6 shadow-flyout">
        <h2 className="text-[14px] font-semibold">{title}</h2>
        {sub && <p className="mt-1 text-[12px] text-ink3">{sub}</p>}
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

// Numbered pagination — "1 2 3 … 8 9 10" with a Prev/Next pair, the
// convention on any dense list/log page. Pair with `usePagination` from
// `lib/pagination.ts`, which slices the already-fetched list client-side
// (the backend's list endpoints don't take limit/offset/cursor params).
export function Pagination({ page, totalPages, onPageChange, from, to, total }: {
  page: number; totalPages: number; onPageChange: (p: number) => void; from: number; to: number; total: number;
}) {
  if (totalPages <= 1) return null;
  const pages: (number | "…")[] = [];
  const add = (p: number) => pages.push(p);
  add(1);
  if (page > 3) pages.push("…");
  for (let p = Math.max(2, page - 1); p <= Math.min(totalPages - 1, page + 1); p++) add(p);
  if (page < totalPages - 2) pages.push("…");
  if (totalPages > 1) add(totalPages);

  const pageBtn = "grid h-7 min-w-7 place-items-center rounded-[4px] px-1.5 text-[12px] transition-colors duration-150";
  return (
    <div className="mt-4 flex items-center justify-between border-t border-line pt-3">
      <span className="mono text-[11px] text-ink3">{from}–{to} of {total}</span>
      <div className="mono flex items-center gap-1">
        <button onClick={() => onPageChange(page - 1)} disabled={page <= 1}
          className={clsx(pageBtn, "px-2 text-ink2 hover:bg-ink/5 disabled:pointer-events-none disabled:opacity-30")}>Prev</button>
        {pages.map((p, i) => p === "…"
          ? <span key={`e${i}`} className="grid h-7 min-w-7 place-items-center text-[12px] text-ink3">…</span>
          : <button key={p} onClick={() => onPageChange(p)}
              className={clsx(pageBtn, p === page ? "bg-accent text-white font-semibold" : "text-ink2 hover:bg-ink/5")}>{p}</button>
        )}
        <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}
          className={clsx(pageBtn, "px-2 text-ink2 hover:bg-ink/5 disabled:pointer-events-none disabled:opacity-30")}>Next</button>
      </div>
    </div>
  );
}

export function statusTone(status: string): "jade" | "ochre" | "crimson" | "neutral" {
  if (status === "CONVERTED" || status === "CAPTURED") return "jade";
  if (status === "ABANDONED" || status === "OPEN") return "ochre";
  if (status === "DENIED") return "crimson";
  return "neutral";
}

export function leverLabel(l: string) {
  if (!l || l === "NONE") return "as-is";
  return l.replace(/_/g, " ").toLowerCase();
}
