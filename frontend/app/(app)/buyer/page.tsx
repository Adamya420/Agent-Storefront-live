"use client";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Button, leverLabel } from "@/components/ui";
import * as api from "@/lib/api";
import { RoleSwitcher } from "@/components/shell";

type Msg = { role: "user" | "agent"; text: string };
type Phase = "chatting" | "confirm" | "running" | "done";
interface Thread {
  id: string; title: string; msgs: Msg[]; intent: api.Intent | null;
  phase: Phase; turns: api.Turn[]; sessionId: string | null; link: string | null;
}

const newThread = (): Thread => ({
  id: Math.random().toString(36).slice(2), title: "New shopping thread",
  msgs: [{ role: "agent", text: "Tell me what you're shopping for — I'll turn it into a signed budget you approve before anything is authorized." }],
  intent: null, phase: "chatting", turns: [], sessionId: null, link: null,
});

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function BuyerChat() {
  const [threads, setThreads] = useState<Thread[]>([newThread()]);
  const [activeId, setActiveId] = useState(threads[0].id);
  const active = threads.find((t) => t.id === activeId)!;
  const patch = (id: string, p: Partial<Thread>) => setThreads((ts) => ts.map((t) => (t.id === id ? { ...t, ...p } : t)));

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line">
        <div className="border-b border-line p-3">
          <RoleSwitcher />
        </div>
        <div className="p-3">
          <button
            onClick={() => { const t = newThread(); setThreads((x) => [t, ...x]); setActiveId(t.id); }}
            className="flex w-full items-center gap-2 rounded-[6px] border border-line2 px-3 py-2 text-[13px] font-medium text-ink transition-colors duration-150 hover:bg-ink/5"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
            New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-1.5 pb-3">
          {threads.map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveId(t.id)}
              title={t.title}
              className={clsx(
                "mb-0.5 flex w-full items-center gap-2 border-l-2 px-2.5 py-2 text-left text-[13px] transition-colors duration-150",
                t.id === activeId
                  ? "border-accent bg-gradient-to-r from-accent-bg to-transparent font-semibold text-ink"
                  : "border-transparent text-ink3 hover:bg-ink/5 hover:text-ink2"
              )}
            >
              <span className={clsx(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                t.phase === "done" ? "bg-jade" : t.phase === "running" ? "bg-ochre animate-pulse" : "bg-ink3"
              )} />
              <span className="truncate">{t.title}</span>
            </button>
          ))}
        </div>
      </aside>
      <ChatPane key={active.id} thread={active} patch={(p) => patch(active.id, p)} />
    </div>
  );
}

// Constraint-based prompts (the extractor parses price + return window, not product
// names), tuned against data/demo-catalog.csv so each reliably drives a lever that
// SETTLES: plain = as-is, an 18-day return forces RETURN_EXTENSION, a tight ceiling
// forces DISCOUNT. (Bundle is intentionally not shown here — its multi-item cart can't be chat-signed.)
const SUGGESTED_PROMPTS: string[] = [
  "Running shoes under ₹5,000",
  "Running shoes under ₹5,000 with an 18-day return window",
  "Running shoes under ₹5,000, delivered in 2 days",
  "Running shoes under ₹4,000",
];

function ChatPane({ thread, patch }: { thread: Thread; patch: (p: Partial<Thread>) => void }) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  // Cancellation flag tied to this pane's lifecycle. ChatPane is keyed by thread
  // id, so switching threads (or leaving /buyer) unmounts it and stops any poll
  // loop still running — no setState on a dead instance (deep-review).
  const cancelled = useRef(false);
  useEffect(() => { cancelled.current = false; return () => { cancelled.current = true; }; }, []);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [thread.msgs, thread.turns]);

  // In-flight is derived from SHARED thread state (phase), not just local `busy`,
  // so a remount (thread switch and back) can't reset the guard and let a second
  // signed run start while the first is still going (deep-review).
  const inFlight = busy || thread.phase === "running";

  async function send() {
    const text = input.trim();
    if (!text || inFlight) return;
    setInput("");
    const msgs = [...thread.msgs, { role: "user" as const, text }];
    const title = thread.intent ? thread.title : text.slice(0, 40);
    patch({ msgs, title });
    setBusy(true);
    try {
      // merge-aware: pass the standing intent so edits refine it instead of resetting
      const r = await api.buyerExtractMerge(text, thread.intent ?? undefined);
      patch({ msgs: [...msgs, { role: "agent", text: r.narration }], intent: r.intent, phase: "confirm" });
    } catch (e) {
      patch({ msgs: [...msgs, { role: "agent", text: "I couldn't parse that — try naming a product and a budget." }] });
    } finally { setBusy(false); }
  }

  async function confirmSign() {
    if (!thread.intent || inFlight) return;
    setBusy(true);
    patch({ phase: "running", msgs: [...thread.msgs, { role: "agent", text: "Signing the mandate and opening a checkout on the ACG rail…" }] });
    const MAX_POLLS = 60;  // ~72s at 1.2s/iter — then stop rather than poll forever
    let turns: api.Turn[] = [];
    try {
      const run = await api.buyerRun(thread.intent);
      for (const t of run.turns) {
        if (cancelled.current) return;
        turns = [...turns, t]; patch({ turns: [...turns], sessionId: run.session_id, link: run.payment_link ?? null }); await sleep(450);
      }
      let next = run.next;
      let polls = 0;
      while (next === "poll" && polls < MAX_POLLS) {
        if (cancelled.current) return;
        await sleep(1200);
        polls += 1;
        const p = await api.buyerPoll(run.session_id);
        for (const t of p.turns) {
          if (cancelled.current) return;
          turns = [...turns, t]; patch({ turns: [...turns] }); await sleep(450);
        }
        next = p.next;
        if (next === "done") break;
      }
      if (cancelled.current) return;
      if (next === "poll") {
        // hit the cap without settling — tell the user rather than spinning silently
        turns = [...turns, { kind: "info", role: "agent", text: "Still awaiting payment. You can leave this open — the transaction will reconcile once the link is paid." } as api.Turn];
        patch({ turns: [...turns], phase: "done" });
      } else {
        patch({ phase: "done" });
      }
    } catch (e) {
      if (cancelled.current) return;
      // append to the LOCAL accumulator, never the stale `thread.turns` prop, so a
      // late failure can't erase turns (incl. a live payment link) already shown.
      patch({ turns: [...turns, { kind: "blocked", role: "agent", text: "The run failed to reach settlement. Check the backend is live." } as api.Turn], phase: "done" });
    } finally { setBusy(false); }
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div ref={scroller} className="flex-1 overflow-y-auto px-6 py-6 sm:px-10 sm:py-8">
        <div className="mx-auto flex max-w-2xl flex-col gap-5">
          {thread.msgs.map((m, i) => <LogMsg key={i} role={m.role} text={m.text} />)}

          {thread.phase === "confirm" && thread.intent && (
            <ConfirmBlock intent={thread.intent} onSign={confirmSign} busy={inFlight} />
          )}

          {thread.turns.length > 0 && (
            <div className="flex flex-col gap-3.5 border-t border-line pt-4">
              {thread.turns.map((t, i) => <TurnRow key={i} turn={t} />)}
            </div>
          )}

          {thread.phase === "done" && (
            <div className="pt-2 text-center text-2xs text-ink3">Thread complete · everything above happened on the tested ACP rail.</div>
          )}
        </div>
      </div>

      <div className="border-t border-line px-6 py-4 sm:px-10">
        {thread.msgs.length <= 1 && thread.turns.length === 0 && (
          <div className="mx-auto mb-3 max-w-2xl">
            <div className="mb-1.5 text-2xs text-ink3">Try one — each shows a different move the offer engine can make:</div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_PROMPTS.map((p) => (
                <button key={p} onClick={() => setInput(p)}
                  className="rounded-pill border border-line2 bg-surface px-3 py-1.5 text-2xs text-ink2 transition-colors hover:border-accent hover:text-ink">
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={1}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder={thread.phase === "confirm" ? "Refine it (e.g. “no, min return should be 5 days”) or approve above" : "What are you shopping for?"}
            className="max-h-32 flex-1 resize-none rounded-[6px] border border-line2 bg-bg px-3.5 py-2.5 text-[13px] text-ink placeholder:text-ink3 transition-colors duration-150 focus:border-accent focus:outline-none"
          />
          <button
            onClick={send}
            disabled={inFlight || !input.trim()}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-[6px] bg-accent text-white transition-[filter,opacity] duration-150 hover:brightness-110 disabled:opacity-30"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}

function LogMsg({ role, text }: { role: "user" | "agent"; text: string }) {
  if (role === "user") {
    return <div className="animate-fade-up border-l-2 border-line2 pl-3.5 text-[14px] text-ink2">{text}</div>;
  }
  return <div className="animate-fade-up text-[14px] text-ink">{text}</div>;
}

function ConfirmBlock({ intent, onSign, busy }: { intent: api.Intent; onSign: () => void; busy: boolean }) {
  const rows: [string, string][] = [
    ["Category", intent.category.replace(/_/g, " ")],
    ["Budget ceiling", api.paise(intent.max_price_paise)],
    ["Min return window", `${intent.min_return_days} days`],
    ["Max delivery", `${intent.max_delivery_days} days`],
    ["Quantity", String(intent.quantity)],
    ["Tolerates", intent.tolerate.map(leverLabel).join(", ") || "—"],
  ];
  return (
    <div className="animate-fade-up">
      <div className="mb-1 text-[13px] font-semibold text-ink">Signed budget</div>
      <div className="flex flex-col">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between border-b border-line py-2">
            <span className="text-[13px] text-ink3">{k}</span>
            <span className="mono text-[13px] text-ink">{v}</span>
          </div>
        ))}
      </div>
      <p className="mt-2.5 text-2xs text-ink3">Nothing is signed until you approve. The agent can only accept an offer inside these bounds; anything outside is refused at the gate.</p>
      <Button variant="primary" onClick={onSign} disabled={busy} className="mt-3">
        {busy ? "Signing…" : "Confirm & sign mandate"}
      </Button>
    </div>
  );
}

const TURN_TITLE: Record<api.Turn["kind"], string> = {
  search: "Searching", offer: "Offer found", reasoning: "Reasoning",
  decision: "Gate decision", no_offer: "No offer", blocked: "Blocked",
  settlement: "Settlement", receipt: "Settled", info: "Info",
};

const TURN_DOT: Record<api.Turn["kind"], string> = {
  search: "bg-ink3", offer: "bg-jade", reasoning: "bg-ink3",
  decision: "bg-ink3", no_offer: "bg-ochre", blocked: "bg-crimson",
  settlement: "bg-jade", receipt: "bg-jade", info: "bg-ink3",
};

function TurnRow({ turn }: { turn: api.Turn }) {
  const isOffer = turn.kind === "offer" && !!turn.offer;
  const lever = isOffer ? turn.offer!.lever_type : "";
  const isRecovery = isOffer && lever !== "NONE" && lever !== "";  // engine made a concession
  const meta = isOffer
    ? `${leverLabel(lever)} · ${turn.offer!.base_sku}`
    : [turn.decision, turn.reason, turn.status, turn.payment_id].filter(Boolean).join(" · ");

  // A lever offer is the product's headline moment — render it as a distinct
  // "offer engine countered" card so the recovery is unmistakable, not a quiet line.
  if (isRecovery) {
    return (
      <div className="animate-fade-up rounded-card border border-jade/40 bg-jade/[0.06] p-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-chip bg-jade/15 px-2 py-0.5 text-2xs font-semibold text-jade">Offer engine countered</span>
          <span className="rounded-chip border border-jade/40 px-2 py-0.5 mono text-2xs text-jade">{leverLabel(lever)}</span>
          <span className="mono text-2xs text-ink3">{turn.offer!.base_sku}</span>
        </div>
        <div className="mt-2.5 flex gap-6">
          {([["Total", api.paise(turn.offer!.total_paise)], ["Return", `${turn.offer!.return_days}d`], ["Ship", `${turn.offer!.shipping_days}d`]] as [string, string][]).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5">
              <span className="text-2xs text-ink3">{k}</span>
              <span className="mono text-[13px] tnum text-ink">{v}</span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[13px] text-ink2">{turn.text}</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-up flex items-start gap-2.5">
      <span className={clsx("mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full", TURN_DOT[turn.kind])} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[13px] font-medium text-ink">{TURN_TITLE[turn.kind]}</span>
          {meta && <span className="mono text-2xs text-ink3">{meta}</span>}
        </div>
        {isOffer && (
          <div className="mt-1.5 flex gap-6">
            {([["Total", api.paise(turn.offer!.total_paise)], ["Return", `${turn.offer!.return_days}d`], ["Ship", `${turn.offer!.shipping_days}d`]] as [string, string][]).map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-1.5">
                <span className="text-2xs text-ink3">{k}</span>
                <span className="mono text-[13px] tnum text-ink">{v}</span>
              </div>
            ))}
          </div>
        )}
        <p className="mt-1 text-[13px] text-ink2">
          {turn.text}
          {turn.payment_link && <a href={turn.payment_link} target="_blank" rel="noreferrer" className="ml-1 text-accent underline">open link</a>}
        </p>
      </div>
    </div>
  );
}
