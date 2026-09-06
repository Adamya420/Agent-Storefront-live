"use client";
import { RoleProvider } from "@/lib/roles";
import { Shell } from "@/components/shell";
import { ConsoleGate } from "@/components/console-gate";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <RoleProvider>
      <ConsoleGate>
        <Shell>{children}</Shell>
      </ConsoleGate>
    </RoleProvider>
  );
}
