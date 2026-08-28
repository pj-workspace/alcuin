# @alcuin/embed

Framework-neutral custom element for published Alcuin agents.

```html
<script type="module" src="./index.js"></script>
<alcuin-agent
  api-url="http://localhost:8000"
  session-token="SHORT_LIVED_SERVER_ISSUED_TOKEN"
  agent-name="Operations Copilot"
  theme="auto"
></alcuin-agent>
```

Pass host context before the first message:

```js
const agent = document.querySelector("alcuin-agent");
agent.setContext({ page: "/incidents", record: { id: "INC-104" } });
agent.addEventListener("alcuin:artifact", (event) => console.log(event.detail));
```

The component emits `alcuin:run-start`, `alcuin:event`, `alcuin:artifact`, and `alcuin:error`.
