import { alcuinApi, API_URL, WORKSPACE_ID } from "@/shared/lib/api";
import { createRunCitationCache } from "./citation-cache";

const historyCache = createRunCitationCache();

export function loadRunCitations(runId: string) {
  return historyCache.load(`${API_URL}\0${WORKSPACE_ID}`, runId, () => alcuinApi.getRunCitations(runId));
}
