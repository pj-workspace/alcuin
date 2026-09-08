import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

test("only the conversation timeline can scroll", () => {
  const conversationPaneRule = css.match(/\.conversation-pane\s*\{[^}]+\}/)?.[0] ?? "";
  const timelineRule = css.match(/\.conversation-scroll\s*\{[^}]+\}/)?.[0] ?? "";

  assert.match(conversationPaneRule, /overflow:\s*clip/);
  assert.doesNotMatch(conversationPaneRule, /overflow:\s*(?:auto|scroll|hidden)/);
  assert.match(timelineRule, /overflow-y:\s*auto/);
  assert.doesNotMatch(timelineRule, /scroll-behavior:\s*smooth/);
});

test("the active turn always participates in live layout measurement", () => {
  const activeTurnRule = css.match(/\.conversation-turn\[data-active\]\s*\{[^}]+\}/)?.[0] ?? "";

  assert.match(activeTurnRule, /content-visibility:\s*visible/);
  assert.match(activeTurnRule, /contain-intrinsic-size:\s*none/);
});
