"use client";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import * as api from "@/lib/api";

// Gates the merchant + rail console behind the shared demo password. The buyer
// chat (/buyer) is left open. The password lives only on the backend; here we just
// collect it, store it in the browser session, and probe a gated endpoint to check.
export function ConsoleGate({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const gated = path.startsWith("/merchant") || path.startsWith("/rail");
  const [status, setStatus] = useState<"checking" | "locked" | "open">("checking");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!gated) { setStatus("open"); return; }
    let alive = true;
    (async () => {
      if (!api.getConsoleKey()) { if (alive) setStatus("locked"); return; }
      try { await api.authCheck(); if (alive) setStatus("open"); }
      catch { if (alive) setStatus("locked"); }
    })();
    return () => { alive = false; };
  }, [gated, path]);

  async function unlock() {
    if (!pw.trim() || busy) return;
    setBusy(true); setErr(false);
    api.setConsoleKey(pw.trim());
    try { await api.authCheck(); setStatus("open"); }
    catch { api.setConsoleKey(""); setErr(true); }
    finally { setBusy(false); }
  }

  if (!gated || status === "open") return <>{children}</>;
  if (status === "checking") return null;

  return (
    <div className="grid min-h-screen place-items-center bg-bg px-6">
      <div className="w-full max-w-sm rounded-card border border-line bg-surface p-6 shadow-flyout">
        <div className="mb-1 text-[15px] font-semibold text-ink">Console locked</div>
        <p className="mb-4 text-[13px] text-ink2">The merchant &amp; rail console is password-protected for this demo. The buyer chat is open — try that without a password.</p>
        <input
          type="password" value={pw} autoFocus
          onChange={(e) => { setPw(e.target.value); setErr(false); }}
          onKeyDown={(e) => e.key === "Enter" && unlock()}
          placeholder="Console password"
          className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-line2"
        />
        {err && <div className="mt-2 text-[12px] text-crimson">Incorrect password.</div>}
        <button
          onClick={unlock} disabled={busy || !pw.trim()}
          className="mt-4 w-full rounded-pill bg-accent py-2 text-[13px] font-semibold text-white disabled:opacity-40"
        >{busy ? "Unlocking…" : "Unlock"}</button>
        <a href="/buyer" className="mt-3 block text-center text-[12px] text-ink3 underline">Go to the buyer chat instead →</a>
      </div>
    </div>
  );
}
