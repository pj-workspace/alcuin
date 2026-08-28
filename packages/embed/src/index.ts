type AlcuinContext = Record<string, unknown>;

const styles = `
  :host { display:block; color:#20211f; font:14px/1.5 ui-sans-serif,system-ui,sans-serif; }
  * { box-sizing:border-box; }
  .shell { min-height:440px; display:flex; flex-direction:column; overflow:hidden; border:1px solid #dedbd2; border-radius:18px; background:#f8f6f0; box-shadow:0 22px 60px rgba(25,26,24,.12); }
  .head { height:58px; display:flex; align-items:center; justify-content:space-between; padding:0 17px; border-bottom:1px solid #e2dfd6; background:rgba(251,250,247,.86); backdrop-filter:blur(18px); }
  .identity { display:flex; align-items:center; gap:10px; font-weight:650; letter-spacing:-.01em; }
  .mark { width:29px;height:29px;border-radius:9px;display:grid;place-items:center;color:white;background:#3157d5;font-family:Georgia,serif;font-size:16px; }
  .status { display:flex;align-items:center;gap:6px;color:#6f736c;font-size:12px; }
  .dot { width:6px;height:6px;border-radius:99px;background:#4d9a69;box-shadow:0 0 0 3px rgba(77,154,105,.12); }
  .body { flex:1;padding:22px;overflow:auto; }
  .intro { max-width:380px;margin:34px auto;text-align:center; }
  .intro h2 { margin:0 0 8px;font:26px/1.15 Georgia,serif;letter-spacing:-.03em; }
  .intro p { margin:0;color:#72756f; }
  .message { max-width:88%;margin:0 0 16px;padding:12px 14px;border-radius:14px;white-space:pre-wrap; }
  .message.user { margin-left:auto;background:#3157d5;color:white;border-bottom-right-radius:5px; }
  .message.agent { background:#fff;border:1px solid #e5e1d8;border-bottom-left-radius:5px; }
  .event { display:flex;align-items:center;gap:8px;margin:8px 2px;color:#777a73;font-size:12px; }
  .event i { width:7px;height:7px;border-radius:99px;background:#7894ff; }
  form { display:flex;gap:9px;padding:12px;border-top:1px solid #e2dfd6;background:#fbfaf7; }
  textarea { flex:1;resize:none;min-height:44px;max-height:110px;border:1px solid #d8d5cc;border-radius:12px;background:#fff;padding:11px 12px;color:inherit;font:inherit;outline:none; }
  textarea:focus { border-color:#7894ff;box-shadow:0 0 0 3px rgba(49,87,213,.1); }
  button { width:44px;border:0;border-radius:12px;background:#20211f;color:#fff;font-size:18px;cursor:pointer; }
  button:disabled { opacity:.42;cursor:not-allowed; }
  :host([theme="dark"]) { color:#f0efe9; }
  :host([theme="dark"]) .shell { background:#141512;border-color:#2c2e29; }
  :host([theme="dark"]) .head,:host([theme="dark"]) form { background:#1a1b18;border-color:#2c2e29; }
  :host([theme="dark"]) .message.agent,:host([theme="dark"]) textarea { background:#20221e;border-color:#333630;color:#f0efe9; }
`;

export class AlcuinAgentElement extends HTMLElement {
  static observedAttributes = ["session-token", "api-url", "theme", "agent-name"];
  private root: ShadowRoot;
  private threadId?: string;
  private context: AlcuinContext = {};
  private running = false;

  constructor() {
    super();
    this.root = this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback() {
    if (this.isConnected) this.render();
  }

  setContext(context: AlcuinContext) {
    this.context = structuredClone(context);
  }

  private get token() { return this.getAttribute("session-token") ?? ""; }
  private get apiUrl() { return (this.getAttribute("api-url") ?? "http://localhost:8000").replace(/\/$/, ""); }

  private render() {
    const name = this.getAttribute("agent-name") ?? "Alcuin Agent";
    this.root.innerHTML = `
      <style>${styles}</style>
      <section class="shell" aria-label="${name}">
        <header class="head"><div class="identity"><span class="mark">A</span>${name}</div><span class="status"><i class="dot"></i>Connected</span></header>
        <main class="body"><div class="intro"><h2>How can I help?</h2><p>This agent uses the published Alcuin definition and your current page context.</p></div></main>
        <form><textarea aria-label="Message" placeholder="Ask the agent…"></textarea><button aria-label="Send" type="submit">↑</button></form>
      </section>`;
    this.root.querySelector("form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = this.root.querySelector("textarea") as HTMLTextAreaElement;
      const prompt = input.value.trim();
      if (prompt) {
        input.value = "";
        void this.send(prompt);
      }
    });
  }

  private appendMessage(kind: "user" | "agent" | "event", text: string) {
    const body = this.root.querySelector(".body");
    body?.querySelector(".intro")?.remove();
    const node = document.createElement("div");
    node.className = kind === "event" ? "event" : `message ${kind}`;
    node.innerHTML = kind === "event" ? `<i></i><span></span>` : "";
    (kind === "event" ? node.querySelector("span") : node)!.textContent = text;
    body?.append(node);
    if (body) body.scrollTop = body.scrollHeight;
    return node;
  }

  private async send(prompt: string) {
    if (this.running || !this.token) return;
    this.running = true;
    this.appendMessage("user", prompt);
    const output = this.appendMessage("agent", "");
    try {
      if (!this.threadId) {
        const encoded = this.token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
        const claims = JSON.parse(atob(encoded + "=".repeat((4 - encoded.length % 4) % 4)));
        const thread = await this.request("/v1/threads", {
          method: "POST",
          body: JSON.stringify({ agent_id: claims.agent_id, title: "Embedded session", context: this.context }),
        });
        this.threadId = thread.id;
      }
      const run = await this.request(`/v1/threads/${this.threadId}/runs`, {
        method: "POST",
        body: JSON.stringify({ input: prompt }),
      });
      this.dispatchEvent(new CustomEvent("alcuin:run-start", { detail: run }));
      const response = await fetch(`${this.apiUrl}/v1/runs/${run.id}/events`, {
        headers: { Authorization: `Bearer ${this.token}` },
      });
      if (!response.ok || !response.body) throw new Error("Unable to stream run");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
          if (!dataLine) continue;
          const event = JSON.parse(dataLine.slice(6));
          this.dispatchEvent(new CustomEvent("alcuin:event", { detail: event }));
          if (event.type === "message.delta") output.textContent += event.payload.delta;
          if (event.type === "tool.requested") this.appendMessage("event", `Using ${event.payload.tool}`);
          if (event.type === "artifact.updated") this.dispatchEvent(new CustomEvent("alcuin:artifact", { detail: event.payload.artifact }));
        }
      }
    } catch (error) {
      output.textContent = error instanceof Error ? error.message : "Agent run failed";
      this.dispatchEvent(new CustomEvent("alcuin:error", { detail: error }));
    } finally {
      this.running = false;
    }
  }

  private async request(path: string, init: RequestInit) {
    const response = await fetch(`${this.apiUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.token}`, ...init.headers },
    });
    if (!response.ok) throw new Error((await response.json()).detail ?? "Alcuin request failed");
    return response.json();
  }
}

export function registerAlcuinAgent(tagName = "alcuin-agent") {
  if (!customElements.get(tagName)) customElements.define(tagName, AlcuinAgentElement);
}

if (typeof window !== "undefined") registerAlcuinAgent();
