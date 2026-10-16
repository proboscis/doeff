# IDE plugins for doeff

> **Needs components outside `pip install doeff`.** The plugins are built from this repository
> (see below), and they call the `doeff-indexer` command. `doeff-indexer` 0.1.3 on PyPI gives
> `doeff run` its auto-discovery, but none of its wheels (any platform) puts the command where its
> entry point looks (`doeff-indexer --help` reports "binary not found"); this repository fixes that
> for the release after 0.1.3. Until then, build `doeff-indexer` from a checkout of this
> repository; this compiles Rust, so install a Rust toolchain (`rustup`) first:

```bash
git clone --depth 1 https://github.com/proboscis/doeff.git
pip install doeff ./doeff/packages/doeff-indexer
```

This repository ships two IDE integrations:

- **PyCharm / IntelliJ**: Kotlin plugin under `ide-plugins/pycharm`
- **VS Code**: TypeScript extension under `ide-plugins/vscode/doeff-runner`

Both rely on `doeff-indexer` to discover Programs, interpreters, Kleisli
programs, and transformers. Install `doeff-indexer` and the `doeff` Python
package in the environment your IDE uses, or set `DOEFF_INDEXER_PATH` to the
indexer binary.

## PyCharm / IntelliJ

- **Features**: Gutter icon on `Program[...]` bindings, dialog to select
  interpreter/Kleisli/transformer, launches a Python run configuration.
- **Requirements**: Python plugin plus a Python SDK. `doeff-indexer` available on
  PATH or via `DOEFF_INDEXER_PATH`.
- **Build/install**:
  - `cd ide-plugins/pycharm`
  - `./gradlew buildPlugin`
  - Install the produced ZIP from *Settings → Plugins → Install from disk*.
- **Usage**: Click the gutter icon beside a `Program` annotation, pick
  interpreter/Kleisli/transformer, then the plugin creates and runs a Python
  configuration (`doeff run --program ...`).
- **Troubleshooting**: If symbols are not found, check the popup for the
  indexer path and command, ensure `doeff-indexer` supports `--file` filtering,
  and re-run `cargo install --path packages/doeff-indexer --force` if needed.

## VS Code

- **Features**: CodeLens on `Program[...]` lines with two actions:
  - **Run**: `python -m doeff run --program <path>` (doeff chooses interpreter)
  - **Run with options**: prompts for interpreter/Kleisli/transformer via
    `doeff-indexer` and runs with those args
  - Writes/updates `.vscode/launch.json` so you can edit the config
- **Requirements**: VS Code Python extension; `doeff` and `doeff-indexer` in the
  active environment (`DOEFF_INDEXER_PATH` if not on PATH).
- **Build/package**:
  - `cd ide-plugins/vscode/doeff-runner`
  - `npm install`
  - `npm run compile`
  - Package: `npx @vscode/vsce package` (produces `doeff-runner-<ver>.vsix`)
- **Install**: In VS Code run *Developer: Install Extension from VSIX...* and
  pick the generated `.vsix`.
- **Usage**: Click a CodeLens above a `Program[...]` binding. Use **Run** for
  defaults or **Run with options** to select interpreter/Kleisli/transformer.
  The exact command is shown in a toast and in the “doeff-runner” output
  channel.
- **Troubleshooting**: Open the “doeff-runner” output channel to see the exact
  indexer commands and stdout/stderr. If interpreters are missing, confirm:
  `doeff-indexer find-interpreters --root <workspace>` (plus `--type-arg` if
  applicable) returns entries.