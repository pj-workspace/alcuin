export async function boundedRunSnapshot<T>(read: (signal: AbortSignal) => Promise<T>, timeoutMs = 5_000): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      read(controller.signal),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new DOMException("Run snapshot timed out", "TimeoutError"));
        }, timeoutMs);
      }),
    ]);
  } finally { clearTimeout(timer); }
}

export function isCurrentRunScope(scope: {
  mounted: boolean; loading: boolean; threadId: string | null;
  activeRunId: string | null; latestRunId: string | null;
}, run: { id: string; thread_id: string }): boolean {
  return scope.mounted && !scope.loading && scope.threadId === run.thread_id
    && (scope.activeRunId === run.id || (scope.activeRunId === null && scope.latestRunId === run.id));
}
