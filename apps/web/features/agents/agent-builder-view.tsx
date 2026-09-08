"use client";

import type { Agent, AgentDefinition, KnowledgeSource, Rule, Skill, SkillInvocationMode, ToolCatalogEntry } from "@alcuin/contracts";
import {
  Blocks,
  BookOpenCheck,
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
  Scale,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/shared/lib/api";
import { StatusPill, Toast } from "@/shared/components/ui";
import { skillDisplayName } from "@/shared/lib/customization";
import { useI18n, type MessageKey } from "@/shared/lib/i18n";

const sections = [
  { id: "identity", group: "Definition", label: "Identity", icon: Bot },
  { id: "instructions", group: "Definition", label: "Instructions", icon: FileJson2 },
  { id: "model", group: "Definition", label: "Model", icon: BrainCircuit },
  { id: "capabilities", group: "Context & Capability", label: "Tools", icon: Wrench },
  { id: "skills", group: "Context & Capability", label: "Skills", icon: BookOpenCheck },
  { id: "knowledge", group: "Context & Capability", label: "Knowledge", icon: Database },
  { id: "rules", group: "Boundaries", label: "Rules", icon: Scale },
  { id: "policies", group: "Boundaries", label: "Policies", icon: ShieldCheck },
  { id: "output", group: "Result", label: "Output", icon: Sparkles },
] satisfies Array<{ id: string; group: MessageKey; label: MessageKey; icon: typeof Bot }>;

export function AgentBuilderView({
  agent,
  agents,
  knowledgeSources,
  tools,
  skills,
  rules,
  customizationError,
  onChanged,
  onCreateAgent,
  onSelectAgent,
}: {
  agent?: Agent;
  agents: Agent[];
  knowledgeSources: KnowledgeSource[];
  tools: ToolCatalogEntry[];
  skills: Skill[];
  rules: Rule[];
  customizationError: string | null;
  onChanged: () => Promise<void>;
  onCreateAgent: () => void;
  onSelectAgent: (agentId: string) => void;
}) {
  const { t } = useI18n();
  const [active, setActive] = useState("identity");
  const [definition, setDefinition] = useState(agent?.definition);
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "unsaved" | "saving" | "error">("saved");
  const [toast, setToast] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const completeness = useMemo(() => {
    if (!definition) return 0;
    return [definition.identity.name, definition.instructions, definition.model.model, definition.tools.length, definition.output_schema].filter(Boolean).length * 20;
  }, [definition]);

  if (!agent || !definition) return null;
  const currentAgent = agent;
  const currentDefinition = definition;
  const changeDefinition = (next: AgentDefinition) => { setDefinition(next); setSaveState("unsaved"); };
  const updateIdentity = (field: "name" | "description", value: string) => changeDefinition({ ...definition, identity: { ...definition.identity, [field]: value } });
  const updateProvider = (provider: string) => changeDefinition({
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
    changeDefinition({ ...definition, knowledge, tools });
  };

  async function save(publish = false) {
    setSaving(true);
    setSaveState("saving");
    try {
      await alcuinApi.saveAgent(currentAgent.id, currentDefinition);
      if (publish) await alcuinApi.publishAgent(currentAgent.id);
      setSaveState("saved");
      notify(t(publish ? "Agent ready in Studio" : "Agent changes saved"));
      await onChanged();
    } catch (error) {
      setSaveState("error");
      notify(error instanceof Error ? error.message : t("Unable to save agent"));
    } finally {
      setSaving(false);
    }
  }

  return <div className="builder-surface">
    <header className="wide-header">
      <div><div className="breadcrumbs"><span>{t("Agents")}</span><ChevronRight size={12} /><label className="builder-agent-switch"><select aria-label={t("Select agent")} value={agent.id} onChange={(event) => onSelectAgent(event.target.value)}>{agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronRight size={11} /></label></div><div className="title-row"><h1>{t("Agent Builder")}</h1><StatusPill status={agent.status} /><span className={`save-indicator ${saveState}`}>{t(saveState === "saved" ? "Saved" : saveState === "unsaved" ? "Unsaved changes" : saveState === "saving" ? "Saving…" : "Save failed")}</span></div><p>{t("Define how this Agent thinks, what it can use, and where it must stop.")}</p></div>
      <div className="header-actions"><button className="button secondary" onClick={onCreateAgent}><Plus size={14} />{t("New agent")}</button><button className="button secondary" disabled={saving || saveState === "saved"} onClick={() => void save()}><Save size={14} />{t("Save changes")}</button><button className="button dark" disabled={saving} onClick={() => void save(true)}><Play size={13} fill="currentColor" />{t("Use in Studio")}</button></div>
    </header>
    <div className="builder-body">
      <aside className="builder-nav">
        {sections.map((section, index) => <div className="builder-nav-item" key={section.id}>{(index === 0 || sections[index - 1].group !== section.group) && <p>{t(section.group)}</p>}<button aria-label={t(section.label)} onClick={() => setActive(section.id)} className={clsx(active === section.id && "active")}><section.icon size={15} /><span>{t(section.label)}</span>{section.id === "capabilities" && <small>{definition.tools.length}</small>}{section.id === "skills" && <small>{definition.skills?.length ?? 0}</small>}{section.id === "rules" && <small>{definition.rules?.length ?? 0}</small>}</button></div>)}
        <div className="definition-score"><div><span>{t("Definition health")}</span><strong>{completeness}%</strong></div><div className="score-track"><i style={{ width: `${completeness}%` }} /></div><p>{t("Ready to use")}</p></div>
      </aside>
      <section className="definition-editor">
        <div className="editor-heading"><span className="section-icon"><Bot size={18} /></span><div><h2>{t(sections.find((item) => item.id === active)?.label ?? "Identity")}</h2><p>{t(sectionDescription(active))}</p></div></div>
        {active === "identity" && <div className="form-stack">
          <label className="field"><span>{t("Agent name")}</span><input value={definition.identity.name} onChange={(event) => updateIdentity("name", event.target.value)} /><small>{t("Shown throughout the Alcuin workspace.")}</small></label>
          <label className="field"><span>{t("Description")}</span><textarea rows={3} value={definition.identity.description} onChange={(event) => updateIdentity("description", event.target.value)} /></label>
          <div className="field"><span>{t("Appearance")}</span><div className="appearance-row"><span className="agent-icon-choice active"><Bot size={19} /></span><small>{t("Alcuin mark · Ultramarine")}</small></div></div>
        </div>}
        {active === "instructions" && <div className="form-stack"><label className="field"><span>{t("System instructions")}</span><textarea className="instruction-editor" rows={14} value={definition.instructions} onChange={(event) => changeDefinition({ ...definition, instructions: event.target.value })} /><small>{t("{count} / 20,000 characters", { count: definition.instructions.length.toLocaleString() })}</small></label><div className="editor-tip"><Sparkles size={15} /><span>{t("These instructions are included in every conversation with this Agent.")}</span></div></div>}
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
              <select value={definition.model.model} onChange={(event) => changeDefinition({ ...definition, model: { ...definition.model, model: event.target.value } })}>
                <option value="deepseek-v4-flash-vision-exp">DeepSeek V4 Flash Vision (Experimental)</option>
                <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
                <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
              </select>
            ) : (
              <input value={definition.model.model} onChange={(event) => changeDefinition({ ...definition, model: { ...definition.model, model: event.target.value } })} />
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
        {active === "capabilities" && <Capabilities definition={definition} tools={tools} onChange={changeDefinition} />}
        {active === "skills" && <SkillBindings definition={definition} skills={skills} error={customizationError} onChange={changeDefinition} />}
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
        {active === "rules" && <RuleBindings definition={definition} rules={rules} error={customizationError} onChange={changeDefinition} />}
        {active === "policies" && <div className="form-stack"><div className="policy-card"><ShieldCheck size={18} /><div><strong>{t("Mutating tools")}</strong><p>{t("External write operations pause the run and create an explicit approval event.")}</p></div><select value={definition.policies.mutating_tools} onChange={(event) => changeDefinition({ ...definition, policies: { ...definition.policies, mutating_tools: event.target.value as "ask" | "deny" | "auto" } })}><option value="ask">{t("Ask every time")}</option><option value="deny">{t("Always deny")}</option><option value="auto">{t("Allow automatically")}</option></select></div><div className="policy-card"><LockKeyhole size={18} /><div><strong>{t("Sensitive values")}</strong><p>{t("Credentials remain scoped secret references and are redacted from execution events.")}</p></div><StatusPill status="enabled" /></div></div>}
        {active === "output" && <div className="form-stack"><label className="field"><span>{t("Output kind")}</span><select value={String(definition.output_schema.type ?? "artifact")} onChange={(event) => changeDefinition({ ...definition, output_schema: { ...definition.output_schema, type: event.target.value } })}><option value="artifact">artifact</option><option value="structured data">{t("structured data")}</option><option value="message only">{t("message only")}</option></select></label><label className="field"><span>{t("Output schema")}</span><textarea className="code-editor" rows={8} value={JSON.stringify(definition.output_schema, null, 2)} readOnly /></label></div>}
        <div className="advanced-toggle"><button onClick={() => setAdvanced((value) => !value)}><Settings2 size={14} />{t("Advanced definition JSON")}<ChevronRight className={clsx(advanced && "rotate")} size={13} /></button>{advanced && <pre>{JSON.stringify(definition, null, 2)}</pre>}</div>
      </section>
      <aside className="builder-preview">
        <div className="preview-label">{t("Live identity preview")}</div><div className="agent-preview-card"><span className="preview-mark">A</span><h3>{definition.identity.name}</h3><p>{definition.identity.description}</p><div className="preview-meta"><span><BrainCircuit size={12} />{definition.model.model}</span><span><Blocks size={12} />{t("{count} extension", { count: definition.extensions.length })}</span></div></div>
        <div className="contract-note"><FileJson2 size={15} /><div><strong>{t("Stable Agent contract")}</strong><p>{t("Instructions, Skills, Rules, knowledge, and tool boundaries travel together when the Agent runs.")}</p></div></div>
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
          <p>{t("Qwen dense + sparse hybrid retrieval. The Agent stores only governed source references.")}</p>
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
      <div className="editor-tip"><ShieldCheck size={15} /><span>{t("Search is always filtered by Workspace and this Agent's bound source IDs. Retrieved content is treated as untrusted reference data.")}</span></div>
    </div>
  );
}

function Capabilities({
  definition,
  tools,
  onChange,
}: {
  definition: Agent["definition"];
  tools: ToolCatalogEntry[];
  onChange: (definition: Agent["definition"]) => void;
}) {
  const { t } = useI18n();
  const builtins = tools.filter((tool) => tool.source === "builtin");
  const extensionTools = tools.filter((tool) => tool.source === "extension");
  const catalogIds = new Set(tools.map((tool) => tool.id));
  const unavailableReferences = definition.tools.filter((tool) => !catalogIds.has(tool));

  function toggle(tool: ToolCatalogEntry) {
    const bound = definition.tools.includes(tool.id);
    if (!tool.available && !bound) return;
    const nextTools = bound
      ? definition.tools.filter((item) => item !== tool.id)
      : [...definition.tools, tool.id];
    const manifestId = tool.extension_manifest_id;
    const stillUsesExtension = manifestId
      ? extensionTools.some((candidate) =>
        candidate.extension_manifest_id === manifestId && nextTools.includes(candidate.id))
      : false;
    const nextExtensions = !manifestId
      ? definition.extensions
      : bound && !stillUsesExtension
        ? definition.extensions.filter((item) => item !== manifestId)
        : Array.from(new Set([...definition.extensions, manifestId]));
    onChange({ ...definition, tools: nextTools, extensions: nextExtensions });
  }

  function removeUnavailable(toolId: string) {
    onChange({ ...definition, tools: definition.tools.filter((item) => item !== toolId) });
  }

  function toolRow(tool: ToolCatalogEntry) {
    const bound = definition.tools.includes(tool.id);
    const knowledgeManaged = tool.id === "knowledge.search";
    const status = knowledgeManaged
      ? t("Managed in Knowledge")
      : tool.available
        ? tool.description
        : t("Unavailable · {status}", { status: toolStatusLabel(tool.status, t) });
    return <button
      type="button"
      className={clsx("capability-row capability-option", bound && "bound", !tool.available && "unavailable")}
      key={tool.id}
      onClick={() => !knowledgeManaged && toggle(tool)}
      aria-pressed={bound}
      disabled={knowledgeManaged}
    >
      <span className="capability-icon">{tool.source === "builtin" ? <Wrench size={15} /> : <Blocks size={15} />}</span>
      <div><strong>{tool.name}</strong><p>{tool.extension_name ? `${tool.extension_name} · ${status}` : status}</p></div>
      <span className={clsx("permission-dot", tool.mutating && "high", !tool.available && "unavailable")} />
      <span className={clsx("capability-binding", bound && "active")}>
        {knowledgeManaged
          ? bound ? <><Check size={12} />{t("Bound")}</> : t("Bind a source")
          : bound ? <><Check size={12} />{t("Bound")}</> : t("Bind")}
      </span>
    </button>;
  }

  return <div className="capability-list"><div className="capability-toolbar"><div><strong>{t("Bound tools")}</strong><p>{t("Capabilities are resolved from the authoritative Workspace Tool Catalog.")}</p></div><span className="capability-count">{t("{count} bound", { count: definition.tools.length })}</span></div>
    <div className="capability-group-label">{t("Built-in tools")}</div>
    {builtins.length ? builtins.map(toolRow) : <div className="empty-editor compact"><Wrench size={22} /><h3>{t("No built-in tools configured")}</h3><p>{t("Configure retrieval services to make their tools available.")}</p></div>}
    <div className="capability-group-label">{t("Installed extension tools")}</div>
    {extensionTools.length ? extensionTools.map(toolRow) : <div className="empty-editor compact"><Blocks size={22} /><h3>{t("No executable extension tools")}</h3><p>{t("Install an MCP, OpenAPI, or built-in extension first.")}</p></div>}
    {unavailableReferences.length > 0 && <><div className="capability-group-label">{t("Unavailable references")}</div>{unavailableReferences.map((tool) => <button type="button" className="capability-row capability-option unavailable" key={tool} onClick={() => removeUnavailable(tool)}><span className="capability-icon"><Wrench size={15} /></span><div><strong>{tool}</strong><p>{t("This tool is not registered in the current Workspace runtime.")}</p></div><span className="permission-dot unavailable" /><span className="capability-binding">{t("Unbind")}</span></button>)}</>}
    <div className="editor-tip"><ShieldCheck size={15} /><span>{t("Read-only tools run within the Agent allow-list. Mutating tools follow the Agent approval policy before execution.")}</span></div>
  </div>;
}

function SkillBindings({
  definition,
  skills,
  error,
  onChange,
}: {
  definition: AgentDefinition;
  skills: Skill[];
  error: string | null;
  onChange: (definition: AgentDefinition) => void;
}) {
  const { t } = useI18n();
  const bindings = definition.skills ?? [];
  const bindingFor = (skill: Skill) => bindings.find((item) => item.skill_version_id === skill.current_version_id);
  const toggle = (skill: Skill) => {
    const current = bindingFor(skill);
    onChange({
      ...definition,
      skills: current
        ? bindings.filter((item) => item.skill_version_id !== skill.current_version_id)
        : [...bindings, { skill_version_id: skill.current_version_id, mode: "auto" }],
    });
  };
  const setMode = (skill: Skill, mode: SkillInvocationMode) => onChange({
    ...definition,
    skills: bindings.map((item) => item.skill_version_id === skill.current_version_id ? { ...item, mode } : item),
  });

  return <div className="binding-editor">
    <div className="capability-toolbar"><div><strong>{t("Skill library")}</strong><p>{t("Bind reusable operating knowledge. Auto Skills stay lightweight until the Agent needs them.")}</p></div><span className="count-badge">{t("{count} bound", { count: bindings.length })}</span></div>
    {error && <div className="wizard-error">{error}</div>}
    <div className="binding-list">
      {skills.map((skill) => {
        const binding = bindingFor(skill);
        return <div className={clsx("binding-row", binding && "selected", !skill.enabled && "disabled")} key={skill.id}>
          <button className={clsx("binding-check", binding && "checked")} disabled={!skill.enabled && !binding} onClick={() => toggle(skill)} aria-label={t(binding ? "Unbind {name}" : "Bind {name}", { name: skillDisplayName(skill) })}>{binding && <Check size={12} />}</button>
          <span className="binding-icon"><BookOpenCheck size={16} /></span>
          <div className="binding-copy"><strong>{skillDisplayName(skill)}</strong><p>{skill.definition.description}</p><small>{skill.source_kind === "native" ? t("Native Skill") : skill.source_kind === "cursor_plugin" ? t("Cursor Plugin") : t("Agent Plugin")}{skill.definition.required_tools.length > 0 ? ` · ${t("{count} required tools", { count: skill.definition.required_tools.length })}` : ""}</small></div>
          {binding ? <select className="compact-select" value={binding.mode} onChange={(event) => setMode(skill, event.target.value as SkillInvocationMode)}><option value="auto">{t("Auto")}</option><option value="always">{t("Always loaded")}</option><option value="manual">{t("Manual")}</option></select> : <span className={clsx("resource-state", skill.enabled ? "available" : "unavailable")}>{t(skill.enabled ? "Available" : "Disabled")}</span>}
        </div>;
      })}
      {!skills.length && !error && <div className="binding-empty"><BookOpenCheck size={18} /><strong>{t("No Skills yet")}</strong><p>{t("Create a native Skill or inspect a compatible plugin in Extensions.")}</p></div>}
    </div>
    <div className="editor-tip"><ShieldCheck size={15} /><span>{t("A Skill can teach the Agent how to work, but it can never grant tools or bypass approval policies.")}</span></div>
  </div>;
}

function RuleBindings({
  definition,
  rules,
  error,
  onChange,
}: {
  definition: AgentDefinition;
  rules: Rule[];
  error: string | null;
  onChange: (definition: AgentDefinition) => void;
}) {
  const { t } = useI18n();
  const bindings = definition.rules ?? [];
  const workspaceRules = rules.filter((rule) => rule.scope === "workspace" && rule.enabled);
  const libraryRules = rules.filter((rule) => rule.scope === "library");
  const bindingFor = (rule: Rule) => bindings.some((item) => item.rule_version_id === rule.current_version_id);
  const toggle = (rule: Rule) => onChange({
    ...definition,
    rules: bindingFor(rule)
      ? bindings.filter((item) => item.rule_version_id !== rule.current_version_id)
      : [...bindings, { rule_version_id: rule.current_version_id }],
  });
  return <div className="binding-editor">
    <div className="capability-toolbar"><div><strong>{t("Operating rules")}</strong><p>{t("Workspace Rules apply everywhere. Library Rules become part of this Agent when bound.")}</p></div><span className="count-badge">{t("{count} active", { count: workspaceRules.length + bindings.length })}</span></div>
    {error && <div className="wizard-error">{error}</div>}
    {workspaceRules.length > 0 && <div className="rule-section"><div className="binding-section-label"><span>{t("Workspace Rules")}</span><small>{t("Applied automatically")}</small></div>{workspaceRules.map((rule) => <RuleRow key={rule.id} rule={rule} selected locked />)}</div>}
    <div className="rule-section"><div className="binding-section-label"><span>{t("Agent Rules")}</span><small>{t("Choose from the reusable library")}</small></div>{libraryRules.map((rule) => <RuleRow key={rule.id} rule={rule} selected={bindingFor(rule)} disabled={!rule.enabled} onToggle={() => toggle(rule)} />)}{!libraryRules.length && !error && <div className="binding-empty"><Scale size={18} /><strong>{t("No reusable Rules yet")}</strong><p>{t("Create Always, Conditional, or Manual Rules in Extensions.")}</p></div>}</div>
    <div className="editor-tip"><Scale size={15} /><span>{t("Manual Rules are available in the Studio Context Ledger and apply from the next turn until changed.")}</span></div>
  </div>;
}

function RuleRow({ rule, selected, locked = false, disabled = false, onToggle }: { rule: Rule; selected: boolean; locked?: boolean; disabled?: boolean; onToggle?: () => void }) {
  const { t } = useI18n();
  const activation = rule.definition.activation;
  const conditionCount = rule.definition.conditions.prompt_terms.length + rule.definition.conditions.context_paths.length + rule.definition.conditions.file_globs.length;
  return <div className={clsx("binding-row rule-binding-row", selected && "selected", disabled && "disabled")}>
    <button className={clsx("binding-check", selected && "checked", locked && "locked")} disabled={locked || disabled} onClick={onToggle} aria-label={rule.definition.name}>{selected && <Check size={12} />}</button>
    <span className="binding-icon"><Scale size={16} /></span>
    <div className="binding-copy"><strong>{rule.definition.name}</strong><p>{rule.definition.description || rule.definition.content.slice(0, 120)}</p><small>{rule.scope === "workspace" ? t("Workspace") : t("Rule library")} · {activation === "always" ? t("Always") : activation === "conditional" ? t("Conditional") : t("Manual")}{conditionCount ? ` · ${t("{count} conditions", { count: conditionCount })}` : ""}</small></div>
    <span className={`activation-badge ${activation}`}>{t(activation === "always" ? "Always" : activation === "conditional" ? "Conditional" : "Manual")}</span>
  </div>;
}

function toolStatusLabel(status: ToolCatalogEntry["status"], t: ReturnType<typeof useI18n>["t"]) {
  return t(({
    available: "Available",
    disabled: "Disabled",
    unchecked: "Health check required",
    unhealthy: "Unhealthy",
    adapter_missing: "Adapter missing",
  } satisfies Record<ToolCatalogEntry["status"], MessageKey>)[status]);
}

function sectionDescription(section: string): MessageKey {
  return ({ identity: "How this Agent appears in Studio.", instructions: "Behavior included in every conversation.", model: "Provider-neutral model binding and scoped credentials.", capabilities: "Tools this Agent may call.", skills: "Reusable operating knowledge loaded only when relevant.", knowledge: "Governed retrieval sources and citation behavior.", rules: "Always, Conditional, and Manual boundaries.", policies: "Human approval and execution safety boundaries.", output: "The structured result this agent produces." } as Record<string, MessageKey>)[section] ?? "Stable behavior and operating boundaries.";
}
