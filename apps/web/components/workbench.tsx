"use client";

import type { AgentDefinition, BootstrapPayload, ExecutionEvent } from "@alcuin/contracts";
import {
  Blocks,
  Bot,
  Braces,
  ChevronDown,
  Command,
  GalleryVerticalEnd,
  Languages,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  Search,
  Settings2,
  Sun,
  Workflow,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { clsx } from "clsx";

import { AgentBuilderView } from "@/components/builder-view";
import { AlcuinMark } from "@/components/alcuin-mark";
import { EmbedView } from "@/components/embed-view";
import { ExtensionsView } from "@/components/extensions-view";
import { RunsView } from "@/components/runs-view";
import { StudioView } from "@/components/studio-view";
import { alcuinApi } from "@/lib/api";
import { statusLabel, useI18n, type MessageKey } from "@/lib/i18n";

export type Surface = "studio" | "agents" | "extensions" | "runs" | "embed";

const nav = [
  { id: "studio", label: "Studio", icon: GalleryVerticalEnd },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "extensions", label: "Extensions", icon: Blocks },
  { id: "runs", label: "Runs", icon: Workflow },
  { id: "embed", label: "Embed", icon: Braces },
] satisfies Array<{ id: Surface; label: MessageKey; icon: typeof Bot }>;

const DEFAULT_AGENT_INSTRUCTIONS = "You are a helpful, domain-neutral agent. Use only explicitly bound capabilities and follow the configured approval policies." satisfies MessageKey;

function slugFromName(name: string) {
  const normalized = name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const slug = normalized.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  if (!slug) return "new-agent";
  return /^[a-z]/.test(slug) ? slug : `agent-${slug}`.slice(0, 64);
}

function newAgentDefinition(name: string, description: string, instructions: string): AgentDefinition {
  return {
    schema_version: "2026-08-28",
    identity: { name, description, icon: "spark" },
    instructions,
    model: {
      provider: "deepseek",
      model: "deepseek-v4-flash-vision-exp",
      credential_ref: "secret://workspace/deepseek-primary",
    },
    extensions: [],
    tools: [],
    knowledge: [],
    runtime: { adapter: "langgraph-react", max_steps: 8 },
    policies: { mutating_tools: "ask", external_side_effects: "ask" },
    context_policy: { accepted: ["page", "record", "selection"], max_bytes: 16_384 },
    output_schema: { type: "artifact", format: "markdown" },
    starter_prompts: [],
  };
}

export function Workbench({ surface }: { surface: Surface }) {
  const { locale, t, toggleLocale } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const [data, setData] = useState<BootstrapPayload | null>(null);
  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [commandOpen, setCommandOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [createAgentError, setCreateAgentError] = useState<string | null>(null);
  const [slugTouched, setSlugTouched] = useState(false);
  const [agentForm, setAgentForm] = useState({
    name: "",
    slug: "",
    description: "",
    instructions: DEFAULT_AGENT_INSTRUCTIONS as string,
  });

  const refresh = useCallback(async (preferredRunId?: string) => {
    try {
      const bootstrap = await alcuinApi.bootstrap();
      setData(bootstrap);
      setSelectedAgentId((current) => {
        const saved = window.localStorage.getItem("alcuin-agent-id");
        const candidate = current ?? saved;
        const next = bootstrap.agents.some((agent) => agent.id === candidate)
          ? candidate
          : bootstrap.agents[0]?.id ?? null;
        if (next) window.localStorage.setItem("alcuin-agent-id", next);
        else window.localStorage.removeItem("alcuin-agent-id");
        return next;
      });
      setError(null);
      const selectedRunId = preferredRunId ?? bootstrap.runs[0]?.id;
      if (selectedRunId) {
        const run = await alcuinApi.getRun(selectedRunId);
        setEvents(run.events);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to connect to Alcuin API");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const saved = window.localStorage.getItem("alcuin-theme");
    const nextTheme = saved === "dark" ? "dark" : "light";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
  }, []);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 820px)");
    const closeOnMobile = () => {
      if (mobile.matches) setSidebarOpen(false);
    };
    closeOnMobile();
    mobile.addEventListener("change", closeOnMobile);
    return () => mobile.removeEventListener("change", closeOnMobile);
  }, []);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((open) => !open);
      }
      if (event.key === "Escape") {
        setCommandOpen(false);
        setCreateAgentOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const activeAgent = data?.agents.find((agent) => agent.id === selectedAgentId) ?? data?.agents[0];
  const switchTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem("alcuin-theme", next);
  };
  const navigate = (target: Surface) => {
    setCommandOpen(false);
    router.push(`/${target}`);
  };
  const selectAgent = useCallback((agentId: string) => {
    setSelectedAgentId(agentId);
    window.localStorage.setItem("alcuin-agent-id", agentId);
    router.push("/agents");
  }, [router]);
  const openCreateAgent = useCallback(() => {
    setAgentForm({ name: "", slug: "", description: "", instructions: t(DEFAULT_AGENT_INSTRUCTIONS) });
    setSlugTouched(false);
    setCreateAgentError(null);
    setCreateAgentOpen(true);
  }, [t]);
  const createAgentValid = agentForm.name.trim().length >= 2
    && /^[a-z][a-z0-9-]{2,63}$/.test(agentForm.slug)
    && agentForm.instructions.trim().length >= 8;
  const createAgent = async () => {
    if (!createAgentValid) return;
    setCreatingAgent(true);
    setCreateAgentError(null);
    try {
      const created = await alcuinApi.createAgent(
        agentForm.slug,
        newAgentDefinition(
          agentForm.name.trim(),
          agentForm.description.trim(),
          agentForm.instructions.trim(),
        ),
      );
      setSelectedAgentId(created.id);
      window.localStorage.setItem("alcuin-agent-id", created.id);
      setCreateAgentOpen(false);
      await refresh();
      router.push("/agents");
    } catch (reason) {
      setCreateAgentError(reason instanceof Error ? reason.message : t("Unable to create agent"));
    } finally {
      setCreatingAgent(false);
    }
  };
  const view = useMemo(() => {
    if (!data) return null;
    switch (surface) {
      case "agents": return (
        <AgentBuilderView
          key={activeAgent?.current_version_id}
          agent={activeAgent}
          agents={data.agents}
          knowledgeSources={data.knowledge_sources}
          extensions={data.extensions}
          onChanged={refresh}
          onCreateAgent={openCreateAgent}
          onSelectAgent={selectAgent}
        />
      );
      case "extensions": return <ExtensionsView extensions={data.extensions} onChanged={refresh} />;
      case "runs": return <RunsView runs={data.runs} events={events} onSelectEvents={setEvents} />;
      case "embed": return <EmbedView agent={activeAgent} />;
      default: return (
        <StudioView
          workspace={data.workspace}
          agent={activeAgent}
          extensions={data.extensions}
          initialEvents={events}
          onRunCreated={refresh}
        />
      );
    }
  }, [activeAgent, data, events, openCreateAgent, refresh, selectAgent, surface]);

  return (
    <main className="app-frame">
      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-button quiet" onClick={() => setSidebarOpen((open) => !open)} aria-label={t("Toggle sidebar")}>
            {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
          </button>
          <Link className="brand" href="/studio"><AlcuinMark size={28} /><span>Alcuin</span></Link>
          <span className="topbar-separator" />
          <button className="workspace-switcher"><span className="workspace-glyph">N</span>{data?.workspace.name ?? t("Workspace")}<ChevronDown size={13} /></button>
        </div>
        <div className="topbar-actions">
          <button className="command-trigger" onClick={() => setCommandOpen(true)}><Search size={14} /><span>{t("Search or jump to")}</span><kbd>⌘ K</kbd></button>
          <button className="language-switch" onClick={toggleLocale} aria-label={t(locale === "en" ? "Switch to Chinese" : "Switch to English")} title={t(locale === "en" ? "Switch to Chinese" : "Switch to English")}><Languages size={14} /><span>{locale === "en" ? "中文" : "EN"}</span></button>
          <button className="icon-button quiet" onClick={switchTheme} aria-label={t("Toggle theme")}>{theme === "light" ? <Moon size={16} /> : <Sun size={16} />}</button>
          <button className="icon-button quiet settings-button" aria-label={t("Settings")}><Settings2 size={16} /></button>
          <div className="avatar">PJ</div>
        </div>
      </header>

      <div className="workspace-frame">
        <aside className={clsx("sidebar", !sidebarOpen && "sidebar-collapsed")}>
          <nav className="primary-nav" aria-label={t("Primary")}>
            {nav.map((item) => (
              <Link key={item.id} href={`/${item.id}`} className={clsx("nav-item", pathname === `/${item.id}` && "active")} title={item.label}>
                <item.icon size={16} /><span>{t(item.label)}</span>
              </Link>
            ))}
          </nav>
          <div className="sidebar-section">
            <div className="sidebar-heading"><span>{t("Agents")}</span><button aria-label={t("New agent")} onClick={openCreateAgent}><Plus size={14} /></button></div>
            {data?.agents.map((agent) => (
              <Link href="/agents" className={clsx("resource-row", agent.id === activeAgent?.id && "active-resource")} key={agent.id} onClick={() => selectAgent(agent.id)}>
                <span className="agent-glyph"><Command size={13} /></span>
                <span><strong>{agent.name}</strong><small>v{agent.version} · {statusLabel(agent.status, locale)}</small></span>
              </Link>
            ))}
          </div>
          <div className="sidebar-section threads-section">
            <div className="sidebar-heading"><span>{t("Recent threads")}</span></div>
            {data?.threads.slice(0, 4).map((thread) => (
              <Link href="/studio" className="thread-row" key={thread.id}><Play size={11} fill="currentColor" /><span>{thread.title}</span></Link>
            ))}
          </div>
          <div className="sidebar-footer"><span className="runtime-dot" />{t("API connected")} <span className="version-label">pre-alpha</span></div>
        </aside>

        <section className="surface">
          {loading && <div className="loading-state"><AlcuinMark className="pulse" size={44} /><p>{t("Composing workspace…")}</p></div>}
          {!loading && error && <div className="connection-error"><span className="mini-mark">!</span><h2>{t("Alcuin API is offline")}</h2><p>{error === "Unable to connect to Alcuin API" ? t("Unable to connect to Alcuin API") : error}</p><code>pnpm dev:api</code><button onClick={() => void refresh()}>{t("Try again")}</button></div>}
          {!loading && !error && view}
        </section>
      </div>

      {createAgentOpen && (
        <div className="sheet-backdrop" onMouseDown={() => !creatingAgent && setCreateAgentOpen(false)}>
          <aside className="inspect-sheet agent-create-sheet" role="dialog" aria-modal="true" aria-labelledby="create-agent-title" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><AlcuinMark size={32} /><div><small>{t("Versioned definition")}</small><h2 id="create-agent-title">{t("Create agent")}</h2></div></div><button className="icon-button quiet" aria-label={t("Close create agent")} onClick={() => setCreateAgentOpen(false)} disabled={creatingAgent}><X size={16} /></button></header>
            <div className="wizard-body agent-create-body">
              <p className="agent-create-intro">{t("Start with a small, domain-neutral definition. Bind tools and knowledge explicitly in the Builder.")}</p>
              <label className="field"><span>{t("Agent name")}</span><input autoFocus value={agentForm.name} onChange={(event) => { const name = event.target.value; setAgentForm((current) => ({ ...current, name, slug: slugTouched ? current.slug : slugFromName(name) })); }} placeholder={t("Research Copilot")} /></label>
              <label className="field"><span>{t("Agent slug")}</span><input value={agentForm.slug} onChange={(event) => { setSlugTouched(true); setAgentForm({ ...agentForm, slug: event.target.value.toLowerCase() }); }} placeholder="research-copilot" /><small>{t("Lowercase letters, numbers, and hyphens. This identifier is stable after creation.")}</small></label>
              <label className="field"><span>{t("Description")}</span><textarea rows={3} value={agentForm.description} onChange={(event) => setAgentForm({ ...agentForm, description: event.target.value })} placeholder={t("What this agent is responsible for.")} /></label>
              <label className="field"><span>{t("System instructions")}</span><textarea rows={7} value={agentForm.instructions} onChange={(event) => setAgentForm({ ...agentForm, instructions: event.target.value })} /></label>
              {createAgentError && <div className="wizard-error">{createAgentError}</div>}
            </div>
            <footer><button className="button secondary" onClick={() => setCreateAgentOpen(false)} disabled={creatingAgent}>{t("Cancel")}</button><button className="button dark" onClick={() => void createAgent()} disabled={creatingAgent || !createAgentValid}>{creatingAgent ? <span className="micro-loader" /> : <Plus size={14} />}{t(creatingAgent ? "Creating agent…" : "Create draft")}</button></footer>
          </aside>
        </div>
      )}

      {commandOpen && (
        <div className="command-backdrop" onMouseDown={() => setCommandOpen(false)}>
          <div className="command-menu" onMouseDown={(event) => event.stopPropagation()}>
            <div className="command-input"><Search size={17} /><input autoFocus placeholder={t("Search agents, extensions, runs…")} /><button onClick={() => setCommandOpen(false)}><X size={16} /></button></div>
            <p className="command-label">{t("Go to")}</p>
            {nav.map((item) => <button key={item.id} className="command-item" onClick={() => navigate(item.id)}><item.icon size={16} /><span>{t(item.label)}</span><small>{t("Open {label}", { label: t(item.label) })}</small></button>)}
          </div>
        </div>
      )}
    </main>
  );
}
