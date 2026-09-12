"use client";

/* eslint-disable @typescript-eslint/no-unused-vars -- react-markdown's node prop must not reach DOM elements. */

import { memo, useEffect, useRef, useState } from "react";
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

export const ThinkingMarkdown = memo(function ThinkingMarkdown({ content }: { content: string }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [contentHeight, setContentHeight] = useState(SOFT_MAX_HEIGHT_PX);
  const heightRef = useRef(SOFT_MAX_HEIGHT_PX);
  const rendered = preprocessMarkdown(content);
  const needsClamp = contentHeight > SOFT_MAX_HEIGHT_PX + SOFT_OVERFLOW_BUFFER_PX;

  useEffect(() => {
    const element = contentRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    // Observe the natural-height inner content, never the animated clamp.
    // Observer entries already contain layout results; no style-write/read cycle.
    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((item) => item.target === element);
      // A hidden mobile panel reports zero width; keep its expansion preference.
      if (!entry || entry.contentRect.width === 0) return;
      const nextHeight = Math.ceil(entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height);
      if (nextHeight === heightRef.current) return;
      heightRef.current = nextHeight;
      setContentHeight(nextHeight);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!needsClamp && expanded) setExpanded(false);
  }, [expanded, needsClamp]);

  const clamped = needsClamp && !expanded;

  return (
    <div className="thinking-md-shell">
      <div
        className="thinking-md"
        style={needsClamp ? {
          maxHeight: clamped ? SOFT_MAX_HEIGHT_PX : contentHeight,
          overflow: "hidden",
          WebkitMaskImage: clamped ? "linear-gradient(to bottom, #000 70%, transparent)" : "none",
          maskImage: clamped ? "linear-gradient(to bottom, #000 70%, transparent)" : "none",
        } : undefined}
      >
        <div ref={contentRef} className="thinking-md-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={thinkingComponents}>
            {rendered}
          </ReactMarkdown>
        </div>
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
});
