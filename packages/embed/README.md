# @alcuin/embed

Framework-neutral custom element for published Alcuin agents.

```js
import "@alcuin/embed";
```

```html
<alcuin-agent
  api-url="http://localhost:8000"
  session-token="SHORT_LIVED_SERVER_ISSUED_TOKEN"
  agent-name="Operations Copilot"
  theme="auto"
  lang="zh-CN"
></alcuin-agent>
```

Set `lang="en"` or `lang="zh-CN"` to localize the component-owned interface.
Agent responses, tool names, and host-provided content remain unchanged.

Pass host context before the first message:

```js
const agent = document.querySelector("alcuin-agent");
agent.setContext({ page: "/incidents", record: { id: "INC-104" } });
agent.addEventListener("alcuin:artifact", (event) => console.log(event.detail));
agent.addEventListener("alcuin:approval", (event) => {
  console.log("Approval requested", event.detail);
});
```

The component emits `alcuin:run-start`, `alcuin:event`, `alcuin:artifact`,
`alcuin:approval`, and `alcuin:error`. Its built-in approval card calls the
governed decision endpoint and resumes the same Run from its last SSE sequence.
Hosts can also call `decideApproval(runId, approvalId, decision, note?)` from
their own approval UI. Transient stream failures reconnect with the standard
`Last-Event-ID` header; events already handled by the component are not replayed.
