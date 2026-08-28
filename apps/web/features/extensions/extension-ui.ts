import type {
  Agent,
  Artifact,
  ExecutionEvent,
  Extension,
  ExtensionUIBlock,
} from "@alcuin/contracts";

export interface ResolvedExtensionUIBlock {
  key: string;
  extensionId: string;
  extensionName: string;
  manifestId: string;
  builtin: boolean;
  block: ExtensionUIBlock;
}

export function extensionToolName(extension: Extension, rawName: string) {
  const builtin = extension.manifest.entrypoints.some((entrypoint) => entrypoint.type === "builtin");
  return builtin ? rawName : `extension.${extension.manifest.id}.${rawName}`;
}

export function resolvedExtensionToolName(
  resolved: ResolvedExtensionUIBlock,
  rawName: string,
) {
  return resolved.builtin ? rawName : `extension.${resolved.manifestId}.${rawName}`;
}

export function resolveExtensionUIBlocks(
  agent: Agent,
  extensions: Extension[],
): ResolvedExtensionUIBlock[] {
  const bound = new Set(agent.definition.extensions);
  return extensions.flatMap((extension) => {
    if (
      !bound.has(extension.manifest.id)
      || extension.status !== "enabled"
      || !["healthy", "degraded"].includes(extension.health)
    ) return [];
    const allowedTools = new Set(agent.definition.tools);
    return extension.manifest.contributions.ui_blocks.filter((block) => {
      const rawTool = block.type === "form"
        ? block.submit.tool
        : block.source.kind === "tool_result"
          ? block.source.tool
          : undefined;
      return !rawTool || allowedTools.has(extensionToolName(extension, rawTool));
    }).map((block) => ({
      key: `${extension.manifest.id}:${block.id}`,
      extensionId: extension.id,
      extensionName: extension.name,
      manifestId: extension.manifest.id,
      builtin: extension.manifest.entrypoints.some((entrypoint) => entrypoint.type === "builtin"),
      block,
    }));
  });
}

export function valueAtPath(value: unknown, path = ""): unknown {
  if (!path) return value;
  if (path.split(".").some((segment) => ["__proto__", "prototype", "constructor"].includes(segment))) {
    return undefined;
  }
  return path.split(".").reduce<unknown>((current, segment) => {
    if (Array.isArray(current) && /^\d+$/.test(segment)) return current[Number(segment)];
    if (current && typeof current === "object") {
      return (current as Record<string, unknown>)[segment];
    }
    return undefined;
  }, value);
}

export function dataForExtensionBlock(
  resolved: ResolvedExtensionUIBlock,
  events: ExecutionEvent[],
  context: Record<string, unknown>,
): unknown {
  const block = resolved.block;
  if (block.type === "form") return undefined;
  const source = block.source;
  let root: unknown;
  if (source.kind === "context") {
    root = context;
  } else if (source.kind === "artifact") {
    root = [...events].reverse().find((event) => event.type === "artifact.updated")
      ?.payload.artifact as Artifact | undefined;
  } else {
    const builtinTool = source.tool ?? "";
    const canonicalTool = resolvedExtensionToolName(resolved, builtinTool);
    root = [...events].reverse().find((event) =>
      event.type === "tool.completed"
      && event.payload.status === "succeeded"
      && event.payload.tool === canonicalTool
    )?.payload.result;
  }
  return valueAtPath(root, source.path);
}

export function formatExtensionUIValue(value: unknown, format = "text"): string {
  if (value === null || value === undefined || value === "") return "—";
  if (format === "json" || typeof value === "object") {
    const serialized = JSON.stringify(value);
    return serialized.length > 240 ? `${serialized.slice(0, 237)}…` : serialized;
  }
  if (format === "number") {
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat().format(number) : String(value);
  }
  if (format === "date") {
    const date = new Date(String(value));
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
  }
  return String(value);
}
