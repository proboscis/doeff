# doeff runner (VS Code)

Run `doeff` `Program` values directly from VS Code. The extension mirrors the PyCharm plugin: it detects annotated `Program[...]` bindings, looks up interpreters/kleisli/transformers via `doeff-indexer`, and launches `doeff run` under the Python debugger.

## Requirements

- VS Code Python extension
- `doeff` installed in the active Python environment (`pip install doeff`)

The extension bundles `doeff-indexer` binaries for common platforms (macOS, Linux, Windows). No additional configuration is needed in most cases.

### Binary Discovery Order

1. **Bundled binary** (platform-specific binary included with the extension)
2. `DOEFF_INDEXER_PATH` environment variable (if set)
3. Python environment bin directory (from the Python extension)
4. System paths (`/usr/local/bin/`, `~/.cargo/bin/`, `~/.local/bin/`, etc.)

## How it works

- CodeLens appears on lines annotated with `Program[...]`
- **Run**: runs `uv run doeff run --program <path>` (fallback: `python -m doeff run --program <path>`)
- **Run with options**: invokes `doeff-indexer` to:
  - resolve the program's qualified name for the current file
  - gather available interpreters, Kleisli programs, and transformers
- A quick-pick dialog lets you choose the interpreter and optional Kleisli/transformer, then starts `python -m doeff run ...` using the configured interpreter
- **➕ Playlist**: saves a worktree-aware execution unit (branch + optional commit pin); edit tools later in the Playlists view
- Appends/updates `.vscode/launch.json` so you can tweak the run config

## Playlists (Worktree-aware)

- **Programs (All Worktrees)** view indexes all `git worktree` checkouts and lets you add any Program to a playlist.
- **Playlists** are stored in `.git/doeff/playlists.json` (shared across all worktrees).
- Running a pinned item can create a temporary detached worktree at the pinned commit when needed.
- Playlist item: click to **Go to Definition**; use the inline ▶ action to Run/Debug.

## Agentic Workflows

The extension integrates with `doeff-agentic` CLI for monitoring and managing agent-based workflows.

### Workflows Tree View

The **Workflows** view (in the doeff sidebar) displays:

```
DOEFF WORKFLOWS
├─ ● a3f8b2c: pr-review-main [blocked]
│   └─ review-agent (blocked)
├─ ○ b7e1d4f: pr-review-feat-x [running]
│   └─ fix-agent (running)
└─ ✓ c9a2e6d: data-pipeline [done]
```

- **Status indicators**: ○ running, ● blocked, ✓ completed, ✗ failed, ◻ stopped
- **Auto-refresh**: Tree updates every 5 seconds
- **Status bar**: Shows active workflow count (click to list workflows)

### Workflow Commands

- `Doeff: List Workflows` - Show workflow picker with actions
- `Doeff: Attach to Workflow` - Open terminal and attach to agent's tmux session
- `Doeff: Watch Workflow` - Open terminal with live status updates
- `Doeff: Stop Workflow` - Stop workflow and kill agent sessions

### Requirements

Workflow features require the `doeff-agentic` CLI. Install with:

```bash
cargo install doeff-agentic
```

## Commands

- `doeff-runner.runDefault`: Quick run with defaults
- `doeff-runner.runOptions`: Run with interpreter/Kleisli/transformer selection
- `doeff-runner.runConfig`: Launch a prepared selection payload (used internally from the quick pick)
- `doeff-runner.addToPlaylist`: Add a Program to a playlist
- `doeff-runner.pickProgram`: Pick a Program across all worktrees
- `doeff-runner.pickAndRun`: Pick and run a Program across all worktrees
- `doeff-runner.pickAndAddToPlaylist`: Pick a Program and add it to a playlist
- `doeff-runner.pickPlaylistItem`: Pick a playlist item (reveal)
- `doeff-runner.pickAndRunPlaylistItem`: Pick and run a playlist item
- `doeff-runner.listWorkflows`: Show workflow picker
- `doeff-runner.attachWorkflow`: Attach to workflow's agent tmux session
- `doeff-runner.watchWorkflow`: Watch workflow updates
- `doeff-runner.stopWorkflow`: Stop workflow and kill agents

## Development

```bash
npm install
npm run watch
```

Package with `npm run vscode:prepublish`.
