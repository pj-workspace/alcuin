"use client";

import type { Agent } from "@alcuin/contracts";
import {
  Check,
  Code2,
  Copy,
  ExternalLink,
  KeyRound,
  Monitor,
  RefreshCcw,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { clsx } from "clsx";
import { createElement, useCallback, useEffect, useMemo, useState } from "react";

import { API_URL, alcuinApi } from "@/lib/api";
import { Toast } from "@/components/ui";

type AlcuinElement = HTMLElement & { setContext: (context: Record<string, unknown>) => void };

export function EmbedView({ agent }: { agent?: Agent }) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const [token, setToken] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [componentReady, setComponentReady] = useState(false);
  const [liveElement, setLiveElement] = useState<AlcuinElement | null>(null);
  const bindLiveElement = useCallback((node: HTMLElement | null) => {
    setLiveElement(node as AlcuinElement | null);
  }, []);
  const fullSnippet = useMemo(() => `import "@alcuin/embed";

<alcuin-agent
  api-url="${API_URL}"
  session-token="${token ?? "YOUR_SHORT_LIVED_TOKEN"}"
  agent-name="${agent?.name ?? "Operations Copilot"}"
  theme="auto"
></alcuin-agent>`, [agent?.name, token]);
  const displaySnippet = useMemo(
    () => token ? fullSnippet.replace(token, `${token.slice(0, 24)}…`) : fullSnippet,
    [fullSnippet, token],
  );

  useEffect(() => {
    let active = true;
    void import("@alcuin/embed").then(({ registerAlcuinAgent }) => {
      registerAlcuinAgent();
      if (active) setComponentReady(true);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (componentReady && liveElement) {
      liveElement.setContext({
        page: "/operations/incidents/INC-104",
        record: { id: "INC-104", status: "mitigating", severity: "SEV-2" },
      });
    }
  }, [componentReady, liveElement, token]);

  if (!agent) return null;
  const currentAgent = agent;
  const notify = (value: string) => { setToast(value); window.setTimeout(() => setToast(null), 2200); };

  async function createSession() {
    try {
      const session = await alcuinApi.createEmbedSession(currentAgent.id);
      setToken(session.token);
      notify("Origin-bound session created");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Session creation failed");
    }
  }

  return <div className="wide-surface embed-surface">
    <header className="wide-header"><div><div className="eyebrow">Portable agent experience</div><h1>Embed Playground</h1><p>The same published definition, delivered through a framework-neutral Web Component and Headless API.</p></div><div className="header-actions"><button className="button secondary"><ExternalLink size={14} />API reference</button><button className="button dark" onClick={() => void createSession()}><KeyRound size={14} />Create session</button></div></header>
    <div className="embed-security"><ShieldCheck size={16} /><div><strong>Scoped by design</strong><span>Tokens bind workspace, published agent version, allowed origin, actions and expiry.</span></div><span className={clsx("session-state", token && "active")}>{token ? <><Check size={12} />Session active</> : "No live token"}</span></div>
    <div className="embed-workspace">
      <section className="code-panel"><header><div><Code2 size={15} /><span>Embed code</span></div><button onClick={() => { void navigator.clipboard.writeText(fullSnippet); notify("Embed snippet copied"); }}><Copy size={13} />Copy</button></header><pre><code>{displaySnippet}</code></pre><div className="code-notes"><div><span>01</span><p>Create sessions server-side. Never expose your workspace credential.</p></div><div><span>02</span><p>Pass host context with <code>element.setContext(...)</code>.</p></div><div><span>03</span><p>Listen for run, artifact, approval and error custom events.</p></div></div><div className="sdk-row"><span>Package</span><code>@alcuin/embed</code><small>0.1.0</small></div></section>
      <section className="preview-panel"><header><div><span>Live host preview</span><small>Northstar Admin · real Web Component</small></div><div className="device-switch"><button className={clsx(device === "desktop" && "active")} onClick={() => setDevice("desktop")} aria-label="Desktop preview"><Monitor size={14} /></button><button className={clsx(device === "mobile" && "active")} onClick={() => setDevice("mobile")} aria-label="Mobile preview"><Smartphone size={14} /></button><button onClick={() => setToken(null)} aria-label="Reset embed session"><RefreshCcw size={13} /></button></div></header><div className={clsx("host-canvas", device === "mobile" && "mobile")}><div className="host-app"><aside><div className="host-logo">N</div><i /><i /><i /><i /></aside><main><div className="host-title"><span>Incident</span><h3>Checkout latency</h3></div><div className="host-stat-row"><div><span>Status</span><strong>Mitigating</strong></div><div><span>Severity</span><strong>SEV-2</strong></div></div><div className="host-lines"><i /><i /><i /></div></main></div><div className="embedded-agent live-component">{token && componentReady ? createElement("alcuin-agent", { ref: bindLiveElement, "api-url": API_URL, "session-token": token, "agent-name": agent.name, theme: "auto" }) : <div className="embed-locked"><KeyRound size={20} /><strong>Create a scoped session</strong><p>The live component stays disconnected until the host obtains a short-lived token.</p><button onClick={() => void createSession()}>Create session</button></div>}</div></div></section>
    </div>
    <section className="embed-events"><div><h2>Host event bridge</h2><p>Keep the containing platform in control.</p></div>{["alcuin:run-start", "alcuin:event", "alcuin:artifact", "alcuin:approval", "alcuin:error"].map((event) => <code key={event}>{event}</code>)}</section>
    {toast && <Toast message={toast} />}
  </div>;
}
