# Five-minute prototype walkthrough

## Start

```bash
cp .env.example .env
pnpm install
uv sync --project apps/api
docker compose up -d searxng qdrant
pnpm dev
```

Open `http://localhost:3000/studio`.

To use the live DeepSeek vision runtime, set `ALCUIN_DEEPSEEK_API_KEY` in the ignored local `.env`. The prototype selects `deepseek-v4-flash-vision-exp` through Chat Completions; without a key it falls back to the deterministic runtime.

Start the self-hosted retrieval dependencies before testing `web.search` or `knowledge.search`:

```bash
docker compose up -d searxng qdrant
```

The local `.env` uses `ALCUIN_SEARXNG_URL=http://localhost:9888`. No search-provider key is required.

Qdrant is exposed at `http://localhost:6333`. Set `ALCUIN_DASHSCOPE_API_KEY` in the ignored local `.env`; knowledge imports and queries use Qwen `text-embedding-v3` dense+sparse embeddings. The provider endpoint remains replaceable through `ALCUIN_DASHSCOPE_HTTP_API_URL`.

## Verify the product path

1. In **Studio**, ask for a current public fact and verify that the trace shows `web.search`, followed by citation sources and a final Markdown answer. Normal searches start in quick mode; the runtime exposes at most two web calls per Run.
2. Attach a PNG, JPEG, WebP, or GIF and ask a question about it. The compact execution panel streams reasoning separately from the final Markdown answer and collapses after the answer starts. Attachments are limited to four images of 5 MiB each.
3. Run “Update this incident to monitoring.” The Run must pause at an approval card. Approve or deny it and inspect the terminal trace.
4. In **Agents → Knowledge**, upload a TXT, Markdown, PDF, or DOCX file (8 MiB maximum), or switch to **Paste text**. Verify that the source shows its document/chunk counts, remains bound to the draft definition, and automatically enables `knowledge.search`. Save and publish, then ask a source-specific question in Studio and verify a `Retrieve` trace plus `knowledge://` citations.
5. In **Agents**, edit identity or instructions. Saving creates another immutable version; publishing makes it embeddable.
6. In **Extensions**, choose **Connect capability**, then import an MCP server, OpenAPI document, or Alcuin Manifest. Confirm the full `Inspect → Review → Install disabled → Bind credentials → Health check → Enable` lifecycle. MCP inspection performs live tool discovery; OpenAPI inspection lets you select operations before installation.
7. Return to **Agents → Capabilities**, bind one enabled extension tool, and save a draft. In **Studio**, explicitly request that capability and verify the trace contains the stable `extension.<manifest-id>.<tool-name>` id plus a real `tool.completed` result.
8. Bind a mutating extension tool with the Agent policy set to **Ask every time**. Verify the model cannot bypass the structured approval card, then approve once and confirm the persisted event order is `tool.requested → approval.required → tool.completed → run.completed` with the real adapter result.
9. In **Embed**, create a session bound to the Playground's current origin. The Playground mounts the real `@alcuin/embed` Web Component, passes host context through `setContext`, and uses the same published Agent Version as Studio. Run one read tool, then approve one write and verify the host receives `alcuin:event` and `alcuin:approval` events. Production hosts must issue the short-lived token server-side for their exact public origin.
10. In **Runs**, select the latest Run and verify that events remain ordered.

## API headers

Studio requests use the prototype workspace header:

```text
X-Alcuin-Workspace: ws_demo
```

Embedded clients use the short-lived session token:

```text
Authorization: Bearer alc1.<payload>.<signature>
```

Do not place workspace credentials or model API keys in browser code.

Run payloads remain backward-compatible with text-only clients:

```json
{
  "input": "What is the dominant color?",
  "thinking": true,
  "attachments": [
    {
      "type": "image",
      "name": "sample.png",
      "media_type": "image/png",
      "data_url": "data:image/png;base64,..."
    }
  ]
}
```

Image bytes are validated at the API boundary and are not written into execution events or logs.

The canonical run stream is available from `GET /v1/runs/{run_id}/events`. Studio requests the lightweight compatible projection with `?protocol=tcm`, which emits `thinking-delta`, `text-delta`, tool, approval, artifact, citation, error, and terminal `done` frames. This `done` frame only closes the stream; it is not a callable tool and does not appear as an execution step.

OpenAPI specifications can be submitted inline as JSON or loaded from a JSON/YAML `spec_url` through `POST /v1/extensions/import/openapi`. Imported write operations are never callable directly; they must execute through an approval-gated Agent run.

For a local end-to-end OpenAPI check, start the domain-neutral fixture with `uv run --project apps/api uvicorn examples.records_api.app:app --port 9411`, then import `http://127.0.0.1:9411/openapi.json`. Its read and patch operations verify path/query/request-body schema projection and the real approval execution path.

Remote MCP and OpenAPI URLs must resolve to public HTTP(S) addresses by default. For trusted local development only, set `ALCUIN_EXTENSION_ALLOW_PRIVATE_NETWORKS=true`; keep it disabled in production. Extension credentials are stored as `secret://` references and must resolve server-side before a health check can pass.

File knowledge ingestion is available through `POST /v1/knowledge/sources/{source_id}/files` as multipart form data. The API derives format from the sanitized filename and validates PDF/DOCX signatures instead of trusting the declared MIME type. Unsupported, encrypted, malformed, oversized, and textless documents fail with stable client errors; parser internals and document content are not returned in errors.
