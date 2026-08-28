"use client";

import type { Agent, KnowledgeSource } from "@alcuin/contracts";
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

const sections = [
  { id: "identity", label: "Identity", icon: Bot },
  { id: "instructions", label: "Instructions", icon: FileJson2 },
  { id: "model", label: "Model", icon: BrainCircuit },
  { id: "capabilities", label: "Capabilities", icon: Wrench },
  { id: "knowledge", label: "Knowledge", icon: Database },
  { id: "policies", label: "Policies", icon: ShieldCheck },
  { id: "output", label: "Output", icon: Sparkles },
];

export function AgentBuilderView({
  agent,
  knowledgeSources,
  onChanged,
}: {
  agent?: Agent;
  knowledgeSources: KnowledgeSource[];
  onChanged: () => Promise<void>;
}) {
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
      notify(publish ? "Agent version published" : "Draft version saved");
      await onChanged();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Unable to save agent");
    } finally {
      setSaving(false);
    }
  }

  return <div className="builder-surface">
    <header className="wide-header">
      <div><div className="breadcrumbs"><span>Agents</span><ChevronRight size={12} /><span>{agent.name}</span></div><div className="title-row"><h1>Agent Builder</h1><StatusPill status={agent.status} /><span className="version-badge">v{agent.version}</span></div><p>Compose behavior from stable, versioned capabilities.</p></div>
      <div className="header-actions"><button className="button secondary"><Eye size={14} />Preview</button><button className="button secondary" disabled={saving} onClick={() => void save()}><Save size={14} />Save draft</button><button className="button dark" disabled={saving} onClick={() => void save(true)}><Play size={13} fill="currentColor" />Publish version</button></div>
    </header>
    <div className="builder-body">
      <aside className="builder-nav">
        <p>Definition</p>
        {sections.map((section) => <button key={section.id} onClick={() => setActive(section.id)} className={clsx(active === section.id && "active")}><section.icon size={15} /><span>{section.label}</span>{section.id === "capabilities" && <small>{definition.tools.length}</small>}</button>)}
        <div className="definition-score"><div><span>Definition health</span><strong>{completeness}%</strong></div><div className="score-track"><i style={{ width: `${completeness}%` }} /></div><p>Ready to publish</p></div>
      </aside>
      <section className="definition-editor">
        <div className="editor-heading"><span className="section-icon"><Bot size={18} /></span><div><h2>{sections.find((item) => item.id === active)?.label}</h2><p>{sectionDescription(active)}</p></div></div>
        {active === "identity" && <div className="form-stack">
          <label className="field"><span>Agent name</span><input value={definition.identity.name} onChange={(event) => updateIdentity("name", event.target.value)} /><small>Shown in Studio and embedded experiences.</small></label>
          <label className="field"><span>Description</span><textarea rows={3} value={definition.identity.description} onChange={(event) => updateIdentity("description", event.target.value)} /></label>
          <div className="field"><span>Appearance</span><div className="appearance-row"><button className="agent-icon-choice active"><Bot size={19} /></button>{["#3157d5", "#596557", "#a45536", "#7656b6", "#252724"].map((color) => <button key={color} className="color-choice" style={{ backgroundColor: color }} aria-label={`Color ${color}`} />)}</div></div>
        </div>}
        {active === "instructions" && <div className="form-stack"><label className="field"><span>System instructions</span><textarea className="instruction-editor" rows={14} value={definition.instructions} onChange={(event) => setDefinition({ ...definition, instructions: event.target.value })} /><small>{definition.instructions.length.toLocaleString()} / 20,000 characters</small></label><div className="editor-tip"><Sparkles size={15} /><span>Keep domain behavior in extensions. The core definition should describe intent, policies, and output expectations.</span></div></div>}
        {active === "model" && <div className="form-stack">
          <label className="field">
            <span>Provider adapter</span>
            <select value={definition.model.provider} onChange={(event) => updateProvider(event.target.value)}>
              <option value="deepseek">DeepSeek</option>
              <option value="openai-compatible">Generic OpenAI-compatible</option>
            </select>
            <small>Provider-specific protocols stay behind the stable AgentRuntime interface.</small>
          </label>
          <label className="field">
            <span>Model identifier</span>
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
            <div className="editor-tip"><Eye size={15} /><span>Vision enabled · PNG, JPEG, WebP, or GIF · up to 4 images, 5 MiB each.</span></div>
          )}
          <label className="field">
            <span>Credential reference</span>
            <div className="secret-input"><LockKeyhole size={14} /><input value={definition.model.credential_ref ?? ""} readOnly /></div>
            <small>Paste the real key into <code>.env</code>; definitions store only this Secret Reference.</small>
          </label>
        </div>}
        {active === "capabilities" && <Capabilities definition={definition} />}
        {active === "knowledge" && (
          <KnowledgeEditor
            sources={knowledgeSources}
            boundSourceIds={definition.knowledge}
            onToggle={toggleKnowledge}
            onImported={async (sourceId) => {
              if (!definition.knowledge.includes(sourceId)) toggleKnowledge(sourceId);
              notify("Knowledge source indexed and bound");
              await onChanged();
            }}
            onSourcesChanged={onChanged}
          />
        )}
        {active === "policies" && <div className="form-stack"><div className="policy-card"><ShieldCheck size={18} /><div><strong>Mutating tools</strong><p>External write operations pause the run and create an explicit approval event.</p></div><select value={definition.policies.mutating_tools} onChange={(event) => setDefinition({ ...definition, policies: { ...definition.policies, mutating_tools: event.target.value as "ask" | "deny" | "auto" } })}><option value="ask">Ask every time</option><option value="deny">Always deny</option><option value="auto">Allow automatically</option></select></div><div className="policy-card"><LockKeyhole size={18} /><div><strong>Sensitive values</strong><p>Credentials remain scoped secret references and are redacted from execution events.</p></div><StatusPill status="enabled" /></div></div>}
        {active === "output" && <div className="form-stack"><label className="field"><span>Output kind</span><select defaultValue="artifact"><option>artifact</option><option>structured data</option><option>message only</option></select></label><label className="field"><span>Output schema</span><textarea className="code-editor" rows={8} value={JSON.stringify(definition.output_schema, null, 2)} readOnly /></label></div>}
        <div className="advanced-toggle"><button onClick={() => setAdvanced((value) => !value)}><Settings2 size={14} />Advanced definition JSON<ChevronRight className={clsx(advanced && "rotate")} size={13} /></button>{advanced && <pre>{JSON.stringify(definition, null, 2)}</pre>}</div>
      </section>
      <aside className="builder-preview">
        <div className="preview-label">Live identity preview</div><div className="agent-preview-card"><span className="preview-mark">A</span><h3>{definition.identity.name}</h3><p>{definition.identity.description}</p><div className="preview-meta"><span><BrainCircuit size={12} />{definition.model.model}</span><span><Blocks size={12} />{definition.extensions.length} extension</span></div><button><Sparkles size={14} />Start a conversation</button></div>
        <div className="contract-note"><FileJson2 size={15} /><div><strong>Versioned contract</strong><p>Saving creates a new immutable version. Publishing makes it available to Embed sessions.</p></div></div>
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
      setError(importMode === "file"
        ? "Choose or name a source, then select a supported file."
        : "Choose or name a source, then provide a document title and content.");
      return;
    }
    if (selectedFile && selectedFile.size > 8 * 1024 * 1024) {
      setError("Files must be 8 MiB or smaller.");
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
      setError(reason instanceof Error ? reason.message : "Unable to import knowledge");
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
          <strong>Workspace sources</strong>
          <p>Qwen dense + sparse hybrid retrieval. Agent versions store only source references.</p>
        </div>
        <button className="button secondary" onClick={() => setImportOpen((open) => !open)}>
          <Plus size={14} />Import document
        </button>
      </div>

      {importOpen && (
        <div className="knowledge-import">
          <div className="knowledge-import-heading">
            <span className="section-icon"><Upload size={16} /></span>
            <div><strong>Create source and index document</strong><p>TXT, Markdown, PDF, or DOCX · 8 MiB maximum</p></div>
          </div>
          <div className="knowledge-import-mode" role="group" aria-label="Import method">
            <button type="button" className={clsx(importMode === "file" && "active")} aria-pressed={importMode === "file"} disabled={submitting} onClick={() => setImportMode("file")}>Upload file</button>
            <button type="button" className={clsx(importMode === "text" && "active")} aria-pressed={importMode === "text"} disabled={submitting} onClick={() => setImportMode("text")}>Paste text</button>
          </div>
          <div className="knowledge-form-grid">
            <label className="field"><span>Knowledge source</span><select value={form.targetSourceId} onChange={(event) => setForm({ ...form, targetSourceId: event.target.value })}><option value="new">Create a new source</option>{sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
            <label className="field"><span>Document title {importMode === "file" && <small>Optional</small>}</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="Embedding guide" /></label>
          </div>
          {form.targetSourceId === "new" && <div className="knowledge-form-grid"><label className="field"><span>Source name</span><input value={form.sourceName} onChange={(event) => setForm({ ...form, sourceName: event.target.value })} placeholder="Product handbook" /></label><label className="field"><span>Description</span><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="Governed internal product knowledge" /></label></div>}
          {importMode === "file" ? (
            <label className={clsx("knowledge-file-picker", selectedFile && "selected")}>
              <input type="file" accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={submitting} onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
              <span className="knowledge-file-icon"><Upload size={18} /></span>
              <span><strong>{selectedFile ? selectedFile.name : "Choose a document"}</strong><small>{selectedFile ? `${(selectedFile.size / 1024).toFixed(1)} KiB · Ready to parse` : "The file is parsed server-side, then indexed with Qwen."}</small></span>
              <span className="knowledge-file-action">{selectedFile ? "Replace" : "Browse"}</span>
            </label>
          ) : (
            <label className="field"><span>Content</span><textarea rows={8} value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} placeholder="Paste plain text or Markdown…" /></label>
          )}
          {error && <div className="knowledge-error">{error}</div>}
          <div className="knowledge-import-actions">
            <button className="button secondary" onClick={() => setImportOpen(false)} disabled={submitting}>Cancel</button>
            <button className="button dark" onClick={() => void importDocument()} disabled={submitting}>
              <Upload size={13} />{phase === "creating" ? "Creating source…" : phase === "indexing" ? "Parsing & indexing…" : error ? "Retry import" : "Index and bind"}
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
                  <small>{source.description || "Workspace knowledge source"}</small>
                </span>
                <span className="knowledge-count"><FileText size={12} />{source.document_count} docs · {source.chunk_count} chunks</span>
                <span className={clsx("knowledge-binding", bound && "active")}>
                  {bound ? <><Check size={12} />Bound</> : "Bind"}
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="empty-editor compact"><Database size={24} /><h3>No knowledge sources yet</h3><p>Import the first governed document for this Workspace.</p></div>
      )}
      <div className="editor-tip"><ShieldCheck size={15} /><span>Search is always filtered by Workspace and the source IDs frozen into this Agent version. Retrieved content is treated as untrusted reference data.</span></div>
    </div>
  );
}

function Capabilities({ definition }: { definition: Agent["definition"] }) {
  return <div className="capability-list"><div className="capability-toolbar"><div><strong>Bound tools</strong><p>Capabilities resolved from enabled extensions.</p></div><button className="button secondary"><Blocks size={14} />Add capability</button></div>{definition.tools.map((tool, index) => <div className="capability-row" key={tool}><span className="capability-icon"><Wrench size={15} /></span><div><strong>{tool}</strong><p>{index === 2 ? "Mutating · approval required" : "Read only · automatic"}</p></div><span className={clsx("permission-dot", index === 2 && "high")} /> <Check size={15} className="success" /></div>)}</div>;
}

function sectionDescription(section: string) {
  return ({ identity: "How this agent appears across Studio and embedded surfaces.", instructions: "Stable behavior and operating boundaries.", model: "Provider-neutral model binding and scoped credentials.", capabilities: "Tools and skills contributed by installed extensions.", knowledge: "Governed retrieval sources and citation behavior.", policies: "Human approval and execution safety boundaries.", output: "The structured result this agent produces." } as Record<string, string>)[section];
}
