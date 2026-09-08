# @alcuin/sdk

Framework-neutral REST and resumable SSE client for Alcuin hosts. Configure the API origin
and Workspace explicitly; the package never reads application environment variables.

```ts
import { createAlcuinClient } from "@alcuin/sdk";

const alcuin = createAlcuinClient({
  baseUrl: "https://agents.example.com",
  workspaceId: "ws_example",
});
```
