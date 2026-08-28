"use client";

import type { Agent, Extension, KnowledgeSource } from "@alcuin/contracts";
import {
  Blocks,
  Bot,
  BrainCircuit,
  Check,
  ChevronRight,
  Database,
  FileText,
  Eye,
  FileJson2,
  LockKeyhole,
  Play,
  Plus,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/lib/api";
import { StatusPill, Toast } from "@/components/ui";
import { useI18n, type MessageKey } from "@/lib/i18n";

const sections = [
  { id: "identity", label: "Identity", icon: Bot },
  { id: "instructions", label: "Instructions", icon: FileJson2 },
  { id: "model", label: "Model", icon: BrainCircuit },
  { id: "capabilities", label: "Capabilities", icon: Wrench },
  { id: "knowledge", label: "Knowledge", icon: Database },
  { id: "policies", label: "Policies", icon: ShieldCheck },
  { id: "output", label: "Output", icon: Sparkles },
] satisfies Array<{ id: string; label: MessageKey; icon: typeof Bot }>;

export function AgentBuilderView({
  agent,
  agents,
  knowledgeSources,
  extensions,
  onChanged,
  onCreateAgent,
  onSelectAgent,
}: {
  agent?: Agent;
  agents: Agent[];
  knowledgeSources: KnowledgeSource[];
  extensions: Extension[];
  onChanged: () => Promise<void>;
  onCreateAgent: () => void;
  onSelectAgent: (agentId: string) => void;
}) {
  const { t } = useI18n();
  const [active, setActive] = useState("identity");
  const [definition, setDefinition] = useState(agent?.definition);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const completeness = useMemo(() => {
    if (!definition) return 0;
    return [definition.identity.name, definition.instructions, definition.model.model, definition.tools.length, definition.output_schema].filter(Boolean).length * 20;
  }, [definition]);

  if (!agent || !definition) return null;
  const currentAgent = agent;
  const currentDefinition = definition;
  const updateIdentity = (field: "name" | "description", value: string) => setDefinition({ ...definition, identity: { ...definition.identity, [field]: value } });
  const updateProvider = (provider: string) => setDefinition({
    ...definition,
    model: provider === "deepseek"
      ? { provider, model: "deepseek-v4-flash-vision-exp", credential_ref: "secret://workspace/deepseek-primary" }
      : { provider, model: "gpt-4.1-mini", credential_ref: "secret://workspace/openai-primary" },
  });
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 2200); };
  const toggleKnowledge = (sourceId: string) => {
    const isBound = definition.knowledge.includes(sourceId);
    const knowledge = isBound
      ? definition.knowledge.filter((item) => item !== sourceId)
      : [...definition.knowledge, sourceId];
    const tools = knowledge.length
      ? Array.from(new Set([...definition.tools, "knowledge.search"]))
      : definition.tools.filter((tool) => tool !== "knowledge.search");
    setDefinition({ ...definition, knowledge, tools });
  };

  async function save(publish = false) {
    setSaving(true);
    try {
      await alcuinApi.saveAgent(currentAgent.id, currentDefinition);
      if (publish) await alcuinApi.publishAgent(currentAgent.id);
      notify(t(publish ? "Agent version published" : "Draft version saved"));
      await onChanged();
    } catch (error) {
      notify(error instanceof Error ? error.message : t("Unable to save agent"));
    } finally {
      setSaving(false);
    }
  }

  return <div className="builder-surface">
    <header className="wide-header">
      <div><div className="breadcrumbs"><span>{t("Agents")}</span><ChevronRight size={12} /><label className="builder-agent-switch"><select aria-label={t("Select agent")} value={agent.id} onChange={(event) => onSelectAgent(event.target.value)}>{agents.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select><ChevronRight size={11} /></label></div><div className="title-row"><h1>{t("Agent Builder")}</h1><StatusPill status={agent.status} /><span className="version-badge">v{agent.version}</span></div><p>{t("Compose behavior from stable, versioned capabilities.")}</p></div>
      <div className="header-actions"><button className="button secondary" onClick={onCreateAgent}><Plus size={14} />{t("New agent")}</button><button className="button secondary"><Eye size={14} />{t("Preview")}</button><button className="button secondary" disabled={saving} onClick={() => void save()}><Save size={14} />{t("Save draft")}</button><button className="button dark" disabled={saving} onClick={() => void save(true)}><Play size={13} fill="currentColor" />{t("Publish version")}</button></div>
    </header>
    <div className="builder-body">
      <aside className="builder-nav">
        <p>{t("Definition")}</p>
        {sections.map((section) => <button key={section.id} onClick={() => setActive(section.id)} className={clsx(active === section.id && "active")}><section.icon size={15} /><span>{t(section.label)}</span>{section.id === "capabilities" && <small>{definition.tools.length}</small>}</button>)}
        <div className="definition-score"><div><span>{t("Definition health")}</span><strong>{completeness}%</strong></div><div className="score-track"><i style={{ width: `${completeness}%` }} /></div><p>{t("Ready to publish")}</p></div>
      </aside>
      <section className="definition-editor">
        <div className="editor-heading"><span className="section-icon"><Bot size={18} /></span><div><h2>{t(sections.find((item) => item.id === active)?.label ?? "Identity")}</h2><p>{t(sectionDescription(active))}</p></div></div>
        {active === "identity" && <div className="form-stack">
          <label className="field"><span>{t("Agent name")}</span><input value={definition.identity.name} onChange={(event) => updateIdentity("name", event.target.value)} /><small>{t("Shown in Studio and embedded experiences.")}</small></label>
          <label className="field"><span>{t("Description")}</span><textarea rows={3} value={definition.identity.description} onChange={(event) => updateIdentity("description", event.target.value)} /></label>
          <div className="field"><span>{t("Appearance")}</span><div className="appearance-row"><button className="agent-icon-choice active"><Bot size={19} /></button>{["#3157d5", "#596557", "#a45536", "#7656b6", "#252724"].map((color) => <button key={color} className="color-choice" style={{ backgroundColor: color }} aria-label={t("Color {color}", { color })} />)}</div></div>
        </div>}
        {active === "instructions" && <div className="form-stack"><label className="field"><span>{t("System instructions")}</span><textarea className="instruction-editor" rows={14} value={definition.instructions} onChange={(event) => setDefinition({ ...definition, instructions: event.target.value })} /><small>{t("{count} / 20,000 characters", { count: definition.instructions.length.toLocaleString() })}</small></label><div className="editor-tip"><Sparkles size={15} /><span>{t("Keep domain behavior in extensions. The core definition should describe intent, policies, and output expectations.")}</span></div></div>}
        {active === "model" && <div className="form-stack">
          <label className="field">
            <span>{t("Provider adapter")}</span>
            <select value={definition.model.provider} onChange={(event) => updateProvider(event.target.value)}>
              <option value="deepseek">DeepSeek</option>
              <option value="openai-compatible">{t("Generic OpenAI-compatible")}</option>
            </select>
            <small>{t("Provider-specific protocols stay behind the stable AgentRuntime interface.")}</small>
          </label>
          <label className="field">
            <span>{t("Model identifier")}</span>
            {definition.model.provider === "deepseek" ? (
              <select value={definition.model.model} onChange={(event) => setDefinition({ ...definition, model: { ...definition.model, model: event.target.value } })}>
                <option value="deepseek-v4-flash-vision-exp">DeepSeek V4 Flash Vision (Experimental)</option>
                <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
                <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
              </select>
            ) : (
              <input value={definition.model.model} onChange={(event) => setDefinition({ ...definition, model: { ...definition.model, model: event.target.value } })} />
            )}
          </label>
          {definition.model.model === "deepseek-v4-flash-vision-exp" && (
            <div className="editor-tip"><Eye size={15} /><span>{t("Vision enabled · PNG, JPEG, WebP, or GIF · up to 4 images, 5 MiB each.")}</span></div>
          )}
          <label className="field">
            <span>{t("Credential reference")}</span>
            <div className="secret-input"><LockKeyhole size={14} /><input value={definition.model.credential_ref ?? ""} readOnly /></div>
            <small>{t("Paste the real key into .env; definitions store only this Secret Reference.")}</small>
          </label>
        </div>}
        {active === "capabilities" && <Capabilities definition={definition} extensions={extensions} onChange={setDefinition} />}
        {active === "knowledge" && (
          <KnowledgeEditor
            sources={knowledgeSources}
            boundSourceIds={definition.knowledge}
            onToggle={toggleKnowledge}
            onImported={async (sourceId) => {
              if (!definition.knowledge.includes(sourceId)) toggleKnowledge(sourceId);
              notify(t("Knowledge source indexed and bound"));
              await onChanged();
            }}
            onSourcesChanged={onChanged}
          />
        )}
        {active === "policies" && <div className="form-stack"><div className="policy-card"><ShieldCheck size={18} /><div><strong>{t("Mutating tools")}</strong><p>{t("External write operations pause the run and create an explicit approval event.")}</p></div><select value={definition.policies.mutating_tools} onChange={(event) => setDefinition({ ...definition, policies: { ...definition.policies, mutating_tools: event.target.value as "ask" | "deny" | "auto" } })}><option value="ask">{t("Ask every time")}</option><option value="deny">{t("Always deny")}</option><option value="auto">{t("Allow automatically")}</option></select></div><div className="policy-card"><LockKeyhole size={18} /><div><strong>{t("Sensitive values")}</strong><p>{t("Credentials remain scoped secret references and are redacted from execution events.")}</p></div><StatusPill status="enabled" /></div></div>}
        {active === "output" && <div className="form-stack"><label className="field"><span>{t("Output kind")}</span><select defaultValue="artifact"><option value="artifact">artifact</option><option value="structured data">{t("structured data")}</option><option value="message only">{t("message only")}</option></select></label><label className="field"><span>{t("Output schema")}</span><textarea className="code-editor" rows={8} value={JSON.stringify(definition.output_schema, null, 2)} readOnly /></label></div>}
        <div className="advanced-toggle"><button onClick={() => setAdvanced((value) => !value)}><Settings2 size={14} />{t("Advanced definition JSON")}<ChevronRight className={clsx(advanced && "rotate")} size={13} /></button>{advanced && <pre>{JSON.stringify(definition, null, 2)}</pre>}</div>
      </section>
      <aside className="builder-preview">
        <div className="preview-label">{t("Live identity preview")}</div><div className="agent-preview-card"><span className="preview-mark">A</span><h3>{definition.identity.name}</h3><p>{definition.identity.description}</p><div className="preview-meta"><span><BrainCircuit size={12} />{definition.model.model}</span><span><Blocks size={12} />{t("{count} extension", { count: definition.extensions.length })}</span></div><button><Sparkles size={14} />{t("Start a conversation")}</button></div>
        <div className="contract-note"><FileJson2 size={15} /><div><strong>{t("Versioned contract")}</strong><p>{t("Saving creates a new immutable version. Publishing makes it available to Embed sessions.")}</p></div></div>
      </aside>
    </div>
    {toast && <Toast message={toast} />}
  </div>;
}

function KnowledgeEditor({
  sources,
  boundSourceIds,
  onToggle,
  onImported,
  onSourcesChanged,
}: {
  sources: KnowledgeSource[];
  boundSourceIds: string[];
  onToggle: (sourceId: string) => void;
  onImported: (sourceId: string) => Promise<void>;
  onSourcesChanged: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [importOpen, setImportOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [importMode, setImportMode] = useState<"file" | "text">("file");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "creating" | "indexing">("idle");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ targetSourceId: "new", sourceName: "", description: "", title: "", content: "" });

  async function importDocument() {
    if (
      (form.targetSourceId === "new" && !form.sourceName.trim())
      || (importMode === "text" && (!form.title.trim() || !form.content.trim()))
      || (importMode === "file" && !selectedFile)
    ) {
      setError(t(importMode === "file"
        ? "Choose or name a source, then select a supported file."
        : "Choose or name a source, then provide a document title and content."));
      return;
    }
    if (selectedFile && selectedFile.size > 8 * 1024 * 1024) {
      setError(t("Files must be 8 MiB or smaller."));
      return;
    }
    setSubmitting(true);
    setError(null);
    let sourceId = form.targetSourceId;
    let createdSource = false;
    try {
      if (sourceId === "new") {
        setPhase("creating");
        const source = await alcuinApi.createKnowledgeSource(
          form.sourceName.trim(),
          form.description.trim(),
        );
        sourceId = source.id;
        createdSource = true;
      }
      setPhase("indexing");
      if (importMode === "file" && selectedFile) {
        await alcuinApi.uploadKnowledgeFile(sourceId, selectedFile, form.title);
      } else {
        await alcuinApi.ingestKnowledgeDocument(sourceId, {
          title: form.title.trim(),
          content: form.content,
          metadata: { imported_via: "agent-builder" },
        });
      }
      setForm({ targetSourceId: sourceId, sourceName: "", description: "", title: "", content: "" });
      setSelectedFile(null);
      setImportOpen(false);
      await onImported(sourceId);
    } catch (reason) {
      if (createdSource) {
        setForm((current) => ({ ...current, targetSourceId: sourceId }));
        await onSourcesChanged();
      }
      setError(reason instanceof Error ? reason.message : t("Unable to import knowledge"));
    } finally {
      setSubmitting(false);
      setPhase("idle");
    }
  }

  function selectFile(file: File | null) {
    setSelectedFile(file);
    setError(null);
    if (file && !form.title.trim()) {
      const inferredTitle = file.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
      setForm((current) => ({ ...current, title: inferredTitle }));
    }
  }

  return (
    <div className="knowledge-editor">
      <div className="capability-toolbar">
        <div>
          <strong>{t("Workspace sources")}</strong>
          <p>{t("Qwen dense + sparse hybrid retrieval. Agent versions store only source references.")}</p>
        </div>
        <button className="button secondary" onClick={() => setImportOpen((open) => !open)}>
          <Plus size={14} />{t("Import document")}
        </button>
      </div>

      {importOpen && (
        <div className="knowledge-import">
          <div className="knowledge-import-heading">
            <span className="section-icon"><Upload size={16} /></span>
            <div><strong>{t("Create source and index document")}</strong><p>{t("TXT, Markdown, PDF, or DOCX · 8 MiB maximum")}</p></div>
          </div>
          <div className="knowledge-import-mode" role="group" aria-label={t("Import method")}>
            <button type="button" className={clsx(importMode === "file" && "active")} aria-pressed={importMode === "file"} disabled={submitting} onClick={() => setImportMode("file")}>{t("Upload file")}</button>
            <button type="button" className={clsx(importMode === "text" && "active")} aria-pressed={importMode === "text"} disabled={submitting} onClick={() => setImportMode("text")}>{t("Paste text")}</button>
          </div>
          <div className="knowledge-form-grid">
            <label className="field"><span>{t("Knowledge source")}</span><select value={form.targetSourceId} onChange={(event) => setForm({ ...form, targetSourceId: event.target.value })}><option value="new">{t("Create a new source")}</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
            <label className="field"><span>{t("Document title")} {importMode === "file" && <small>{t("Optional")}</small>}</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder={t("Embedding guide")} /></label>
          </div>
          {form.targetSourceId === "new" && <div className="knowledge-form-grid"><label className="field"><span>{t("Source name")}</span><input value={form.sourceName} onChange={(event) => setForm({ ...form, sourceName: event.target.value })} placeholder={t("Product handbook")} /></label><label className="field"><span>{t("Description")}</span><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder={t("Governed internal product knowledge")} /></label></div>}
          {importMode === "file" ? (
            <label className={clsx("knowledge-file-picker", selectedFile && "selected")}>
              <input type="file" accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={submitting} onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
              <span className="knowledge-file-icon"><Upload size={18} /></span>
              <span><strong>{selectedFile ? selectedFile.name : t("Choose a document")}</strong><small>{selectedFile ? `${(selectedFile.size / 1024).toFixed(1)} KiB · ${t("Ready to parse")}` : t("The file is parsed server-side, then indexed with Qwen.")}</small></span>
              <span className="knowledge-file-action">{t(selectedFile ? "Replace" : "Browse")}</span>
            </label>
          ) : (
            <label className="field"><span>{t("Content")}</span><textarea rows={8} value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} placeholder={t("Paste plain text or Markdown…")} /></label>
          )}
          {error && <div className="knowledge-error">{error}</div>}
          <div className="knowledge-import-actions">
            <button className="button secondary" onClick={() => setImportOpen(false)} disabled={submitting}>{t("Cancel")}</button>
            <button className="button dark" onClick={() => void importDocument()} disabled={submitting}>
              <Upload size={13} />{t(phase === "creating" ? "Creating source…" : phase === "indexing" ? "Parsing & indexing…" : error ? "Retry import" : "Index and bind")}
            </button>
          </div>
        </div>
      )}

      {sources.length ? (
        <div className="knowledge-source-list">
          {sources.map((source) => {
            const bound = boundSourceIds.includes(source.id);
            return (
              <button
                className={clsx("knowledge-source-row", bound && "bound")}
                key={source.id}
                onClick={() => onToggle(source.id)}
                aria-pressed={bound}
              >
                <span className="capability-icon"><Database size={15} /></span>
                <span className="knowledge-source-copy">
                  <strong>{source.name}</strong>
                  <small>{source.description || t("Workspace knowledge source")}</small>
                </span>
                <span className="knowledge-count"><FileText size={12} />{t("{documents} docs · {chunks} chunks", { documents: source.document_count, chunks: source.chunk_count })}</span>
                <span className={clsx("knowledge-binding", bound && "active")}>
                  {bound ? <><Check size={12} />{t("Bound")}</> : t("Bind")}
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="empty-editor compact"><Database size={24} /><h3>{t("No knowledge sources yet")}</h3><p>{t("Import the first governed document for this Workspace.")}</p></div>
      )}
      <div className="editor-tip"><ShieldCheck size={15} /><span>{t("Search is always filtered by Workspace and the source IDs frozen into this Agent version. Retrieved content is treated as untrusted reference data.")}</span></div>
    </div>
  );
}

function extensionToolName(manifestId: string, toolName: string) {
  return `extension.${manifestId}.${toolName}`;
}

function Capabilities({
  definition,
  extensions,
  onChange,
}: {
  definition: Agent["definition"];
  extensions: Extension[];
  onChange: (definition: Agent["definition"]) => void;
}) {
  const { t } = useI18n();
  const available = extensions.flatMap((extension) => {
    const executable = extension.manifest.entrypoints.some((entrypoint) => entrypoint.type === "mcp" || entrypoint.type === "openapi");
    if (!executable || extension.status !== "enabled" || !["healthy", "degraded"].includes(extension.health)) return [];
    return extension.manifest.contributions.tools.flatMap((tool) => {
      const rawName = String(tool.name ?? "").trim();
      if (!rawName) return [];
      return [{
        id: extensionToolName(extension.manifest.id, rawName),
        rawName,
        manifestId: extension.manifest.id,
        extensionName: extension.name,
        description: String(tool.description ?? t("Extension tool")),
        mutating: Boolean(tool.mutating),
      }];
    });
  });
  const dynamicIds = new Set(available.map((tool) => tool.id));
  const existing = definition.tools.filter((tool) => !dynamicIds.has(tool));

  function toggle(tool: (typeof available)[number]) {
    const bound = definition.tools.includes(tool.id);
    const tools = bound
      ? definition.tools.filter((item) => item !== tool.id)
      : [...definition.tools, tool.id];
    const stillUsesExtension = available.some((candidate) =>
      candidate.manifestId === tool.manifestId && tools.includes(candidate.id));
    const nextExtensions = bound && !stillUsesExtension
      ? definition.extensions.filter((item) => item !== tool.manifestId)
      : Array.from(new Set([...definition.extensions, tool.manifestId]));
    onChange({ ...definition, tools, extensions: nextExtensions });
  }

  return <div className="capability-list"><div className="capability-toolbar"><div><strong>{t("Bound tools")}</strong><p>{t("Capabilities are resolved from enabled, healthy Workspace extensions.")}</p></div><span className="capability-count">{t("{count} bound", { count: definition.tools.length })}</span></div>
    {existing.length > 0 && <><div className="capability-group-label">{t("Current definition")}</div>{existing.map((tool) => <div className="capability-row" key={tool}><span className="capability-icon"><Wrench size={15} /></span><div><strong>{tool}</strong><p>{t("Built-in or previously bound capability")}</p></div><span className="permission-dot" /><Check size={15} className="success" /></div>)}</>}
    <div className="capability-group-label">{t("Installed extension tools")}</div>
    {available.length ? available.map((tool) => { const bound = definition.tools.includes(tool.id); return <button type="button" className={clsx("capability-row capability-option", bound && "bound")} key={tool.id} onClick={() => toggle(tool)} aria-pressed={bound}><span className="capability-icon"><Blocks size={15} /></span><div><strong>{tool.rawName}</strong><p>{tool.extensionName} · {tool.description}</p></div><span className={clsx("permission-dot", tool.mutating && "high")} /><span className={clsx("capability-binding", bound && "active")}>{bound ? <><Check size={12} />{t("Bound")}</> : t("Bind")}</span></button>; }) : <div className="empty-editor compact"><Blocks size={22} /><h3>{t("No executable extension tools")}</h3><p>{t("Enable and health-check an MCP or OpenAPI extension first.")}</p></div>}
    <div className="editor-tip"><ShieldCheck size={15} /><span>{t("Read-only tools run within the Agent allow-list. Mutating tools follow the Agent approval policy before execution.")}</span></div>
  </div>;
}

function sectionDescription(section: string): MessageKey {
  return ({ identity: "How this agent appears across Studio and embedded surfaces.", instructions: "Stable behavior and operating boundaries.", model: "Provider-neutral model binding and scoped credentials.", capabilities: "Tools and skills contributed by installed extensions.", knowledge: "Governed retrieval sources and citation behavior.", policies: "Human approval and execution safety boundaries.", output: "The structured result this agent produces." } as Record<string, MessageKey>)[section] ?? "Stable behavior and operating boundaries.";
}
