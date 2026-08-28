"use client";

import type { Extension, ExtensionInspection } from "@alcuin/contracts";
import {
  Blocks, Check, ChevronRight, CircleGauge, Download, FileJson2, Globe2,
  KeyRound, Network, Plus, Search, ShieldCheck, Terminal, Wrench, X,
} from "lucide-react";
import { clsx } from "clsx";
import { useMemo, useState } from "react";

import { StatusPill, Toast } from "@/shared/components/ui";
import { alcuinApi } from "@/shared/lib/api";
import { useI18n, type MessageKey } from "@/shared/lib/i18n";

type ConnectorKind = "mcp" | "openapi" | "manifest";
type SheetStage = "configure" | "review" | "manage";
type HealthReport = { status: "healthy" | "degraded" | "unhealthy"; details: Record<string, unknown> };

const lifecycleSteps = ["Inspect", "Review", "Install disabled", "Bind credentials", "Health check", "Enable"] satisfies MessageKey[];
const initialMcp = { name: "", extensionId: "", description: "", transport: "stdio" as "stdio" | "sse" | "streamable_http", command: "", args: "[]", cwd: "", url: "" };
const initialOpenApi = { name: "", extensionId: "", auth: "none" as "none" | "api_key" | "bearer", source: "paste" as "paste" | "url", specText: "", specUrl: "", baseUrl: "" };

function connectorType(extension: Extension): MessageKey {
  const type = extension.manifest.entrypoints[0]?.type;
  return type === "builtin" ? "Built-in" : type === "mcp" ? "MCP" : type === "openapi" ? "OpenAPI" : "Manifest";
}

export function ExtensionsView({ extensions, onChanged }: { extensions: Extension[]; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState("");
  const [protocol, setProtocol] = useState("All");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [connector, setConnector] = useState<ConnectorKind>("mcp");
  const [stage, setStage] = useState<SheetStage>("configure");
  const [inspection, setInspection] = useState<ExtensionInspection | null>(null);
  const [installed, setInstalled] = useState<Extension | null>(null);
  const [healthReport, setHealthReport] = useState<HealthReport | null>(null);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [selectionDirty, setSelectionDirty] = useState(false);
  const [credentialRefs, setCredentialRefs] = useState<Record<string, string>>({});
  const [mcp, setMcp] = useState(initialMcp);
  const [openapi, setOpenapi] = useState(initialOpenApi);
  const [manifestText, setManifestText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 2200); };

  const visible = useMemo(() => extensions.filter((extension) => {
    const matchesText = extension.name.toLowerCase().includes(filter.toLowerCase());
    return matchesText && (protocol === "All" || connectorType(extension) === protocol);
  }), [extensions, filter, protocol]);

  const currentStep = stage === "configure" ? 1 : stage === "review" ? 2 : installed?.status === "enabled" ? 6 : installed?.health === "healthy" || installed?.health === "degraded" ? 5 : Object.keys(installed?.credential_refs ?? {}).length ? 4 : 3;

  function openConnector() {
    setSheetOpen(true); setStage("configure"); setInspection(null); setInstalled(null);
    setHealthReport(null); setSelectedTools([]); setSelectionDirty(false);
    setCredentialRefs({}); setError(null);
  }

  function configureExtension(extension: Extension) {
    setSheetOpen(true); setStage("manage"); setInstalled(extension);
    setInspection({
      valid: true,
      manifest: extension.manifest,
      permission_summary: {
        total: extension.manifest.permissions.length,
        high_risk: extension.manifest.permissions.filter((item) => item.risk === "high").map((item) => item.id),
        requires_review: extension.manifest.permissions.some((item) => item.risk === "high"),
      },
      entrypoints: extension.manifest.entrypoints,
      install_state: "installed",
    });
    setCredentialRefs(extension.credential_refs); setHealthReport(null); setError(null);
  }

  async function inspectConnector(selected?: string[]) {
    if (connector === "mcp") {
      let args: string[];
      try {
        const parsed = JSON.parse(mcp.args || "[]");
        if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) throw new Error();
        args = parsed;
      } catch { throw new Error(t("MCP arguments must be a JSON array of strings.")); }
      return alcuinApi.importMcpExtension({
        name: mcp.name, extension_id: mcp.extensionId, description: mcp.description,
        selected_tools: selected,
        entrypoint: mcp.transport === "stdio"
          ? { type: "mcp", transport: "stdio", command: mcp.command, args, ...(mcp.cwd ? { cwd: mcp.cwd } : {}) }
          : { type: "mcp", transport: mcp.transport, url: mcp.url },
      });
    }
    if (connector === "openapi") {
      return alcuinApi.importOpenApiExtension({
        name: openapi.name, extension_id: openapi.extensionId, auth: openapi.auth,
        selected_operations: selected,
        ...(openapi.source === "url" ? { spec_url: openapi.specUrl } : { spec_text: openapi.specText }),
        ...(openapi.baseUrl ? { base_url: openapi.baseUrl } : {}),
      });
    }
    let manifest: Record<string, unknown>;
    try { manifest = JSON.parse(manifestText); }
    catch { throw new Error(t("Manifest must be valid JSON.")); }
    return alcuinApi.inspectExtensionManifest(manifest);
  }

  async function runInspection(selected?: string[]) {
    setBusy(true); setError(null);
    try {
      const result = await inspectConnector(selected);
      setInspection(result);
      setSelectedTools(result.manifest.contributions.tools.map((tool) => String(tool.name)));
      setSelectionDirty(false); setStage("review");
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Inspection failed")); }
    finally { setBusy(false); }
  }

  async function installDisabled() {
    if (!inspection || selectionDirty) return;
    setBusy(true); setError(null);
    try {
      const extension = await alcuinApi.installExtension(inspection.manifest);
      setInstalled(extension); setCredentialRefs(extension.credential_refs); setStage("manage");
      await onChanged(); notify(t("Extension installed disabled"));
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Installation failed")); }
    finally { setBusy(false); }
  }

  async function bindCredentials() {
    if (!installed) return;
    setBusy(true); setError(null);
    try {
      const updated = await alcuinApi.bindExtensionCredentials(installed.id, credentialRefs);
      setInstalled(updated); setHealthReport(null); await onChanged(); notify(t("Credential references bound"));
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Credential binding failed")); }
    finally { setBusy(false); }
  }

  async function runHealthCheck() {
    if (!installed) return;
    setBusy(true); setError(null);
    try {
      const report = await alcuinApi.healthExtension(installed.id) as HealthReport;
      setHealthReport(report); setInstalled({ ...installed, health: report.status }); await onChanged();
      if (report.status !== "healthy" && report.status !== "degraded") setError(String(report.details.message ?? t("Health check failed")));
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Health check failed")); }
    finally { setBusy(false); }
  }

  async function enableInstalled() {
    if (!installed) return;
    setBusy(true); setError(null);
    try {
      const updated = await alcuinApi.setExtension(installed.id, true) as Extension;
      setInstalled(updated); await onChanged(); notify(t("Extension enabled"));
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Enable failed")); }
    finally { setBusy(false); }
  }

  async function toggle(extension: Extension) {
    try {
      if (extension.health === "unchecked") await alcuinApi.healthExtension(extension.id);
      await alcuinApi.setExtension(extension.id, extension.status !== "enabled");
      await onChanged(); notify(t(extension.status === "enabled" ? "Extension disabled" : "Extension enabled"));
    } catch (reason) { notify(reason instanceof Error ? reason.message : t("Extension update failed")); }
  }

  const credentialRequirements = inspection?.manifest.credential_requirements ?? [];
  const hasBoundCredentials = credentialRequirements.every((requirement) => !requirement.required || Boolean(credentialRefs[String(requirement.id)]));

  return <div className="wide-surface extensions-surface">
    <header className="wide-header"><div><div className="eyebrow">{t("Capability registry")}</div><h1>{t("Extensions")}</h1><p>{t("Bring tools, knowledge and specialized behavior into Alcuin through explicit contracts.")}</p></div><div className="header-actions"><button className="button secondary"><Globe2 size={14} />{t("Browse registry")}</button><button className="button dark" onClick={openConnector}><Plus size={14} />{t("Connect capability")}</button></div></header>
    <div className="metric-strip"><div><span>{t("Installed")}</span><strong>{extensions.length}</strong><small>{t("this workspace")}</small></div><div><span>{t("Available tools")}</span><strong>{extensions.reduce((sum, extension) => sum + extension.manifest.contributions.tools.length, 0)}</strong><small>{t("normalized contracts")}</small></div><div><span>{t("Permission posture")}</span><strong>{t("Governed")}</strong><small>{t("write actions require approval")}</small></div><div><span>{t("Runtime health")}</span><strong className={clsx("healthy-text", extensions.some((item) => item.status === "enabled" && item.health === "unhealthy") && "unhealthy-text")}><i />{t(extensions.some((item) => item.status === "enabled" && item.health === "unhealthy") ? "Attention" : "Operational")}</strong><small>{t("enabled adapters")}</small></div></div>
    <div className="extensions-toolbar"><div className="filter-input"><Search size={14} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t("Filter extensions…")} /></div><div className="protocol-filter">{(["All", "MCP", "OpenAPI", "Built-in"] satisfies MessageKey[]).map((item) => <button key={item} className={clsx(protocol === item && "active")} onClick={() => setProtocol(item)}>{t(item)}</button>)}</div></div>
    <div className="extension-grid">
      {visible.map((extension) => <article className="extension-card" key={extension.id}><div className="extension-card-top"><span className="extension-logo ops"><Network size={20} /></span><div><StatusPill status={extension.status} /><StatusPill status={extension.health} /></div></div><h2>{extension.name}</h2><p>{extension.manifest.description}</p><div className="extension-tags"><span>{t(connectorType(extension))}</span><span>v{extension.version}</span></div><div className="extension-stats"><span><Wrench size={13} />{t("{count} tools", { count: extension.manifest.contributions.tools.length })}</span><span><Blocks size={13} />{t("{count} UI blocks", { count: extension.manifest.contributions.ui_blocks.length })}</span><span><ShieldCheck size={13} />{t("{count} permissions", { count: extension.manifest.permissions.length })}</span></div><div className="extension-card-footer"><button className="text-button" onClick={() => configureExtension(extension)}>{t("Configure")}<ChevronRight size={13} /></button><button className={clsx("toggle", extension.status === "enabled" && "on")} onClick={() => void toggle(extension)} aria-label={t("Toggle {name}", { name: extension.name })}><i /></button></div></article>)}
      <button className="extension-card extension-add" onClick={openConnector}><span className="add-ring"><Plus size={20} /></span><h2>{t("Connect a capability")}</h2><p>{t("Inspect an Alcuin Manifest, MCP server, or OpenAPI specification.")}</p><div className="connect-options"><span><Terminal size={13} />MCP</span><span><Globe2 size={13} />OpenAPI</span><span><Blocks size={13} />{t("Manifest")}</span></div></button>
    </div>
    <section className="lifecycle-panel"><div><span className="section-icon"><CircleGauge size={18} /></span><div><h2>{t("Installation is an explicit trust decision")}</h2><p>{t("Extensions never receive runtime access silently.")}</p></div></div><div className="lifecycle-steps">{lifecycleSteps.map((step, index) => <div key={step}><span>{index + 1}</span><strong>{t(step)}</strong>{index < 5 && <ChevronRight size={13} />}</div>)}</div></section>

    {sheetOpen && <div className="sheet-backdrop" onMouseDown={() => !busy && setSheetOpen(false)}><aside className="inspect-sheet extension-wizard" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="extension-logo">{connector === "mcp" ? <Terminal size={18} /> : connector === "openapi" ? <Globe2 size={18} /> : <FileJson2 size={18} />}</span><div><small>{t(stage === "configure" ? "Connect capability" : stage === "review" ? "Permission review" : "Installed extension")}</small><h2>{inspection?.manifest.name ?? installed?.name ?? t("New extension")}</h2></div></div><button className="icon-button quiet" onClick={() => setSheetOpen(false)} disabled={busy}><X size={16} /></button></header>
      <div className="wizard-progress">{lifecycleSteps.map((item, index) => <div key={item} className={clsx(index + 1 < currentStep && "complete", index + 1 === currentStep && "active")}><span>{index + 1 < currentStep ? <Check size={11} /> : index + 1}</span><small>{t(item)}</small></div>)}</div>
      {stage === "configure" && <div className="wizard-body"><div className="connector-tabs">{(["mcp", "openapi", "manifest"] as ConnectorKind[]).map((item) => <button key={item} className={clsx(connector === item && "active")} onClick={() => setConnector(item)}>{item === "mcp" ? <Terminal size={13} /> : item === "openapi" ? <Globe2 size={13} /> : <FileJson2 size={13} />}{item === "openapi" ? "OpenAPI" : item.toUpperCase()}</button>)}</div>
        {connector === "mcp" && <div className="wizard-form"><div className="knowledge-form-grid"><label className="field"><span>{t("Name")}</span><input value={mcp.name} onChange={(event) => setMcp({ ...mcp, name: event.target.value })} placeholder={t("Filesystem MCP")} /></label><label className="field"><span>{t("Extension ID")}</span><input value={mcp.extensionId} onChange={(event) => setMcp({ ...mcp, extensionId: event.target.value })} placeholder="acme.filesystem" /></label></div><label className="field"><span>{t("Description")}</span><input value={mcp.description} onChange={(event) => setMcp({ ...mcp, description: event.target.value })} placeholder={t("Governed access to project files")} /></label><label className="field"><span>{t("Transport")}</span><select value={mcp.transport} onChange={(event) => setMcp({ ...mcp, transport: event.target.value as typeof mcp.transport })}><option value="stdio">{t("Local stdio")}</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label>{mcp.transport === "stdio" ? <><div className="knowledge-form-grid"><label className="field"><span>{t("Command")}</span><input value={mcp.command} onChange={(event) => setMcp({ ...mcp, command: event.target.value })} placeholder="npx" /></label><label className="field"><span>{t("Working directory")} <small>{t("Optional")}</small></span><input value={mcp.cwd} onChange={(event) => setMcp({ ...mcp, cwd: event.target.value })} placeholder="/project" /></label></div><label className="field"><span>{t("Arguments · JSON array")}</span><textarea rows={4} className="code-editor" value={mcp.args} onChange={(event) => setMcp({ ...mcp, args: event.target.value })} /></label></> : <label className="field"><span>{t("Server URL")}</span><input value={mcp.url} onChange={(event) => setMcp({ ...mcp, url: event.target.value })} placeholder="https://mcp.example.com/mcp" /></label>}</div>}
        {connector === "openapi" && <div className="wizard-form"><div className="knowledge-form-grid"><label className="field"><span>{t("Name")}</span><input value={openapi.name} onChange={(event) => setOpenapi({ ...openapi, name: event.target.value })} placeholder={t("Records API")} /></label><label className="field"><span>{t("Extension ID")}</span><input value={openapi.extensionId} onChange={(event) => setOpenapi({ ...openapi, extensionId: event.target.value })} placeholder="acme.records" /></label></div><div className="knowledge-form-grid"><label className="field"><span>{t("Authentication")}</span><select value={openapi.auth} onChange={(event) => setOpenapi({ ...openapi, auth: event.target.value as typeof openapi.auth })}><option value="none">{t("None")}</option><option value="api_key">{t("API Key")}</option><option value="bearer">{t("Bearer token")}</option></select></label><label className="field"><span>{t("Base URL override")} <small>{t("Optional")}</small></span><input value={openapi.baseUrl} onChange={(event) => setOpenapi({ ...openapi, baseUrl: event.target.value })} placeholder="https://api.example.com/v1" /></label></div><div className="knowledge-import-mode"><button className={clsx(openapi.source === "paste" && "active")} onClick={() => setOpenapi({ ...openapi, source: "paste" })}>{t("File or paste")}</button><button className={clsx(openapi.source === "url" && "active")} onClick={() => setOpenapi({ ...openapi, source: "url" })}>{t("Specification URL")}</button></div>{openapi.source === "url" ? <label className="field"><span>{t("OpenAPI URL")}</span><input value={openapi.specUrl} onChange={(event) => setOpenapi({ ...openapi, specUrl: event.target.value })} placeholder="https://api.example.com/openapi.yaml" /></label> : <><label className="openapi-file"><input type="file" accept=".json,.yaml,.yml,application/json,application/yaml,text/yaml" onChange={(event) => { const file = event.target.files?.[0]; if (file) void file.text().then((specText) => setOpenapi({ ...openapi, specText })); }} /><Download size={13} />{t("Load JSON or YAML file")}</label><label className="field"><span>{t("Specification")}</span><textarea rows={9} className="code-editor" value={openapi.specText} onChange={(event) => setOpenapi({ ...openapi, specText: event.target.value })} placeholder="openapi: 3.1.0…" /></label></>}</div>}
        {connector === "manifest" && <label className="field"><span>{t("Alcuin Extension Manifest")}</span><textarea rows={15} className="code-editor" value={manifestText} onChange={(event) => setManifestText(event.target.value)} placeholder={'{"manifest_version":"1",…}'} /></label>}
      </div>}
      {stage === "review" && inspection && <div className="wizard-body review-body"><div className="inspect-valid"><Check size={16} /><div><strong>{t("Contract is valid")}</strong><p>{t("{tools} tools · {blocks} UI blocks · compatible {compatibility}", { tools: inspection.manifest.contributions.tools.length, blocks: inspection.manifest.contributions.ui_blocks.length, compatibility: inspection.manifest.compatibility })}</p></div></div><div className="review-section"><div className="review-heading"><h3>{t("Contributed tools")}</h3><small>{t("Select the operations this Workspace may install.")}</small></div>{inspection.manifest.contributions.tools.map((tool) => { const name = String(tool.name); const selected = selectedTools.includes(name); return <label className="tool-selection" key={name}><input type="checkbox" checked={selected} onChange={() => { setSelectedTools(selected ? selectedTools.filter((item) => item !== name) : [...selectedTools, name]); setSelectionDirty(true); }} /><span><strong>{name}</strong><small>{String(tool.description ?? t("No description"))}</small></span><em>{t(tool.mutating ? "write · approval" : "read only")}</em></label>; })}</div><div className="review-section"><div className="review-heading"><h3>{t("Requested permissions")}</h3><small>{t("High-risk permissions require an explicit decision.")}</small></div>{inspection.manifest.permissions.map((permission) => <div className="permission-row" key={permission.id}><ShieldCheck size={16} /><div><strong>{permission.id}</strong><p>{permission.reason}</p></div><span>{permission.risk}</span></div>)}</div><div className="review-section"><div className="review-heading"><h3>{t("Runtime entrypoint")}</h3></div><pre>{JSON.stringify(inspection.manifest.entrypoints[0], null, 2)}</pre></div></div>}
      {stage === "manage" && installed && inspection && <div className="wizard-body manage-body"><div className="installed-summary"><span className="extension-logo ops"><Network size={19} /></span><div><strong>{installed.name}</strong><p>{t(connectorType(installed))} · v{installed.version} · {t("{count} tools", { count: installed.manifest.contributions.tools.length })} · {t("{count} UI blocks", { count: installed.manifest.contributions.ui_blocks.length })}</p></div><StatusPill status={installed.status} /><StatusPill status={installed.health} /></div>{credentialRequirements.length > 0 && <div className="review-section"><div className="review-heading"><h3>{t("Credential references")}</h3><small>{t("Only secret:// references are stored. Raw values stay outside definitions and events.")}</small></div>{credentialRequirements.map((requirement) => <label className="field" key={String(requirement.id)}><span>{String(requirement.id)} · {String(requirement.type)}</span><div className="secret-input"><KeyRound size={14} /><input value={credentialRefs[String(requirement.id)] ?? ""} onChange={(event) => setCredentialRefs({ ...credentialRefs, [String(requirement.id)]: event.target.value })} placeholder="secret://workspace/service-primary" /></div></label>)}<button className="button secondary compact-button" onClick={() => void bindCredentials()} disabled={busy || !hasBoundCredentials}>{t("Bind references")}</button></div>}<div className="review-section"><div className="review-heading"><h3>{t("Health verification")}</h3><small>{t("MCP performs live tool discovery. OpenAPI validates credentials and endpoint reachability.")}</small></div>{healthReport ? <div className={clsx("health-report", healthReport.status)}><CircleGauge size={16} /><div><StatusPill status={healthReport.status} /><p>{String(healthReport.details.message ?? t(healthReport.details.reachable ? "Endpoint reachable and contract ready." : "Adapter check completed."))}</p></div></div> : <div className="health-report unchecked"><CircleGauge size={16} /><div><strong>{t("Not checked")}</strong><p>{t("Run a live check before enabling this extension.")}</p></div></div>}</div></div>}
      {error && <div className="wizard-error">{error}</div>}
      <footer><button className="button secondary" onClick={() => setSheetOpen(false)} disabled={busy}>{t(installed?.status === "enabled" ? "Done" : "Cancel")}</button>{stage === "configure" && <button className="button dark" onClick={() => void runInspection()} disabled={busy}>{busy ? <span className="micro-loader" /> : <Search size={14} />}{t("Inspect connection")}</button>}{stage === "review" && selectionDirty && <button className="button secondary" onClick={() => void runInspection(selectedTools)} disabled={busy || !selectedTools.length}><ShieldCheck size={14} />{t("Review selected permissions")}</button>}{stage === "review" && !selectionDirty && <button className="button dark" onClick={() => void installDisabled()} disabled={busy || !selectedTools.length}><Download size={14} />{t("Install disabled")}</button>}{stage === "manage" && installed?.status !== "enabled" && installed?.health !== "healthy" && installed?.health !== "degraded" && <button className="button dark" onClick={() => void runHealthCheck()} disabled={busy || !hasBoundCredentials}><CircleGauge size={14} />{t("Run health check")}</button>}{stage === "manage" && installed?.status !== "enabled" && (installed?.health === "healthy" || installed?.health === "degraded") && <button className="button dark" onClick={() => void enableInstalled()} disabled={busy}><Check size={14} />{t("Enable extension")}</button>}</footer>
    </aside></div>}
    {toast && <Toast message={toast} />}
  </div>;
}
