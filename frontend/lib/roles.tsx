"use client";
import { createContext, useContext, useMemo } from "react";
import { usePathname } from "next/navigation";

export type Role = "merchant" | "rail" | "buyer";

export interface NavItem { href: string; label: string; }
export interface RoleDef { key: Role; label: string; sub: string; home: string; nav: NavItem[]; }

// The redesign drops nav icons deliberately (Linear-restraint direction) —
// a left accent bar + weight/color carries the active state instead.
export const ROLES: Record<Role, RoleDef> = {
  merchant: {
    key: "merchant", label: "Merchant", sub: "ACG Sports", home: "/merchant/home",
    nav: [
      { href: "/merchant/home", label: "Overview" },
      { href: "/merchant/analytics", label: "Analytics" },
      { href: "/merchant/products", label: "Products" },
      { href: "/merchant/offers", label: "Offers" },
      { href: "/merchant/transactions", label: "Transactions" },
      { href: "/merchant/disputes", label: "Disputes" },
      { href: "/merchant/reports", label: "Reports" },
      { href: "/merchant/settings", label: "Settings" },
    ],
  },
  rail: {
    key: "rail", label: "Rail", sub: "Razorpay side", home: "/rail/overview",
    nav: [
      { href: "/rail/overview", label: "Overview" },
      { href: "/rail/sessions", label: "Sessions" },
      { href: "/rail/gate-log", label: "Gate log" },
      { href: "/rail/webhooks", label: "Webhooks & events" },
      { href: "/rail/provenance", label: "Provenance" },
    ],
  },
  buyer: { key: "buyer", label: "Buyer agent", sub: "stand-in", home: "/buyer", nav: [] },
};

const RoleCtx = createContext<{ role: Role; def: RoleDef }>({ role: "merchant", def: ROLES.merchant });

export function useRole() { return useContext(RoleCtx); }

export function roleFromPath(path: string): Role {
  if (path.startsWith("/rail")) return "rail";
  if (path.startsWith("/buyer")) return "buyer";
  return "merchant";
}

export function RoleProvider({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const role = roleFromPath(path);
  const value = useMemo(() => ({ role, def: ROLES[role] }), [role]);
  return <RoleCtx.Provider value={value}>{children}</RoleCtx.Provider>;
}
