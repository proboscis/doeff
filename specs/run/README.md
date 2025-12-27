# Run v0 Specification

Reproducible execution records for arbitrary command execution.

## Purpose

A Run captures exactly what is needed to reproduce a command execution:
- **exec**: What to execute and how (argv, cwd, env, timeout)
- **code_state**: Which code state to use (repo, commit, optional patch)

## Design Principles

### Purity
Run only contains execution information. Metadata like tags, notes, or experiment information belongs in higher layers (e.g., Experiment layer).

### Reproducibility
All fields are resolved/determined:
- No template variables (`{var}` forbidden)
- No interactive input (resolved before creation)
- No derived values (store resolved values directly)

## Layer Structure

```
+-------------------------------------------+
|  Experiment Layer                         |
|  - hypothesis, tags, notes                |
|  - control/treatment assignment           |
|  - references: [run_id, ...]              |
+--------------------+----------------------+
                     |
+--------------------v----------------------+
|  Run Layer (pure, reproducible)           |
|  - exec (resolved)                        |
|  - code_state (checkpoint)                |
+--------------------+----------------------+
                     |
+--------------------v----------------------+
|  Jump Layer (separate axis)               |
|  - run_id -> [uri, file, doc]             |
+-------------------------------------------+
```

## Files

- `run.cue` - CUE schema definition
- `run.schema.json` - JSON Schema for validation

## Python Usage

```python
from doeff.run_record import Run, create_run, Patch

# Create a new Run
run = create_run(
    argv=["python", "-m", "mymodule"],
    cwd=".",
    repo_url="git@github.com:org/repo.git",
    base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
    env={"CUDA_VISIBLE_DEVICES": "0"},
    timeout_sec=3600,
)

# Serialize to JSON
json_str = run.to_json()

# Parse from JSON
run = Run.from_json(json_str)

# Access fields
print(run.run_id)           # run_01ARZ3NDEKTSV4RRFFQ69G5FAV
print(run.exec.argv)        # ('python', '-m', 'mymodule')
print(run.code_state.repo_url)  # git@github.com:org/repo.git
```

## Validation

```python
from doeff.run_record import validate_run

errors = validate_run(data_dict)
if errors:
    print("Validation errors:", errors)
```

## Patch Storage Strategy

Patches are stored as git blobs with refs in the `refs/patches/` namespace:

```bash
# Save patch
git diff > /tmp/patch.diff
BLOB_SHA=$(git hash-object -w /tmp/patch.diff)
git update-ref refs/patches/{run_id} $BLOB_SHA
git push origin refs/patches/{run_id}

# Apply patch during reproduction
git fetch origin {patch.ref}
git cat-file -p FETCH_HEAD > /tmp/patch.diff
git apply /tmp/patch.diff
```

## Invariants

| Field | Constraint |
|-------|------------|
| `run_version` | Always `0` |
| `run_id` | ULID format with `run_` prefix |
| `exec.argv` | Non-empty, fully resolved, no template variables |
| `exec.cwd` | Relative path from repo root |
| `exec.env` | All string values |
| `exec.timeout_sec` | 0 = unlimited |
| `code_state.repo_url` | Clone-able URL |
| `code_state.base_commit` | Full SHA (40 chars) |
| `code_state.patch` | Optional, omitted if no diff |
| `code_state.patch.ref` | `refs/patches/{run_id}` format |
| `code_state.patch.sha256` | SHA-256 of patch content |
