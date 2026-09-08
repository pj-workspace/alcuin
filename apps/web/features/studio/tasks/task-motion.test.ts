import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("./task-motion.css", import.meta.url), "utf8");
const canvas = readFileSync(new URL("./task-canvas.tsx", import.meta.url), "utf8");

test("task motion uses the bounded 140, 180, and 220 millisecond contract", () => {
  assert.match(css, /--task-motion-fast: 140ms/);
  assert.match(css, /--task-motion-base: 180ms/);
  assert.match(css, /--task-motion-slow: 220ms/);
  assert.match(css, /task-status-enter var\(--task-motion-base\)/);
  assert.match(css, /task-result-enter var\(--task-motion-slow\)/);
});

test("waiting stops the working loop and reduced motion disables all task displacement", () => {
  assert.match(css, /\.task-step-row\[data-waiting\] \.task-step-node[^}]+animation: none/);
  assert.match(css, /task-canvas\[data-task-status="waiting_for_approval"\][\s\S]+?animation: none/);
  const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(reduced, /\.task-inline-status/);
  assert.match(reduced, /\.task-canvas/);
  assert.match(reduced, /\.task-step-working-dot/);
  assert.match(reduced, /animation: none !important/);
});

test("ordinary chat produces no Task DOM", () => {
  assert.match(canvas, /if \(!task\) return null/);
  assert.doesNotMatch(canvas, /version|publish|deploy|embed/i);
});
