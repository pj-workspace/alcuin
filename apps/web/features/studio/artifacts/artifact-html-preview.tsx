"use client";

import { useEffect, useRef, useState } from "react";
import { Code2, Eye, LoaderCircle, ShieldCheck } from "lucide-react";

import { useI18n } from "@/shared/lib/i18n";
import { ARTIFACT_SANDBOX, buildArtifactPreview } from "./artifact-html";

const PREVIEW_INTERVAL_MS = 800;

export function ArtifactHtmlPreview({ content, title, running }: { content: string; title: string; running: boolean }) {
  const { t } = useI18n();
  const [view, setView] = useState<"preview" | "source">("preview");
  const [snapshot, setSnapshot] = useState("");
  const [dark, setDark] = useState(false);
  const latestContent = useRef(content);
  const darkRef = useRef(dark);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => { latestContent.current = content; }, [content]);
  useEffect(() => { darkRef.current = dark; }, [dark]);
  useEffect(() => {
    const root = document.documentElement;
    const update = () => setDark(root.dataset.theme === "dark" || root.classList.contains("dark"));
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme", "class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const render = () => setSnapshot(buildArtifactPreview(latestContent.current, { interactive: !running, dark: darkRef.current }));
    render();
    if (!running) return;
    // Read the newest buffer at a bounded interval. Individual SSE chunks never reload the iframe.
    const timer = window.setInterval(render, PREVIEW_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!running) setSnapshot(buildArtifactPreview(content, { interactive: true, dark: darkRef.current }));
  }, [content, running]);

  useEffect(() => {
    iframeRef.current?.contentWindow?.postMessage({ type: "alcuin:preview-theme", dark }, "*");
  }, [dark]);

  return (
    <section className="artifact-html" aria-label={title}>
      <div className="artifact-html-toolbar">
        <div className="artifact-view-tabs" role="tablist" aria-label={t("Artifact view")}>
          <button id="artifact-html-preview-tab" type="button" role="tab" aria-selected={view === "preview"} aria-controls="artifact-html-preview-panel" tabIndex={view === "preview" ? 0 : -1} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "End"].includes(event.key)) { event.preventDefault(); setView("source"); document.getElementById("artifact-html-source-tab")?.focus(); } }} onClick={() => setView("preview")}><Eye size={13} />{t("Preview")}</button>
          <button id="artifact-html-source-tab" type="button" role="tab" aria-selected={view === "source"} aria-controls="artifact-html-source-panel" tabIndex={view === "source" ? 0 : -1} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) { event.preventDefault(); setView("preview"); document.getElementById("artifact-html-preview-tab")?.focus(); } }} onClick={() => setView("source")}><Code2 size={13} />{t("Source code")}</button>
        </div>
        <span className="artifact-preview-status" role="status">{running ? <><LoaderCircle size={11} />{t("Building preview…")}</> : <><ShieldCheck size={12} />{t("Interactive preview")}</>}</span>
      </div>
      <div id="artifact-html-preview-panel" role="tabpanel" aria-labelledby="artifact-html-preview-tab" className="artifact-html-panel" hidden={view !== "preview"}>
        {snapshot ? <iframe ref={iframeRef} className="artifact-html-frame" title={title} sandbox={ARTIFACT_SANDBOX} referrerPolicy="no-referrer" allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; payment 'none'; usb 'none'; fullscreen 'none'" srcDoc={snapshot} onLoad={() => iframeRef.current?.contentWindow?.postMessage({ type: "alcuin:preview-theme", dark: darkRef.current }, "*")} /> : <div className="artifact-loading"><span className="micro-loader" />{t("Loading preview…")}</div>}
      </div>
      <div id="artifact-html-source-panel" role="tabpanel" aria-labelledby="artifact-html-source-tab" className="artifact-html-panel" hidden={view !== "source"}>
        <pre className="artifact-source-code" tabIndex={0}><code>{content}</code></pre>
      </div>
      <p className="artifact-preview-note">{t(running ? "Preview updates as the artifact takes shape. Interactions activate when generation finishes." : "Runs in an isolated preview. External resources and API requests are blocked.")}</p>
    </section>
  );
}
