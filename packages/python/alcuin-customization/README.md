# alcuin-customization

Framework-neutral behavior customization for Alcuin.

The package owns:

- strict `SKILL.md` parsing and progressive resource indexes;
- deterministic Always / Conditional / Manual Rule resolution;
- read-only inspection of Agent Plugins 1.0 and Cursor Plugin archives;
- a fail-closed boundary for scripts, hooks, variables, and MCP declarations.

The native Skill runtime boundary is intentionally framework-neutral:

- `BoundSkillProvider` requires `workspace_id` and immutable `agent_version_id` on every lookup;
- `SkillRegistry` exposes only exact, enabled Agent-bound Skill versions for the current Run;
- `SkillBuiltinTools` implements bounded `skill.load` and `skill.read_resource` progressive loading;
- resources are served only from the imported snapshot, and only readable reference/text resources;
- scripts and assets stay inert, and Skill metadata never changes the Agent tool allow-list.

It never installs resources, opens a database, executes bundled code, calls an MCP server, or
assembles provider requests. Workspace authorization and immutable version persistence belong to
the API and storage adapters. Those adapters must derive runtime scope server-side instead of
accepting Workspace, Agent-version, or active-Skill identity from arbitrary tool arguments.
