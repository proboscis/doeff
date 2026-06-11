# Replay Keying Design

This document settles the C2 replay-key contract for ADR 0001 D7 and
`spec-workflow-orchestration.md` §9.

## Decision

Agent cache identity lives at L3. A cache key contains only fields that can
change the worker result distribution:

- author prompt;
- result schema;
- resolved identity fingerprint: adapter kind, model, and identity binding.

Execution substrate is excluded. A result produced through `tmux` can replay
when the same L3 call resumes through a headless CI substrate, because substrate
is below the result-distribution boundary.

## Node Identity

Every expanded node receives a stable structural path:

```text
workflow-name / body-index / phase-name / control-node[index] / node-kind
```

Static fan-out uses the literal branch index in source order:

```text
my-flow/0/Implement/0/parallel[3]/agent
my-flow/2/Review/0/parallel-for[1]/agent
```

Bounded loops are not unrolled during expansion. The loop body has one static
node path, and runtime journal entries append the loop iteration index when the
body executes:

```text
my-flow/1/Fix/0/loop/1/agent @ iteration 0
my-flow/1/Fix/0/loop/1/agent @ iteration 1
```

The pure helper `node_identity_fingerprint(...)` hashes the workflow name,
static node path, and the tuple of loop indices. Non-loop nodes use an empty
loop-index tuple.

## Cache Key

`agent_cache_key(...)` hashes a canonical JSON payload:

```json
{
  "prompt": "...",
  "schema": {"type": "object"},
  "resolved_identity": "sha256(adapter, model, identity)"
}
```

The function accepts `substrate` only to make the exclusion explicit at call
sites. The value is intentionally ignored.

## Edited Workflow Resume

Resume maps a new workflow onto an existing journal by comparing the ordered
agent cache keys produced by expansion and plan-time profile resolution.
Replay is valid for the longest unchanged prefix. At the first key mismatch,
all later entries are considered invalid for that edited workflow, even if a
later key happens to match again. This avoids replaying a result whose upstream
dataflow may have changed.

The helper `longest_valid_prefix(previous_keys, current_keys)` implements this
comparison. C3 can use the returned prefix length to decide which journal
entries can be adopted without re-running agents.
