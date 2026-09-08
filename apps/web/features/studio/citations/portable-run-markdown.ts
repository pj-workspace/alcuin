import type { ExecutionEvent } from "@alcuin/contracts";

import { loadRunCitations } from "./citation-access";
import { resolvePortableCitationMarkdown } from "./portable-citations";

/** A copy operation may resolve historical evidence; failure becomes an explicit unverified marker. */
export async function portableRunMarkdown(content: string, events: readonly ExecutionEvent[], runId: string | null | undefined, locale: "en" | "zh" = "en", options: { resolveHistory?: boolean } = {}): Promise<string> {
  return resolvePortableCitationMarkdown({ content, events, runId, locale, loadSources: options.resolveHistory !== false ? loadRunCitations : undefined });
}
