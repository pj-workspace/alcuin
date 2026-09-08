import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../../../app/globals.css", import.meta.url), "utf8");

test("attachment transitions are bounded and reduced-motion disables continuous feedback", () => {
  assert.match(css, /composer-attachment-enter 180ms/);
  assert.match(css, /composer-attachment-remove 160ms/);
  assert.match(css, /attachment-status-in 180ms/);
  assert.match(css, /attachment-drop-in 180ms/);
  assert.match(css, /preview-content-in var\(--motion-base\)/);
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reduced, /\.composer-attachment/);
  assert.match(reduced, /\.attachment-loader \{ animation: none !important; \}/);
});
