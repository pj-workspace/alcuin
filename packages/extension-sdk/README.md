# @alcuin/extension-sdk

Typed authoring, offline conformance checks, stable tool ids, and a runnable
stdio MCP scaffold for Alcuin extensions.

```ts
import { defineExtension, extensionToolId } from "@alcuin/extension-sdk";

const manifest = defineExtension({
  manifest_version: "1",
  id: "acme.support",
  name: "Acme Support",
  version: "0.1.0",
  description: "Support operations exposed through MCP.",
  compatibility: ">=0.1.0",
  contributions: {
    tools: [],
    skills: [],
    agent_templates: [],
    knowledge_connectors: [],
    ui_blocks: [],
  },
  entrypoints: [
    { type: "mcp", transport: "streamable_http", url: "https://example.com/mcp" },
  ],
  config_schema: { type: "object" },
  permissions: [],
  credential_requirements: [],
});

extensionToolId(manifest.id, "search");
```

Create a runnable local stdio MCP extension:

```bash
pnpm --filter @alcuin/extension-sdk exec alcuin-extension create ./my-extension \
  --id acme.support --name "Acme Support"
```

Validate any native manifest without contacting Alcuin:

```bash
pnpm --filter @alcuin/extension-sdk exec alcuin-extension validate \
  ./my-extension/alcuin.extension.json
```

The generated manifest intentionally uses an absolute local `cwd`; change it
before distributing the extension package to another machine.
