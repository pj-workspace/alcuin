# Contributing to Alcuin

Alcuin uses a protected release branch and a dedicated integration branch.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Reviewed, release-ready history |
| `develop` | Integration branch for the next release |
| `feat/*` | New capabilities |
| `fix/*` | Defect fixes |
| `refactor/*` | Internal restructuring without intended behavior changes |
| `docs/*` | Documentation-only work |
| `chore/*` | Tooling, maintenance, and repository operations |

Create work from the latest `develop`:

```bash
git switch develop
git pull --ff-only
git switch -c feat/short-description
```

## Pull Requests

- Target `develop` for normal work.
- Keep each pull request focused on one reviewable outcome.
- Describe the motivation, behavior change, verification, and known limitations.
- Resolve review conversations before merging.
- Use squash merge unless preserving a small, intentional commit series adds value.

Releases are promoted through a pull request from `develop` to `main`.

## Commit Messages

Use Conventional Commits:

```text
feat(runtime): add runtime adapter contract
fix(auth): enforce workspace ownership on agent reads
docs(architecture): describe extension manifest lifecycle
```

## Definition of Done

- The requested behavior is implemented.
- Relevant automated checks pass.
- Security and tenancy boundaries are preserved.
- Documentation matches actual behavior.
- The branch contains no unrelated generated files, credentials, or local state.

