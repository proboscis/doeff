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

## Hy(doeff-hy)のコードを行き来する

`.hy` / `.hyk` / `.hyp`(languageId `hy`)の file で、次の機能が使えます。

- **定義へ移動**(F12): カーソルの名前を mangle(`-` → `_`)して照合し、次の順で探します。
  1. 同じ file の定義(`Color.RED` のような入れ物の member も)
  2. その file の import(名前・`:as` の別名・module の別名 + dotted の `alias.fn`・相対 import)から決めた module の Hy の定義
  3. import 先が Python の module(索引に無い物)なら、workspace の中の `a/b.py` か `a/b/__init__.py`(root 直下 → `src/` → workspace 全体)の `def` / `class` / `name =` の行
  4. どれでも見つからなければ workspace 全体の同名の定義を全部
- **参照の一覧**(Shift+F12): 全 file の参照と定義から同じ名前の位置を集めます。定義の module が 1 つに定まる時は、import と dotted の修飾で別の module の同名を除き、修飾を解けない参照(`self.x` など)は名前だけで数えます。
- **ファイルの目次**(Outline・パンくず): 定義を入れ物で入れ子にします(class の method と field、enum の member、handler の effect 節)。横に kind と引数を出します。
- **workspace の記号の検索**(Cmd/Ctrl+T): 全 file の定義を名前で絞ります。
- **hover**: 定義の kind・引数・module・docstring を出します。

### 索引の取り方

- 索引は `doeff-indexer hy-index` が出す JSON(版 1)です。起動時に Hy の file を持つ workspace の folder ごとに `hy-index --root <folder>` を背景で実行し、編集中は 0.5 秒待ってから `--stdin --path <file>` でその file だけを取り直します。file の作成・削除・改名も反映します。
- `doeff-indexer` は上の「Binary Discovery Order」と同じ順で探します。見つかった binary が `hy-index` を知らない古い版なら、1 度だけ通知を出して Hy の機能を止めます(Python 向けの機能はそのまま動きます)。
- 子 process は同時に 1 つだけ走らせます。失敗・契約に合わない出力・file ごとの読み取りの問題は Output の `doeff-runner` に理由つきで出ます。

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

VS Code を起動せずに走る単体テスト(Hy の解決の論理・playlist・worktree)は次で実行します。

```bash
npm run test:unit
```

compile の後、`out/test/**/*.test.js` を mocha の API で実行します(`scripts/run-unit-tests.js`。同梱の mocha の CLI は Node 22 以降で起動できないため)。fixture は `test-fixtures/` にあります。
