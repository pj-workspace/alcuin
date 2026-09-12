import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const component = readFileSync(new URL("./thinking-markdown.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

test("thinking height follows natural content without synchronous layout reads or per-token observers", () => {
  assert.doesNotMatch(component, /useLayoutEffect|scrollHeight|getBoundingClientRect|offsetHeight|style\.maxHeight/);
  assert.match(component, /new ResizeObserver/);
  assert.match(component, /entry\.borderBoxSize\?\.\[0\]\?\.blockSize \?\? entry\.contentRect\.height/);
  assert.match(component, /if \(nextHeight === heightRef\.current\) return/);
  assert.match(component, /observer\.observe\(element\);\s*return \(\) => observer\.disconnect\(\);\s*\}, \[\]\)/);
  assert.match(component, /<div ref=\{contentRef\} className="thinking-md-content">\s*<ReactMarkdown/);
});

test("the natural content wrapper preserves full text, edge spacing, and existing clamp motion", () => {
  assert.match(component, /\{rendered\}\s*<\/ReactMarkdown>/);
  assert.match(component, /maxHeight: clamped \? SOFT_MAX_HEIGHT_PX : contentHeight/);
  assert.match(component, /aria-expanded=\{expanded\}/);
  assert.match(css, /\.thinking-md-content \{ display: flow-root; \}/);
  assert.match(css, /\.thinking-md-content > :first-child \{ margin-top: 0 !important; \}/);
  assert.match(css, /\.thinking-md-content > :last-child \{ margin-bottom: 0 !important; \}/);
  assert.match(css, /\.thinking-md \{[^}]*transition: max-height 180ms ease/);
  const reducedMotion = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reducedMotion, /transition-duration: \.01ms !important/);
});
