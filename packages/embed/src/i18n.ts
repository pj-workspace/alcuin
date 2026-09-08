export type EmbedLocale = "en" | "zh";

const messages = {
  en: {
    agentName: "Alcuin Agent",
    connected: "Connected",
    introTitle: "How can I help?",
    introBody: "This agent uses the published Alcuin definition and your current page context.",
    message: "Message",
    promptPlaceholder: "Ask the agent…",
    send: "Send",
    embeddedSession: "Embedded session",
    runFailed: "Agent run failed",
    usingTool: (tool: string) => `Using ${tool}`,
    approvalRequired: "Approval required",
    reviewOperation: "Review this external operation.",
    deny: "Deny",
    approveOnce: "Approve once",
    approvedAndExecuted: "Approved and executed",
    denied: "Denied",
    requestFailed: "Alcuin request failed",
  },
  zh: {
    agentName: "Alcuin 智能体",
    connected: "已连接",
    introTitle: "有什么可以帮你？",
    introBody: "此智能体使用已发布的 Alcuin 定义和当前页面上下文。",
    message: "消息",
    promptPlaceholder: "向智能体提问…",
    send: "发送",
    embeddedSession: "嵌入式会话",
    runFailed: "智能体运行失败",
    usingTool: (tool: string) => `正在使用 ${tool}`,
    approvalRequired: "需要审批",
    reviewOperation: "请确认此项外部操作。",
    deny: "拒绝",
    approveOnce: "仅批准本次",
    approvedAndExecuted: "已批准并执行",
    denied: "已拒绝",
    requestFailed: "Alcuin 请求失败",
  },
} as const;

export function resolveEmbedLocale(language?: string | null): EmbedLocale {
  return language?.trim().toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function getEmbedMessages(language?: string | null) {
  return messages[resolveEmbedLocale(language)];
}
