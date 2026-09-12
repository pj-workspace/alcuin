import type { Thread } from "@alcuin/contracts";

/** Leave a full generation window after reclaiming a crashed 30-second lease. */
export function threadTitleWatchAction({ status, attempts, elapsedMs, recovered }: {
  status: NonNullable<Thread["title_status"]>;
  attempts: number;
  elapsedMs: number;
  recovered: boolean;
}): "stop" | "read" | "ensure" {
  if (status === "ready" || (status === "pending" && attempts >= 4) || elapsedMs >= 55_000) return "stop";
  return status === "generating" && elapsedMs >= 32_000 && !recovered ? "ensure" : "read";
}

export function threadTitleStatus(thread: Pick<Thread, "title" | "title_status">): NonNullable<Thread["title_status"]> {
  if (thread.title_status) return thread.title_status;
  return !thread.title.trim() || ["Working session", "New thread", "New agent thread", "新会话", "未命名会话"].includes(thread.title.trim()) ? "pending" : "ready";
}

export function threadTitleText(thread: Pick<Thread, "title" | "title_status">, locale: "en" | "zh"): string {
  const status = threadTitleStatus(thread);
  if (status === "generating") return locale === "zh" ? "正在命名会话…" : "Naming conversation…";
  if (status === "pending") return locale === "zh" ? "新会话" : "New thread";
  return thread.title;
}

/** A naming refresh updates one identity only and never replaces message state. */
export function mergeThreadTitle(current: Thread, incoming: Thread): Thread {
  if (current.id !== incoming.id || current.workspace_id !== incoming.workspace_id) return current;
  const currentTime = Date.parse(current.updated_at ?? current.created_at);
  const incomingTime = Date.parse(incoming.updated_at ?? incoming.created_at);
  if (incomingTime < currentTime) return current;
  if (threadTitleStatus(current) === "ready" && threadTitleStatus(incoming) !== "ready") return current;
  if (current.title === incoming.title && current.title_status === incoming.title_status && (incoming.updated_at ?? current.updated_at) === current.updated_at) return current;
  return { ...current, title: incoming.title, title_status: incoming.title_status, updated_at: incoming.updated_at ?? current.updated_at };
}
