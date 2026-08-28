"use client";

import type { Agent } from "@alcuin/contracts";
import {
  Blocks,
  Bot,
  BrainCircuit,
  Check,
  ChevronRight,
  Database,
  Eye,
  FileJson2,
  LockKeyhole,
  Play,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
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

export function AgentBuilderView({ agent, onChanged }: { agent?: Agent; onChanged: () => Promise<void> }) {
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
        {active === "knowledge" && <div className="empty-editor"><Database size={24} /><h3>No knowledge collections bound</h3><p>Bind a governed retrieval collection or install a knowledge connector.</p><button className="button secondary"><Database size={14} />Bind knowledge</button></div>}
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

function Capabilities({ definition }: { definition: Agent["definition"] }) {
  return <div className="capability-list"><div className="capability-toolbar"><div><strong>Bound tools</strong><p>Capabilities resolved from enabled extensions.</p></div><button className="button secondary"><Blocks size={14} />Add capability</button></div>{definition.tools.map((tool, index) => <div className="capability-row" key={tool}><span className="capability-icon"><Wrench size={15} /></span><div><strong>{tool}</strong><p>{index === 2 ? "Mutating · approval required" : "Read only · automatic"}</p></div><span className={clsx("permission-dot", index === 2 && "high")} /> <Check size={15} className="success" /></div>)}</div>;
}

function sectionDescription(section: string) {
  return ({ identity: "How this agent appears across Studio and embedded surfaces.", instructions: "Stable behavior and operating boundaries.", model: "Provider-neutral model binding and scoped credentials.", capabilities: "Tools and skills contributed by installed extensions.", knowledge: "Governed retrieval sources and citation behavior.", policies: "Human approval and execution safety boundaries.", output: "The structured result this agent produces." } as Record<string, string>)[section];
}
