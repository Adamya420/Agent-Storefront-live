"use client";
import { useParams } from "next/navigation";
import { SessionDetailView } from "@/components/session-detail";

// Merchant-scoped session detail — same underlying record and rendering as
// /rail/sessions/[id], but kept under /merchant so clicking a session id from
// a merchant page never silently swaps the sidebar into the Rail role.
export default function MerchantSessionDetail() {
  const { id } = useParams<{ id: string }>();
  return <SessionDetailView id={id} />;
}
