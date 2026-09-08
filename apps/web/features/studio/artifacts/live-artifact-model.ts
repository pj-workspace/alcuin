import type { ArtifactResource } from "@alcuin/contracts";

import { isArtifactResource } from "./artifact-workspace-model.ts";

export function resolveDisplayedArtifact({
  enabled,
  eventArtifact,
}: {
  enabled: boolean;
  eventArtifact?: unknown;
}): ArtifactResource | undefined {
  // A reply is never an Artifact. Only an explicit resource event opens a canvas.
  return enabled && isArtifactResource(eventArtifact) ? eventArtifact : undefined;
}
