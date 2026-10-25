"use client";

import type { ArtifactReferenceMessagePart } from "@alcuin/contracts";
import { FileText, X, ArrowUpRight } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { useI18n } from "@/shared/lib/i18n";
import { artifactReferenceCopy } from "./artifact-reference-copy";
import "./artifact-reference.css";

gsap.registerPlugin(useGSAP);

export function ComposerArtifactReference({ reference, onRemove, disabled }: { reference: ArtifactReferenceMessagePart; onRemove: () => void; disabled: boolean }) {
  const { locale } = useI18n();
  const copy = artifactReferenceCopy(locale);
  const [preview, setPreview] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo(root.current, { autoAlpha: 0, y: 5 }, { autoAlpha: 1, y: 0, duration: .18, clearProps: "opacity,visibility,transform" });
    });
    return () => mm.revert();
  }, { scope: root, dependencies: [reference.artifact_id, reference.version], revertOnUpdate: true });
  return <div className="composer-artifact-reference" data-testid="composer-artifact-reference" ref={root}>
    <div className="artifact-reference-row"><button className="artifact-reference-open" type="button" onClick={() => setPreview(true)} aria-label={copy("View referenced document")}><FileText size={17} /><span><strong>{reference.title}</strong><small>{copy("Saved content")}</small></span><ArrowUpRight size={14} /></button><button type="button" className="icon-button quiet" aria-label={copy("Remove document reference")} onClick={onRemove} disabled={disabled}><X size={15} /></button></div>
    <p>{copy("Saved content will be sent with your next message. The original stays unchanged.")}</p>
    {preview && <ArtifactReferenceDialog reference={reference} sent={false} onClose={() => setPreview(false)} />}
  </div>;
}

export function MessageArtifactReferences({ references }: { references: readonly ArtifactReferenceMessagePart[] }) {
  const { locale } = useI18n();
  const copy = artifactReferenceCopy(locale);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const preview = references.find((reference) => reference.artifact_id === previewId);
  return <>{references.map((reference) => <button key={reference.artifact_id} type="button" className="message-artifact-reference artifact-reference-open" data-testid="message-artifact-reference" onClick={() => setPreviewId(reference.artifact_id)} aria-label={`${copy("View referenced document")}: ${reference.title}`}><FileText size={16} /><span><small>{copy("Referenced document")}</small><strong>{reference.title}</strong></span><ArrowUpRight size={13} /></button>)}{preview && <ArtifactReferenceDialog reference={preview} sent onClose={() => setPreviewId(null)} />}</>;
}

function ArtifactReferenceDialog({ reference, sent, onClose }: { reference: ArtifactReferenceMessagePart; sent: boolean; onClose: () => void }) {
  const { locale } = useI18n();
  const copy = artifactReferenceCopy(locale);
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  useGSAP(() => {
    const node = dialog.current;
    if (!node) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (closing) gsap.to(node, { autoAlpha: 0, y: 6, duration: reduced ? 0 : .14, onComplete: () => { node.close(); onClose(); } });
    // The native modal must stay focusable while showModal() assigns initial
    // focus; autoAlpha's visibility:hidden would prevent that during entry.
    else if (!reduced) gsap.fromTo(node, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: .18, clearProps: "opacity,transform" });
  }, { scope: dialog, dependencies: [closing], revertOnUpdate: true });
  const partial = reference.source_run_status && reference.source_run_status !== "completed";
  return createPortal(<dialog className="artifact-reference-dialog" ref={dialog} aria-labelledby={titleId} onKeyDown={(event) => {
    if (event.key !== "Tab") return;
    const close = event.currentTarget.querySelector<HTMLButtonElement>("button");
    const content = event.currentTarget.querySelector<HTMLPreElement>("pre");
    const destination = event.shiftKey && document.activeElement === close ? content
      : !event.shiftKey && document.activeElement === content ? close : null;
    if (destination) { event.preventDefault(); destination.focus(); }
  }} onCancel={(event) => { event.preventDefault(); setClosing(true); }} onClick={(event) => { if (event.target === event.currentTarget) setClosing(true); }}>
    <div className="artifact-reference-dialog-body"><header><div><small>{copy("Referenced document")}</small><h2 id={titleId}>{reference.title}</h2></div><button autoFocus type="button" className="icon-button quiet" aria-label={copy("Close document reference")} onClick={() => setClosing(true)}><X size={18} /></button></header>
    <p className="artifact-reference-explanation">{copy(sent ? "This is the snapshot sent with this message. Later edits do not change it." : "Preview of the saved content selected for your next message.")}</p>
    {partial && <p className="artifact-reference-warning" role="note">{copy("The source run did not complete. This document may be incomplete.")}</p>}
    <pre tabIndex={0}>{reference.content}</pre><footer><span>{copy("Source document")}</span><code>{reference.artifact_id}</code>{reference.sha256 && <code title="SHA-256">SHA-256 · {reference.sha256}</code>}</footer></div>
  </dialog>, document.body);
}
