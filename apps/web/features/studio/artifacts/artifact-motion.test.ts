import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../../../app/globals.css", import.meta.url), "utf8");

test("Artifact open, edit, save, and conflict transitions stay in the product motion range", () => {
  assert.match(css, /artifact-surface-in 180ms/);
  assert.match(css, /artifact-editor-in 180ms/);
  assert.match(css, /artifact-state-in 160ms/);
  assert.match(css, /artifact-notice-in 180ms/);
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reduced, /\.artifact-workspace-surface/);
  assert.match(reduced, /\.artifact-editor-notice/);
});
