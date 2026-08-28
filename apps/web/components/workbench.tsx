"use client";

import type { BootstrapPayload, ExecutionEvent } from "@alcuin/contracts";
import {
  Blocks,
  Bot,
  Braces,
  ChevronDown,
  Command,
  GalleryVerticalEnd,
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

export type Surface = "studio" | "agents" | "extensions" | "runs" | "embed";

const nav = [
  { id: "studio", label: "Studio", icon: GalleryVerticalEnd },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "extensions", label: "Extensions", icon: Blocks },
  { id: "runs", label: "Runs", icon: Workflow },
  { id: "embed", label: "Embed", icon: Braces },
] satisfies Array<{ id: Surface; label: string; icon: typeof Bot }>;

export function Workbench({ surface }: { surface: Surface }) {
  const pathname = usePathname();
  const router = useRouter();
  const [data, setData] = useState<BootstrapPayload | null>(null);
  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [commandOpen, setCommandOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  const refresh = useCallback(async () => {
    try {
      const bootstrap = await alcuinApi.bootstrap();
      setData(bootstrap);
      setError(null);
      if (bootstrap.runs[0]) {
        const run = await alcuinApi.getRun(bootstrap.runs[0].id);
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
      if (event.key === "Escape") setCommandOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const activeAgent = data?.agents[0];
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
  const view = useMemo(() => {
    if (!data) return null;
    switch (surface) {
      case "agents": return <AgentBuilderView agent={activeAgent} onChanged={refresh} />;
      case "extensions": return <ExtensionsView extensions={data.extensions} onChanged={refresh} />;
      case "runs": return <RunsView runs={data.runs} events={events} onSelectEvents={setEvents} />;
      case "embed": return <EmbedView agent={activeAgent} />;
      default: return (
        <StudioView
          workspace={data.workspace}
          agent={activeAgent}
          initialEvents={events}
          onRunCreated={refresh}
        />
      );
    }
  }, [activeAgent, data, events, refresh, surface]);

  return (
    <main className="app-frame">
      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-button quiet" onClick={() => setSidebarOpen((open) => !open)} aria-label="Toggle sidebar">
            {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
          </button>
          <Link className="brand" href="/studio"><AlcuinMark size={28} /><span>Alcuin</span></Link>
          <span className="topbar-separator" />
          <button className="workspace-switcher"><span className="workspace-glyph">N</span>{data?.workspace.name ?? "Workspace"}<ChevronDown size={13} /></button>
        </div>
        <div className="topbar-actions">
          <button className="command-trigger" onClick={() => setCommandOpen(true)}><Search size={14} /><span>Search or jump to</span><kbd>⌘ K</kbd></button>
          <button className="icon-button quiet" onClick={switchTheme} aria-label="Toggle theme">{theme === "light" ? <Moon size={16} /> : <Sun size={16} />}</button>
          <button className="icon-button quiet" aria-label="Settings"><Settings2 size={16} /></button>
          <div className="avatar">PJ</div>
        </div>
      </header>

      <div className="workspace-frame">
        <aside className={clsx("sidebar", !sidebarOpen && "sidebar-collapsed")}>
          <nav className="primary-nav" aria-label="Primary">
            {nav.map((item) => (
              <Link key={item.id} href={`/${item.id}`} className={clsx("nav-item", pathname === `/${item.id}` && "active")} title={item.label}>
                <item.icon size={16} /><span>{item.label}</span>
              </Link>
            ))}
          </nav>
          <div className="sidebar-section">
            <div className="sidebar-heading"><span>Agents</span><button aria-label="New agent"><Plus size={14} /></button></div>
            {data?.agents.map((agent) => (
              <Link href="/agents" className="resource-row active-resource" key={agent.id}>
                <span className="agent-glyph"><Command size={13} /></span>
                <span><strong>{agent.name}</strong><small>v{agent.version} · {agent.status}</small></span>
              </Link>
            ))}
          </div>
          <div className="sidebar-section threads-section">
            <div className="sidebar-heading"><span>Recent threads</span></div>
            {data?.threads.slice(0, 4).map((thread) => (
              <Link href="/studio" className="thread-row" key={thread.id}><Play size={11} fill="currentColor" /><span>{thread.title}</span></Link>
            ))}
          </div>
          <div className="sidebar-footer"><span className="runtime-dot" />API connected <span className="version-label">pre-alpha</span></div>
        </aside>

        <section className="surface">
          {loading && <div className="loading-state"><AlcuinMark className="pulse" size={44} /><p>Composing workspace…</p></div>}
          {!loading && error && <div className="connection-error"><span className="mini-mark">!</span><h2>Alcuin API is offline</h2><p>{error}</p><code>pnpm dev:api</code><button onClick={() => void refresh()}>Try again</button></div>}
          {!loading && !error && view}
        </section>
      </div>

      {commandOpen && (
        <div className="command-backdrop" onMouseDown={() => setCommandOpen(false)}>
          <div className="command-menu" onMouseDown={(event) => event.stopPropagation()}>
            <div className="command-input"><Search size={17} /><input autoFocus placeholder="Search agents, extensions, runs…" /><button onClick={() => setCommandOpen(false)}><X size={16} /></button></div>
            <p className="command-label">Go to</p>
            {nav.map((item) => <button key={item.id} className="command-item" onClick={() => navigate(item.id)}><item.icon size={16} /><span>{item.label}</span><small>Open {item.label}</small></button>)}
          </div>
        </div>
      )}
    </main>
  );
}
