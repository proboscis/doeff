# 設計: 日次の全体検証の母集団を「処理ステージごとに独立」にし、テスト file の置き場を母集団へ結ぶ

- card: acp:kanban-issue:ki-08ec2d7c901f(盤 agora-redesign)
- 依頼: lt-HQ3E81PXS63W6AEWT540VDEQQ5(investigate・計画段)
- 基準版: origin/main 9df950b5477f9456216ab447b1c37879981425ec
- 範囲: **設計まで**。実装は dev の依頼へ渡す(実装依頼書 = implementation-request.md)。
- 書いた会話: この依頼を受けた agora の会話(Opus 5.5)

## 1. 実測した事実(2026-09-24〜25)

| # | 事実 | 出典 |
|---|---|---|
| F1 | card が名指した赤 3 本(`test_sessionhost_acp_record.py`)は現在の本線で緑。file 全体 19 passed(67.8 s・4da6ca4a・この pod)。 | evidence/focused-record-tests.log |
| F2 | card 起票の後、別の会話が `make test-packages`(c5aae481・09-24 01:04 JST)と `make test-rust`(35068f8e)を日次の `gate.full` に `&&` で足し、doeff-agents の赤 22 本を直した(a112fd2a・e9025c13 — Mac で package 全体 2635 passed / 0 failed)。 | git log |
| F3 | その後の日次(断面 f271ae39・09-24 03:30 JST)は赤。失敗 3 本はすべて root の `tests/`(`test_effectful.py` 2・`test_semgrep_gate.py` 1)。`&&` のため **`make test-packages` と `make test-rust` は 1 本も走っていない**。台帳の処理ステージは `full` の 1 つだけ。`ai land partition compensation --repo doeff` = absent(最後の緑 de9b8cda・09-21)。 | evidence/daily-f271ae39.md |
| F4 | `make test-packages` の loop は package ごとに `|| exit 1`。最初に赤になった package で止まり、後ろの package は走らない(doeff-agents は名前順で 3 番目)。`make test-rust` は失敗を貯めて最後に exit 1 する形で、後ろを隠さない。 | Makefile |
| F5 | 着地の道具(dotfiles `agentcli/land_config.full_stages` / `land.run_full_stages`)は `gate.full` を**名前つき処理ステージの列**で宣言でき、日次は全処理ステージを最後まで走らせて処理ステージごとに記録する(`stop_on_first_failure=False`・`blocks_rest` の処理ステージだけが後ろを止める)。herdr-hud・dotfiles・agent-control-plane・agora-controllers は移行済み、doeff だけが 1 本の文字列のまま。 | land.py 1680〜1700 行の註・各 repo の `.agents/land-queue.toml` |
| F6 | `packages/doeff-agents/conformance/` の 34 file(tmux / herdr の実 pane を使う黒箱の交代ゲート・実モデルは使わない)は、root の testpaths にも `make test-packages` にも日次にも入っていない。他の母集団外の test 名の file 5 本(PyCharm の検体・例示 script 2・fixture の module・道具の script)はテストではない。 | evidence/out-of-population-files.log |
| F7 | root の 1 session に全 package の tests を集める案(card の元案 = testpaths に足す)は、package をまたぐ同名の test file が 4 組(`test_cli.py`・`test_effect_handlers.py`・`test_effects.py`・`test_types.py`)あり、既定の import mode では衝突する。package の dir から走らせた時も ini は root の pyproject が使われ、node id は repo の根からの相対(`packages/doeff-agents/tests/...`)なので、package ごとの session でも失敗名は package をまたいで一意。package 側の pyproject に `[tool.pytest]` は 0 件。 | evidence/rootdir-and-collisions.log |
| F8 | `gate.full` を使うのは日次だけ(`full_paths = []`)。`.agents/land-queue.toml` への変更は着地で検査されない(tier = "true")。 | .agents/land-queue.toml |
| F9 | `remote_check` は同期のたびに、git の名簿に無い file を遠隔から消す(`.venv` だけ除外)。`packages/doeff-vm/target` は毎回消えるので、遠隔の `make sync` は毎回 Rust を最初から build する。 | remote_check.py 620〜645 行 |

## 2. 要件

- **Q1** 日次の母集団 = root の pytest(testpaths)∪ package ごとの pytest(`packages/*/tests` に Python のテストが在る package)∪ Rust crate ごとの cargo test。各母集団の結果は独立に記録され、1 つの母集団の赤・時間切れが他の母集団を未実行にしない(例外は build の前提だけ)。
- **Q2** package の母集団の中で、1 package の赤が他の package を未実行にしない。最後に失敗した package を全部名指し、1 つでも失敗なら終了コードは 0 以外。
- **Q3** repo の test 名の file(`test_*.py` / `*_test.py`・`fixtures` の下を除く)は、宣言された母集団の根の下にあるか、理由つきの除外の表に載る。新しい package の `tests/` は宣言を編集せずに母集団に入る。
- **Q4** Q1〜Q3 は root の既定の pytest で走るテストで固定する(ADR-DOE-ENFORCE-001 に条を足す)。root の処理ステージは日次で毎回走るので、固定のテスト自身が「呼び手の無い木」に落ちない。
- **Q5** card の元案「testpaths に `packages/doeff-agents/tests` を足す」は採らない(F7 の衝突・二重実行・開発者の既定の `uv run pytest` が約 16 分重くなる)。戻せる決定として card に記録する。

範囲外(別の持ち主): zeus でだけ赤の root 3 本の修理(日次の修理の流れ)/ `conformance/` を日次へ入れるか(宿の tmux の有無が未測定 — 別の card)/ `remote_check` が `target/` を消さないようにする費用の最適化(dotfiles・別の card)。

## 3. 設計

### 3.1 責務(module)

| id | 置き場 | 持つもの | 隠すもの |
|---|---|---|---|
| `gate-full-stages` | `.agents/land-queue.toml` `[gate].full` | 日次が測る母集団の一覧・順序・build の前提(`blocks_rest`)・各処理ステージの実行場所(遠隔の前置き) | 各母集団の中身の列挙(Makefile が持つ) |
| `make-test-packages` | `Makefile` `test-packages` | package の母集団の規則(`packages/*/tests` に `test_*.py` が在る)・package ごとの独立 session・赤の後も続ける・失敗の一覧 | 日次の存在(どこから呼ばれるかを知らない) |
| `make-test-rust` | `Makefile` `test-rust` | crate の母集団(変更なし) | 同上 |
| `root-testpaths` | `pyproject.toml` testpaths | root の母集団(変更なし) | 同上 |
| `population-pin` | ADR-DOE-ENFORCE-001 R8 + law + deftest、`tests/test_daily_test_population.py` | 「母集団が処理ステージごとに独立」「package の loop が全部を訪ねる」「test 名の file は母集団か除外の表に在る」の固定・除外の表 | 各母集団の走らせ方の細部 |
| `land-mechanism`(外部・変更しない) | dotfiles `land_config` / `land.run_full_stages` / `remote_check` | 処理ステージの読み替え・全部走らせる方策・処理ステージごとの記録・遠隔同期 | — |

### 3.2 `gate.full` の新しい形(公開契約 = land_config.full_stages の閉じた鍵 name / run / blocks_rest / layers)

```toml
full = [
  { name = "build",    run = "<RC> -- sh -c \"<lane> make sync\"", blocks_rest = true },
  { name = "root",     run = "<RC> -- sh -c \"<lane> make sync && <lane> PYTHONUNBUFFERED=1 uv run --no-sync pytest -q -m 'not e2e'\"" },
  { name = "packages", run = "<RC> -- sh -c \"<lane> make sync && <lane> PYTHONUNBUFFERED=1 make test-packages PACKAGE_UV_RUN='uv run --no-sync'\"" },
  { name = "rust",     run = "<RC> -- sh -c \"<lane> make sync && <lane> make test-rust\"" },
]
# <RC>   = python3 $HOME/dotfiles/agentcli/src/agentcli/remote_check.py --node zeus --tree . --label doeff-gate-full --sync-timeout 900 --run-timeout 3600 --fallback-local
# <lane> = UV_CACHE_DIR=\$HOME/.cache/uv-lanes/land-doeff
```

- **各テストの処理ステージは自分で `make sync` をやり直す**(自己完結)。理由: 遠隔の同期は処理ステージごとに走り `target/` を消す(F9)、処理ステージは 1 つずつ独立に手元へ倒れ得る(`--fallback-local`)、同じ label の作業 dir は処理ステージの合間に別の走行へ渡り得る。どの場合も「前の処理ステージが作った build」を前提にすると、別の木の build でテストする形になる。
- **`build` の処理ステージ**は帰属のためにある: build が壊れた日は `build` が赤 1 つ + 残り 3 つが「前提が成功しなかったので未実行」になり、同じ build の誤りが 3 つの赤として並ばない。費用は成功の日に Rust の build 1 回分。
- 着地の側(`full_command` が `&&` で 1 本へ戻す)は `full_paths = []` で使われない。使われても 4 本の `remote_check` が順に走るだけで意味は変わらない。

### 3.3 `make test-packages`

`make test-rust` と同じ形へ: package ごとに `(cd $dir && $(PACKAGE_UV_RUN) pytest tests/ -m "not e2e") || failed="$failed <name>"`、loop の後で `failed` が空でなければ `test-packages failed:<names>` を出して exit 1。package の選び方(Python のテストが無い package を飛ばす)と独立 session(F7)は変えない。

### 3.4 固定のテスト(population-pin)

1. **宣言の形の pin**(ADR-DOE-ENFORCE-001 の deftest・tomllib で読む): `gate.full` が列 / 名前が一意 / `build` が先頭で `blocks_rest = true` / 他の処理ステージは `blocks_rest` を立てない / root の pytest・`make test-packages`・`make test-rust` をそれぞれ**ちょうど 1 つ**の処理ステージが呼び、1 つの処理ステージが 2 つの母集団を呼ばない / テストの処理ステージは母集団の命令の前に `make sync` を持つ。
2. **package の loop の挙動**(`tests/test_daily_test_population.py`): `make -s test-packages PACKAGE_UV_RUN=<偽の runner>` を走らせる。偽の runner は自分の cwd を記録し、名指した package でだけ 1 を返す。(a) 全部成功 → rc 0・訪ねた集合 = 列挙した集合。(b) 名前順で最初の package を失敗させる → rc ≠ 0・残りの package も全部訪ねる・出力が失敗の package を名指す。実 pytest は 1 本も走らない(数秒)。
3. **完全性**(同じ file): git の名簿の test 名の file(`fixtures` を除く)の各々が、root の testpaths の下か、2. で訪ねた `packages/<p>/tests` の下か、除外の表(path → 理由)に在る。除外の表に在るのに file が消えた行(古い行)も赤。package の母集団は Makefile を実際に走らせた結果から取り、glob を写さない(定義点は Makefile の 1 つ)。

除外の表の初期値 = F6 の 39 file(`conformance/` は「tmux / herdr の実 pane が要る黒箱の交代ゲート。日次の宿での可否が未測定 — card <後続>」)。

### 3.5 組み立て点から消える判断

- 「どの母集団をどこまで走らせるか」を `&&` の短絡が暗黙に決めていた判断は消える(`land.run_full_stages` の既定の方策 = 全部走らせる、に一本化)。
- package の loop が「赤の後の package を走らせない」と暗黙に決めていた判断は消える。

## 4. 変更シナリオ(事前の主張は claims-before-blind.md に固定)

hardware / storage / effects / distribution / concurrency / simulation の 6 軸と、実弾(root の赤が後ろを隠す)の再現。

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 母集団は処理ステージごとに独立・build だけが後ろを止める | 宣言の pin(tomllib で読む deftest) | `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy` の新しい deftest | root の既定 pytest(`docs/adr` は testpaths)→ 日次の `root` 処理ステージ | 文字列の中の命令の判別は字面。land_config の parser 自体は doeff から呼ばない(実装時に 1 回実測する)。壊れた宣言は日次の道具が理由つきで未実行にする(F5 ③) |
| package の loop は全部を訪ね、失敗を全部名指す | 偽の runner で Makefile を実際に走らせる挙動のテスト | `tests/test_daily_test_population.py` | 同上 | make / sh / find が要る。実 pytest の rc の意味(5 = 収集 0)は偽の runner では扱わない |
| test 名の file は母集団か除外の表 | 完全性のテスト(git の名簿 × testpaths × Makefile の実走の訪問先 × 除外の表) | 同上 | 同上 | 名前の規則(`test_*.py` / `*_test.py`)の外で定義したテストは数えない(root の ini は `python_files` を変えていない)。`.hy` の deftest は Python の包みの file で数える |
| 台帳 | enforcement-ledger.json を同じ commit で更新 | `docs/adr/enforcement-ledger.json` | pre-commit の hook(R7)と既定 pytest(R5) | — |
