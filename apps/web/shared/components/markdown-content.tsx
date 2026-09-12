"use client";

import type { Components, ExtraProps } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { clsx } from "clsx";
import { createContext, memo, useContext, useMemo, type ComponentProps, type ReactNode } from "react";

import { preprocessMarkdown } from "@/shared/lib/markdown";

type LinkRenderer = (link: { href?: string; children: ReactNode }) => ReactNode | undefined;
const LinkRendererContext = createContext<LinkRenderer | undefined>(undefined);
type ParserProps = {
  content: string;
  remarkPlugins?: NonNullable<Parameters<typeof ReactMarkdown>[0]["remarkPlugins"]>;
  urlTransform?: Parameters<typeof ReactMarkdown>[0]["urlTransform"];
};
const noPlugins: NonNullable<ParserProps["remarkPlugins"]> = [];

// Keep the component identity stable so an opened citation retains its real DOM anchor.
function MarkdownLink({ node, children, ...props }: ComponentProps<"a"> & ExtraProps) {
  void node;
  const renderLink = useContext(LinkRendererContext);
  return renderLink?.({ href: props.href, children })
    ?? <a target="_blank" rel="noreferrer" {...props}>{children}</a>;
}

const components: Components = {
  table: ({ node, children, ...props }) => {
    void node;
    return <div className="markdown-table-wrap"><table {...props}>{children}</table></div>;
  },
  a: MarkdownLink,
};

// Source metadata and popover state flow to MarkdownLink through context. They
// do not invalidate the Markdown syntax tree or replace its existing anchors.
const ParsedMarkdown = memo(function ParsedMarkdown({ content, remarkPlugins = noPlugins, urlTransform }: ParserProps) {
  const plugins = useMemo(() => [remarkGfm, ...remarkPlugins], [remarkPlugins]);
  return <ReactMarkdown remarkPlugins={plugins} urlTransform={urlTransform} components={components}>
    {preprocessMarkdown(content)}
  </ReactMarkdown>;
});

export function MarkdownContent({
  content,
  variant = "answer",
  renderLink,
  remarkPlugins = noPlugins,
  urlTransform,
}: {
  content: string;
  variant?: "answer" | "thinking" | "artifact";
  renderLink?: LinkRenderer;
  remarkPlugins?: NonNullable<Parameters<typeof ReactMarkdown>[0]["remarkPlugins"]>;
  urlTransform?: Parameters<typeof ReactMarkdown>[0]["urlTransform"];
}) {
  return <LinkRendererContext.Provider value={renderLink}><div className={clsx("markdown-content", `markdown-${variant}`)}>
    <ParsedMarkdown content={content} remarkPlugins={remarkPlugins} urlTransform={urlTransform} />
  </div></LinkRendererContext.Provider>;
}
