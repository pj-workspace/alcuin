"use client";

import type { Extension, ExtensionInspection } from "@alcuin/contracts";
import { Blocks, Check, ChevronRight, CircleGauge, Download, Globe2, Network, Plus, Search, ShieldCheck, Terminal, Wrench, X } from "lucide-react";
import { useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/lib/api";
import { StatusPill, Toast } from "@/components/ui";

export function ExtensionsView({ extensions, onChanged }: { extensions: Extension[]; onChanged: () => Promise<void> }) {
  const [filter, setFilter] = useState("");
  const [inspect, setInspect] = useState<ExtensionInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 2200); };
  const visible = extensions.filter((extension) => extension.name.toLowerCase().includes(filter.toLowerCase()));

  async function inspectMcp() {
    setInspecting(true);
    try { setInspect(await alcuinApi.inspectSampleMcp()); }
    catch (error) { notify(error instanceof Error ? error.message : "Inspection failed"); }
    finally { setInspecting(false); }
  }

  async function toggle(extension: Extension) {
    try {
      if (extension.health === "unchecked") await alcuinApi.healthExtension(extension.id);
      await alcuinApi.setExtension(extension.id, extension.status !== "enabled");
      await onChanged();
      notify(extension.status === "enabled" ? "Extension disabled" : "Extension enabled");
    } catch (error) { notify(error instanceof Error ? error.message : "Extension update failed"); }
  }

  return <div className="wide-surface extensions-surface">
    <header className="wide-header"><div><div className="eyebrow">Capability registry</div><h1>Extensions</h1><p>Bring tools, knowledge and specialized behavior into Alcuin through explicit contracts.</p></div><div className="header-actions"><button className="button secondary"><Globe2 size={14} />Browse registry</button><button className="button dark" onClick={() => void inspectMcp()}><Plus size={14} />Inspect extension</button></div></header>
    <div className="metric-strip"><div><span>Installed</span><strong>{extensions.length}</strong><small>this workspace</small></div><div><span>Available tools</span><strong>{extensions.reduce((sum, extension) => sum + extension.manifest.contributions.tools.length, 0)}</strong><small>normalized contracts</small></div><div><span>Permission posture</span><strong>Governed</strong><small>write actions require approval</small></div><div><span>Runtime health</span><strong className="healthy-text"><i />Operational</strong><small>all active adapters</small></div></div>
    <div className="extensions-toolbar"><div className="filter-input"><Search size={14} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter extensions…" /></div><div className="protocol-filter"><button className="active">All</button><button>MCP</button><button>OpenAPI</button><button>Built-in</button></div></div>
    <div className="extension-grid">
      {visible.map((extension) => <article className="extension-card" key={extension.id}>
        <div className="extension-card-top"><span className="extension-logo ops"><Network size={20} /></span><div><StatusPill status={extension.status} /><StatusPill status={extension.health} /></div></div>
        <h2>{extension.name}</h2><p>{extension.manifest.description}</p>
        <div className="extension-tags"><span>{extension.manifest.entrypoints[0]?.type === "builtin" ? "Native" : String(extension.manifest.entrypoints[0]?.type ?? "Manifest").toUpperCase()}</span><span>v{extension.version}</span></div>
        <div className="extension-stats"><span><Wrench size={13} />{extension.manifest.contributions.tools.length} tools</span><span><ShieldCheck size={13} />{extension.manifest.permissions.length} permissions</span></div>
        <div className="extension-card-footer"><button className="text-button">Configure<ChevronRight size={13} /></button><button className={clsx("toggle", extension.status === "enabled" && "on")} onClick={() => void toggle(extension)} aria-label={`Toggle ${extension.name}`}><i /></button></div>
      </article>)}
      <article className="extension-card extension-add" onClick={() => void inspectMcp()}><span className="add-ring"><Plus size={20} /></span><h2>Connect a capability</h2><p>Inspect an Alcuin Manifest, MCP server, or OpenAPI specification.</p><div className="connect-options"><span><Terminal size={13} />MCP</span><span><Globe2 size={13} />OpenAPI</span><span><Blocks size={13} />Manifest</span></div></article>
    </div>
    <section className="lifecycle-panel"><div><span className="section-icon"><CircleGauge size={18} /></span><div><h2>Installation is an explicit trust decision</h2><p>Extensions never receive runtime access silently.</p></div></div><div className="lifecycle-steps">{["Inspect", "Review permissions", "Install disabled", "Bind credentials", "Health check", "Enable"].map((step, index) => <div key={step}><span>{index + 1}</span><strong>{step}</strong>{index < 5 && <ChevronRight size={13} />}</div>)}</div></section>
    {inspect && <div className="sheet-backdrop" onMouseDown={() => setInspect(null)}><aside className="inspect-sheet" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="extension-logo"><Terminal size={18} /></span><div><small>Extension inspection</small><h2>{inspect.manifest.name}</h2></div></div><button className="icon-button quiet" onClick={() => setInspect(null)}><X size={16} /></button></header><div className="inspect-valid"><Check size={16} /><div><strong>Manifest is valid</strong><p>Compatible with Alcuin {inspect.manifest.compatibility}</p></div></div><h3>Requested permissions</h3>{inspect.manifest.permissions.map((permission) => <div className="permission-row" key={permission.id}><ShieldCheck size={16} /><div><strong>{permission.id}</strong><p>{permission.reason}</p></div><span>{permission.risk}</span></div>)}<h3>Runtime entrypoint</h3><pre>{JSON.stringify(inspect.manifest.entrypoints[0], null, 2)}</pre><footer><button className="button secondary" onClick={() => setInspect(null)}>Cancel</button><button className="button dark" onClick={() => notify("Inspection complete — install API ready")}><Download size={14} />Install disabled</button></footer></aside></div>}
    {inspecting && <div className="inspecting"><span className="micro-loader" />Inspecting manifest…</div>}
    {toast && <Toast message={toast} />}
  </div>;
}
