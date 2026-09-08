import assert from "node:assert/strict";
import test from "node:test";

import { pinnedTurnSpacerPx } from "./use-pinned-turn-scroll.ts";

test("short active turns receive only viewport clearance", () => {
  assert.equal(pinnedTurnSpacerPx(760, 260), 476);
});

test("long active turns do not create trailing blank space", () => {
  assert.equal(pinnedTurnSpacerPx(760, 900), 0);
});

test("conversation history position never inflates the spacer", () => {
  const spacer = pinnedTurnSpacerPx(760, 260);
  assert.ok(spacer < 760);
});
