"use client";

import { Blocks, Database, Send, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { ExecutionEvent, ExtensionUIFormBlock } from "@alcuin/contracts";

import {
  dataForExtensionBlock,
  formatExtensionUIValue,
  resolvedExtensionToolName,
  valueAtPath,
  type ResolvedExtensionUIBlock,
} from "@/lib/extension-ui";
import { useI18n } from "@/lib/i18n";

export function ExtensionUIBlocks({
  blocks,
  events,
  context,
  busy,
  onSubmit,
}: {
  blocks: ResolvedExtensionUIBlock[];
  events: ExecutionEvent[];
  context: Record<string, unknown>;
  busy: boolean;
  onSubmit: (
    resolved: ResolvedExtensionUIBlock,
    tool: string,
    arguments_: Record<string, unknown>,
  ) => Promise<void>;
}) {
  const { t } = useI18n();
  if (blocks.length === 0) {
    return <div className="extension-blocks-empty"><Blocks size={23} /><h3>{t("No extension UI")}</h3><p>{t("Bind an enabled extension that contributes declarative blocks.")}</p></div>;
  }
  return <div className="extension-block-stack">
    <div className="extension-block-trust"><ShieldCheck size={14} /><span>{t("Rendered by Alcuin from a validated manifest. No extension JavaScript is executed.")}</span></div>
    {blocks.map((resolved) => {
      if (resolved.block.type === "form") {
        return <ExtensionForm key={resolved.key} resolved={resolved} block={resolved.block} context={context} busy={busy} onSubmit={onSubmit} />;
      }
      const data = dataForExtensionBlock(resolved, events, context);
      if (resolved.block.type === "table") {
        const tableBlock = resolved.block;
        const rows = Array.isArray(data) ? data.slice(0, 100) : [];
        return <section className="extension-ui-block extension-table-block" data-ui-block={resolved.key} key={resolved.key}>
          <BlockHeader resolved={resolved} />
          {rows.length > 0 ? <div className="extension-table-wrap"><table><thead><tr>{tableBlock.columns.map((column) => <th key={`${column.label}:${column.path}`}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{tableBlock.columns.map((column) => <td key={`${column.label}:${column.path}`}>{column.format === "status" ? <span className="extension-value-status">{formatExtensionUIValue(valueAtPath(row, column.path), column.format)}</span> : formatExtensionUIValue(valueAtPath(row, column.path), column.format)}</td>)}</tr>)}</tbody></table></div> : <div className="extension-block-no-data"><Database size={16} /><span>{tableBlock.empty_state ?? t("No data available")}</span></div>}
        </section>;
      }
      const cardBlock = resolved.block;
      return <section className="extension-ui-block extension-card-block" data-ui-block={resolved.key} key={resolved.key}>
        <BlockHeader resolved={resolved} />
        <dl>{cardBlock.fields.map((field) => <div key={`${field.label}:${field.path}`}><dt>{field.label}</dt><dd>{field.format === "status" ? <span className="extension-value-status">{formatExtensionUIValue(valueAtPath(data, field.path), field.format)}</span> : formatExtensionUIValue(valueAtPath(data, field.path), field.format)}</dd></div>)}</dl>
      </section>;
    })}
  </div>;
}

function BlockHeader({ resolved }: { resolved: ResolvedExtensionUIBlock }) {
  return <header className="extension-block-header"><div><span>{resolved.extensionName}</span><h3>{resolved.block.title}</h3>{resolved.block.description && <p>{resolved.block.description}</p>}</div><em>{resolved.block.type}</em></header>;
}

function ExtensionForm({
  resolved,
  block,
  context,
  busy,
  onSubmit,
}: {
  resolved: ResolvedExtensionUIBlock;
  block: ExtensionUIFormBlock;
  context: Record<string, unknown>;
  busy: boolean;
  onSubmit: (
    resolved: ResolvedExtensionUIBlock,
    tool: string,
    arguments_: Record<string, unknown>,
  ) => Promise<void>;
}) {
  const { t } = useI18n();
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(
    block.fields.map((field) => {
      const defaultValue = field.default_path ? valueAtPath(context, field.default_path) : undefined;
      return [field.name, defaultValue === undefined ? (field.options?.[0] ?? "") : String(defaultValue)];
    }),
  ));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const arguments_ = Object.fromEntries(block.fields.flatMap((field) => {
      const raw = values[field.name] ?? "";
      if (!raw && !field.required) return [];
      return [[field.name, field.input === "number" ? Number(raw) : raw]];
    }));
    await onSubmit(
      resolved,
      resolvedExtensionToolName(resolved, block.submit.tool),
      arguments_,
    );
  }

  return <section className="extension-ui-block extension-form-block" data-ui-block={resolved.key}>
    <BlockHeader resolved={resolved} />
    <form onSubmit={(event) => void submit(event)}>
      {block.fields.map((field) => <label key={field.name}><span>{field.label}{field.required && <i>{t("Required")}</i>}</span>{field.input === "textarea" ? <textarea required={field.required} placeholder={field.placeholder} value={values[field.name] ?? ""} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })} /> : field.input === "select" ? <select required={field.required} value={values[field.name] ?? ""} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}>{field.options?.map((option) => <option key={option} value={option}>{option}</option>)}</select> : <input type={field.input} required={field.required} placeholder={field.placeholder} value={values[field.name] ?? ""} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })} />}</label>)}
      <div className="extension-form-footer"><span><ShieldCheck size={12} />{t("Agent policy applies")}</span><button className="button dark" disabled={busy} type="submit"><Send size={13} />{block.submit.label}</button></div>
    </form>
  </section>;
}
