"use client";

import { ExternalLink, FileText, Flag, Hammer, Link2, StickyNote } from "lucide-react";
import type { TaskEvidence } from "@alcuin/contracts";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";

export function TaskEvidenceList({
  evidence,
  empty = true,
}: {
  evidence: TaskEvidence[];
  empty?: boolean;
}) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);

  if (evidence.length === 0) {
    return empty ? <p className="task-evidence-empty">{copy.noEvidence}</p> : null;
  }

  return (
    <ol className="task-evidence-list" aria-label={copy.evidence}>
      {evidence.map((item) => {
        const href = safeHref(item.source_uri);
        return (
          <li className="task-evidence-item" data-kind={item.kind} key={item.id}>
            <span className="task-evidence-icon"><EvidenceIcon kind={item.kind} /></span>
            <span className="task-evidence-copy">
              <strong>{item.label}</strong>
              {item.summary && <span>{item.summary}</span>}
              {!href && item.resource_id && <code>{item.resource_id}</code>}
            </span>
            {href && (
              <a href={href} target="_blank" rel="noreferrer" aria-label={`${copy.source}: ${item.label}`}>
                <ExternalLink size={13} />
              </a>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function EvidenceIcon({ kind }: { kind: string }) {
  if (kind === "citation") return <Link2 size={13} />;
  if (kind === "tool_result") return <Hammer size={13} />;
  if (kind === "artifact") return <FileText size={13} />;
  if (kind === "checkpoint") return <Flag size={13} />;
  return <StickyNote size={13} />;
}

function safeHref(locator?: string | null): string | null {
  if (!locator) return null;
  try {
    const url = new URL(locator);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}
