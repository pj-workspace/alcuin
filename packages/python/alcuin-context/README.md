# alcuin-context

Provider-neutral context assembly for Alcuin runtimes.

The package owns deterministic layer ordering, normalized conversation messages, conservative
token budgets, context traces, and compaction protocols. It does not call a model, read a
database, know about FastAPI, or serialize a provider-specific request. Applications compose a
`ContextCompactor` and persistence port around these pure contracts.

Raw conversation messages remain immutable. A compaction is an auditable summary over an exact
message prefix; it overlays that prefix for future model requests without rewriting history.
