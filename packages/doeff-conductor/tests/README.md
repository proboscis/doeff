# doeff-conductor Tests

This directory contains tests for the doeff-conductor package.

## Test Categories

### Unit Tests
- `test_effects.py` - Tests for effect construction
- `test_types.py` - Tests for type definitions
- `test_agent_handler.py` - Tests for agent handler with fully mocked dependencies
- `test_worktree_handler.py` - Tests for worktree management
- `test_issue_handler.py` - Tests for issue lifecycle
- `test_git_handler.py` - Tests for git operations

### Integration Tests  
- `test_workflow_e2e.py` - Tests for workflow execution with real handlers (but mocked agents)

### E2E Tests
- `test_agent_e2e.py` - Full pipeline tests including:
  - Mock agentic tests (mock at doeff-agentic handler level, fast)
  - Real OpenCode tests (require running OpenCode server)

## Running Tests

### Quick Start

```bash
# Run all unit and mock-based tests
cd packages/doeff-conductor
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_agent_e2e.py
```

### Running E2E Tests

E2E tests with real OpenCode are disabled by default. To enable them:

```bash
# Enable E2E tests
CONDUCTOR_E2E=1 uv run pytest

# Run only E2E tests
uv run pytest -m e2e

# Run only tests that require OpenCode
CONDUCTOR_E2E=1 uv run pytest -m requires_opencode
```

### OpenCode Server Requirements

Tests marked with `@pytest.mark.requires_opencode` need a running OpenCode server:

1. **Option 1: Use existing server**
   ```bash
   CONDUCTOR_OPENCODE_URL=http://localhost:4096 CONDUCTOR_E2E=1 uv run pytest -m requires_opencode
   ```

2. **Option 2: Start manually**
   ```bash
   # In one terminal
   opencode serve --port 4096
   
   # In another terminal
   CONDUCTOR_E2E=1 uv run pytest -m requires_opencode
   ```

## Test Architecture

### Mocking Strategy

Conductor tests mock at the **doeff-agentic handler level**, not HTTP level:

```
┌─────────────────┐
│   Conductor     │  ← Tests live here
│   (effects)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  doeff-agentic  │  ← Mock at this level for conductor tests
│   (handlers)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   OpenCode      │  ← HTTP testing belongs in doeff-agentic tests
│   (HTTP API)    │
└─────────────────┘
```

This keeps tests focused on conductor's responsibility: orchestrating agentic effects.

### Test Fixtures

```python
def test_with_repo(test_repo, worktree_base, issues_dir):
    """Fixtures for git repository testing."""
    # test_repo - Path to initialized git repository
    # worktree_base - Path for worktree storage
    # issues_dir - Path for issue storage

def test_with_opencode(opencode_url):
    """Fixture for real OpenCode URL."""
    # opencode_url - URL of running OpenCode server (or None)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CONDUCTOR_E2E` | Enable E2E tests | `0` (disabled) |
| `CONDUCTOR_OPENCODE_URL` | OpenCode server URL | Auto-detect |

## Markers

| Marker | Description |
|--------|-------------|
| `@pytest.mark.e2e` | End-to-end test |
| `@pytest.mark.requires_opencode` | Requires OpenCode server |
| `@pytest.mark.slow` | Long-running test |

## Related Testing

OpenCode HTTP communication tests belong in `packages/doeff-agentic/tests/`:
- `test_opencode_handler.py` - HTTP-level testing with MockOpenCodeServer