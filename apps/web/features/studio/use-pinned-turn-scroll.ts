"use client";

import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

const TOP_PAD_PX = 24;
const BOTTOM_CLEARANCE_PX = 24;
const NEAR_BOTTOM_PX = 88;

type PinnedTurnScroll = {
  anchorRef: RefObject<HTMLElement | null>;
  endRef: RefObject<HTMLElement | null>;
  scrollRef: RefObject<HTMLDivElement | null>;
  spacerPx: number;
};

function relativeTop(container: HTMLElement, element: HTMLElement): number {
  const containerRect = container.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  return elementRect.top - containerRect.top + container.scrollTop;
}

/**
 * Adds only the clearance needed to pin a short active turn near the viewport top.
 * The active turn's position in the full conversation must never contribute to the
 * spacer: doing so duplicates the whole history height and leaves a blank viewport.
 */
export function pinnedTurnSpacerPx(visibleHeight: number, turnHeight: number): number {
  return Math.max(0, Math.floor(visibleHeight - turnHeight - TOP_PAD_PX));
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
  const reducedMotionRef = useRef(false);
  const previousScrollTopRef = useRef(0);
  const spacerValueRef = useRef(0);
  const [spacerPx, setSpacerPx] = useState(0);

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
    if (!container || !anchor || !end) return;

    const anchorTop = relativeTop(container, anchor);
    const endBottom = relativeTop(container, end) + end.offsetHeight;
    const turnHeight = Math.max(anchor.offsetHeight, endBottom - anchorTop);
    const visibleHeight = Math.max(160, container.clientHeight - BOTTOM_CLEARANCE_PX);
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

  useLayoutEffect(() => {
    const isNewTurn = activeTurnRef.current !== turnKey;
    if (isNewTurn) {
      activeTurnRef.current = turnKey;
      autoFollowRef.current = true;
      measure("pin");
      return;
    }
    measure("follow");
  }, [measure, streamVersion, turnKey]);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncMotion = () => { reducedMotionRef.current = motion.matches; };
    syncMotion();
    motion.addEventListener("change", syncMotion);
    const onScroll = () => {
      const top = container.scrollTop;
      const distance = container.scrollHeight - top - container.clientHeight;
      const movedUp = top < previousScrollTopRef.current - 2;
      if (movedUp && distance > NEAR_BOTTOM_PX) autoFollowRef.current = false;
      else if (distance <= NEAR_BOTTOM_PX) autoFollowRef.current = true;
      previousScrollTopRef.current = top;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      motion.removeEventListener("change", syncMotion);
    };
  }, []);

  useLayoutEffect(() => {
    const container = scrollRef.current;
    const anchor = anchorRef.current;
    const end = endRef.current;
    if (!container || !anchor || !end || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (autoFollowRef.current) measure("follow");
    });
    observer.observe(container);
    observer.observe(anchor);
    observer.observe(end);
    return () => observer.disconnect();
  }, [measure, turnKey]);

  useLayoutEffect(() => {
    if (autoFollowRef.current) measure("follow");
  }, [measure, spacerPx]);

  return { anchorRef, endRef, scrollRef, spacerPx };
}
