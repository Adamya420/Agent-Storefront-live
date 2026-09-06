"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import clsx from "clsx";
import { ROLES, useRole, type Role } from "@/lib/roles";
import { useTheme } from "./theme";

export function RoleSwitcher() {
  const { role, def } = useRole();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-ink/5">
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13px] font-semibold">{def.label}</span>
          <span className="block truncate text-[11px] text-ink3">{def.sub}</span>
        </span>
        <svg viewBox="0 0 24 24" className={clsx("h-3.5 w-3.5 shrink-0 text-ink3 transition-transform duration-150", open && "rotate-180")} fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="animate-fade-in absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-md border border-line bg-surface shadow-flyout">
            {(Object.keys(ROLES) as Role[]).map((r) => {
              const rd = ROLES[r];
              return (
                <button key={r} onClick={() => { setOpen(false); router.push(rd.home); }}
                  className={clsx("flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors",
                    r === role ? "bg-accent-bg text-ink font-medium" : "text-ink2 hover:bg-ink/5")}>
                  <span className={clsx("h-1.5 w-1.5 rounded-full", r === role ? "bg-accent" : "bg-transparent")} />
                  <span className="flex-1">{rd.label}<span className="ml-1.5 text-[11px] text-ink3">{rd.sub}</span></span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export function Shell({ children, headerRight }: { children: React.ReactNode; headerRight?: React.ReactNode }) {
  const { def } = useRole();
  const path = usePathname();
  const { theme, toggle } = useTheme();
  const active = def.nav.find((n) => path === n.href || (n.href !== def.home && path.startsWith(n.href)));
  const title = active?.label || def.label;

  // The buyer chat owns its own full-screen layout (history sidebar + role switcher),
  // so it doesn't get the merchant/rail nav shell — otherwise there'd be two sidebars.
  if (def.key === "buyer") {
    return <div className="h-screen overflow-hidden">{children}</div>;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex h-full w-[208px] shrink-0 flex-col border-r border-line px-3.5 py-4">
        <RoleSwitcher />
        <nav className="mt-6 flex flex-1 flex-col gap-0.5 overflow-y-auto">
          {def.nav.map((it) => {
            const on = path === it.href || (it.href !== def.home && path.startsWith(it.href));
            return (
              <Link key={it.href} href={it.href}
                className={clsx("border-l-2 px-2.5 py-[7px] text-[13px] transition-colors duration-150",
                  on ? "border-accent bg-gradient-to-r from-accent-bg to-transparent font-semibold text-ink" : "border-transparent text-ink3 hover:text-ink2")}>
                {it.label}
              </Link>
            );
          })}
        </nav>
        <button onClick={toggle} className="mt-4 flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] text-ink3 transition-colors hover:bg-ink/5 hover:text-ink2">
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
            {theme === "light" ? <path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z" strokeLinejoin="round" /> : <circle cx="12" cy="12" r="4" />}
          </svg>
          {theme === "light" ? "Dark theme" : "Light theme"}
        </button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center border-b border-line px-8">
          <h1 className="text-[19px] font-bold tracking-tight text-ink">{title}</h1>
          <div className="ml-auto flex items-center gap-3">
            {headerRight}
            <span className="mono flex items-center gap-1.5 text-[11px] text-ink3">
              <span className="h-1.5 w-1.5 rounded-full bg-crimson" />Razorpay Test
            </span>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto px-8 py-8">{children}</main>
      </div>
    </div>
  );
}
