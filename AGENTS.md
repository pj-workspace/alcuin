# Repository Instructions

## Product Direction

Alcuin is a domain-neutral, extensible AI agent platform. Platform code must not embed healthcare, TCM, legal, finance, or other domain-specific behavior. Domain features belong in explicit extensions.

## Branch Workflow

- Never commit directly to `main`.
- Start implementation branches from `develop`.
- Use `feat/`, `fix/`, `refactor/`, `docs/`, or `chore/` prefixes.
- Normal pull requests target `develop`.
- Only release pull requests target `main` from `develop`.
- Keep commits atomic and use Conventional Commit messages.

## Engineering Principles

- Keep agent definitions declarative and runtime implementations replaceable.
- Enforce workspace ownership and permission checks at service boundaries.
- Treat tools, MCP servers, knowledge sources, and credentials as scoped resources.
- Preserve streaming and structured execution events across runtime adapters.
- Prefer stable interfaces over provider-specific conditionals in domain code.
- Make extensions explicit through manifests or registered interfaces.
- Do not log secrets, raw credentials, or unredacted sensitive tool arguments.
- Add focused tests for behavior changes and report unrelated baseline failures separately.

## Documentation

- Update architecture documentation when changing module boundaries or contracts.
- Distinguish implemented behavior from roadmap intent.
- Avoid capability claims that are not supported by code and verification.

