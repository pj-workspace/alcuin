"use client";

import type { PluginInspection, Rule, RuleActivation, Skill } from "@alcuin/contracts";
import { BookOpenCheck, Check, FileArchive, Plus, Scale, Search, ShieldCheck, Upload, X } from "lucide-react";
import { clsx } from "clsx";
import { useMemo, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import { Toast } from "@/shared/components/ui";
import { skillDisplayName } from "@/shared/lib/customization";
import { useI18n } from "@/shared/lib/i18n";

type LibraryTab = "skills" | "rules" | "plugins";

function slugFromName(name: string) {
  return name.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

export function CustomizationLibrary({
  skills,
  rules,
  error,
  onChanged,
}: {
  skills: Skill[];
  rules: Rule[];
  error: string | null;
  onChanged: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState<LibraryTab>("skills");
  const [filter, setFilter] = useState("");
  const [editor, setEditor] = useState<"skill" | "rule" | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 2200); };
  const query = filter.trim().toLowerCase();
  const visibleSkills = useMemo(() => skills.filter((skill) => !query || `${skillDisplayName(skill)} ${skill.definition.description} ${skill.slug}`.toLowerCase().includes(query)), [query, skills]);
  const visibleRules = useMemo(() => rules.filter((rule) => !query || `${rule.definition.name} ${rule.definition.description} ${rule.slug}`.toLowerCase().includes(query)), [query, rules]);

  async function toggleSkill(skill: Skill) {
    setBusyId(skill.id);
    try {
      await alcuinApi.setSkillEnabled(skill.id, !skill.enabled);
      await onChanged();
      notify(t(skill.enabled ? "Skill disabled" : "Skill enabled"));
    } catch (reason) { notify(reason instanceof Error ? reason.message : t("Skill update failed")); }
    finally { setBusyId(null); }
  }

  async function toggleRule(rule: Rule) {
    setBusyId(rule.id);
    try {
      await alcuinApi.setRuleEnabled(rule.id, !rule.enabled);
      await onChanged();
      notify(t(rule.enabled ? "Rule disabled" : "Rule enabled"));
    } catch (reason) { notify(reason instanceof Error ? reason.message : t("Rule update failed")); }
    finally { setBusyId(null); }
  }

  return <div className="customization-library">
    <header className="wide-header library-header"><div><div className="eyebrow">{t("Agent foundation")}</div><h1>{t("Skills & Rules")}</h1><p>{t("Reusable behavior that stays explicit, inspectable, and scoped to this Workspace.")}</p></div><div className="header-actions">{tab === "skills" && <button className="button dark" onClick={() => setEditor("skill")}><Plus size={14} />{t("New Skill")}</button>}{tab === "rules" && <button className="button dark" onClick={() => setEditor("rule")}><Plus size={14} />{t("New Rule")}</button>}</div></header>
    <div className="library-tabs" role="tablist"><button className={clsx(tab === "skills" && "active")} onClick={() => setTab("skills")}><BookOpenCheck size={15} />{t("Skill Library")}<span>{skills.length}</span></button><button className={clsx(tab === "rules" && "active")} onClick={() => setTab("rules")}><Scale size={15} />{t("Rules")}<span>{rules.length}</span></button><button className={clsx(tab === "plugins" && "active")} onClick={() => setTab("plugins")}><FileArchive size={15} />{t("Plugin Inspector")}</button></div>
    {tab !== "plugins" && <div className="extensions-toolbar library-toolbar"><div className="filter-input"><Search size={14} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t(tab === "skills" ? "Filter Skills…" : "Filter Rules…")} /></div><span>{t("{count} enabled", { count: tab === "skills" ? skills.filter((item) => item.enabled).length : rules.filter((item) => item.enabled).length })}</span></div>}
    {error && <div className="library-error wizard-error">{error}</div>}

    {tab === "skills" && <div className="resource-grid">{visibleSkills.map((skill) => <article className={clsx("resource-card", !skill.enabled && "disabled")} key={skill.id}><div className="resource-card-top"><span className="resource-mark"><BookOpenCheck size={18} /></span><span className={clsx("source-badge", skill.source_kind)}>{skill.source_kind === "native" ? t("Native") : skill.source_kind === "cursor_plugin" ? "Cursor" : t("Agent Plugin")}</span></div><h2>{skillDisplayName(skill)}</h2><p>{skill.definition.description}</p><div className="resource-meta"><span>{t("{count} resources", { count: skill.definition.resources.length })}</span><span>{t("{count} tools declared", { count: skill.definition.required_tools.length })}</span></div><div className="resource-card-footer"><span>{t(skill.definition.disable_model_invocation ? "User invoked only" : "Model discoverable")}</span><button className={clsx("toggle", skill.enabled && "on")} disabled={busyId === skill.id} onClick={() => void toggleSkill(skill)} aria-label={skillDisplayName(skill)}><i /></button></div></article>)}{!visibleSkills.length && !error && <LibraryEmpty icon={<BookOpenCheck size={22} />} title={t("No Skills found")} body={t("Create a focused Skill with instructions the Agent can load when needed.")} />}</div>}

    {tab === "rules" && <div className="resource-grid">{visibleRules.map((rule) => <article className={clsx("resource-card", !rule.enabled && "disabled")} key={rule.id}><div className="resource-card-top"><span className="resource-mark rule"><Scale size={18} /></span><span className={`activation-badge ${rule.definition.activation}`}>{t(rule.definition.activation === "always" ? "Always" : rule.definition.activation === "conditional" ? "Conditional" : "Manual")}</span></div><h2>{rule.definition.name}</h2><p>{rule.definition.description || rule.definition.content.slice(0, 150)}</p><div className="resource-meta"><span>{rule.scope === "workspace" ? t("Every Agent") : rule.scope === "library" ? t("Reusable library") : t("One thread")}</span><span>{t("Priority {priority}", { priority: rule.definition.priority })}</span></div><div className="resource-card-footer"><span>{rule.source_kind === "native" ? t("Native Rule") : rule.source_kind === "cursor_plugin" ? t("Imported from Cursor") : t("Imported plugin")}</span><button className={clsx("toggle", rule.enabled && "on")} disabled={busyId === rule.id} onClick={() => void toggleRule(rule)} aria-label={rule.definition.name}><i /></button></div></article>)}{!visibleRules.length && !error && <LibraryEmpty icon={<Scale size={22} />} title={t("No Rules found")} body={t("Create a Workspace Rule or reusable Agent boundary.")} />}</div>}

    {tab === "plugins" && <PluginInspector onInstalled={onChanged} />}
    {editor === "skill" && <SkillEditor onClose={() => setEditor(null)} onChanged={async () => { setEditor(null); await onChanged(); notify(t("Skill created")); }} />}
    {editor === "rule" && <RuleEditor onClose={() => setEditor(null)} onChanged={async () => { setEditor(null); await onChanged(); notify(t("Rule created")); }} />}
    {toast && <Toast message={toast} />}
  </div>;
}

function LibraryEmpty({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return <div className="library-empty">{icon}<strong>{title}</strong><p>{body}</p></div>;
}

function SkillEditor({ onClose, onChanged }: { onClose: () => void; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [form, setForm] = useState({ name: "", slug: "", description: "", instructions: "", requiredTools: "", invocation: "auto" as "auto" | "user" });
  const [slugTouched, setSlugTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.slug) && form.name.trim().length > 1 && form.description.trim().length > 0 && form.instructions.trim().length > 0;
  async function submit() {
    if (!valid) return;
    setBusy(true); setError(null);
    try {
      await alcuinApi.createSkill({ slug: form.slug, definition: { name: form.slug, description: form.description.trim(), instructions: form.instructions.trim(), disable_model_invocation: form.invocation === "user", user_invocable: true, required_tools: form.requiredTools.split(",").map((item) => item.trim()).filter(Boolean), paths: [], metadata: { display_name: form.name.trim() }, resources: [] } });
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Unable to create Skill")); }
    finally { setBusy(false); }
  }
  return <div className="sheet-backdrop" onMouseDown={onClose}><aside className="inspect-sheet resource-editor" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="extension-logo"><BookOpenCheck size={18} /></span><div><small>{t("Skill Library")}</small><h2>{t("New Skill")}</h2></div></div><button className="icon-button quiet" onClick={onClose}><X size={16} /></button></header><div className="wizard-body"><div className="knowledge-form-grid"><label className="field"><span>{t("Display name")}</span><input value={form.name} onChange={(event) => { const name = event.target.value; setForm({ ...form, name, slug: slugTouched ? form.slug : slugFromName(name) }); }} /></label><label className="field"><span>{t("Skill slug")}</span><input value={form.slug} onChange={(event) => { setSlugTouched(true); setForm({ ...form, slug: event.target.value }); }} /></label></div><label className="field"><span>{t("Description")}</span><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder={t("When should the Agent use this Skill?")} /></label><label className="field"><span>{t("Instructions")}</span><textarea rows={12} value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} placeholder={t("Write the reusable workflow in Markdown…")} /></label><div className="knowledge-form-grid"><label className="field"><span>{t("Invocation")}</span><select value={form.invocation} onChange={(event) => setForm({ ...form, invocation: event.target.value as "auto" | "user" })}><option value="auto">{t("Model discoverable")}</option><option value="user">{t("User invoked only")}</option></select></label><label className="field"><span>{t("Required tool IDs")} <small>{t("Optional")}</small></span><input value={form.requiredTools} onChange={(event) => setForm({ ...form, requiredTools: event.target.value })} placeholder="knowledge.search, web.search" /></label></div><div className="editor-tip"><ShieldCheck size={15} /><span>{t("Required tools are declarations only. Bind those tools separately on the Agent.")}</span></div>{error && <div className="wizard-error">{error}</div>}</div><footer><button className="button secondary" onClick={onClose}>{t("Cancel")}</button><button className="button dark" disabled={!valid || busy} onClick={() => void submit()}>{busy ? <span className="micro-loader" /> : <Check size={14} />}{t("Create Skill")}</button></footer></aside></div>;
}

function RuleEditor({ onClose, onChanged }: { onClose: () => void; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [form, setForm] = useState({ name: "", slug: "", description: "", content: "", scope: "library" as "workspace" | "library", activation: "always" as RuleActivation, promptTerms: "", priority: 100 });
  const [slugTouched, setSlugTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const terms = form.promptTerms.split("\n").map((item) => item.trim()).filter(Boolean);
  const valid = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.slug) && form.name.trim().length > 0 && form.content.trim().length > 0 && (form.activation !== "conditional" || terms.length > 0);
  async function submit() {
    if (!valid) return;
    setBusy(true); setError(null);
    try {
      await alcuinApi.createRule({ slug: form.slug, scope: form.scope, definition: { name: form.name.trim(), description: form.description.trim(), content: form.content.trim(), activation: form.activation, conditions: { prompt_terms: form.activation === "conditional" ? terms : [], context_paths: [], file_globs: [] }, priority: form.priority } });
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Unable to create Rule")); }
    finally { setBusy(false); }
  }
  return <div className="sheet-backdrop" onMouseDown={onClose}><aside className="inspect-sheet resource-editor" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="extension-logo"><Scale size={18} /></span><div><small>{t("Boundaries")}</small><h2>{t("New Rule")}</h2></div></div><button className="icon-button quiet" onClick={onClose}><X size={16} /></button></header><div className="wizard-body"><div className="knowledge-form-grid"><label className="field"><span>{t("Rule name")}</span><input value={form.name} onChange={(event) => { const name = event.target.value; setForm({ ...form, name, slug: slugTouched ? form.slug : slugFromName(name) }); }} /></label><label className="field"><span>{t("Rule slug")}</span><input value={form.slug} onChange={(event) => { setSlugTouched(true); setForm({ ...form, slug: event.target.value }); }} /></label></div><label className="field"><span>{t("Description")}</span><input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label><div className="knowledge-form-grid"><label className="field"><span>{t("Scope")}</span><select value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value as typeof form.scope })}><option value="library">{t("Reusable Agent Rule")}</option><option value="workspace">{t("Every Agent in Workspace")}</option></select></label><label className="field"><span>{t("Activation")}</span><select value={form.activation} onChange={(event) => setForm({ ...form, activation: event.target.value as RuleActivation })}><option value="always">{t("Always")}</option><option value="conditional">{t("Conditional")}</option><option value="manual">{t("Manual")}</option></select></label></div>{form.activation === "conditional" && <label className="field"><span>{t("Prompt terms")}</span><textarea rows={4} value={form.promptTerms} onChange={(event) => setForm({ ...form, promptTerms: event.target.value })} placeholder={t("One deterministic term per line…")} /><small>{t("The Rule activates when the prompt contains one of these terms.")}</small></label>}<label className="field"><span>{t("Rule content")}</span><textarea rows={11} value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} placeholder={t("Write the boundary or instruction in Markdown…")} /></label>{error && <div className="wizard-error">{error}</div>}</div><footer><button className="button secondary" onClick={onClose}>{t("Cancel")}</button><button className="button dark" disabled={!valid || busy} onClick={() => void submit()}>{busy ? <span className="micro-loader" /> : <Check size={14} />}{t("Create Rule")}</button></footer></aside></div>;
}

function PluginInspector({ onInstalled }: { onInstalled: () => Promise<void> }) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<PluginInspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function inspect() {
    if (!file) return;
    setBusy(true); setError(null); setInspection(null); setInstalled(false);
    try { setInspection(await alcuinApi.inspectPluginPackage(file)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : t("Plugin inspection failed")); }
    finally { setBusy(false); }
  }
  async function install() {
    if (!file || !inspection || installing) return;
    setInstalling(true); setError(null);
    try {
      await alcuinApi.installPluginPackage(file, inspection.inspection_receipt);
      setInstalled(true);
      await onInstalled();
    } catch (reason) { setError(reason instanceof Error ? reason.message : t("Plugin installation failed")); }
    finally { setInstalling(false); }
  }
  const report = inspection?.inspection;
  return <div className="plugin-inspector"><div className="plugin-drop"><FileArchive size={28} /><h2>{t("Inspect an Agent or Cursor plugin")}</h2><p>{t("Alcuin reads the package as data. Hooks, commands, agents, and scripts never execute during inspection.")}</p><label className="button secondary"><Upload size={14} />{file ? file.name : t("Choose ZIP package")}<input className="visually-hidden" type="file" accept=".zip,application/zip" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setInspection(null); setError(null); setInstalled(false); }} /></label><button className="button dark" disabled={!file || busy} onClick={() => void inspect()}>{busy ? <span className="micro-loader" /> : <Search size={14} />}{t("Inspect safely")}</button></div>{error && <div className="wizard-error">{error}</div>}{inspection && report && <div className="plugin-report"><div className="plugin-report-heading"><span className="inspect-status valid"><Check size={14} /></span><div><small>{report.format === "cursor-plugin" ? "Cursor Plugin" : "Agent Plugin 1.0"}</small><h2>{report.name}</h2><p>{report.description}</p></div></div><div className="plugin-report-metrics"><div><strong>{report.skills.length}</strong><span>{t("Skills")}</span></div><div><strong>{report.rules.length}</strong><span>{t("Rules")}</span></div><div><strong>{report.mcp_servers.length}</strong><span>MCP</span></div><div><strong>{report.warnings.length}</strong><span>{t("Warnings")}</span></div></div>{report.warnings.length > 0 && <div className="plugin-warnings">{report.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}<div className="context-ledger-note"><strong>{t(installed ? "Installed disabled" : "Inspection complete — nothing installed")}</strong><p>{t(installed ? "Imported Skills and Rules remain disabled until you review and enable them." : "Review the normalized resources, then install the exact inspected archive disabled-first.")}</p></div><div className="plugin-install-actions"><span>{t("Signed review expires shortly")}</span><button className="button dark" disabled={installed || installing} onClick={() => void install()}>{installing ? <span className="micro-loader" /> : <ShieldCheck size={14} />}{t(installed ? "Installed disabled" : "Install disabled")}</button></div></div>}</div>;
}
