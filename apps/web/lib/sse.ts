export type ParsedSseFrame =
  | { kind: "done" }
  | { kind: "event"; value: unknown }
  | { kind: "invalid" };

export function parseSseData(value: string): ParsedSseFrame {
  const trimmed = value.trim();
  if (trimmed === "[DONE]") return { kind: "done" };
  try {
    return { kind: "event", value: JSON.parse(trimmed) as unknown };
  } catch {
    return { kind: "invalid" };
  }
}

export function parseSseBuffer(buffer: string): {
  remainder: string;
  frames: ParsedSseFrame[];
} {
  const chunks = buffer.split("\n\n");
  const remainder = chunks.pop() ?? "";
  const frames: ParsedSseFrame[] = [];
  for (const chunk of chunks) {
    for (const line of chunk.split("\n")) {
      if (line.startsWith("data: ")) frames.push(parseSseData(line.slice(6)));
    }
  }
  return { remainder, frames };
}
