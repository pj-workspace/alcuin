import type { ExecutionEvent } from "@alcuin/contracts";
import type { ProviderModelProfile, ProviderStatus, ReasoningEffort } from "@alcuin/sdk";

const TIER_ORDER = new Map([
  ["flash", 0],
  ["vision", 1],
  ["pro", 2],
  ["standard", 3],
]);

export type EffectiveRunProfile = {
  model: string | null;
  reasoningEffort: ReasoningEffort | null;
};

export function modelsForAgent(
  providers: ProviderStatus[] | null | undefined,
  providerId: string,
): ProviderModelProfile[] {
  const provider = (providers ?? []).find((candidate) => candidate.id === providerId);
  if (!provider?.configured) return [];
  return [...provider.models].sort((left, right) => {
    const tierDelta = (TIER_ORDER.get(left.tier) ?? 99) - (TIER_ORDER.get(right.tier) ?? 99);
    return tierDelta || left.label.localeCompare(right.label);
  });
}

export function runProfileFromEvents(events: ExecutionEvent[]): EffectiveRunProfile {
  const started = events.find((event) => event.type === "run.started");
  if (!started) return { model: null, reasoningEffort: null };
  const model = typeof started.payload.model === "string" ? started.payload.model : null;
  const effort = started.payload.reasoning_effort;
  const reasoningEffort = effort === "none" || effort === "low" || effort === "medium" || effort === "high"
    ? effort
    : null;
  return { model, reasoningEffort };
}

export function tierLabelKey(tier: string): "Flash" | "Vision" | "Pro" | "Standard" | null {
  if (tier === "flash") return "Flash";
  if (tier === "vision") return "Vision";
  if (tier === "pro") return "Pro";
  if (tier === "standard") return "Standard";
  return null;
}
