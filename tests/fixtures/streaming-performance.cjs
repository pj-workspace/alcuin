/* Browser init script and CommonJS fixture; no dependencies and no production writes.
 * CDP: source + '\ninstallStreamingBenchmark({ durationMs: 5000 });'
 * Playwright: page.addInitScript(installStreamingBenchmark, { durationMs: 5000 });
 * Read window.__streamingBenchmark.snapshot(); never inspect real message content.
 */
function installStreamingBenchmark(config = {}) {
  if (window.__streamingBenchmark) window.__streamingBenchmark.uninstall();
  const durationMs = Math.max(100, config.durationMs || 5000);
  const batchMs = Math.max(10, config.batchMs || 100);
  const fragments = Math.max(2, config.fragments || 3200);
  const timestamp = "2026-09-12T00:00:00.000Z";
  const threadId = "thr_streaming_benchmark";
  const runId = "run_streaming_benchmark";
  const input = "Run the isolated streaming benchmark.";
  const reasoning = ("检查固定的合成材料，逐项比较数据与来源。这里仅用于评估流式渲染的连续性，不调用外部工具。\n\n").repeat(130).slice(0, 6000);
  const section = "\n## 固定数据检查\n\n- 第一项：对照输入记录。\n- 第二项：验证输出顺序。\n\n| 项目 | 状态 |\n| --- | --- |\n| 输入 | 完整 |\n| 输出 | 连续 |\n\n```javascript\nconst result = { complete: true };\n```\n\n参考 [固定示例](https://example.com/benchmark)。\n";
  const answer = "基准正文开始。\n" + section.repeat(10) + "\nBENCHMARK_COMPLETE_3200\n";
  const marker = "BENCHMARK_COMPLETE_3200";
  const originalFetch = window.fetch.bind(window);
  let created = false, accepted = false, completed = false, bootstrap;
  let timers = [], observer, longObserver, raf = 0;
  let metrics, frameGaps, start = 0, lastFrame = 0;
  const reset = () => {
    metrics = { firstDeltaMs: -1, firstAnswerDeltaMs: -1, firstAnswerDomMs: -1,
      finalDeltaMs: -1, finalDomMs: -1, markdownMutations: 0, longTasks: 0,
      longTaskMs: 0, maxLongTaskMs: 0, emittedFragments: 0, emittedReasoningChars: 0,
      emittedAnswerChars: 0, blockedWrites: 0, streamConnections: 0 };
    frameGaps = []; start = 0; lastFrame = 0;
  };
  reset();
  const elapsed = () => start ? performance.now() - start : -1;
  const selector = config.markdownSelector || ".agent-turn .markdown-content";
  const markdownRegions = `${selector}, .agent-turn .thinking-md`;
  const observeDom = () => {
    if (!document.documentElement) return;
    observer = new MutationObserver((records) => {
      if (!start) return;
      const relevant = records.filter((record) => {
        const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        return target && (target.closest?.(markdownRegions) || [...record.addedNodes].some((node) => node.nodeType === 1 && (node.matches?.(markdownRegions) || node.querySelector?.(markdownRegions))));
      });
      if (!relevant.length) return;
      metrics.markdownMutations += relevant.length;
      const text = [...document.querySelectorAll(selector)].map((node) => node.textContent || "").join("");
      if (metrics.firstAnswerDomMs < 0 && text.includes("基准正文开始")) metrics.firstAnswerDomMs = elapsed();
      if (metrics.finalDomMs < 0 && text.includes(marker)) metrics.finalDomMs = elapsed();
    });
    observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  };
  if (document.documentElement) observeDom();
  else document.addEventListener("DOMContentLoaded", observeDom, { once: true });
  try {
    longObserver = new PerformanceObserver((list) => {
      if (!start) return;
      for (const entry of list.getEntries()) if (entry.startTime >= start && (metrics.finalDomMs < 0 || entry.startTime <= start + metrics.finalDomMs)) {
        metrics.longTasks++; metrics.longTaskMs += entry.duration;
        metrics.maxLongTaskMs = Math.max(metrics.maxLongTaskMs, entry.duration);
      }
    });
    longObserver.observe({ entryTypes: ["longtask"] });
  } catch { /* Unsupported browsers still report frame and DOM metrics. */ }
  const frame = (now) => {
    if (start && metrics.finalDomMs < 0) {
      if (lastFrame) frameGaps.push(now - lastFrame);
      lastFrame = now;
    }
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
  const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  const thread = () => ({ id: threadId, workspace_id: bootstrap?.workspace?.id || "ws_demo",
    agent_id: bootstrap?.agents?.[0]?.id || "agt_starter", agent_version_id: bootstrap?.agents?.[0]?.current_version_id || "av_benchmark",
    title: "Streaming benchmark", title_status: "ready", context: {}, created_at: timestamp, updated_at: timestamp,
    last_message_sequence: completed ? 2 : accepted ? 1 : 0 });
  const run = () => ({ id: runId, workspace_id: thread().workspace_id, thread_id: threadId,
    agent_version_id: thread().agent_version_id, status: completed ? "completed" : "running", input,
    input_message_id: "msg_benchmark_user", output_message_id: completed ? "msg_benchmark_answer" : null,
    created_at: timestamp, completed_at: completed ? new Date(Date.parse(timestamp) + durationMs).toISOString() : null });
  const message = (role, text, sequence) => ({ id: role === "user" ? "msg_benchmark_user" : "msg_benchmark_answer",
    workspace_id: thread().workspace_id, thread_id: threadId, run_id: runId, agent_version_id: thread().agent_version_id,
    sequence, role, status: "completed", parts: [{ type: "text", text }], created_at: timestamp });
  const events = [];
  const emittedSequences = new Set();
  const reasoningCount = Math.floor(fragments * 0.75);
  const split = (text, count) => Array.from({ length: count }, (_, i) => text.slice(Math.floor(i * text.length / count), Math.floor((i + 1) * text.length / count)));
  const pieces = [...split(reasoning, reasoningCount).map((text) => ({ type: "thinking-delta", textDelta: text })),
    ...split(answer, fragments - reasoningCount).map((text) => ({ type: "text-delta", textDelta: text }))];
  function stream(url, init, request) {
    metrics.streamConnections++;
    const cursor = Math.max(Number(url.searchParams.get("after") || 0), Number(new Headers(init?.headers || request?.headers).get("Last-Event-ID") || 0));
    const compact = url.searchParams.get("protocol") === "chat";
    let cancelled = false;
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        if (!start) start = performance.now();
        const emit = (sequence, data) => {
          if (cancelled || sequence <= cursor) return;
          const event = { ...data, eventId: `benchmark_${sequence}`, runId, sequence,
            timestamp: new Date(Date.parse(timestamp) + Math.round(sequence * durationMs / (fragments + 2))).toISOString() };
          const canonical = { id: event.eventId, workspace_id: thread().workspace_id, run_id: runId, sequence,
            timestamp: event.timestamp, type: ({ meta: "run.started", "thinking-delta": "reasoning.delta", "text-delta": "message.delta", done: "run.completed" })[event.type],
            payload: event.textDelta !== undefined ? { delta: event.textDelta } : event.type === "done" ? { status: "completed" } : { model: "benchmark", thinking: true } };
          if (!emittedSequences.has(sequence)) { emittedSequences.add(sequence); events.push(canonical); }
          controller.enqueue(encoder.encode(`id: ${sequence}\n${compact ? "" : `event: ${canonical.type}\n`}data: ${JSON.stringify(compact ? event : canonical)}\n\n`));
        };
        emit(1, { type: "meta", provider: "benchmark", chatModel: "benchmark", thinking: true, reasoningEffort: "high" });
        const batches = Math.ceil(durationMs / batchMs);
        for (let batch = 0; batch < batches; batch++) timers.push(setTimeout(() => {
          if (cancelled) return;
          const from = Math.floor(batch * pieces.length / batches), to = Math.floor((batch + 1) * pieces.length / batches);
          for (let i = from; i < to; i++) {
            if (i + 2 <= cursor) continue;
            if (metrics.firstDeltaMs < 0) metrics.firstDeltaMs = elapsed();
            const piece = pieces[i];
            if (piece.type === "text-delta" && metrics.firstAnswerDeltaMs < 0) metrics.firstAnswerDeltaMs = elapsed();
            metrics.emittedFragments++;
            metrics[piece.type === "text-delta" ? "emittedAnswerChars" : "emittedReasoningChars"] += piece.textDelta.length;
            emit(i + 2, piece);
          }
          if (batch === batches - 1) {
            metrics.finalDeltaMs = elapsed(); completed = true;
            emit(fragments + 2, { type: "done" });
            controller.enqueue(encoder.encode("data: [DONE]\n\n")); controller.close();
          }
        }, Math.min(durationMs, (batch + 1) * batchMs)));
        const signal = init?.signal || request?.signal;
        signal?.addEventListener("abort", () => { cancelled = true; try { controller.close(); } catch {} }, { once: true });
      },
      cancel() { cancelled = true; },
    });
    return new Response(body, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" } });
  }
  window.fetch = async function(inputRequest, init) {
    const request = inputRequest instanceof Request ? inputRequest : undefined;
    const url = new URL(request?.url || String(inputRequest), location.href);
    const method = (init?.method || request?.method || "GET").toUpperCase();
    const path = url.pathname;
    if (!path.startsWith("/v1/")) return originalFetch(inputRequest, init);
    if (path === "/v1/bootstrap" && method === "GET") {
      bootstrap = await (await originalFetch(inputRequest, init)).json();
      return json({ ...bootstrap, threads: created ? [thread()] : [], runs: accepted ? [run()] : [] });
    }
    if (path === "/v1/threads" && method === "POST") { created = true; return json(thread(), 201); }
    if (/^\/v1\/threads\/[^/]+\/runs$/.test(path) && method === "POST") {
      if (!path.includes(threadId)) { metrics.blockedWrites++; return json({ detail: "Select a new benchmark conversation" }, 409); }
      if (accepted) return json({ detail: "Reload to start a fresh benchmark sample" }, 409);
      accepted = true; completed = false; return json(run(), 202);
    }
    if (path.startsWith(`/v1/threads/${threadId}`)) {
      if (path.endsWith("/title") || path.endsWith("/title/ensure")) return json(thread());
      if (path.endsWith("/configuration")) return json({ thread_id: threadId, workspace_id: thread().workspace_id,
        agent_version_id: thread().agent_version_id, revision: 0, active_skill_version_ids: [], manual_rule_version_ids: [], created_at: timestamp, updated_at: timestamp });
      if (path.endsWith("/artifacts")) return json([]);
      const messages = accepted ? [message("user", input, 1), ...(completed ? [message("assistant", answer, 2)] : [])] : [];
      if (path.endsWith("/messages")) return json(messages);
      return json({ thread: thread(), messages, runs: accepted ? [run()] : [], citation_events: [] });
    }
    if (path.startsWith(`/v1/runs/${runId}`)) {
      if (path.endsWith("/events")) return stream(url, init, request);
      if (path.endsWith("/citations")) return json([]);
      if (path.endsWith("/context")) return json({ detail: "Synthetic benchmark has no context trace" }, 404);
      return json({ ...run(), events });
    }
    if (method !== "GET" && method !== "HEAD") { metrics.blockedWrites++; return json({ detail: "Writes disabled by isolated benchmark" }, 409); }
    if (/^\/v1\/(threads|runs|tasks)(\/|$)/.test(path)) return json([]);
    return originalFetch(inputRequest, init);
  };
  window.__streamingBenchmark = {
    expected: { reasoning, answer, fragments, threadId, runId }, reset,
    snapshot() {
      const sorted = [...frameGaps].sort((a, b) => a - b);
      return { ...metrics, frameCount: sorted.length, frameGapP95Ms: sorted[Math.floor(sorted.length * .95)] || 0,
        maxFrameGapMs: sorted.at(-1) || 0, framesOver50Ms: sorted.filter((gap) => gap > 50).length,
        finalDeltaToDomMs: metrics.finalDomMs < 0 || metrics.finalDeltaMs < 0 ? -1 : metrics.finalDomMs - metrics.finalDeltaMs,
        answerDeltaToFirstDomMs: metrics.firstAnswerDomMs < 0 || metrics.firstAnswerDeltaMs < 0 ? -1 : metrics.firstAnswerDomMs - metrics.firstAnswerDeltaMs };
    },
    uninstall() { timers.forEach(clearTimeout); observer?.disconnect(); longObserver?.disconnect(); cancelAnimationFrame(raf); window.fetch = originalFetch; delete window.__streamingBenchmark; },
  };
  return window.__streamingBenchmark;
}
if (typeof module !== "undefined") module.exports = { installStreamingBenchmark };
