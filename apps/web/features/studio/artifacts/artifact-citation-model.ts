import type { ArtifactResource, ExecutionEvent } from "@alcuin/contracts";

/** Citation ids are local to a Run; matching ids from another reply are not evidence. */
export function citationsForArtifact(
  artifact: Pick<ArtifactResource, "source_run_id">,
  events: readonly ExecutionEvent[],
): ExecutionEvent[] {
  return events.filter((event) => event.run_id === artifact.source_run_id && event.type === "citation.created");
}
