# Five-minute prototype walkthrough

## Start

```bash
cp .env.example .env
pnpm install
uv sync --project apps/api
pnpm dev
```

Open `http://localhost:3000/studio`.

To use the live DeepSeek vision runtime, set `ALCUIN_DEEPSEEK_API_KEY` in the ignored local `.env`. The prototype selects `deepseek-v4-flash-vision-exp` through Chat Completions; without a key it falls back to the deterministic runtime.

## Verify the product path

1. In **Studio**, attach a PNG, JPEG, WebP, or GIF and ask a question about it. The compact execution panel streams reasoning separately from the final Markdown answer and collapses after the answer starts. Attachments are limited to four images of 5 MiB each.
2. Run “Update this incident to monitoring.” The Run must pause at an approval card. Approve or deny it and inspect the terminal trace.
3. In **Agents**, edit identity or instructions. Saving creates another immutable version; publishing makes it embeddable.
4. In **Extensions**, inspect the sample MCP manifest. Review its permissions and disabled-first lifecycle.
5. In **Embed**, create an origin-bound session and copy the generated Web Component snippet.
6. In **Runs**, select the latest Run and verify that events remain ordered.

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
