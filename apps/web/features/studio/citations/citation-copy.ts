export function citationCopy(locale: "en" | "zh") {
  return locale === "zh" ? {
    sources: "来源与证据", retrieved: "检索到的来源", close: "关闭证据", open: "查看来源",
    excerpt: "检索摘录", noExcerpt: "此来源没有保存摘录，请打开原文核对。",
    missing: "引用未匹配", missingDetail: "这条引用没有对应的本次检索来源，暂时无法核验。",
    original: "打开原文", knowledge: "知识库", web: "网页", other: "来源",
    page: "页", chunk: "片段", document: "文档", noLocation: "未保存原文定位",
    note: "这里展示工具返回的来源；只有正文中的引用标记或对应链接，才表示该处关联了此来源。",
    excerptNote: "摘录为检索时保存的内容，可能不完整。请结合上下文核验结论。",
    unavailable: "原文暂不可打开", unknownTitle: "未命名来源", retrievedAt: "检索时间",
    loading: "正在核对原文…", loadError: "原文加载失败，仍可查看保存的摘录。", fullDocument: "定位文档片段",
    loadSources: "查看引用来源", loadingSources: "正在加载来源…", noSources: "这次运行没有保存可核验的来源。", sourcesError: "来源加载失败，请重试。", retry: "重试",
    pendingSource: "点击后加载这条引用对应的来源。",
    copying: "正在复制…", copyFailed: "复制失败，请重试。",
  } : {
    sources: "Sources & evidence", retrieved: "Retrieved sources", close: "Close evidence", open: "View source",
    excerpt: "Retrieved excerpt", noExcerpt: "No excerpt was saved for this source. Open the original to verify it.",
    missing: "Unmatched citation", missingDetail: "This reference has no matching source in this run and cannot be verified yet.",
    original: "Open original", knowledge: "Knowledge", web: "Web", other: "Source",
    page: "Page", chunk: "Passage", document: "Document", noLocation: "Original location not saved",
    note: "These sources were returned by tools. Only an inline citation or matching link associates a specific passage with a source.",
    excerptNote: "This excerpt was saved at retrieval time and may be incomplete. Check the surrounding context before relying on the claim.",
    unavailable: "Original unavailable", unknownTitle: "Untitled source", retrievedAt: "Retrieved",
    loading: "Checking the original…", loadError: "The original could not be loaded. The saved excerpt is still available.", fullDocument: "Locate document passage",
    loadSources: "View cited sources", loadingSources: "Loading sources…", noSources: "This run has no saved sources to verify.", sourcesError: "Sources could not be loaded. Try again.", retry: "Retry",
    pendingSource: "Open this reference to load its saved source.",
    copying: "Copying…", copyFailed: "Copy failed. Try again.",
  };
}
