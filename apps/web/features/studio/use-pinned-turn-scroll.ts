"use client";

import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { createScrollMeasureScheduler, type ScrollMeasureMode } from "./scroll-measure-scheduler.ts";

const TOP_PAD_PX = 24;
const BOTTOM_CLEARANCE_PX = 24;
const NEAR_BOTTOM_PX = 88;

type PinnedTurnScroll = {
  anchorRef: RefObject<HTMLElement | null>;
  endRef: RefObject<HTMLElement | null>;
  scrollRef: RefObject<HTMLDivElement | null>;
  spacerPx: number;
  canJumpToLatest: boolean;
  jumpToLatest: () => void;
};

/**
 * Adds only the clearance needed to pin a short active turn near the viewport top.
 * The active turn's position in the full conversation must never contribute to the
 * spacer: doing so duplicates the whole history height and leaves a blank viewport.
 */
export function pinnedTurnSpacerPx(visibleHeight: number, turnHeight: number): number {
  return Math.max(0, Math.floor(visibleHeight - turnHeight - TOP_PAD_PX));
}

/** Pinning clearance is not unread content. Ignore it when resuming follow. */
export function latestContentDistance(scrollHeight: number, spacer: number, scrollTop: number, viewportHeight: number): number {
  return Math.max(0, scrollHeight - spacer - scrollTop - viewportHeight);
}

/** Pins the latest user turn near the viewport top and soft-follows long streams. */
export function usePinnedTurnScroll(
  turnKey: string,
  streamVersion: number,
): PinnedTurnScroll {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const anchorRef = useRef<HTMLElement | null>(null);
  const endRef = useRef<HTMLElement | null>(null);
  const activeTurnRef = useRef<string | null>(null);
  const autoFollowRef = useRef(true);
  const upwardIntentRef = useRef(false);
  const reducedMotionRef = useRef(false);
  const previousScrollTopRef = useRef(0);
  const spacerValueRef = useRef(0);
  const schedulerRef = useRef<ReturnType<typeof createScrollMeasureScheduler> | null>(null);
  const [spacerPx, setSpacerPx] = useState(0);
  const [canJumpToLatest, setCanJumpToLatest] = useState(false);

  const updateSpacer = useCallback((value: number) => {
    const next = Math.max(0, Math.floor(value));
    if (next === spacerValueRef.current) return;
    spacerValueRef.current = next;
    setSpacerPx(next);
  }, []);

  const measure = useCallback((mode: "pin" | "follow") => {
    const container = scrollRef.current;
    const anchor = anchorRef.current;
    const end = endRef.current;
    if (!container || !anchor || !end) {
      setCanJumpToLatest(false);
      updateSpacer(0);
      return;
    }
    // A hidden mobile Chat panel has no meaningful geometry. Preserve its
    // reading position until ResizeObserver reports the visible panel again.
    if (container.clientHeight === 0 || container.clientWidth === 0) return;

    // Read geometry together, before updating the spacer or scrolling.
    const containerRect = container.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    const endRect = end.getBoundingClientRect();
    const scrollTop = container.scrollTop;
    const anchorTop = anchorRect.top - containerRect.top + scrollTop;
    const endBottom = endRect.bottom - containerRect.top + scrollTop;
    const turnHeight = Math.max(anchorRect.height, endBottom - anchorTop);
    const visibleHeight = Math.max(160, container.clientHeight - BOTTOM_CLEARANCE_PX);
    const distance = latestContentDistance(container.scrollHeight, spacerValueRef.current, scrollTop, container.clientHeight);
    setCanJumpToLatest(!autoFollowRef.current && distance > NEAR_BOTTOM_PX);
    updateSpacer(pinnedTurnSpacerPx(visibleHeight, turnHeight));

    if (mode === "pin" || (mode === "follow" && autoFollowRef.current)) {
      const target = turnHeight + TOP_PAD_PX <= visibleHeight
        ? Math.max(0, anchorTop - TOP_PAD_PX)
        : Math.max(0, endBottom - visibleHeight);
      container.scrollTo({
        top: target,
        behavior: mode === "pin" && !reducedMotionRef.current ? "smooth" : "auto",
      });
      // `scrollTo({ behavior: "smooth" })` does not reach the target immediately.
      // Tracking the future target makes its intermediate frames look like a user
      // scrolling upward and disables stream following on the next scroll event.
      previousScrollTopRef.current = container.scrollTop;
    }
  }, [updateSpacer]);

  const scheduleMeasure = useCallback((mode: ScrollMeasureMode) => {
    schedulerRef.current ??= createScrollMeasureScheduler(
      measure,
      (callback) => window.requestAnimationFrame(callback),
      (handle) => window.cancelAnimationFrame(handle),
    );
    schedulerRef.current.schedule(mode);
  }, [measure]);

  const jumpToLatest = useCallback(() => {
    upwardIntentRef.current = false;
    autoFollowRef.current = true;
    setCanJumpToLatest(false);
    // Resume immediately rather than queueing a smooth scroll behind live tokens.
    scrollRef.current?.focus({ preventScroll: true });
    scheduleMeasure("follow");
  }, [scheduleMeasure]);

  useLayoutEffect(() => () => {
    schedulerRef.current?.cancel();
  }, [turnKey]);

  useLayoutEffect(() => {
    const isNewTurn = activeTurnRef.current !== turnKey;
    if (isNewTurn) {
      activeTurnRef.current = turnKey;
      upwardIntentRef.current = false;
      autoFollowRef.current = true;
      setCanJumpToLatest(false);
      scheduleMeasure("pin");
      return;
    }
    scheduleMeasure("follow");
  }, [scheduleMeasure, streamVersion, turnKey]);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncMotion = () => { reducedMotionRef.current = motion.matches; };
    syncMotion();
    motion.addEventListener("change", syncMotion);
    const onScroll = () => {
      const top = container.scrollTop;
      const distance = latestContentDistance(container.scrollHeight, spacerValueRef.current, top, container.clientHeight);
      const movedUp = top < previousScrollTopRef.current - 2;
      if (movedUp && distance > NEAR_BOTTOM_PX) autoFollowRef.current = false;
      else if (distance <= NEAR_BOTTOM_PX && !upwardIntentRef.current) autoFollowRef.current = true;
      setCanJumpToLatest(!autoFollowRef.current && distance > NEAR_BOTTOM_PX);
      previousScrollTopRef.current = top;
    };
    // Wheel/touch/key intent arrives before the browser's scroll event. Stop
    // an already scheduled stream-follow frame from overwriting that input.
    const onDirection = (upward: boolean) => {
      upwardIntentRef.current = upward;
      if (upward) autoFollowRef.current = false;
    };
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY !== 0) onDirection(event.deltaY < 0);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || target.closest("input, textarea, select"))) return;
      if (["ArrowUp", "PageUp", "Home"].includes(event.key)) onDirection(true);
      else if (["ArrowDown", "PageDown", "End"].includes(event.key)) onDirection(false);
    };
    let touchY: number | null = null;
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? null; };
    const onTouchMove = (event: TouchEvent) => {
      const next = event.touches[0]?.clientY;
      if (next !== undefined && touchY !== null && Math.abs(next - touchY) > 2) onDirection(next > touchY);
      touchY = next ?? null;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    container.addEventListener("wheel", onWheel, { passive: true });
    container.addEventListener("keydown", onKeyDown);
    container.addEventListener("touchstart", onTouchStart, { passive: true });
    container.addEventListener("touchmove", onTouchMove, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("keydown", onKeyDown);
      container.removeEventListener("touchstart", onTouchStart);
      container.removeEventListener("touchmove", onTouchMove);
      motion.removeEventListener("change", syncMotion);
    };
  }, []);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    const anchor = anchorRef.current;
    const end = endRef.current;
    if (!container || !anchor || !end || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      scheduleMeasure("follow");
    });
    observer.observe(container);
    observer.observe(anchor);
    observer.observe(end);
    return () => observer.disconnect();
  }, [scheduleMeasure, turnKey]);

  useLayoutEffect(() => {
    if (autoFollowRef.current) scheduleMeasure("follow");
  }, [scheduleMeasure, spacerPx]);

  return { anchorRef, endRef, scrollRef, spacerPx, canJumpToLatest, jumpToLatest };
}
