const zh: Record<string, string> = {
  "Use in conversation": "基于此文档继续",
  "Saved content will be sent with your next message. The original stays unchanged.": "已保存的内容会随下一条消息发送，原文不会被覆盖。",
  "Remove document reference": "移除文档引用",
  "Referenced document": "引用的文档",
  "Close document reference": "关闭文档引用",
  "View referenced document": "查看引用的文档",
  "This is the snapshot sent with this message. Later edits do not change it.": "这是随这条消息发送的内容快照，之后的编辑不会改变它。",
  "Preview of the saved content selected for your next message.": "预览将随下一条消息发送的已保存内容。",
  "The source run did not complete. This document may be incomplete.": "来源执行没有完成，这份文档可能不完整。",
  "Add an instruction for this document before sending.": "请先说明你希望如何处理这份文档。",
  "Document references are available in Chat, not in a Task or plan.": "文档引用用于普通对话，暂不支持任务或计划模式。",
  "This document changed. Attach it again from the canvas before sending.": "这份文档已有更改，请从画布重新附加后发送。",
  "This document is unavailable in this conversation. Remove it or select another document.": "这份文档在当前对话中不可用，请移除或选择另一份文档。",
  "This document is too large to reference. Shorten it before attaching it again.": "这份文档过大，请精简后再重新附加。",
  "Source document": "来源文档",
  "Saved content": "已保存的内容",
};
export function artifactReferenceCopy(locale: "en" | "zh") {
  return (key: string) => locale === "zh" ? zh[key] ?? key : key;
}

export function artifactReferenceError(error: unknown): string | null {
  if (!error || typeof error !== "object" || !("detail" in error)) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || !("code" in detail)) return null;
  switch (detail.code) {
    case "artifact_reference_changed": return "This document changed. Attach it again from the canvas before sending.";
    case "artifact_reference_unavailable": return "This document is unavailable in this conversation. Remove it or select another document.";
    case "artifact_reference_too_large": return "This document is too large to reference. Shorten it before attaching it again.";
    default: return null;
  }
}
