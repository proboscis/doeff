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
  4. それでも無い module(uv の git / path 依存の package など workspace の外の物)は、workspace の Python 環境に `uv run --no-sync --project <workspace の root> python -c …` で置き場所を聞きます(`hy` が入っていれば `.hy` の module も引けます)。`.hy` ならその 1 file を `hy-index --file` で索引して定義へ、`.py` なら 3 と同じ探し方で飛びます。聞いた結果は workspace の root ごとに持ち、`uv.lock` か `pyproject.toml` が変わると聞き直します。外の file の索引は別に持ち、workspace の記号の検索と参照の一覧には混ぜません。uv が無い・時間切れ等は Output に理由を出して、この段を飛ばします。
  5. どれでも見つからなければ workspace 全体の同名の定義を全部
- **参照の一覧**(Shift+F12): 全 file の参照と定義から同じ名前の位置を集めます。定義の module が 1 つに定まる時は、import と dotted の修飾で別の module の同名を除き、修飾を解けない参照(`self.x` など)は名前だけで数えます。
- **ファイルの目次**(Outline・パンくず): 定義を入れ物で入れ子にします(class の method と field、enum の member、handler の effect 節)。横に kind と引数を出します。
- **workspace の記号の検索**(Cmd/Ctrl+T): 全 file の定義を名前で絞ります。
- **hover**: 定義の kind・引数・module・docstring を出します。effect には扱う handler の数、defk / deff / defp には中で撃つ effect、handler には扱う effect を足します。

### effect・handler・defk を行き来する

名前が effect であるとは、`defclass` / `defrecord` の基底に `EffectBase`(`doeff.EffectBase` のような dotted も最後の区切りで比べます)か effect のクラスがある(推移的に)か、どこかの `defhandler` にその名前の節があることです。workspace の外の package の effect は、定義へ移動で一度開いて索引を取った物まで数えます。

- **Cmd+クリック**: effect の名前の上では、クラスの定義とその effect を扱う全 handler の節を返します(複数なら peek で選べます)。普通の関数・defk は今までどおりです。
- **実装へ移動**(Cmd+F12): effect の名前の上(クラスの定義・`(PutRow …)` の呼び出し・import・handler の節の頭)ではその effect を扱う全 handler の節へ、handler の名前の上ではその handler の節の一覧へ移動します。
- **呼び出し階層**(Shift+Alt+H): defk / deff / defp / defn / defhandler / effect の節 / effect のクラスが項目です。出ていく呼び出しは行き先ごとにまとめ、effect の生成には「effect」と出します。effect のクラスから出ていくと、その effect を扱う handler の節へ降ります。入ってくる呼び出しは、名前で絞ってから定義へ移動と同じ解決で行き先を確かめます。handler の節へは、その effect を撃つ場所から入ってきます。
- **コード上の注記**: effect のクラスの上に「handler N 個」「撃つ場所 M 箇所」、defhandler の上に「扱う effect: …」、defk / deff / defp の上に「撃つ effect N 個」「呼び出し元 M 箇所」を出します。押すと 1 件なら直接移動し、複数なら peek で一覧を出します。
- **ナビゲーションパネル**(activity bar の「doeff Hy」): Effects(module ごとの effect → Handlers と Performed by)、Handlers(handler → 扱う節 → 同じ effect の他の handler)、Programs(defk / deff / defp → Performs・Calls・Called by。effect からは Handlers へ降りられます)、Current file(今開いている file の分だけ)。項目を押すとその位置へ移動し、右クリックで「参照を表示」「呼び出し階層を表示」を選べます。view の上に絞り込みと更新のボタンがあります。子は展開した時に作り、既に開いた経路に戻る項目は「循環」として止めます。

### linter の結果(doeff-linter)

規則の判定の正本は linter で、拡張はその出力を表示するだけです(自分では判定しません)。operator の逐語は "linter must be the source of truth and editor must follow that" です。

- **呼び方**: 設定 `doeff-runner.hy.lintCommand`(workspace ごと)。既定は `doeff-linter --output-format editor-json` です。空にすると呼びません。
  - 起動時と workspace の変化、「再実行」のボタンでは、repo 全体を呼びます(引数なし)。
  - 保存時と編集の 0.8 秒後には、その file を `--stdin --path <path>` で呼びます。
  - 子 process は同時に 1 つで、時間切れがあります。終了コード 2 と、契約(`lint-contract-v1`)に合わない出力は、理由を Output に出します。
- **波線**(問題の一覧): 重さは linter のとおりです(error = 新しい破れ、warning = 登録簿に載った既知の破れ、info)。文には、直し方と、規則の ID・ADR の law の名が付きます。
- **「違反(linter)」パネル**: law(無ければ規則の ID)→ file → 違反の順に並べます。押すとその位置へ移動します。
  - 上の `$(law)` で「規則の一覧」に切り替えます。針のつながっていない規則は灰色で、linter がまだ見ていない物です。
- **「層の地図(linter)」パネル**: linter の `modules` の層(core・intent・protocol・foundation・entry・層の外)→ dir → file の木です。色は違反の有無だけで付けます。

### タグで閲覧

定義(hy-index の definitions)を軸で並べ替えて見るパネルです。判定はせず、軸の値は hy-index と linter の出力を読むだけです。

- **軸**: service・層(linter の modules)、context・role(定義の `:tags` → 無ければ linter が読んだ module の頭の MODULE-TAGS)、`:tags` に書いた任意の鍵(`:owner` を書けば owner の軸が出る)、kind、生の副作用の分類(hy-index の `raw.direct`)、違反の規則(linter の違反で範囲が定義に入る物)。
  - 値が無い時は「(不明)」か「なし」です。複数の値を持つ定義(生の副作用の分類など)は、各値の下に出ます。
- **並べ方**: view の上のボタンで、軸の順を 1〜3 段選びます。各段の項目には件数と違反の数が出ます。末端は定義で、押すとその範囲を選んで移動します。
- **絞り込み**: 軸 = 値 の条件を AND で足します(値は件数つきで複数選べます)。今の条件は view の説明欄に出ます。解除のボタンもあります。
- **保存した見方**: 並べ方と絞り込みに名前を付けて、設定 `doeff-runner.hy.browseViews` に保存します(workspace の設定に置けば repo で共有できます)。同梱の見方は「service ▸ 層」「層 ▸ service」「生の副作用 ▸ service」の 3 つです。

### 層を見分ける表示(linter の layers から)

層とその説明、違反の理由は、すべて linter(doeff-linter の editor-json)が出します。拡張は文を持ちません。

- **エクスプローラーの印**: file に層の頭文字(C・I・P・F・E …)を付けます。層の外の file には付けません。
  - 文字は層を表し、色は違反の有無を優先します。違反のある file は問題の色(赤)、違反が無ければ linter の層の順に当てた色です。層は文字で、壊れているかは色で見分けます。
  - tooltip には、linter の層の一行の説明と、その file の層を何で決めたか(`layer_reason`)を出します。
- **ステータスバー**: 今開いている file の「層: protocol — 相手の話し方へ訳す handler(context: …)」を出します。押すと linter の `layers` の表(知っていること・知らないこと・迷った時の問い)を開きます。
- **hover**: `MODULE-TAGS` の辞書と、定義の契約の辞書の `:tags` / `:role` の上で、書かれた role・linter の決めた層・層の決め方・層の説明を出します。
  - 違反のある行では、その違反の「これは何か」「なぜ違反か」「law の :statement」「直し方」を出します。問題の一覧の文にも同じものを出し、行末の注記は短い文のままです。
- **「層の地図(linter)」**: 層の項目の description に、linter の一行の説明を出します。
- linter が説明を出さない時(古い binary、設定に説明が無い)は、層の名前だけを出し、説明の欄は空にします。説明は doeff-linter の設定 `[tool.doeff-linter.layers.describe.<層>]` に書きます。

### 生の副作用に触る handler の印(事実の表示)

http・asyncio・時刻・乱数・file・process・環境変数・network・db・thread に直接触る定義に印を付けます。handler(defhandler と effect の節)を見分けるための機能です。同じ effect を実際の I/O で扱う本番の handler と、純粋な模擬の handler を並べて区別できます。

- **証拠を集めるのは doeff-indexer(`hy-index`・契約 版 3 の `raw`)** です。目録は doeff-indexer に同梱の `packages/doeff-indexer/data/raw_side_effects.json` の 1 か所にあり、拡張は証拠を表示するだけで目録を持ちません。
- これは事実の表示で、規則の違反の表示ではありません。何が違反かの判定の正本は linter です。
- **証拠の集め方**(hy-index の中): 索引の imports・references・calls と定義の範囲だけを使います(実行はしません)。参照を import で完全な名前に直して(`(import subprocess :as sp)` の `sp.run` は `subprocess.run`)、目録と比べます。
  - 強い根拠: import を通した名前と、呼び出しの頭の組み込み `open`。
  - 弱い根拠(印に「?」): method 名だけの一致(`(.read-text p)` など)。pathlib の method は、pathlib が見える時だけ数えます。
  - 数えない物: 例外の型(`httpx.ReadTimeout` など)と、純粋な module(`urllib.parse` など)。
- **経由**: 呼ぶ定義(同じ file か import で決まった物)が生に触るなら「経由で触る」とします。経路つきで深さ 4 まで辿ります。file をまたぐので、起動時の全体の索引で計算します。編集中の file には、直前の全体の結果を引き継ぎます。
- **印の出し方**:
  - Handlers パネル: 直接触る handler と節は `$(zap)` と「生: http, time」、経由だけなら `$(debug-stackframe)` と「経由: time」。Effects パネルの handler の節にも同じ印が付きます。view の上の `$(zap)` で「生の副作用に触る handler だけ表示」を切り替えます。
  - コード上の注記: defhandler と effect の節の上に「⚡ 生の副作用: http(httpx.post・42 行)」(経由だけなら「↳ 経由で生の副作用: …」)。押すと証拠の位置の一覧です。defk / deff / defp は直接の証拠だけを「⚡ 生の副作用に直接触る: …」と出します。
  - hover: 証拠の一覧(分類・名前・行・直接か経由か、経由なら経路)。
- **設定** `doeff-runner.hy.rawSideEffects`: 目録に足す名前(分類 → 名前の配列)です。hy-index に `--raw-catalog-extra` で渡します。
  - 名前の書き方: dotted の名前、`.name` は method 名、`builtin:name` は組み込み。
  - 読めない値の理由は Output に出ます。
- **見逃す形**: handler の引数で渡された client や関数(`[client]`・`[#^ Callable jev]`)の上の method 呼び出しは、型の注釈(`#^ httpx.Client client`)が無いと見分けられません。

### 索引の取り方

- 索引は `doeff-indexer hy-index` が出す JSON(版 3。版 3 だけを受け付けます)です。起動時に Hy の file を持つ workspace の folder ごとに `hy-index --root <folder>` を背景で実行し、編集中は 0.5 秒待ってから `--stdin --path <file>` でその file だけを取り直します。file の作成・削除・改名も反映します。
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
