"use client";

import type { Thread } from "@alcuin/contracts";
import { useEffect, useRef } from "react";

import { useI18n } from "@/shared/lib/i18n";
import { threadTitleStatus, threadTitleText } from "./thread-title-model";
import "./thread-title.css";

export function ThreadTitle({ thread }: { thread: Thread }) {
  const { locale } = useI18n();
  const element = useRef<HTMLSpanElement>(null);
  const status = threadTitleStatus(thread);
  const previous = useRef({ id: thread.id, status });
  useEffect(() => {
    const before = previous.current;
    if (before.id === thread.id && before.status !== "ready" && status === "ready" && element.current) {
      element.current.dataset.arrived = "true";
      const node = element.current;
      const timer = window.setTimeout(() => delete node.dataset.arrived, 1000);
      previous.current = { id: thread.id, status };
      return () => { window.clearTimeout(timer); delete node.dataset.arrived; };
    }
    previous.current = { id: thread.id, status };
  }, [thread.id, status]);
  const title = threadTitleText(thread, locale);
  return <span ref={element} className="thread-title" data-title-status={status} title={title} aria-label={title} aria-live={status === "ready" ? "polite" : "off"} onAnimationEnd={(event) => { if (event.animationName === "thread-title-arrive") delete event.currentTarget.dataset.arrived; }}>{title}</span>;
}
