"use client";

import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

const TOP_PAD_PX = 24;
const COMPOSER_CLEARANCE_PX = 140;
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
    const visibleHeight = Math.max(160, container.clientHeight - COMPOSER_CLEARANCE_PX);
    const pinDistance = Math.max(0, anchorTop - TOP_PAD_PX);
    updateSpacer(visibleHeight - turnHeight - TOP_PAD_PX + pinDistance);

    if (mode === "pin" || (mode === "follow" && autoFollowRef.current)) {
      const target = turnHeight + TOP_PAD_PX <= visibleHeight
        ? Math.max(0, anchorTop - TOP_PAD_PX)
        : Math.max(0, endBottom - visibleHeight);
      container.scrollTop = target;
      previousScrollTopRef.current = target;
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
    const onScroll = () => {
      const top = container.scrollTop;
      const distance = container.scrollHeight - top - container.clientHeight;
      const movedUp = top < previousScrollTopRef.current - 2;
      if (movedUp && distance > NEAR_BOTTOM_PX) autoFollowRef.current = false;
      else if (distance <= NEAR_BOTTOM_PX) autoFollowRef.current = true;
      previousScrollTopRef.current = top;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    if (autoFollowRef.current) measure("follow");
  }, [measure, spacerPx]);

  return { anchorRef, endRef, scrollRef, spacerPx };
}
