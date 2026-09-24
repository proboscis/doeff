# 設計: 日次の全体検証の母集団を「処理ステージごとに独立」にし、テスト file の置き場を母集団へ結ぶ

- card: acp:kanban-issue:ki-08ec2d7c901f(盤 agora-redesign)
- 依頼: lt-HQ3E81PXS63W6AEWT540VDEQQ5(investigate・計画段)
- 基準版: origin/main 9df950b5477f9456216ab447b1c37879981425ec
- 範囲: **設計まで**。実装は dev の依頼へ渡す(implementation-request.md)。
- 版: **改訂 1(盲検 A・B の後)**。盲検の前の版 = design.before-blind.md(sha256 d0c1fe04… = SHA256SUMS.txt の盲検前の記録)。
  改訂の理由と再現 = counterexamples.md。事前の主張 = claims-before-blind.md(書き換えていない)。

## 1. 実測した事実(2026-09-24〜25)

| # | 事実 | 出典 |
|---|---|---|
| F1 | card が名指した赤 3 本(`test_sessionhost_acp_record.py`)は現在の本線で緑。file 全体 19 passed(67.8 s・4da6ca4a・この pod)。 | evidence/focused-record-tests.log |
| F2 | card 起票の後、別の会話が `make test-packages`(c5aae481・09-24 01:04 JST)と `make test-rust`(35068f8e)を日次の `gate.full` に `&&` で足し、doeff-agents の赤 22 本を直した(a112fd2a・e9025c13 — Mac で package 全体 2635 passed / 0 failed)。 | git log |
| F3 | その後の日次(断面 f271ae39・09-24 03:30 JST)は赤。失敗 3 本はすべて root の `tests/`(`test_effectful.py` 2・`test_semgrep_gate.py` 1)。`&&` のため `make test-packages` と `make test-rust` は 1 本も走っていない。台帳の処理ステージは `full` の 1 つだけで、**`coverage = {"complete": true, "unexecuted": []}`**(走っていない母集団が在るのに「全部測った」と記録)。最後の緑は de9b8cda(09-21)。 | evidence/daily-f271ae39.json |
| F4 | `make test-packages` の loop は package ごとに `|| exit 1`。最初に赤になった package で止まり、後ろは走らない(Python のテストを持つ package は 27・名前順の先頭は doeff-adr)。`make test-rust` は失敗を貯めて最後に exit 1 する形。 | Makefile・evidence/proto/e4_pins_i_rerun.log |
| F5 | 着地の道具(dotfiles `land_config.full_stages` / `land.run_full_stages`)は `gate.full` を名前つき処理ステージの列で宣言でき、日次は全段を最後まで走らせて段ごとに記録する(`blocks_rest` の段だけが後ろを止める)。herdr-hud・dotfiles・agent-control-plane・agora-controllers は移行済み、doeff だけが 1 本の文字列のまま。 | land.py 1680〜1700 行の註・evidence/proto/e3_stages.log |
| F6 | test 名の file(`test_*.py` / `*_test.py`・`fixtures` を除く)のうち、root の testpaths にも `packages/*/tests` にも居ないものは 41 本: `packages/doeff-agents/conformance/` 32(tmux / herdr の実 pane を使う黒箱の交代ゲート・実モデルは使わない)・`docs/design/` の設計の模型 4・テストでない file 5(PyCharm の検体・例示の script 2・fixture の module・道具の script)。 | evidence/out-of-population-files.log |
| F7 | pytest の要約の `FAILED` / `PASSED` 行は **cwd からの相対**(`config.cwd_relative_nodeid`)。package の dir から走らせると `tests/test_x.py::…` になり、package をまたいで衝突する(同名の test file が 4 組 — `test_cli.py`・`test_effect_handlers.py`・`test_effects.py`・`test_types.py`)。repo の根から `pytest packages/<p>/tests` で走らせた時だけ `packages/<p>/tests/…` になる。package の pyproject に `[tool.pytest]` は 0 件で、ini は root の pyproject が使われる。 | evidence/A-real-repo-summary-path.log・rootdir-and-collisions.log |
| F8 | 日次の道具は失敗名を要約の行から逐語で取り、「repo の根から pytest へそのまま渡せる」ことを前提にする。 | land.py `_failed_name_pytest` の註 |
| F9 | `gate.full` を使うのは日次だけ(`full_paths = []`)。`.agents/land-queue.toml` への変更は着地で検査されない(tier = "true")。 | .agents/land-queue.toml |
| F10 | `remote_check` は同期のたびに、git の名簿に無い file を遠隔から消す(`.venv` だけ除外)。`packages/doeff-vm/target` は毎回消え、遠隔の `make sync` は毎回 Rust を最初から build する。 | remote_check.py 620〜645 行 |
| F11 | 広い走行の受付(dotfiles `agent/tests/broad_run_admission.py`・doeff は root の conftest から呼ぶ)は、1 回の pytest が `per_run_max`(doeff は 400)件以下なら 60 秒の時間上限を据え、切れると `local-timeout` の未実行で終わる。受付の正本の無い宿では素通し。 | broad_run_admission.py 325〜440 行 |

## 2. 要件

- **Q1** 日次の母集団 = root の pytest(testpaths)∪ package ごとの pytest(`packages/*/tests` に Python のテストが在る package)∪ Rust crate ごとの cargo test。各母集団の結果は独立に記録され、1 つの母集団の赤・時間切れが他の母集団を未実行にしない(例外は build の前提だけ)。
- **Q2** package の母集団の中で、1 package の赤が他の package を未実行にしない。最後に失敗した package を全部名指し、1 つでも失敗なら終了コードは 0 以外。
- **Q2b** 日次に載る失敗名は、どの母集団のものも repo の根から pytest へそのまま渡せ、互いに衝突しない。
- **Q3** repo の test 名の file は、宣言された母集団の根の下にあるか、理由つきの除外の表(path か dir の接頭辞)に載る。新しい package の `tests/` は宣言を編集せずに母集団に入る。
- **Q4** Q1〜Q3 は root の既定の pytest で走るテストで固定する(ADR-DOE-ENFORCE-001 に条を足す)。root の段は日次で毎回走るので、固定のテスト自身が「呼び手の無い木」に落ちない。
- **Q5** card の元案「testpaths に `packages/doeff-agents/tests` を足す」は採らない(F7 の衝突・二重実行・開発者の既定の `uv run pytest` が約 16 分重くなる)。戻せる決定として card に記録する。

範囲外(別の持ち主): zeus でだけ赤の root 3 本の修理(日次の修理の流れ)/ `conformance/` を日次へ入れるか(宿の tmux の有無が未測定 — 後続の card)/ F11 の時間上限が日次の package の段に掛かる件(受付の側で直す — 後続の card)/ `remote_check` が `target/` を消さないようにする費用の最適化(dotfiles)。

## 3. 設計

### 3.1 責務(module)

| id | 置き場 | 持つもの | 隠すもの |
|---|---|---|---|
| `gate-full-stages` | `.agents/land-queue.toml` `[gate].full` | 日次が測る母集団の一覧・順序・build の前提(`blocks_rest`)・各段の実行場所(遠隔の前置き) | 各母集団の中身の列挙と走らせ方(Makefile / pyproject が持つ) |
| `make-test-packages` | `Makefile` `test-packages` | package の母集団の規則(`packages/*/tests` に `fixtures` 以外の `test_*.py` が在る)・package ごとの独立 session・赤の後も続ける・失敗の一覧・**失敗名は repo の根からの相対(Q2b)** | 日次の存在(どこから呼ばれるかを知らない)・他の母集団の中身 |
| `make-test-rust` | `Makefile` `test-rust` | crate の母集団(変更なし) | 同上 |
| `root-testpaths` | `pyproject.toml` testpaths | root の母集団と、package の母集団にも効く pytest の ini(変更なし) | 同上 |
| `population-pin` | ADR-DOE-ENFORCE-001 R8 + law + deftest、`tests/test_daily_test_population.py` | Q1〜Q3 の期待値(実装から独立に数える)・除外の表 | 各母集団の走らせ方の細部(cwd 等) |
| `land-mechanism`(外部・変更しない) | dotfiles `land_config` / `land.run_full_stages` / `remote_check` | 段の読み替え・全部走らせる方策・段ごとの記録・遠隔同期 | — |

### 3.2 `gate.full` の新しい形(公開契約 = land_config.full_stages の閉じた鍵 name / run / blocks_rest / layers)

実物の試作 = evidence/proto/gate-full.proposed.toml(今の 1 本の文字列から make_proposed_decl.py で機械的に組んだもの・`full_stages` が受け付けることを実測済み)。

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

- 各テストの段は自分で `make sync` をやり直す(自己完結)。遠隔の同期は段ごとに走り `target/` を消す(F10)、段は 1 つずつ独立に手元へ倒れ得る(`--fallback-local`)、同じ label の作業 dir は段の合間に別の走行へ渡り得る。どの場合も「前の段が作った build」を前提にすると別の木の build でテストする形になる。
- `build` の段は帰属のためにある: build が壊れた日は `build` が赤 1 つ + 残り 3 つが「前提が成功しなかったので未実行」になる(evidence/proto/e3_stages.log)。費用は成功の日に Rust の build 1 回分。
- `timeout_s = 7200` は全段の合計の上限(`run_full_stages` が残りを各段へ渡す)。今の zeus の実測(root 約 480 s + package 約 960 s + Rust)に build 3 回分が足されても収まる見込み。最初の日次で所要を読み、足りなければ上げる(戻せる)。

### 3.3 `make test-packages`

試作 = evidence/proto/makefile-proposed.diff。

```make
test-packages:
	@echo "Running tests in subpackages..."
	@failed=""; \
	for dir in packages/*/; do \
		(Python のテストが無い package を飛ばす既存の if はそのまま) \
		echo "=== Testing $$(basename $$dir) ==="; \
		$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" || failed="$$failed $$(basename $$dir)"; \
	done; \
	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi
```

- `cd` をやめ、repo の根から package の `tests` を渡す(F7・Q2b)。session は package ごとに独立のまま(同名の test file の衝突を避ける)。
- cwd が package の dir から repo の根へ変わる。静的な候補 25 行はどれも cwd を偽の session の作業 dir に渡すだけで、package の dir であることに頼らない(counterexamples.md の A)。全数での確認は日次の最初の走行が行う。

### 3.4 固定のテスト(population-pin)

試作 = evidence/proto/pins.py(設計段の模型 — 本実装は下の置き場へ書き直す)。

1. **宣言の形**(ADR-DOE-ENFORCE-001 の deftest・tomllib で読む): `gate.full` が列 / 名前が一意 / 先頭が `blocks_rest = true` の `build` / 他の段は `blocks_rest` を立てない / root の pytest・`make test-packages`・`make test-rust` をそれぞれ**ちょうど 1 つ**の段が呼び、1 つの段が 2 つの母集団を呼ばない / テストの段は母集団の命令より前に `make sync` を持つ。
2. **package の母集団の網羅**(`tests/test_daily_test_population.py`): 期待する package の集合を**テストが file system から独立に数える**(`packages/<p>/tests` の下に `fixtures` 以外の `test_*.py` が在る p の全部)。`make -s test-packages PACKAGE_UV_RUN=<偽の runner>` を repo の根で走らせ、偽の runner は受け取った引数を記録して、期待の集合の名前順で最初の package でだけ 1 を返す。検めるのは: 引数に `packages/<p>/tests` を受けた p の集合 = 期待の集合(欠けも余りも赤)/ rc ≠ 0 / 出力の `test-packages failed:` がその package を名指す。
3. **失敗名の契約**(同じ file): tmp_path に模型の木(root の pyproject・同名の失敗 `tests/test_cli.py::test_fail` を持つ 2 package・緑の 1 package)を作り、**本物の** Makefile の `test-packages` を `make -f <repo の Makefile> -C <tmp>` で本物の pytest(`PACKAGE_UV_RUN="<sys.executable> -m"`)で走らせ、`FAILED packages/a/tests/test_cli.py::test_fail` と `FAILED packages/b/tests/test_cli.py::test_fail` の 2 行・緑の package も訪ねたこと・`test-packages failed: a b` を見る(数秒)。
4. **完全性**(同じ file): git の名簿の test 名の file(`fixtures` を除く)の各々が、root の testpaths の下か、2. の期待の集合の `packages/<p>/tests` の下か、除外の表(path か `/` で終わる dir の接頭辞 → 理由)に在る。除外の表の行で、該当する file が 1 本も無いもの(古い行)も赤。**package の pyproject が `[tool.pytest.ini_options]` を持ったら赤**(root の ini と conftest が package の母集団に効かなくなる — 盲検 A の副次)。

除外の表の初期値(6 行): `packages/doeff-agents/conformance/`(後続の card を理由に書く)・`docs/design/`・`ide-plugins/pycharm/test_program_detection.py`・`packages/doeff-agentic/examples/`・`packages/doeff-test-target/src/doeff_test_target/effects/test_effects.py`・`tools/test_python_versions.py`。

### 3.5 組み立て点から消える判断

- 「どの母集団をどこまで走らせるか」を `&&` の短絡が暗黙に決めていた判断は消える(`run_full_stages` の既定の方策 = 全部走らせる、に一本化)。
- package の loop が「赤の後の package を走らせない」と暗黙に決めていた判断は消える。
- 「失敗名が何からの相対か」を cwd が暗黙に決めていた判断は、`make-test-packages` の契約(repo の根からの相対)になる。

## 4. 変更シナリオ

事前の主張 = claims-before-blind.md(S0〜S6)。盲検と実測の結果は counterexamples.md と report.json の scenarios。

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 母集団は段ごとに独立・build だけが後ろを止める・各段は自己完結 | 宣言の形の pin(tomllib で読む deftest) | `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy` の新しい deftest | root の既定 pytest(`docs/adr` は testpaths)→ 日次の `root` の段 | 命令の判別は字面。land_config の parser は doeff から呼ばない(実装時に 1 回実測して記録)。壊れた宣言は日次の道具が理由つきで未実行にする |
| package の loop は期待の集合を全部訪ね、失敗を全部名指す | 偽の runner で本物の Makefile を走らせ、file system から独立に数えた期待と比べる | `tests/test_daily_test_population.py` | 同上 | make / sh / find が要る |
| 失敗名は repo の根からの相対で衝突しない | 模型の木で本物の Makefile と本物の pytest を走らせ、`FAILED` 行を読む | 同上 | 同上 | 日次の道具の抽出器そのものは呼ばない(doeff から dotfiles を import しない)。`FAILED <path>` の形は pytest の契約 |
| test 名の file は母集団か除外の表・package は自前の pytest 設定を持たない | 完全性のテスト(git の名簿 × testpaths × 期待の集合 × 除外の表)+ package の pyproject の検め | 同上 | 同上 | 名前の規則(`test_*.py` / `*_test.py`)の外で定義したテストは数えない(root の ini は `python_files` を変えていない)。`.hy` の deftest は Python の包みの file で数える |
| 台帳 | enforcement-ledger.json を同じ commit で更新(law +1・deftest +1) | `docs/adr/enforcement-ledger.json` | pre-commit の hook(R7)と既定 pytest(R5) | — |
