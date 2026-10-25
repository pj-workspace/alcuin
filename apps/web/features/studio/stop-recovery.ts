/** Bounded recovery only for a Run whose stop request was accepted. */
export const STOP_RECOVERY_DELAYS = [1_000, 2_000, 4_000, 8_000] as const;

export function stopRecoveryDelay(attempt: number): number | null {
  return STOP_RECOVERY_DELAYS[attempt] ?? null;
}
