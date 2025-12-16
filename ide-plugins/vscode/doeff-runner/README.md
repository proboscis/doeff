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
- **Run**: immediately runs `python -m doeff run --program <path>` (doeff chooses the interpreter)
- **Run with options**: invokes `doeff-indexer` to:
  - resolve the program's qualified name for the current file
  - gather available interpreters, Kleisli programs, and transformers
- A quick-pick dialog lets you choose the interpreter and optional Kleisli/transformer, then starts `python -m doeff run ...` using the configured interpreter
- Appends/updates `.vscode/launch.json` so you can tweak the run config

## Commands

- `doeff-runner.runDefault`: Quick run with defaults
- `doeff-runner.runOptions`: Run with interpreter/Kleisli/transformer selection
- `doeff-runner.runConfig`: Launch a prepared selection payload (used internally from the quick pick)

## Development

```bash
npm install
npm run watch
```

Package with `npm run vscode:prepublish`.
