import assert from "node:assert/strict";
import test from "node:test";

import { pinnedTurnSpacerPx } from "./use-pinned-turn-scroll.ts";
import { createScrollMeasureScheduler, type ScrollMeasureMode } from "./scroll-measure-scheduler.ts";

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

test("token, resize and spacer changes share one measurement per frame", () => {
  const frames = new Map<number, () => void>();
  const measured: ScrollMeasureMode[] = [];
  let nextId = 0;
  const scheduler = createScrollMeasureScheduler(
    (mode) => measured.push(mode),
    (callback) => { frames.set(++nextId, callback); return nextId; },
    (id) => { frames.delete(id); },
  );
  scheduler.schedule("follow");
  scheduler.schedule("pin");
  scheduler.schedule("follow");
  assert.equal(frames.size, 1);
  const callback = frames.get(1)!;
  frames.delete(1);
  callback();
  assert.deepEqual(measured, ["pin"]);
  scheduler.schedule("follow");
  assert.equal(frames.size, 1);
  frames.get(2)!();
  assert.deepEqual(measured, ["pin", "follow"]);
});

test("navigation and unmount cancel queued measurements without poisoning later turns", () => {
  const callbacks: Array<() => void> = [];
  const cancelled: number[] = [];
  const measured: ScrollMeasureMode[] = [];
  const scheduler = createScrollMeasureScheduler(
    (mode) => measured.push(mode),
    (callback) => { callbacks.push(callback); return callbacks.length; },
    (id) => cancelled.push(id),
  );
  scheduler.schedule("pin");
  scheduler.cancel();
  assert.deepEqual(cancelled, [1]);
  callbacks[0](); // A late, already-cancelled callback has no pending work.
  assert.deepEqual(measured, []);
  scheduler.schedule("follow");
  callbacks[0](); // A stale callback must not consume the next turn's work.
  assert.deepEqual(measured, []);
  callbacks[1]();
  assert.deepEqual(measured, ["follow"]);
});
