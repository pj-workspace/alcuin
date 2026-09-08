import { createAlcuinClient } from "@alcuin/sdk";

const API_URL = process.env.NEXT_PUBLIC_ALCUIN_API_URL ?? "http://localhost:8000";
const WORKSPACE_ID = process.env.NEXT_PUBLIC_ALCUIN_WORKSPACE_ID ?? "ws_demo";

const alcuinApi = createAlcuinClient({
  baseUrl: API_URL,
  workspaceId: WORKSPACE_ID,
});

export { API_URL, WORKSPACE_ID, alcuinApi };
