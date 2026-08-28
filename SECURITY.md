# Security Policy

## Reporting a Vulnerability

Do not disclose suspected vulnerabilities in public issues or discussions.

Use the repository's **Security** tab to submit a private vulnerability report. Include:

- A concise description of the issue
- Affected component and revision
- Reproduction steps or a minimal proof of concept
- Expected impact
- Any proposed mitigation

Secrets, API keys, access tokens, credentials, and private user data must never be included in public reports.

## Security Priorities

Alcuin treats the following boundaries as security-critical:

- Workspace and resource ownership
- Agent and tool permissions
- MCP endpoint and local process policy
- Secret storage and runtime credential resolution
- SSRF, command execution, and file access controls
- Prompt injection across retrieved or tool-returned content
- Auditability of human approvals and external actions

