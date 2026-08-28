"use client";

import type { Agent } from "@alcuin/contracts";
import { Check, Code2, Copy, ExternalLink, KeyRound, Monitor, RefreshCcw, ShieldCheck, Smartphone, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { clsx } from "clsx";

import { API_URL, alcuinApi } from "@/lib/api";
import { AlcuinMark } from "@/components/alcuin-mark";
import { Toast } from "@/components/ui";

export function EmbedView({ agent }: { agent?: Agent }) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const [token, setToken] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [conversation, setConversation] = useState<Array<{ role: "user" | "agent"; text: string }>>([]);
  const snippet = useMemo(() => `<script type="module" src="https://cdn.example.com/alcuin-embed.js"></script>\n\n<alcuin-agent\n  api-url="${API_URL}"\n  session-token="${token ? `${token.slice(0, 24)}…` : "YOUR_SHORT_LIVED_TOKEN"}"\n  agent-name="${agent?.name ?? "Operations Copilot"}"\n  theme="auto"\n></alcuin-agent>`, [agent?.name, token]);
  if (!agent) return null;
  const currentAgent = agent;
  const notify = (value: string) => { setToast(value); window.setTimeout(() => setToast(null), 2200); };

  async function createSession() {
    try { const session = await alcuinApi.createEmbedSession(currentAgent.id); setToken(session.token); notify("Origin-bound session created"); }
    catch (error) { notify(error instanceof Error ? error.message : "Session creation failed"); }
  }
  function demoSend() {
    if (!message.trim()) return;
    const input = message.trim(); setMessage(""); setConversation((items) => [...items, { role: "user", text: input }]);
    window.setTimeout(() => setConversation((items) => [...items, { role: "agent", text: "I found INC-104 in the host context. Mitigation is active and checkout latency is recovering." }]), 550);
  }
  return <div className="wide-surface embed-surface">
    <header className="wide-header"><div><div className="eyebrow">Portable agent experience</div><h1>Embed Playground</h1><p>The same published definition, delivered through a framework-neutral Web Component and Headless API.</p></div><div className="header-actions"><button className="button secondary"><ExternalLink size={14} />API reference</button><button className="button dark" onClick={() => void createSession()}><KeyRound size={14} />Create session</button></div></header>
    <div className="embed-security"><ShieldCheck size={16} /><div><strong>Scoped by design</strong><span>Tokens bind workspace, published agent version, allowed origin, actions and expiry.</span></div><span className={clsx("session-state", token && "active")}>{token ? <><Check size={12} />Session active</> : "No live token"}</span></div>
    <div className="embed-workspace">
      <section className="code-panel"><header><div><Code2 size={15} /><span>Embed code</span></div><button onClick={() => { void navigator.clipboard.writeText(snippet); notify("Embed snippet copied"); }}><Copy size={13} />Copy</button></header><pre><code>{highlightSnippet(snippet)}</code></pre><div className="code-notes"><div><span>01</span><p>Create sessions server-side. Never expose your workspace credential.</p></div><div><span>02</span><p>Pass host context with <code>element.setContext(...)</code>.</p></div><div><span>03</span><p>Listen for run, artifact, approval and error custom events.</p></div></div><div className="sdk-row"><span>Package</span><code>@alcuin/embed</code><small>0.1.0</small></div></section>
      <section className="preview-panel"><header><div><span>Live host preview</span><small>Northstar Admin</small></div><div className="device-switch"><button className={clsx(device === "desktop" && "active")} onClick={() => setDevice("desktop")}><Monitor size={14} /></button><button className={clsx(device === "mobile" && "active")} onClick={() => setDevice("mobile")}><Smartphone size={14} /></button><button><RefreshCcw size={13} /></button></div></header><div className={clsx("host-canvas", device === "mobile" && "mobile")}><div className="host-app"><aside><div className="host-logo">N</div><i /><i /><i /><i /></aside><main><div className="host-title"><span>Incident</span><h3>Checkout latency</h3></div><div className="host-stat-row"><div><span>Status</span><strong>Mitigating</strong></div><div><span>Severity</span><strong>SEV-2</strong></div></div><div className="host-lines"><i /><i /><i /></div></main></div><div className="embedded-agent"><header><div><AlcuinMark size={26} /><span><strong>{agent.name}</strong><small><i />Connected</small></span></div><button>×</button></header><div className="embedded-body">{conversation.length === 0 ? <div className="embedded-intro"><Sparkles size={20} /><h3>How can I help?</h3><p>I can use this incident&apos;s context and your enabled operations tools.</p><button onClick={() => { setMessage("Summarize this incident"); }}>Summarize this incident</button></div> : conversation.map((item, index) => <div className={clsx("embed-message", item.role)} key={index}>{item.text}</div>)}</div><div className="embedded-composer"><input value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") demoSend(); }} placeholder="Ask about this incident…" /><button onClick={demoSend}>↑</button></div></div></div></section>
    </div>
    <section className="embed-events"><div><h2>Host event bridge</h2><p>Keep the containing platform in control.</p></div>{["alcuin:run-start", "alcuin:event", "alcuin:artifact", "alcuin:error"].map((event) => <code key={event}>{event}</code>)}</section>
    {toast && <Toast message={toast} />}
  </div>;
}

function highlightSnippet(snippet: string) { return snippet; }
