export type ToolDisplayStatus = "running" | "success" | "error" | "stopped";

/** Presentation only: stopping never invents a tool execution receipt. */
export function toolDisplayStatus(status: ToolDisplayStatus, tool: string, runStatus?: string): ToolDisplayStatus {
  if (status !== "running") return status;
  if (runStatus === "cancelled" || (runStatus === "cancel_requested" && tool === "human.ask")) return "stopped";
  return status;
}
