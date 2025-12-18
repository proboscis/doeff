# VS Code doeff-runner: Playlists

> **Status**: PLANNING — implementation tracked in `specs/vscode-runner/playlist/todo.md`.

## Summary
- Add **Playlists**: multiple named lists of run configurations (“playlist items”) across entrypoints.
- Each playlist item pins an entrypoint plus selected Kleisli (`--apply`) and transformer
  (`--transform`).
- Expose playlists as a dedicated sidebar view so users can quickly see and run each configuration.

## Motivation
The extension already supports per-entrypoint execution via CodeLens and the Programs tree:
- **Run**: `python -m doeff run --program ...` (doeff chooses interpreter)
- **Options**: pick interpreter / Kleisli / transformer and run

However, workflows often involve a fixed set of “daily” entrypoints with specific toolchains:
- “auth/login with tracing transform”
- “ingest pipeline with staging env kleisli”
- “smoke test programs” across multiple modules

A playlist makes those run configurations explicit, visible, and shareable.

## Terminology
- **Entrypoint**: an indexed `Program[...]` assignment.
- **Tool**: Kleisli (`--apply`) or transformer (`--transform`) selection.
- **Playlist item**: a saved run configuration (entrypoint + selected toolchain).
- **Playlist**: an ordered collection of playlist items.

## Goals
1. Quickly run a curated list of entrypoints + toolchains without re-selecting options.
2. Make run configs visible in a dedicated “Playlists” view.
3. Store playlists in a workspace file so teams can share them via git.
4. Support multiple named playlists with independent item lists.
5. Keep existing run commands unchanged (no breaking changes).

## Non-Goals
- “Run all” with dependency or value passing semantics (playlist items are independent).
- Capturing/storing runtime output or exit status per item (can be added later).
- Prompting for arbitrary interpreter parameters (separate feature).
- Multi-root workspaces (assume a single workspace root in v1).
- Changing `doeff` CLI behavior.

## UX
### View: Playlists
Add a new view under the existing **doeff** activity bar container:
- **doeff → Playlists**

Tree structure:
- Playlist
  - Playlist item
    - Actions: Run / Remove

Each playlist item label should be compact but informative, e.g.:
- `login_program  (apply: with_user, transform: trace)`
- `sync_users  (transform: profile)`

### Add to playlist
Entry points:
- **CodeLens** on `Program[...]` lines: an inline `[+]` action (e.g. `[+] Playlist`)
- **Programs tree** entrypoint row: an inline `[+]` action (same command)
- **Command palette**: `doeff-runner.addToPlaylist` (asks for entrypoint first)

Flow:
1. Select playlist (quick pick; offers “Create playlist…”).
2. Select configuration:
   - Kleisli: optional picker.
   - Transformer: optional picker.
   - Tool pickers should reuse existing type-arg filtering when the entrypoint is `Program[T]`;
     untyped `Program` shows all tools.
3. Save (optionally prompt for a short display name; default is derived from entrypoint + toolchain).

### Run playlist item
Entry points:
- Playlists tree item action: `Run`
- Command palette: `doeff-runner.runPlaylistItem` (quick pick across all playlist items)

Mode:
- By default, playlist runs honor the current global debug toggle (Run vs Debug).
- Optional future enhancement: per-item mode override.

### Remove from playlist
Entry points:
- Playlists tree item action: `Remove`
- Optional: remove via command palette (`doeff-runner.removePlaylistItem`)

### Minimal management
- `doeff-runner.createPlaylist`
- `doeff-runner.deletePlaylist`
- `doeff-runner.openPlaylistsFile`

## Data Model
### Workspace file
Path: `.vscode/doeff-runner.playlists.json`

Schema `v1`:
```jsonc
{
  "version": 1,
  "playlists": [
    {
      "id": "uuid",
      "name": "Daily",
      "items": [
        {
          "id": "uuid",
          "name": "Login (trace)",
          "program": "myapp.features.auth.login_program",
          "entrypoint": { "file": "src/features/auth.py", "line": 42 }, // optional, workspace-relative
          "apply": "myapp.features.auth.with_user", // optional
          "transform": "myapp.transforms.trace", // optional
          "args": {
            "format": "text",
            "report": false,
            "reportVerbose": false
          },
          "cwd": "${workspaceFolder}" // optional
        }
      ]
    }
  ]
}
```

Notes:
- `apply` is optional; `transform` is optional.
- `args` is optional; defaults match `doeff run` defaults.
- If no playlists exist yet, prompt to create the first playlist (optionally offer a default name like
  “Favorites”).
- Duplicates are allowed: the same `program` may appear multiple times in a playlist with different
  `apply`/`transform` selections (items are identified by `id`).

## Execution Mapping
Playlist item → `doeff run` args:
- Always: `run --program <program>`
- If set: `--apply <apply>`
- If set: `--transform <qualified_name>`
- If set: `--format <text|json>`, `--report`, `--report-verbose`

VS Code execution mode is the same as today:
- **Debug**: run via Python debug adapter (`module: doeff`)
- **Run**: execute in an integrated terminal using the selected Python interpreter

### Interpreter resolution (automatic)
When running a playlist item, the extension determines the interpreter automatically based on the
entrypoint location:
1. Resolve the entrypoint’s source location:
   - Prefer `entrypoint.file` + `entrypoint.line` from the playlist item if present.
   - Otherwise, locate the program via `doeff-indexer index` and extract its file/line.
2. Call `doeff-indexer find-interpreters --proximity-file <file> --proximity-line <line>` and use
   the first result (closest match).
3. Execute `doeff run` with `--interpreter <resolved>`.

Fallback: if interpreter resolution fails, run without `--interpreter` (equivalent to “Run”).

## Storage & Migration
- If the file is missing: treat as no playlists (create on first save).
- If invalid JSON: show a non-fatal error and continue without playlists.
- Write pretty JSON with deterministic ordering to reduce merge conflicts.

## Testing Strategy
- Unit tests:
  - Parsing/validation: missing optional fields, unknown `version`, invalid shapes.
  - Arg mapping: playlist item → `doeff run` args.
- Manual test matrix:
  - Add/remove items.
  - Missing interpreter/tool symbols: show error + “repair item” path.
  - Windows quoting + transform selection.

## Risks & Mitigations
- **Symbol renames break items**: provide “Repair item” that reselects missing symbols.
- **Merge conflicts in playlist file**: stable IDs + deterministic write + minimal diffs.
- **Single-root assumption**: if multiple workspace roots are open, show an error and disable playlists.

## Open Questions
1. Should “Run all items” exist (and if so, only in Run mode)?
2. Should playlist items optionally persist a mode override (`run` vs `debug`)?
