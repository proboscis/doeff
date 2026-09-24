# 実装依頼書: doeff の日次の全体検証の母集団を処理ステージごとに独立させ、テスト file の置き場を固定する

- card: acp:kanban-issue:ki-08ec2d7c901f(盤 agora-redesign・repo:doeff)
- 親の依頼: lt-HQ3E81PXS63W6AEWT540VDEQQ5(計画段)・依頼者 = 会話 c-3S6FSS1P9KAKHCTCZZ6724P3ZX
- 設計: この dir の design.md(改訂 1)・counterexamples.md・evidence/proto/(試作)
- 範囲: doeff の中だけ。dotfiles(着地の道具・受付)は変えない。

## なぜ

日次の全体検証(`.agents/land-queue.toml` の `gate.full`)は root の pytest → `make test-packages` → `make test-rust` を
`&&` でつないだ 1 本の命令で、root が赤の日は後ろの 2 つが 1 本も走らない。2026-09-24 の日次(断面 f271ae39)がそうで、
しかも台帳は `coverage = complete` と記録した(evidence/daily-f271ae39.json)。`make test-packages` の loop も最初の赤の
package で止まり、失敗名は package の dir からの相対(`tests/...`)なので package をまたいで衝突する。doeff-agents の
約 2,600 本のテストは、まだ一度も日次で測られていない。

## 変えるもの(5 file)

1. **`Makefile` の `test-packages`**(design.md §3.3・試作 evidence/proto/makefile-proposed.diff)
   - `cd` をやめ、repo の根から `$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e"` を呼ぶ。
   - 赤の後も続け、最後に `test-packages failed: <package 名…>` を出して exit 1(`test-rust` と同じ形)。
   - Python のテストが無い package を飛ばす既存の判定はそのまま。頭注に理由(失敗名は repo の根からの相対・赤で後ろを隠さない)を書く。
2. **`.agents/land-queue.toml` の `[gate].full`**(design.md §3.2・試作 evidence/proto/gate-full.proposed.toml)
   - 1 本の文字列を `build`(`blocks_rest = true`)/ `root` / `packages` / `rust` の 4 段の列にする。各テストの段は自分で `make sync` から始める。
   - 試作は今の文字列から evidence/proto/make_proposed_decl.py で機械的に組んだもの。そのまま使ってよい。
   - 頭注に: なぜ段に分けるか(F3 の実弾)・なぜ各段で `make sync` をやり直すか(遠隔の同期が `target/` を消す・段ごとに手元へ倒れ得る)・戻し方(1 本の文字列へ戻す)・決めた会話と日付。
3. **`docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy`**
   - `:scope` に `.agents/land-queue.toml` を足す。
   - `R8` を足す: 「日次の全体検証の母集団は root の pytest・package ごとの pytest・Rust crate ごとの cargo test の 3 つで、`gate.full` の別々の処理ステージに置く(build だけが後ろを止める)。1 つの母集団の赤は他の母集団を未実行にしない。package の母集団は全 package を訪ね、失敗名は repo の根からの相対で出す。repo の test 名の file は母集団の根の下か、理由つきの除外の表に在る」。
   - law を 1 つ足す(名前の例 `daily-populations-are-independent-and-complete`)。反例に: 2026-09-24 の日次(root の赤で後ろ 2 つが走らず coverage = complete)/ card ki-9fc7d4bca4dc の記憶の行(読み手を見落とした変更が、呼び手の無い木の緑のまま着地)/ package の loop の `|| exit 1` / package の dir から走らせた失敗名の衝突。
   - deftest を 1 つ足す(宣言の形の pin — design.md §3.4 の 1.)。段の run の中の命令は `sh -c "…"` の内側を ` && ` で区切って1 命令ずつ判じる(試作 pins.py は「他の母集団の命令を含む段を root と数えない」荒い判別で、2 つの母集団を 1 段に畳んだ宣言を別の文言で赤にした — evidence/proto/e5_scenarios.log の S0 陰性)。挙動の本体 `tests/test_daily_test_population.py` の現存も pin する(既存の deftest と同じ作法)。
4. **`docs/adr/enforcement-ledger.json`**: law +1・deftest +1 を同じ commit で(R5 / R7 — `make hooks-install` 済みなら commit 時に突合される)。
5. **`tests/test_daily_test_population.py`**(新規・design.md §3.4 の 2.〜4.・試作 evidence/proto/pins.py)
   - 期待する package の集合は**テストが file system から数える**(Makefile の実走から取らない — 盲検 B の反例)。
   - 除外の表は path か `/` で終わる dir の接頭辞 → 理由。初期値 6 行(design.md §3.4)。`conformance/` の理由には後続の card acp:kanban-issue:ki-2832a04913cc を書く。

## 順番(TDD — CLAUDE.md)

1. テストを先に書いて commit(3. の deftest と 5.)。今の本線で次が赤になることを見る: 宣言の形(列でない)/ 網羅(最初の package の 1 回で止まる)/ 失敗名(`tests/...` の形)。完全性は除外の表があれば緑 — 行を 1 つ消すと赤になることを 1 回撃って記録する。
2. 1. と 2. を実装して緑にする。

## 検証(1:1 で PR 本文 / 報告に表で書く)

| 確かめること | テスト |
|---|---|
| `gate.full` は 4 段の列・build だけが `blocks_rest`・各母集団をちょうど 1 段が呼ぶ・各テストの段は `make sync` から | `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy::test-adr-doe-enforce-001-daily-populations-are-separate-stages`(名前は例) |
| package の loop は期待の集合を全部訪ね、最初の package が赤でも続け、最後に名指し、rc ≠ 0 | `tests/test_daily_test_population.py::test_make_test_packages_visits_every_package_after_a_red`(名前は例) |
| 失敗名は repo の根からの相対で package をまたいで衝突しない(模型の木・本物の Makefile・本物の pytest) | `tests/test_daily_test_population.py::test_package_failure_names_are_repo_root_relative` |
| test 名の file は母集団の根の下か除外の表・除外の表に古い行が無い | `tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population` |
| package の pyproject は自分の pytest の設定を持たない | `tests/test_daily_test_population.py::test_no_package_declares_its_own_pytest_ini` |
| 新しい宣言を日次の道具が受け付ける | 報告に実測を貼る: `python3 -c` で `~/dotfiles/agentcli/src` を path に足し `agentcli.land_config.full_stages(tomllib.load(...)["gate"])` → 4 段・`build` だけ `blocks_rest` |
| 台帳 | `tests/test_enforcement_ledger.py`(既存) |

走らせてよいのは上の file の焦点だけ(各 60 秒以内): `uv run --no-sync pytest tests/test_daily_test_population.py docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy tests/test_enforcement_ledger.py -q`。

## してはいけないこと

- `make test-packages` の実走(実 pytest で全 package)・`make sync`・root の全体の pytest・日次の全体検証(`ai land verify`)を撃たない。全数は日次の役目で、次の日次がこの変更の最初の実測になる。
- dotfiles(`land.py`・`land_config.py`・`remote_check.py`・`broad_run_admission.py`)を変えない。受付の 60 秒の上限が日次の package の段に掛かる件は card acp:kanban-issue:ki-31fc80403050 が持つ — doeff の conftest や Makefile で受付を迂回しない。
- `conformance/` を母集団へ入れない(card acp:kanban-issue:ki-2832a04913cc が決める)。除外の表に載せるだけ。
- root の testpaths を変えない(root の testpaths に在る 5 package は root と packages の両方で走る — 母集団の独立の費用として受け入れる)。

## 既知の危険(報告に書く)

- cwd が package の dir から repo の根へ変わる。静的な候補 25 行は cwd に頼らない形だった(counterexamples.md の A)が、全数では未確認。次の日次で cwd 由来の赤が出たら、その package のテストを file 相対の path へ直す(e9025c13 の `test_handler_protocol` と同じ直し方)。
- `timeout_s = 7200` は全段の合計。build が 4 回になる。次の日次の所要を読んで足りなければ上げる。

## 着地

- worktree は `~/.worktrees/doeff-wt-<slug>`(branch `wt/<slug>`)。着地は `ai land request --branch wt/<slug>`。
- commit の trailer に card の鍵(acp:kanban-issue:ki-08ec2d7c901f)。

## 報告

仕上げたら、この依頼の郵便へ `ai reply <この依頼の郵便 id> --kind report "<結果>"` で返す(依頼者 = 会話 c-3S6FSS1P9KAKHCTCZZ6724P3ZX)。
報告に: 着地の番号(L…)か本線の commit・検証の表(テスト名と passed の数)・TDD の赤の記録・`full_stages` の実測・既知の危険。
