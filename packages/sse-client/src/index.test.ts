import assert from "node:assert/strict";
import test from "node:test";

import { parseSseBuffer, streamJsonSse } from "./index.ts";

function response(...chunks: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}

test("parses standard ids, event names, CRLF, and multiline JSON data", () => {
  const parsed = parseSseBuffer(
    ": heartbeat\r\nid: 7\r\nevent: message.delta\r\ndata: {\"delta\":\r\ndata: \"hello\"}\r\n\r\n",
  );
  assert.deepEqual(parsed.frames, [{
    kind: "event",
    id: "7",
    event: "message.delta",
    value: { delta: "hello" },
  }]);
  assert.equal(parsed.remainder, "");
});

test("reconnects with Last-Event-ID and suppresses replayed events", async () => {
  const calls: string[] = [];
  const values: number[] = [];
  const fetcher: typeof fetch = async (_input, init) => {
    const headers = new Headers(init?.headers);
    calls.push(headers.get("Last-Event-ID") ?? "");
    if (calls.length === 1) return response('id: 1\ndata: {"value":1}\n\n');
    return response(
      'id: 1\ndata: {"value":1}\n\n',
      'id: 2\ndata: {"value":2}\n\n',
      "data: [DONE]\n\n",
    );
  };

  const result = await streamJsonSse({
    url: "https://alcuin.test/events",
    fetcher,
    reconnectDelayMs: 0,
    onEvent(value) {
      values.push((value as { value: number }).value);
    },
  });

  assert.deepEqual(calls, ["", "1"]);
  assert.deepEqual(values, [1, 2]);
  assert.deepEqual(result, { lastEventId: 2, reconnects: 1 });
});

test("accepts a protocol-specific terminal JSON event without a DONE sentinel", async () => {
  const result = await streamJsonSse({
    url: "https://alcuin.test/events",
    fetcher: async () => response('id: 4\ndata: {"type":"done"}\n\n'),
    onEvent() {},
    isTerminal(value) {
      return (value as { type?: string }).type === "done";
    },
  });
  assert.deepEqual(result, { lastEventId: 4, reconnects: 0 });
});
