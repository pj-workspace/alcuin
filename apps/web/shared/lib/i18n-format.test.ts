import assert from "node:assert/strict";
import test from "node:test";

import { formatTranslation } from "./i18n-format.ts";

test("falls back to the English key when a locale entry is missing", () => {
  assert.equal(formatTranslation(undefined, "Context ledger"), "Context ledger");
});

test("formats placeholders in translated and fallback strings", () => {
  assert.equal(formatTranslation("{count} 条规则", "{count} rules", { count: 3 }), "3 条规则");
  assert.equal(formatTranslation(undefined, "{count} rules", { count: 3 }), "3 rules");
});
