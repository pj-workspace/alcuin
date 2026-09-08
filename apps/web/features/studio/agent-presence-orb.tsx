"use client";

import { clsx } from "clsx";
import { useEffect, useRef, useState } from "react";
import { ThinkingOrb, type OrbSize, type OrbState } from "thinking-orbs";

// State changes stay perceptible without making the Agent feel sluggish. The orb's
// own continuous motion remains calm; only the crossfade follows the 180 ms UI token.
const ORB_CROSSFADE_MS = 180;

export function AgentPresenceOrb({
  state,
  active = false,
  size = 20,
  className,
}: {
  state: OrbState;
  active?: boolean;
  size?: number;
  className?: string;
}) {
  const [shown, setShown] = useState(state);
  const [leaving, setLeaving] = useState<OrbState | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const shownRef = useRef(state);
  const transitionTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (state === shownRef.current) return;

    if (transitionTimerRef.current !== null) {
      window.clearTimeout(transitionTimerRef.current);
      transitionTimerRef.current = null;
    }

    const previous = shownRef.current;
    shownRef.current = state;
    setShown(state);

    if (reducedMotion) {
      setLeaving(null);
      return;
    }

    setLeaving(previous);
    transitionTimerRef.current = window.setTimeout(() => {
      setLeaving(null);
      transitionTimerRef.current = null;
    }, ORB_CROSSFADE_MS);
  }, [reducedMotion, state]);

  useEffect(() => () => {
    if (transitionTimerRef.current !== null) {
      window.clearTimeout(transitionTimerRef.current);
    }
  }, []);

  const preset = orbPreset(size);
  const canvasStyle = { width: size, height: size };
  const layerStyle = { width: size, height: size };

  return (
    <span
      className={clsx("agent-presence-orb", className)}
      style={{
        position: "relative",
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flex: "0 0 auto",
        overflow: "hidden",
        lineHeight: 0,
      }}
      aria-hidden
    >
      {leaving && !reducedMotion && (
        <span
          className="presence-orb-layer presence-orb-layer-out"
          style={{
            ...layerStyle,
            position: "absolute",
            inset: 0,
            animationDuration: `${ORB_CROSSFADE_MS}ms`,
          }}
        >
          <ThinkingOrb
            state={leaving}
            size={preset}
            theme="auto"
            speed={orbSpeed(leaving, active)}
            style={canvasStyle}
          />
        </span>
      )}
      <span
        className={clsx("presence-orb-layer", leaving && !reducedMotion && "presence-orb-layer-in")}
        style={{
          ...layerStyle,
          animationDuration: `${ORB_CROSSFADE_MS}ms`,
        }}
      >
        <ThinkingOrb
          state={shown}
          size={preset}
          theme="auto"
          speed={orbSpeed(shown, active)}
          paused={reducedMotion}
          style={canvasStyle}
        />
      </span>
    </span>
  );
}

function orbPreset(size: number): OrbSize {
  return size <= 32 ? 20 : 64;
}

function orbSpeed(state: OrbState, active: boolean): number {
  if (!active) return 0.72;
  if (state === "searching" || state === "solving") return 1.08;
  if (state === "connecting") return 1.05;
  if (state === "composing" || state === "weaving") return 1.02;
  return 1;
}
