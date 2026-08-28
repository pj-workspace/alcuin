#!/usr/bin/env node

import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import type { ExtensionManifest } from "@alcuin/contracts";

import { MCP_PYTHON_SDK_SPECIFIER, validateExtensionManifest } from "./index.js";

const SCHEMA_URL = "https://alcuin.dev/schemas/alcuin-extension.schema.json";

function argument(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function usage(): never {
  console.error(`Usage:
  alcuin-extension validate <alcuin.extension.json>
  alcuin-extension create <directory> --id <manifest-id> --name <display-name>`);
  process.exit(2);
}

async function validateFile(path: string) {
  const source = await readFile(resolve(path), "utf8");
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid JSON";
    console.error(`ERROR $: ${message}`);
    process.exitCode = 1;
    return;
  }
  const result = validateExtensionManifest(value);
  result.errors.forEach((issue) => console.error(`ERROR ${issue.path}: ${issue.message}`));
  result.warnings.forEach((issue) => console.warn(`WARN  ${issue.path}: ${issue.message}`));
  if (!result.valid) {
    process.exitCode = 1;
    return;
  }
  const manifest = value as ExtensionManifest;
  console.log(`Valid Alcuin extension: ${manifest.id}@${manifest.version}`);
}

async function directoryIsNonEmpty(path: string): Promise<boolean> {
  try {
    await access(path);
    return (await readdir(path)).length > 0;
  } catch {
    return false;
  }
}

async function createExtension(directory: string) {
  const id = argument("--id");
  const name = argument("--name");
  if (!id || !name) usage();
  const target = resolve(directory);
  if (await directoryIsNonEmpty(target)) {
    throw new Error(`Target directory is not empty: ${target}`);
  }
  const manifest: ExtensionManifest = {
    $schema: SCHEMA_URL,
    manifest_version: "1",
    id,
    name,
    version: "0.1.0",
    description: `${name} MCP extension for Alcuin.`,
    compatibility: ">=0.1.0",
    contributions: {
      tools: [
        {
          name: "echo",
          description: "Return a message to verify the extension transport.",
          input_schema: {
            type: "object",
            properties: { message: { type: "string" } },
            required: ["message"],
            additionalProperties: false,
          },
          mutating: false,
          approval: "auto",
        },
      ],
      skills: [],
      agent_templates: [],
      knowledge_connectors: [],
      ui_blocks: [],
    },
    entrypoints: [
      {
        type: "mcp",
        transport: "stdio",
        command: "uv",
        args: ["run", "--with", MCP_PYTHON_SDK_SPECIFIER, "python", "server.py"],
        cwd: target,
      },
    ],
    config_schema: { type: "object", additionalProperties: false },
    permissions: [
      {
        id: "tools:read",
        reason: "Call the extension's read-only MCP tools",
        risk: "low",
        required: true,
      },
    ],
    credential_requirements: [],
  };
  const validation = validateExtensionManifest(manifest);
  if (!validation.valid) {
    throw new Error(validation.errors.map((issue) => `${issue.path}: ${issue.message}`).join("\n"));
  }
  const server = `from mcp.server.fastmcp import FastMCP\nfrom mcp.types import ToolAnnotations\n\n\nmcp = FastMCP(${JSON.stringify(id)}, json_response=True)\n\n\n@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\ndef echo(message: str) -> dict[str, str]:\n    \"\"\"Return a message to verify the extension transport.\"\"\"\n    return {\"message\": message}\n\n\nif __name__ == \"__main__\":\n    mcp.run(transport=\"stdio\")\n`;
  const readme = `# ${name}\n\nGenerated with \`@alcuin/extension-sdk\`.\n\nValidate the manifest:\n\n\`\`\`bash\npnpm --filter @alcuin/extension-sdk exec alcuin-extension validate alcuin.extension.json\n\`\`\`\n\nImport \`alcuin.extension.json\` from Alcuin Extension Center, review permissions, install disabled, run the live health check, then enable it.\n`;
  await mkdir(target, { recursive: true });
  await writeFile(resolve(target, "alcuin.extension.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  await writeFile(resolve(target, "server.py"), server);
  await writeFile(resolve(target, "README.md"), readme);
  console.log(`Created ${id} in ${target}`);
}

async function main() {
  const command = process.argv[2];
  const path = process.argv[3];
  if (!command || !path) usage();
  if (command === "validate") return validateFile(path);
  if (command === "create") return createExtension(path);
  usage();
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : "Extension command failed");
  process.exitCode = 1;
});
