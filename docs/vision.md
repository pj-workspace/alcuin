# Project Vision

## Purpose

Alcuin should make a capable agent a composable product artifact rather than a hard-coded application feature.

An agent definition should be able to declare:

- Identity and instructions
- Model and generation policy
- Tool and MCP capabilities
- Knowledge sources and retrieval policy
- Memory and context policy
- Runtime strategy
- Human approval boundaries
- Access scope and credentials
- Version and publication state

The runtime should execute that definition through a stable event contract so chat, workflows, evaluations, and external clients can observe the same run consistently.

## Initial Audience

- Full-stack AI developers building production agent applications
- Teams that need private RAG and MCP-connected tools
- Builders who want to move from a single ReAct assistant to versioned workflows and multi-agent systems without replacing the whole platform

## Non-goals for the Foundation Milestone

- A public agent marketplace
- Autonomous self-modifying production agents
- A visual node editor before the runtime contract stabilizes
- Training or hosting foundation models
- Domain-specific decision systems in the platform core

## Foundation Milestone

The first milestone should establish:

1. A workspace-aware resource model.
2. Versioned agent definitions.
3. A runtime adapter and structured event protocol.
4. Built-in and MCP tool catalogs with scoped permissions.
5. Knowledge bindings with auditable citations.
6. A full-stack management and chat experience.
7. A migration path for the existing TCM application as the first domain extension.

