# ADR-0007: Native Skills and Plugin Compatibility Importers

- Status: Proposed for Phase 2
- Date: 2026-08-29

## Context

Alcuin has an ordered `active_skills` slot in the Context Kernel and an untyped `skills` array in
the native Extension Manifest. The current shared tree now also contains strict customization
contracts and a framework-neutral parsing/import package, but persistence and runtime wiring remain
separate concerns. A contract or parser must not be described as an installed and executable Skills
system.

Phase 2 needs a portable way to bring existing Agent Skills into the Studio-centered Agent
Foundation. It also needs compatibility importers for the published Agent Plugins 1.0.0 format and
the current Cursor Plugin format without turning imported packages into trusted executable code.

Three evidence sources shape this decision:

1. [Agent Plugins 1.0.0](https://agent-plugins.org/specification) defines a closed root
   `plugin.json`, fixed `skills/` and `mcp.json` component locations, path containment, per-component
   failure boundaries, and exactly two portable component types: Agent Skills and MCP servers.
2. [Agent Skills](https://agentskills.io/specification) defines `SKILL.md` and progressive
   disclosure: load name and description for discovery, the full instructions only on activation,
   and referenced scripts, references, or assets only as needed.
3. [Cursor Plugins](https://cursor.com/docs/reference/plugins) support Agent Plugins unchanged and
   also define a Cursor-specific `.cursor-plugin/plugin.json` format with rules, agents, commands,
   hooks, variables, skills, and MCP servers. Cursor-specific components are not part of the Agent
   Plugins portability floor.

The local deepseek-harness checkout at commit
`cd5ef8148158c3a752a658978873241fdf8e2bbc1` provides an additional implementation reference. Its
Skill subsystem separates a provider registry, filesystem provider, catalog consumer, and on-demand
loader. It demonstrates useful catalog/body separation, deterministic collision handling,
scope-aware lookup, and bounded progressive disclosure. It is implemented through Cordis, but
Cordis is deepseek-harness infrastructure rather than an Alcuin contract.

## Current Shared-tree Boundary

The repository currently contains:

- Workspace-scoped, immutable Agent Versions and a Context Kernel with an ordered but unresolved
  `active_skills` extension point;
- disabled-first native Extension installation, permission review, health checks, enable/disable,
  Agent tool allow-lists, and approval-gated mutation;
- `secret://` credential references resolved only at the server boundary;
- MCP and OpenAPI inspection and execution through the Alcuin gateway;
- an untyped Extension Manifest `contributions.skills` placeholder;
- stable contracts in `alcuin_core.customization` for exact Skill-version bindings, immutable Skill
  and Rule definitions, and revision-checked Thread configuration updates, with typed Skill and
  Rule bindings now carried by `AgentDefinition`; and
- `alcuin-customization`, which strictly parses `SKILL.md`, indexes bounded inert resources, resolves
  Always / Conditional / Manual Rules, exposes framework-neutral bounded Skill runtime tools, and
  performs read-only ZIP inspection for Agent Plugins 1.0 and Cursor Plugins.

`alcuin-customization` does not install resources, open a database, execute bundled code, call MCP,
or assemble provider requests. The shared tree does **not** yet make its contract objects a complete
native Skill Registry merely by defining them. A storage migration draft and API customization
resolver are present, but repository/service wiring, importer installation, built-in tool
registration, and immutable per-Run configuration snapshots are not yet complete end to end. Hook
execution and a general script runtime remain absent by design.

## Decision

### 1. Alcuin owns a native customization boundary

Phase 2 uses the framework-neutral `alcuin-customization` package. The API composes it with storage,
the Context Kernel, and runtime adapters; the package itself must not import FastAPI, provider SDKs,
LangGraph, Cordis, database adapters, or a plugin-loader framework.

The normalized domain has three separate views:

- **Skill summary** — stable name, description, compatibility metadata, source provenance, immutable
  revision id, digest, and invocation visibility;
- **Skill definition** — the validated `SKILL.md` instruction body for one immutable revision; and
- **Skill resource** — one bounded file under that revision's contained resource root, classified as
  reference, asset, or inert script.

Every installed Skill and revision belongs to a Workspace. Imported package identity, original
format, original plugin version, component path, content digest, diagnostics, and license metadata
remain provenance; they do not become runtime authority.

The binding contract is `AgentSkillBinding(skill_version_id, mode)`. It addresses one exact immutable
Skill version and gives that binding one invocation mode: `auto`, `always`, or `manual`. The binding
does not point at a mutable Skill head and does not permit an importer to replace the selected body
in place. `AgentDefinition.skills` now carries these bindings. Publication must reject a missing,
disabled, cross-Workspace, or duplicate-name binding once persistence/service validation is
connected. The registry never resolves a collision by ambient filesystem order at Run time. Import
diagnostics may report collisions, but an Agent Version must contain one explicit winner for each
visible Skill name.

The existing untyped `contributions.skills` field is migrated or replaced by typed references; it
does not become the registry itself. Importers, future remote catalogs, and native authoring all
write through the same inspection and registry boundary.

### 2. Skills use progressive loading

The runtime follows three tiers:

1. **Catalog:** only the name, bounded description, revision id, and invocation guidance for enabled
   Agent-bound Skills enter the model-visible catalog. Raw paths, provider internals, secrets, and
   full instruction bodies are excluded.
2. **Instructions:** a dedicated Alcuin Skill loader resolves one exact Agent-bound revision and
   returns the full validated instruction body only when the model or user activates it. The load is
   bounded and audited through the existing tool/event boundary.
3. **Resources:** a separate bounded read resolves only a named resource inside the same immutable
   Skill revision. References and assets are read on demand. Files under `scripts/` remain inert
   resources in this slice and cannot be executed through the Skill loader.

Catalog metadata and active instruction bodies remain distinct Context Kernel trace sources. The
catalog advertises availability; an activated body occupies the existing active-Skills position in
context or is returned as the structured result of the loader for the next provider step. Context
compaction never rewrites a Skill revision. Keyword-based harness routing is not introduced.

The Thread customization contract is `ThreadConfigurationUpdate(expected_revision,
active_skill_version_ids, manual_rule_version_ids)`. Its required storage semantics are
revision-checked and sticky-future: a successful update affects the next accepted Run and later Runs
until another configuration update replaces it. It never mutates an in-flight or completed Run.
Every Run must freeze the exact active Skill version ids, manually selected Rule version ids, and
Thread configuration revision it accepted, so retries, resume, context inspection, and replay cannot
observe later Thread changes. Updating a Skill or Rule creates a new version; it does not silently
retarget an existing Thread configuration. The contract and context resolver exist in the shared
tree; atomic Run-time snapshot persistence remains integration work below.

Disabled, unbound, or permission-ineligible Skills are omitted from the catalog rather than exposed
and rejected after the model spends a tool turn. An empty registry produces no catalog or loader
tool. The runtime rechecks Workspace ownership, Agent binding, revision, and enabled state at load
time so a stale catalog cannot grant access.

### 3. Agent Plugins 1.0.0 use a strict, limited compatibility inspector

The current Agent Plugin path accepts a bounded ZIP archive and requires the canonical 1.0.0 plugin
schema identifier. It is a safe read-only inspector that produces normalized candidates,
permissions, credential-variable names, warnings, and `ready_for_disabled_install`; it does not
install or execute the package.

The implemented inspector:

- requires root `plugin.json` and rejects an archive that also contains a Cursor manifest;
- retains only recognized manifest fields and reports unknown top-level fields as warnings;
- discovers only immediate `skills/<name>/SKILL.md` children and root `mcp.json`;
- validates each Skill against Agent Skills rules and skips only the invalid Skill with a diagnostic;
- treats malformed `mcp.json` as an isolated MCP-component warning, and skips an individually
  unsupported or invalid server without discarding valid Skills;
- rejects absolute paths, `..` traversal, and symbolic links in the ZIP;
- rejects encrypted ZIPs and duplicate paths, and normalizes one common wrapper directory;
- caps the input archive at 10 MiB, expanded content at 24 MiB, and entries at 256 files; and
- records Skill scripts as non-readable inert resource metadata rather than executable content.

This inspector does not yet implement every normative Agent Plugins 1.0.0 client rule. In
particular, current inspection is intentionally tolerant of unknown plugin fields, produces MCP
previews rather than a complete schema-selected execution mapping, and recognizes credential-style
placeholders without claiming that they are portable Agent Plugins header expansion. Therefore the
current boundary is accurately described as **Agent Plugins 1.0 compatibility inspection**, not a
conformant Agent Plugins client.

The integration part of this slice turns inspected Skills into disabled native Skill candidates and
routes inspected MCP declarations through the existing reviewed Extension/MCP boundary. It must not
bypass the inspector or turn a preview into a runnable server automatically.

If Alcuin launches an imported stdio MCP server after explicit enablement, the adapter supplies a
contained read-only `PLUGIN_ROOT` and a Workspace/plugin-instance-scoped writable `PLUGIN_DATA`, and
expands those variables only in the fields allowed by Agent Plugins 1.0.0. `PLUGIN_DATA` is created
before launch, preserved across updates of that installed plugin instance, and may be removed only
on explicit uninstall or retention cleanup. This does not sandbox the subprocess by itself;
ordinary Alcuin permission review, credential binding, network policy, deadlines, and process
isolation still apply.

Alcuin must not claim full conformant-client status until the applicable normative client checklist,
closed-schema behavior, complete path and post-expansion containment rules, MCP schema selection,
environment semantics, and failure boundaries are implemented and covered by dedicated tests.

### 4. Cursor Plugins import Skills, MCP declarations, and textual Rules only

The current Cursor inspector recognizes `.cursor-plugin/plugin.json` in a bounded ZIP. It supports
declared or default paths, recursively discovers Skills below the selected Skill roots, and accepts a
root `SKILL.md` only when no manifest Skill path and no discovered `skills/` entry exist. MCP may be
declared inline, by manifest path, or through root `mcp.json`. All resolved paths remain within the
staged package root.

Phase 2 maps:

- Cursor Skills into the native Skill Registry;
- Cursor MCP servers into the existing Extension/MCP inspection path; and
- Cursor `variables` declarations into credential requirements or non-secret configuration schema;
  and
- textual `.md`, `.mdc`, and `.markdown` Rules into disabled, versioned Alcuin Rule candidates.

Cursor Rule activation maps conservatively:

- `alwaysApply: true` becomes Alcuin `always`;
- non-empty `globs` becomes deterministic Alcuin `conditional` file matching;
- Cursor Agent Requested behavior, represented by descriptive non-always Rules without deterministic
  globs, becomes Alcuin `manual` with a warning until a trusted Agent rule-selection protocol exists;
  and
- other textual Rules default to `manual` rather than silently becoming always-on instructions.

Imported Rules remain disabled until reviewed and enabled through Alcuin's Rule lifecycle. A Manual
Rule participates only when its exact immutable Rule version id appears in the Thread's
`manual_rule_version_ids`; prompt similarity alone cannot activate it.

Cursor hooks, commands, and agents are recognized as unsupported executable or runtime components,
reported in `disabled_components`, and never translated into Alcuin Instructions, Rules, Skills,
tools, or lifecycle callbacks. Cursor compatibility in this slice means safe inspection and
controlled import of Skills, MCP declarations, variables, and textual Rules—not full Cursor behavior
or marketplace compatibility.

### 5. Hooks, scripts, permissions, and secrets fail closed

Imported Markdown is untrusted instruction content. It cannot widen an Agent tool allow-list,
override Workspace ownership, change mutation policy, approve a tool call, access a credential, or
grant a filesystem or network capability.

- Agent Skills `allowed-tools` is experimental metadata. Alcuin records it for diagnostics but does
  not treat it as pre-approval or permission.
- Skill `scripts/` files are stored as inert resources. No import, install, enable, catalog, or Skill
  activation step executes them. A future script runner requires a separate sandbox and permission
  ADR.
- Agent Plugin client-extension hooks and Cursor hooks are disabled and non-executable. A hook file
  cannot register lifecycle callbacks in Phase 2.
- MCP servers remain disabled after import. Enabling requires reviewed permissions, bound credentials,
  a live health check, and the existing Agent allow-list.
- Cursor variables describe configuration shape only. Secret values are supplied separately and
  stored as `secret://` references; raw values never enter package storage, Agent definitions,
  context snapshots, execution events, model prompts, or logs.
- No literal value in an imported manifest, MCP header, or subprocess environment is accepted as an
  Alcuin credential. Sensitive header or environment fields without a supported external binding
  are rejected with a diagnostic rather than persisted as ordinary package configuration.
- Resource reads are Workspace- and revision-scoped, path-contained, size-bounded, and returned as
  data. A resource path never becomes ambient host filesystem access.

Import inspection reports every executable-looking component and its default-disabled state before
installation. Installation, plugin enablement, Agent binding, Skill activation, MCP enablement, and
tool approval are separate decisions.

### 6. Alcuin does not fork or embed the Cordis runtime

The deepseek-harness Skill design is used as an architectural reference for:

- separating registry, providers/importers, catalog projection, and full-body loading;
- keeping discovery metadata small and loading bodies on demand;
- making lookup scope-aware and rechecking visibility at activation; and
- preserving diagnostics when one source is incomplete or invalid.

Alcuin does not vendor Cordis, run arbitrary Cordis plugins, reproduce its dynamic plugin tree,
expose its model-facing `cordis_*` tools, or share one deepseek-harness runtime across Workspaces.
Alcuin's Skill Registry remains a native platform service behind the existing Runtime Adapter and
Context Kernel contracts. A future deepseek-harness integration, if adopted, is an isolated Runtime
Bridge adapter or sidecar that consumes normalized Alcuin Skills; it is not the Skill Registry and
does not redefine Workspace, permission, secret, event, or persistence boundaries.

## Delivery Boundary

### Contracts and inspection already present in the shared tree

- `alcuin_core.customization` defines exact Skill/Rule version references, Skill invocation modes,
  immutable definitions, resource limits, and revision-checked Thread configuration input.
- `AgentDefinition` carries typed exact-version Skill and Rule bindings and rejects duplicate
  version ids within each binding list.
- `alcuin-customization` strictly parses Skill metadata and bodies, keeps script content inert,
  renders summary-only catalogs and one selected instruction body, and deterministically resolves
  Rules.
- Its framework-neutral `SkillRegistry` and built-in `skill.load` / `skill.read_resource` handlers
  enforce exact Workspace/Agent-version eligibility, bounded paging, and inert script/asset
  resources without widening the Agent tool allow-list.
- The bounded ZIP inspector distinguishes Agent Plugins 1.0 from Cursor Plugins, isolates invalid
  Skills/MCP declarations, reports executable components and permissions, and produces only a
  disabled-install candidate.
- Cursor textual Rules map to Always, Conditional, or Manual candidates; Agent Requested currently
  maps to Manual with an explicit warning.
- A storage migration draft defines Workspace-scoped Skill/Rule versions, Agent-version bindings,
  and revisioned Thread configuration tables. The API customization resolver can project eligible
  Rules, Skill catalogs, and active Skill bodies into Context Kernel sections.

These are library and inspection capabilities. They do not by themselves prove that an imported
Skill or Rule traverses an end-to-end install, review, persistence, Agent publication, Thread update,
Run snapshot, runtime-tool registration, and provider execution path.

### Remaining Phase 2 integration

- Complete Workspace-scoped repository and service boundaries for inspected, disabled, enabled,
  listed, versioned, bound, and loaded Skills and Rules on top of the drafted schema.
- Validate exact version ownership, enabled state, duplicate names, invocation mode, and
  required-tool eligibility when immutable Agent Versions are published.
- Persist revision-checked Thread configuration with sticky-future semantics. Every newly accepted
  Run snapshots its exact Skill version ids, manual Rule version ids, and configuration revision;
  existing Runs remain unchanged.
- Turn inspected Cursor textual Rules into disabled native Rule versions without changing their
  conservative activation mapping.
- Route inspected MCP previews through the existing Extension inspection path without auto-enabling
  any server.
- Register the bounded Skill runtime handlers with the current tool loop and make the Context Kernel
  consume one consistent per-Run snapshot rather than rereading mutable Thread configuration.
- Keep all imported hooks and scripts disabled and prove that `allowed-tools` grants no authority.
- Reuse the existing permission, credential-reference, approval, MCP gateway, and immutable Agent
  Version boundaries rather than creating a second security model.
- Add conformance fixtures, malformed-package isolation tests, Workspace isolation tests, stale-load
  tests, Thread snapshot/replay tests, progressive-loading budget tests, and secret/non-execution
  tests.

### Roadmap, not this slice

- Marketplace discovery, remote Git installation, package updates, signatures, publisher trust, and
  dependency resolution;
- strict Agent Plugins conformant-client behavior across every supported transport;
- Cursor agents, commands, hooks, canvases, team marketplaces, and marketplace metadata;
- any hook runtime or automatic Skill script execution;
- sandboxed script capabilities, dependency installation, and package build steps;
- project/user filesystem scanning, live file watchers, remote Skill providers, and hot catalog
  replacement;
- organization/team Skill distribution, RBAC, policy packs, and delegated administration;
- user-explicit slash invocation, automatic Skill recommendation, configuration presets, and Skill
  evaluation/version promotion workflows; and
- a deepseek-harness Runtime Bridge, workflow runtime, or multi-agent runtime.

## Rejected Alternatives

### Treat `contributions.skills` as already implemented

The field is untyped and has no persistence, binding, loading, or enforcement behavior. Calling it a
registry would preserve an accidental schema placeholder as a runtime contract.

### Eagerly inject every installed Skill body

This breaks progressive disclosure, consumes context for unused capabilities, weakens provenance,
and lets any installed package influence every Run even when the Agent did not bind it.

### Execute imported hooks or scripts after installation

Installation proves only structural validity. It is not authorization to execute repository code,
observe prompts, mutate files, start processes, use the network, or read credentials.

### Adopt deepseek-harness or Cordis as the platform plugin model

That would couple Alcuin's Workspace, permission, lifecycle, and runtime contracts to another
application's composition framework. The useful Skill seams can be implemented behind Alcuin's
existing stable interfaces without a runtime fork.

## Consequences

- Portable Skills can enter Alcuin without making the platform domain-specific or provider-specific.
- Agent Versions, not ambient install order, determine which Skill revision a Run may load.
- Thread configuration changes are forward-only and reproducible: future Runs inherit the latest
  accepted revision, while every accepted Run retains the exact version set it started with.
- Cursor textual Rules can be reviewed inside Alcuin without silently converting Agent Requested
  guidance into automatic model authority.
- The model pays catalog cost for available Skills and full-body cost only for activated Skills.
- Imported packages remain inert until separate review, enablement, binding, activation, and tool
  approval decisions permit each layer.
- Agent Plugin and Cursor compatibility share native Skill/MCP inspection boundaries, while the
  Cursor adapter additionally produces controlled textual Rule candidates. Source-specific
  diagnostics prevent false claims of full Cursor or Agent Plugins conformance.
- Static imported revisions are the first implementation; live providers and hot refresh require a
  later consistency protocol.

## Verification Required Before Acceptance

- Compatibility fixtures cover the current Agent Plugin boundary: exact plugin schema identifier,
  immediate-child Skill discovery, skipped nested Skills, isolated invalid Skill/MCP components,
  archive containment, warning behavior, and non-execution. Normative closed-schema and complete MCP
  execution fixtures are required before a conformance claim.
- Agent Skills fixtures cover frontmatter, parent-directory name matching, bounded descriptions,
  immutable bodies, relative resources, and three-tier disclosure.
- Cursor fixtures cover manifest detection, explicit/default paths, recursive Skills, root-Skill
  fallback, variables, textual Rule activation mapping, unsupported agents/commands/hooks, and
  disabled executable components.
- Security tests prove archive/symlink containment, Workspace isolation, immutable Agent binding,
  stale-catalog rechecks, disabled script/hook non-execution, `allowed-tools` non-authority, and secret
  redaction.
- Versioning tests prove sticky-future Thread updates, optimistic configuration revision checks,
  exact Run snapshots, no retroactive mutation, and deterministic replay after later Skill/Rule
  versions or Thread configuration changes.
- Runtime tests prove that only catalog metadata is present before activation and that one Skill load
  introduces only the selected revision and bounded referenced resources.

## References

- [Agent Plugins Specification 1.0.0](https://agent-plugins.org/specification)
- [Agent Skills Specification](https://agentskills.io/specification)
- [Agent Skills client implementation guide](https://agentskills.io/client-implementation/adding-skills-support)
- [Cursor Plugins](https://cursor.com/docs/plugins)
- [Cursor Plugins reference](https://cursor.com/docs/reference/plugins)
- [Cursor Hooks](https://cursor.com/docs/hooks)
- Local deepseek-harness: `docs/subsystems/skills.md`, `packages/skill/skill`,
  `packages/skill/skill-filesystem`, and `packages/skill/tool-skill` at
  `cd5ef8148158c3a752a658978873241fdf8e2bbc1`
