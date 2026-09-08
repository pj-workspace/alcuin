"use client";

/* eslint-disable @typescript-eslint/no-unused-vars -- react-markdown's node prop must not reach DOM elements. */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { preprocessMarkdown } from "@/shared/lib/markdown";

const SOFT_MAX_HEIGHT_PX = 144;
const SOFT_OVERFLOW_BUFFER_PX = 24;

const thinkingComponents: Components = {
  p: ({ node: _node, children, ...props }) => <p {...props}>{children}</p>,
  ul: ({ node: _node, children, ...props }) => <ul {...props}>{children}</ul>,
  ol: ({ node: _node, children, ...props }) => <ol {...props}>{children}</ol>,
  li: ({ node: _node, children, ...props }) => <li {...props}>{children}</li>,
  code: ({ node: _node, className, children, ...props }) => {
    const isBlock = /\blanguage-/.test(className ?? "");
    return isBlock
      ? <code className={className} {...props}>{children}</code>
      : <code className="thinking-md-code" {...props}>{children}</code>;
  },
  pre: ({ node: _node, children, ...props }) => (
    <pre className="thinking-md-pre" {...props}>{children}</pre>
  ),
  strong: ({ node: _node, children, ...props }) => <strong {...props}>{children}</strong>,
  em: ({ node: _node, children, ...props }) => <em {...props}>{children}</em>,
  blockquote: ({ node: _node, children, ...props }) => <blockquote {...props}>{children}</blockquote>,
  h1: ({ node: _node, children, ...props }) => <h4 {...props}>{children}</h4>,
  h2: ({ node: _node, children, ...props }) => <h4 {...props}>{children}</h4>,
  h3: ({ node: _node, children, ...props }) => <h4 {...props}>{children}</h4>,
  h4: ({ node: _node, children, ...props }) => <h4 {...props}>{children}</h4>,
  a: ({ node: _node, children, ...props }) => (
    <a target="_blank" rel="noreferrer" {...props}>{children}</a>
  ),
  table: ({ node: _node, children, ...props }) => (
    <div className="thinking-md-table-wrap"><table {...props}>{children}</table></div>
  ),
  hr: () => <hr className="thinking-md-hr" />,
};

export function ThinkingMarkdown({ content }: { content: string }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [needsClamp, setNeedsClamp] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [contentHeight, setContentHeight] = useState(SOFT_MAX_HEIGHT_PX);
  const rendered = preprocessMarkdown(content);

  useLayoutEffect(() => {
    const element = contentRef.current;
    if (!element) return;
    const previousMaxHeight = element.style.maxHeight;
    element.style.maxHeight = "none";
    const measuredHeight = Math.ceil(element.scrollHeight);
    const overflows = measuredHeight > SOFT_MAX_HEIGHT_PX + SOFT_OVERFLOW_BUFFER_PX;
    element.style.maxHeight = previousMaxHeight;
    setContentHeight(measuredHeight);
    setNeedsClamp(overflows);
  }, [rendered]);

  useEffect(() => {
    if (!needsClamp && expanded) setExpanded(false);
  }, [expanded, needsClamp]);

  const clamped = needsClamp && !expanded;

  return (
    <div className="thinking-md-shell">
      <div
        ref={contentRef}
        className="thinking-md"
        style={needsClamp ? {
          maxHeight: clamped ? SOFT_MAX_HEIGHT_PX : contentHeight,
          overflow: "hidden",
          WebkitMaskImage: clamped ? "linear-gradient(to bottom, #000 70%, transparent)" : "none",
          maskImage: clamped ? "linear-gradient(to bottom, #000 70%, transparent)" : "none",
        } : undefined}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={thinkingComponents}>
          {rendered}
        </ReactMarkdown>
      </div>
      {needsClamp && (
        <button
          type="button"
          className="thinking-md-more"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}
