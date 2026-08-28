import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { clsx } from "clsx";

import { preprocessMarkdown } from "@/shared/lib/markdown";

const components: Components = {
  table: ({ node, children, ...props }) => {
    void node;
    return <div className="markdown-table-wrap"><table {...props}>{children}</table></div>;
  },
  a: ({ node, children, ...props }) => {
    void node;
    return <a target="_blank" rel="noreferrer" {...props}>{children}</a>;
  },
};

export function MarkdownContent({
  content,
  variant = "answer",
}: {
  content: string;
  variant?: "answer" | "thinking" | "artifact";
}) {
  const rendered = preprocessMarkdown(content);

  return <div className={clsx("markdown-content", `markdown-${variant}`)}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {rendered}
    </ReactMarkdown>
  </div>;
}
