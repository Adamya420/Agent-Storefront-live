"use client";
import { useParams } from "next/navigation";
import { SessionDetailView } from "@/components/session-detail";

export default function RailSessionDetail() {
  const { id } = useParams<{ id: string }>();
  return <SessionDetailView id={id} />;
}
