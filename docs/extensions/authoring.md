# Extension authoring

Alcuin extensions are explicit capability packages. An extension may expose
tools through MCP, describe an OpenAPI service, or select a trusted built-in
adapter registered by the host. Installing a manifest never enables it
automatically.

## Start with the SDK

The TypeScript package `@alcuin/extension-sdk` is the author-facing contract.
It re-exports the public Manifest types, validates values at runtime, creates
stable Agent tool ids, and includes a runnable stdio MCP scaffold.

```bash
pnpm --filter @alcuin/extension-sdk build
pnpm --filter @alcuin/extension-sdk exec alcuin-extension create ./my-extension \
  --id acme.support --name "Acme Support"
```

The scaffold contains:

- `alcuin.extension.json`, including a reviewed read-only `echo` tool;
- `server.py`, a minimal MCP server using the compatible Python MCP v1 API;
- `README.md`, containing the local validation and installation flow.

The generated stdio entrypoint records the absolute scaffold directory as its
`cwd`, so it runs immediately on the authoring machine. Replace that path or
package the server behind remote MCP before distributing it.

## Validate before importing

```bash
pnpm --filter @alcuin/extension-sdk exec alcuin-extension validate \
  ./my-extension/alcuin.extension.json
```

Validation fails on malformed identity/version fields, unsupported entrypoint
configuration, duplicate tools or permissions, invalid tool input schemas,
undeclared high-risk mutations, and credential contract errors. Warnings do
not fail validation; for example, a built-in adapter warns that the target
Alcuin host must register it explicitly.

The public JSON Schema remains available at
`docs/schemas/alcuin-extension.schema.json`. The SDK adds semantic conformance
checks that JSON Schema alone cannot express.

## Runtime identity

MCP and OpenAPI contributions resolve to a stable Agent tool id:

```text
extension.<manifest-id>.<tool-name>
```

Use `extensionToolId(manifestId, toolName)` rather than composing this string in
host code. A tool is advertised only when its extension is enabled, healthy,
owned by the active Workspace, and explicitly bound to the Agent Definition.
The same conditions are checked again immediately before execution.

## Permissions and writes

Read-only tools should declare a low-risk permission and MCP
`readOnlyHint=true`. Mutating tools must declare `mutating: true`,
`approval: "ask"`, and at least one high-risk permission. Alcuin still applies
the Agent policy at runtime; a Manifest cannot bypass structured approval.

Credentials are requirements, never values. Store only a `secret://` reference
during installation. Raw credentials must remain in the host's secret backend
and must not appear in the Manifest, Agent Definition, events, or logs.

## Declarative UI blocks

Extensions may contribute `card`, `table`, and `form` blocks to the Studio
Context Canvas. Alcuin owns every rendered element; manifests cannot provide
HTML, JavaScript, React components, event handlers, or remote UI bundles.

Cards and tables read bounded values from the controlled thread `context`, the
latest `artifact.updated` payload, or the latest successful result of a tool
declared by the same Manifest:

```json
{
  "id": "incident-results",
  "type": "table",
  "title": "Incident results",
  "source": {
    "kind": "tool_result",
    "tool": "search_incidents",
    "path": "incidents"
  },
  "columns": [
    { "label": "Incident", "path": "id" },
    { "label": "Status", "path": "status", "format": "status" }
  ]
}
```

A form names primitive fields and one declared submit tool. Submission creates
a persisted Tool Run against the Agent's immutable version; the backend
rechecks Workspace ownership, extension health, the Agent tool allow-list, and
the tool JSON Schema. Mutating tools stop at `approval.required` and are not
executed until the user approves the Run.

```json
{
  "id": "update-incident",
  "type": "form",
  "title": "Update incident",
  "fields": [
    { "name": "ticket_id", "label": "Incident ID", "input": "text", "required": true },
    { "name": "status", "label": "Status", "input": "select", "options": ["monitoring", "resolved"] }
  ],
  "submit": { "tool": "update_ticket", "label": "Request update" }
}
```

Tool and block ids are resolved only from enabled, healthy extensions bound to
the active Agent. Unknown tools, duplicate block/field ids, unsafe paths, and
unsupported input types fail Manifest inspection.

## Installation contract

Every extension follows the same trust sequence:

```text
Inspect → Review permissions → Install disabled → Bind secret references
→ Live health check → Enable → Bind selected tools to an Agent Version
```

For MCP, the live health check performs actual tool discovery. Only tools the
operator selected during review remain approved after discovery refresh.
