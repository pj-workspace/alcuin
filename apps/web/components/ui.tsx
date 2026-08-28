"use client";

import { Check, CircleAlert, LoaderCircle, X } from "lucide-react";
import { clsx } from "clsx";

import { AlcuinMark } from "@/components/alcuin-mark";

export function StatusPill({ status }: { status: string }) {
  const normalized = status.replaceAll("_", " ");
  const icon = status === "completed" || status === "healthy" || status === "enabled"
    ? <Check size={11} />
    : status === "failed" || status === "unhealthy"
      ? <X size={11} />
      : status === "running" || status === "queued"
        ? <LoaderCircle className="spin" size={11} />
        : <CircleAlert size={11} />;
  return <span className={clsx("status-pill", `status-${status}`)}>{icon}{normalized}</span>;
}

export function PanelEmpty({ title, body }: { title: string; body: string }) {
  return <div className="panel-empty"><AlcuinMark size={32} /><h3>{title}</h3><p>{body}</p></div>;
}

export function Toast({ message, tone = "default" }: { message: string; tone?: "default" | "error" }) {
  return <div className={clsx("toast", tone === "error" && "toast-error")}>{message}</div>;
}
