export type ScrollMeasureMode = "pin" | "follow";

/** Coalesce token, resize and spacer notifications into one read/write pass. */
export function createScrollMeasureScheduler(
  measure: (mode: ScrollMeasureMode) => void,
  requestFrame: (callback: () => void) => number,
  cancelFrame: (handle: number) => void,
) {
  let frame: number | null = null;
  let pending: ScrollMeasureMode | null = null;
  let generation = 0;
  return {
    schedule(mode: ScrollMeasureMode) {
      // A new turn must still pin even when a resize follows in the same frame.
      if (mode === "pin" || pending === null) pending = mode;
      if (frame !== null) return;
      const scheduledGeneration = generation;
      frame = requestFrame(() => {
        if (scheduledGeneration !== generation) return;
        const next = pending;
        frame = null;
        pending = null;
        if (next) measure(next);
      });
    },
    cancel() {
      generation += 1;
      if (frame !== null) cancelFrame(frame);
      frame = null;
      pending = null;
    },
  };
}
