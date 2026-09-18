# 反例生成のための入力一式(固定)

対象版: doeff `origin/main 14f1a783` から切った worktree
`/Users/s22625/.worktrees/doeff-wt-autocompact-window`。
関連する他 repo は `/Users/s22625/repos/agent-control-plane`(Haskell)と
`/Users/s22625/dotfiles`(Python)で、2026-09-18 の作業 checkout。

## 1. このシステムと、固定した要件

`agentd`(doeff-agents の sessionhost)は、依頼の行を受けて coding agent の CLI
(`claude` / `codex`)を **process として起こす**制御面。起こす時の argv は agentd が組む。

claude の会話は文脈が伸びると CLI 自身が要約して畳む(auto-compact)。畳む閾値は
CLI の旗 `--autocompact <auto|tokens>` / env / profile の設定で決まり、**何も指定しないと
モデルの窓いっぱい**(Fable / Opus は 1M token)まで畳まない。

| id | 種類 | 要件 |
| --- | --- | --- |
| R1 | 品質 | 1 手番の資源消費は毎手番読み直す文脈の大きさでほぼ決まる。閾値を下げれば 1 手番が安くなる |
| R2 | 制約 | CLI が受ける閾値は `auto` か 100,000〜1,000,000 の整数ちょうど。**幅の外の値を argv に載せると CLI は argv 解釈の段で異常終了し、stream-json の行を 1 つも吐かない**(その手番は丸ごと消える) |
| R3 | 制約 | agentd は charter(手番ごとの宣言の物)の値を読み、**値の方策を code に置かない** |
| R4 | 機能 | 計画席(lead)と実装席(worker)で別の閾値を持てること |
| R5 | 品質 | 起こす腕(launch / resume / rehydrate)のどれを通っても同じ閾値が効くこと |
| R6 | 制約 | 起動 argv の凍結接頭(`claude --dangerously-skip-permissions --settings {...}`)と `--effort` の位置(index 4)を動かさない |
| R7 | 品質 | 宣言側の打ち間違いで席が起きなくなってはいけない。誤りは縮退し、縮退したことが外から読めること |

## 2. 責務の所有者と隠す知識

| 要素 | 所有する判断 | 他から隠す知識 |
| --- | --- | --- |
| `autocompact-derivation`(新設・実装済み) | argv に載せる閾値の文字列を決める唯一の点 | 幅の定数、床の値、縮退の向き、受ける型 |
| `agentd-argv-builder` | argv の旗の綴りと並び | CLI の綴り |
| `agentd-arms`(launch / resume / rehydrate) | charter の欄をどう params へ運ぶか | — |
| `acp-charter`(Haskell `Plan.charterFor`) | 方策の既定 charter に会話の宣言を重ねる | — |
| `route-dispatcher`(起票側・**段 2 で実装予定**) | 役(lead / worker)ごとの閾値の値 | 幅、縮退の向き、CLI の綴り |

agentd 側から「役が何か」の知識が消え、宣言側から「幅・縮退・CLI の綴り」の知識が消える、
というのが分割の主張。

## 3. 公開契約

### `autocompact-derivation`(純粋)

```
claude-autocompact-value(params) -> str
  params["auto_compact_window"] が
    無い / 空文字        -> 床(400000)を同じ関門に通した結果
    "auto"(大小問わず)  -> "auto"
    int / 整数値の float -> 幅の中なら 10 進の文字列、外なら "auto"
    10 進の文字列        -> 同上
    bool / その他        -> "auto"

claude-autocompact-args(params) -> list   ; 常に 2 要素、空の並びを返さない
```

不変条件: 戻りは `auto` か 100k〜1M の 10 進整数の文字列のみ。既定も宣言も同じ関門を通る。
縮退の向きは常に `auto`。導出点はこの 1 つだけ。

### `launch-argv`

```
build-claude-argv(params) -> argv
build-claude-resume-argv(params) -> argv    ; 基礎の旗は build-claude-argv と共有

argv = ["claude" "--dangerously-skip-permissions"]
     + ["--settings" "{\"disableAllHooks\":true}"]   ; session_hooks != "inherit" の時だけ
     + ["--effort" <語>]                              ; 宣言があれば
     + ["--model" <名>]                               ; 宣言があれば
     + ["--autocompact" <auto|100000..1000000>]       ; 常に載る
     + ["--mcp-config" <json> "--strict-mcp-config"]  ; server があれば
     + ["--session-id" <id>]                          ; fresh だけ
```

不変条件: 凍結接頭 4 語を動かさない / `--effort` は index 4 / `--autocompact` は `--model` の後・
`--mcp-config` の前 / `--autocompact` は常に載る / prompt を argv に載せない。

### `charter-object`

- 欄 `auto_compact_window`: token の整数、または `"auto"`。無い = 宣言なし。
- 綴りの定義点は `acp/effects.py` の `CHARTER_AUTO_COMPACT_WINDOW_KEY`。
- launch / rehydrate の腕は charter を丸ごと params にする(素通し)。
- **resume の腕は `judgment.resume-params-of` の名簿が写す**(名簿に無い欄は落ちる)。
- agentd はこの欄を読むだけで、役(lead / worker)を知らない。

## 4. 将来変更についての主張(事前予測)と、維持すると主張する契約

| id | 変更 | 変わると予測する責務 | 維持すると主張する契約 |
| --- | --- | --- | --- |
| CC1 | 役ごとに別の閾値を配る | `route-dispatcher` と `acp-charter` だけ | `launch-argv` の凍結接頭と `--effort` の位置。agentd の code に役の語が現れない |
| CC2 | 閾値の値そのものを調整する | 宣言側(台帳 / 起票側の設定)だけ。**code の差分 0 行** | `charter-object` の綴り |
| CC3 | CLI の綴り・受ける幅が変わる | `autocompact-derivation` の定数 1 点だけ | 縮退の向きは `auto`・導出は 1 点 |
| CC4 | 起こす腕が増える / 運び方が変わる | `agentd-arms` だけ | どの腕でも同じ閾値が効く |

主張の前提: (a) CLI の綴り `--autocompact` と幅 100k〜1M は claude 2.1.274 の実測で、公開契約では
ない。(b) 配達方策の行は `current` の 1 つだけで、既定 charter は全会話で共通。
(c) 会話が宣言できる欄の閉語彙は現状 model / effort / workDir で、役の語は無い。

## 5. 実コードと、実装済み / 提案中の区別

**実装済み(この worktree)**

- `packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy`
  — 定数群・`claude-autocompact-value` / `claude-autocompact-args`・`build-claude-argv` への挿入
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py`
  — `CHARTER_AUTO_COMPACT_WINDOW_KEY`
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy`
  — `resume-params-of` の名簿への追加
- `packages/doeff-agents/tests/sessionhost_impls_deftests.hy`
  — 追加した deftest 4 本

**提案中(未実装)**

- 段 2: `agent-control-plane/src/Acp/App/Messaging/Plan.hs` の `charterFor` に欄を通し、
  起票側(dotfiles の route 設定)が役ごとの値を宣言する。

**参考(別実装・既存)**

- `dotfiles/agentcli/src/agentcli/headless.py` の `autocompact_value` — 同じ問いに対する
  別系統の走行係の実装。

## 6. 検査の実体・設定・検出範囲

- 単体: `packages/doeff-agents/tests/sessionhost_impls_deftests.hy`(Hy の `deftest`。
  fake substrate で impl handler を直接束縛し、生 IO ゼロ)。
- 適合検査: `packages/doeff-agents/conformance/test_s13_argv_wiring.py`
  (実際に agentd を起こし、shim が記録した `sys.argv` を assert する)。
- 静的検査: repo 直下 `.semgrep.yaml`(`doeff-agents-no-claude-print-mode` 等)、
  `ruff`(`pyproject.toml` の `[tool.ruff]`)。**Hy のコードは ruff の対象ではない。**
- 走らせ方: repo の `.venv` の pytest。`.hy` の deftest は pytest の収集口が要る。
  **全数・広範囲の走行は受付の門(test-admission)で止められる。焦点(`-k` / `::` の名指し)だけ許される。**

## 7. 読んでよいもの・してはいけないこと

上記 path を読み取り専用で読んでよい。共有ソースを変更しない。全数検査を走らせない。
他の会話へ連絡しない。日本語で返す。
