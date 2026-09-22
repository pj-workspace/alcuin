import type { ExecutionEvent, RunStatus } from "@alcuin/contracts";

export type QuestionRecord = {
  id: string;
  question: string;
  options: string[];
  state: "pending" | "answered" | "skipped" | "closed";
  answer?: string;
  answeredAt?: string;
};

export function questionRecords(events: ExecutionEvent[], status: RunStatus | "submitting"): QuestionRecord[] {
  const records = new Map<string, QuestionRecord>();
  const interactions = events.filter((event) => event.type === "input.required" || event.type === "input.answered");
  for (const event of interactions.sort((a, b) => a.sequence - b.sequence)) {
    const p = event.payload;
    if (typeof p.input_id !== "string") continue;
    if (event.type === "input.required" && typeof p.question === "string") {
      if (records.has(p.input_id)) continue;
      records.set(p.input_id, {
        id: p.input_id, question: p.question,
        options: Array.isArray(p.options) ? p.options.filter((v): v is string => typeof v === "string") : [],
        state: status === "waiting_for_input" ? "pending" : "closed",
      });
    }
    if (event.type === "input.answered") {
      const record = records.get(p.input_id);
      if (!record) continue;
      records.set(p.input_id, { ...record, state: p.skip ? "skipped" : "answered",
        answer: typeof p.answer === "string" ? p.answer : "",
        answeredAt: typeof p.answered_at === "string" ? p.answered_at : undefined });
    }
  }
  return [...records.values()];
}
