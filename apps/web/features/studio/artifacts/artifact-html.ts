/** Preview documents run in an opaque-origin iframe; never attach their DOM to Studio. */
export const ARTIFACT_SANDBOX = "allow-scripts";

export function artifactPreviewPolicy(interactive: boolean): string {
  return [
    "default-src 'none'",
    `script-src ${interactive ? "'unsafe-inline'" : "'none'"}`,
    "style-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "connect-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "media-src 'none'",
  ].join("; ");
}

export function buildArtifactPreview(
  content: string,
  options: { interactive: boolean; dark: boolean },
  parse: (html: string) => Document = (html) => new DOMParser().parseFromString(html, "text/html"),
): string {
  const document = parse(content);
  // Remove navigations and nested browsing contexts that are unrelated to the artifact.
  for (const node of document.querySelectorAll("base, meta[http-equiv], iframe, frame, frameset, object, embed, link, script[src]")) node.remove();
  for (const element of document.querySelectorAll("*")) {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (["target", "ping", "action", "formaction", "srcset"].includes(name)) element.removeAttribute(attribute.name);
      if (["src", "href", "xlink:href", "poster", "background", "data"].includes(name)) {
        const value = attribute.value.trim();
        if (!value.startsWith("#") && !/^data:(image\/|font\/)/i.test(value)) element.removeAttribute(attribute.name);
      }
    }
  }
  if (!options.interactive) for (const script of document.querySelectorAll("script")) script.remove();
  const policy = document.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content = artifactPreviewPolicy(options.interactive);
  const referrer = document.createElement("meta");
  referrer.name = "referrer";
  referrer.content = "no-referrer";
  const viewport = document.createElement("meta");
  viewport.name = "viewport";
  viewport.content = "width=device-width, initial-scale=1";
  const style = document.createElement("style");
  style.textContent = `:root{color-scheme:${options.dark ? "dark" : "light"}}html{min-height:100%;background:${options.dark ? "#1b1c1e" : "#fffefb"};color:${options.dark ? "#eeede8" : "#252522"};font-family:system-ui,sans-serif}body{margin:0;padding:24px;box-sizing:border-box}img,svg,video{max-width:100%}*{box-sizing:border-box}@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:0.01ms!important;animation-iteration-count:1!important;transition-duration:0.01ms!important;scroll-behavior:auto!important}}`;
  document.head.prepend(policy, referrer, viewport, style);
  if (options.interactive) {
    const themeBridge = document.createElement("script");
    // Theme-only messages keep local form/widget state intact across Studio theme changes.
    themeBridge.textContent = `addEventListener('message',function(event){if(event.source!==parent||event.data?.type!=='alcuin:preview-theme')return;var dark=event.data.dark===true;document.documentElement.style.colorScheme=dark?'dark':'light';document.documentElement.style.backgroundColor=dark?'#1b1c1e':'#fffefb';document.documentElement.style.color=dark?'#eeede8':'#252522';});`;
    document.head.appendChild(themeBridge);
  }
  return `<!doctype html>\n${document.documentElement.outerHTML}`;
}

export function artifactDownloadName(title: string, format: "docx" | "html" | "md"): string {
  const stem = title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-").replace(/^[.\s]+|[.\s]+$/g, "").slice(0, 120);
  return `${stem || "Alcuin artifact"}.${format}`;
}
