# 設計検証 lt-6NTWFZDM0EAGNFGW7TYZ4RM5ES — 変えた所の検が doeff-traverse の例の検を選ぶ

## 0. 完了の範囲

**実装完了まで**。実装は doeff の main に入った df9e627561c91221180ee02e11fc9846e767d962(L2121・2026-10-12 06:19 JST)。
直す前の版は cffba412b6583c2c5ae98225d0dc9a1b7263105e(実装の commit の最初の親。載せ直しの後の親は c9e1622b589dcde007b42baaa5817ea9332b61e7)。
この記録は実装とマージの後に作った。順が逆なので、§3 の主張は盲検の前に固定した版(この file を最初に push した commit)を正とし、
盲検の後で書き換えない。

## 1. 受入の条件(依頼 lt-6NTWFZDM0EAGNFGW7TYZ4RM5ES の本文から)

- R1: マージの前の検(変えた所の検)で、examples か doeff-hy の macro を変えた時に、例の検が選ばれるかを確かめる。
- R2: 選ばれないなら選ぶようにする。

固定した事実(直す前の cffba412b で実測):

- doeff のマージの列は検を走らせない(`.agents/land-queue.toml` の `mode = "focus"`・門は衝突の印の走査だけ — operator の裁定)。マージの前の検は、
  書き手が登記の前に手元で走らせる入口 `make test-changed`(`scripts/run_changed_tests.py`)である。
- 入口の選び = 契約の組(root の `pyproject.toml` の `[[tool.doeff.contract-tests]]`・接頭辞 → 検の列)→ 変えた検の file → 逆依存の検
  (doeff-linter の `--affected-tests`・変えた file を import / require する検)。合計の壁時計の上限は既定 60 秒で、番の来なかった検は「未測」と名指す。
- 例の検 `packages/doeff-traverse/tests/test_examples_run.py` は examples/ を glob して `python -m hy` の子 process で走らせ、期待の出力と比べる。
  例も macro も import しないので、逆依存では選ばれない。契約の表にも無かった。
- ex09_for_do_when.hy に 1 行足した差分で入口を走らせると、選んだ検は `test_traverse_answer_types.py` と repo 全体の行の 2 本だけ
  (`evidence/pre/entry-before-fix-example-change.txt`)。macros.hy を変えた差分では、入口が「doeff_hy.macros は直接の使い手 780 で逆依存を辿らない」と名指す。

## 2. 責務(module)

| id | 何か | 持つ知識・判断 | 隠す物 | 公開の口 | 作用 | 寿命 |
| --- | --- | --- | --- | --- | --- | --- |
| contract-table | root の `pyproject.toml` の `[[tool.doeff.contract-tests]]` | どの path の接頭辞の変更で、どの検を必ず先頭で走らせるか(import の逆依存で選べない検の宣言) | なし(宣言の表) | 行 = `prefix` と `tests`(または `prefix` と `reason` の除外) | なし(読まれるだけ) | repo の版と同じ |
| change-entry | `scripts/run_changed_tests.py`(`make test-changed`) | 変えた file の求め方・表の読み(`read_contract_table` の 1 か所)・選びの順(契約 → 変えた検 → 逆依存)・session の束ね・予算と未測 | pytest の起こし方・結果の受け取り(ReportPlugin) | `select_targets(table, changes, patterns, repo)`・入口の要約と `land-focus-counts:` の 1 行・rc | git を読む・pytest の子 process・linter の子 process | 1 回の走行 |
| reverse-deps | doeff-linter の `--affected-tests` | import / require の逆依存・広すぎる module(hub)の名指し | 依存の木 | JSON(tests・hubs・unreadable) | なし(読むだけ) | 1 回の問い |
| examples-check | `packages/doeff-traverse/tests/test_examples_run.py` | 例の母集団(examples/*.hy の glob)・期待の出力(examples/expected/<stem>.txt)・BROKEN と OUTPUT_NOT_COMPARED の表・macro の持ち主(doeff_hy)の確かめ | 走らせ方の下請け | pytest の id `test_example_runs[<file>]` ほか | 子 process・例の file を読む | 検 1 つ |
| macro-owner | `packages/doeff-hy/src/doeff_hy/`(例が require する `doeff-hy.macros` と、その展開が読む module) | macro の名と展開 | 展開の中身 | `(require doeff-hy.macros [名 …])` | compile の時だけ | compile 1 回 |
| table-guard | `tests/test_contract_table_follows_packages.py`(今回 検を 1 つ足し、模型の写しを直した) | 表と木のずれ(package の増減・検の file の有無)と、今回足した「例・期待の出力・macros.hy の変更が例の検を選ぶ」 | — | pytest の検 | 木を読む・模型の repo を tmp に作る | 検 1 つ |
| daily-gate | `Makefile` の `test-packages`・`.agents/land-queue.toml` の gate.full | 日次に何を走らせるか | — | 日次の全体検証 | — | 日次 1 回(変えない) |

今回変えた物: contract-table(2 行)・table-guard(検 1 つ・模型の写しの 1 か所)。変えない物: change-entry・reverse-deps・examples-check・macro-owner・daily-gate。

不変の条件:

- I1(contract-table × examples-check): 例の検が歩く入力(examples/ の `.hy` と expected/ の `.txt`)と、例が require する macro の持ち主の source の
  どの 1 file を変えた差分でも、`select_targets` が例の検を選ぶ。
- I2(contract-table): doeff-hy の検(packages/doeff-hy/tests/)だけを変えた差分では、例の検を選ばない(予算 60 秒を例の検に払わせない)。
- I3(change-entry): 予算に収まらない時、例の検は「未測」と名指され、赤にも緑にも数えない(入口の既存の振る舞い・今回変えない)。

## 3. 事前の主張(盲検の前に固定)

### S1(軸 storage・適用): 例・期待の出力の置き場が変わる

- 変更: (a) 新しい例 ex10 と期待の出力を examples/ に足す。(b) 例の置き場を doeff-traverse の package の外(例 repo の根の `examples/traverse/`)へ移す。
- 主張: (a) 表・table-guard・change-entry は変えない — 接頭辞 `packages/doeff-traverse/` と table-guard の glob が新しい file を拾う。
  (b) examples-check の `EXAMPLES` と、table-guard の `EXAMPLES_DIR`、表の行(新しい置き場の接頭辞 → 例の検)の 3 か所を一緒に変える(共同の不変条件 I1)。
  表を直し忘れると table-guard が移した path を名指して赤。table-guard の `EXAMPLES_DIR` を直し忘れると「例が無い」で赤(空の glob で緑にならない)。
- 前提: table-guard は表の repo 全体の行(`prefix = ""`)に載っていて、どの変更の差分でも入口が走らせる。
- 予想の範囲: (a) examples/ だけ。(b) examples-check・table-guard・contract-table(置き場の path を 3 か所が持つ — 写しであり、table-guard がずれを赤にする)。

### S2(軸 effects・適用): 例が require する macro の持ち主の source が変わる

- 変更: (a) `packages/doeff-hy/src/doeff_hy/` の macros.hy か、その展開が読む module(record.hy など)を変える。(b) doeff-hy の検だけを変える。
  (c) macro が doeff-hy の外の新しい package へ分かれる。
- 主張: (a) 表・入口は変えない — 行 `packages/doeff-hy/src/doeff_hy/` が拾う。(b) 例の検は選ばれない(I2)。(c) 表の行と table-guard の `EXAMPLE_MACROS`、
  examples-check の `_MACRO_OWNER` を一緒に変える。table-guard は `EXAMPLE_MACROS` が無いと赤。
- 前提: 例は `doeff-hy.macros` だけを require する(examples-check の macro の持ち主の確かめが他の require を断る)。
- 予想の範囲: (a) macro-owner だけ。(b) doeff-hy の検だけ。(c) macro-owner・contract-table・table-guard・examples-check。

### S3(軸 hardware・適用): 入口を遅い・負荷の高い機体で走らせる

- 変更: 例や macro を変えた差分で、入口を負荷の高い Pod(load average 100 超)で走らせる。
- 主張: 表と入口は変えない。予算に収まらない検(例の検・その後ろの repo 全体の行)は「未測」と名指され、rc は 0(赤にも合格にも数えない)。
  例の検を速くする変更(例を並べて走らせる)は examples-check の中に収まり、表と入口を変えない。
- 前提: 入口は束を順に走らせ、残りの予算を次の束に渡す。
- 予想の範囲: 変更なし(振る舞いの確かめ)。速くする変更は examples-check だけ。

### 軸ごとの不適用

- distribution: 入口は書き手の作業木の上で 1 機体で走る。マージの列は検を走らせない(operator の裁定)。走る所を移す変更は門の配線の話で、表の中身は変わらない。
- concurrency: 入口は束を順に 1 process ずつ走らせ、表は読むだけ。この変更は並行を足さない。
- simulation: 選び(`select_targets`)は純粋な関数で、table-guard は子 process を起こさずに本物の表で直に呼ぶ。時間・乱数の決定性に関わる変更は無い。

## 4. 強制方法

| 守る責務 | 強制方法と選んだ理由 | 実装の在り処 | 実行の経路 | 限界 |
| --- | --- | --- | --- | --- |
| I1: 例の検の入力の変更が例の検を選ぶ | 本物の表と入口と同じ `select_targets` で、例 12・期待の出力 8・macros.hy の各 1 file の差分を作り、例の検が選ばれるかを見る。表は toml の宣言で型では表せず、例の検は import しないので逆依存でも表せない | `tests/test_contract_table_follows_packages.py::test_changing_an_example_or_its_macros_selects_the_examples_check`(定数 `EXAMPLES_CHECK`・`EXAMPLES_DIR`・`EXAMPLE_MACROS`) | 入口の契約の組の repo 全体の行(どの差分でも走る)・日次の root の段(`pytest tests`) | macro の持ち主の source のうち、確かめるのは macros.hy の 1 file。他の module は接頭辞が同じなので同じ行が拾う(行を file 単位に狭めると table-guard は気づかない)。置き場の path は examples-check と table-guard の 2 か所に写しがある |
| I2: doeff-hy の検だけの差分は例の検を選ばない | 行の接頭辞を src に絞る(`packages/doeff-hy/src/doeff_hy/`) | `pyproject.toml` の行 | 入口 | 検で固定していない(table-guard は選ぶ側だけを見る) |
| I3: 予算越えは未測 | 入口の既存の振る舞い(打ち切った束の検を未測と名指し、rc を変えない) | `scripts/run_changed_tests.py`(`run_batches`・`classify`) | 入口 | 未測の検は日次まで測られない。負荷の高い Pod では例・macro の差分で repo 全体の行の 2 本が未測になる(実測) |
| 表の行が読める・書いた検が在る | 既存の読み手(接頭辞の dir が木に在る・書いた検の file が在る)と既存の検 | `read_contract_table`・`test_every_package_has_a_contract_row_or_a_reasoned_exemption` | 入口・日次 | — |

<!-- ここから下は盲検の後に書いた。§0〜§4 は盲検の前の版(sha256 5c3a1778…・commit 06653af3)のまま。 -->

## 5. 盲検

起動の記録は `blind/blind-meta.md`。第 1 候補(codex の `gpt-6-astra`・low)は 401 で起きず(`blind/codex-attempt.txt`)、第 2 候補の
Opus 5.5(Agent tool の subagent・effort xhigh を要求・実の model と effort は観測していない)で A・B を別々の新しい文脈で同時に起こした。
返答は `blind/blind-a-return.md`・`blind/blind-b-return.md`(未加工)。

## 6. 再現と判定

### 6.1 盲検 A — 成立(予想の範囲の誤り・この変更の外の漏洩)

- 失敗例: 期待の出力に `…` を置いた例 ex10 を examples/ に足す(S1(a) の前提のまま)。
- 再現(`evidence/repro/a-new-example-with-any.txt`): 例は走り期待の出力に合う。選び(`select_targets`)は ex10 の `.hy` と `.txt` の
  どちらでも例の検を選び、table-guard は緑。ただし examples-check の `test_tables_name_examples_as_they_are` が「`…` を置いた例に
  `CASE_FAILURE_MUTANTS` の行が無い」で赤。行を 1 つ足すと緑(`evidence/repro/a-new-example-needs-a-mutant-row.txt`)。
- 判定: §3 S1(a) の「表・table-guard・入口は変えない」と I1 は成り立った。外れたのは予想の範囲「examples/ だけ」— examples-check の
  例ごとの表(`CASE_FAILURE_MUTANTS`)に行が要る。原因は §2 で examples-check の持つ知識を c9e1622b5(card ki-2d5b129f2ff2・載せ直しの
  親)より前の形で書き、その表を落とした事(予測の誤り)。行が要る事は examples-check の共同の不変条件で、検が名指して赤にする(黙らない)。
- A の指す漏洩(examples-check が例の source の文字列 — handler の式・mock の値 — を持ち、出力を変えない例の中の書き換えでも検を直す)は
  examples-check の設計(card ki-2d5b129f2ff2)の持ち分で、この依頼の変更(表と table-guard)の外。ここでは直さず、依頼元へ知らせる。
  §3 の主張は書き換えない。

### 6.2 盲検 B — 成立(境界の誤り)→ 直した

- 失敗例: `<-` の実行時の意味は doeff-hy の外の `doeff_core_effects/outcomes.py` が持つ(doeff-hy の `outcome_forms.hy` の頭の註
  「実行時の意味は doeff_core_effects.outcomes が持ち、ここは形だけを作る」・`macros.hy` の `_install-guard-globals` が defk の
  globals に置く)。B は outcomes.py に「coroutine を Await にする」判断を足し、ex05・ex08 が Await を書かない形にした後、outcomes.py だけを
  戻す変更を入口に通した。
- 再現(直す前の df9e62756):
  - 選び(`evidence/repro/b-selection-before-fix.txt`): outcomes.py と `doeff/do.py` は例の検を選ばない。macros.hy と outcome_forms.hy は選ぶ。
  - 機能の差分を当てた木で ex05・ex08 は緑(`evidence/repro/b-feature-applied-examples-green.txt`)。outcomes.py だけを戻すと
    `test_example_runs[ex05…]`・`[ex08…]` が赤(`evidence/repro/b-runtime-reverted-examples-red.txt`)。
  - B の入口の実測(`blind/b-artifacts/entry-probe.txt`): 変えた file は outcomes.py の 1 本で、例の検は選ばれず rc 0。
- 判定: §3 S2(a) の「macro の持ち主の source の変更は `packages/doeff-hy/src/doeff_hy/` の行が拾う」は、macro の意味の半分を持つ
  outcomes.py について成り立たなかった。隠す知識の漏洩ではなく、表が macro の持ち主を doeff-hy の dir だけと取り違えた(持ち主の境界の誤り)。
- 直し(commit c50541815): 表に file の接頭辞 `packages/doeff-core-effects/doeff_core_effects/outcomes.py` → 例の検 の行を足した
  (core-effects の他の file には払わせない)。table-guard に `EXAMPLE_MACRO_RUNTIME`(outcomes.py)を足し、macros.hy が実行時の module の名
  `"doeff_core_effects.outcomes"` を名指す事も確かめる(移した時に古い名のまま緑にしない)。
- 再検証:
  - 表を直す前に table-guard だけを広げると、outcomes.py を名指して赤(`evidence/checks/c-guard-runtime-half-red-before-row.txt`)。
  - 行を足した後: table-guard と入口の検 31 本が緑。選びは outcomes.py・macros.hy・ex05 で例の検を選び、doeff-core-effects の scheduler.py と
    doeff-hy の検(test_hy_stubs.py)だけの差分では選ばない(`evidence/checks/c-after-fix-guard-and-selection.txt`)。
  - B の手順を直した後の版で入口に通すと(基点 = B の機能を入れた状態・作業木 = outcomes.py だけを戻した物)、予算 300 秒で例の検が選ばれ
    ex05・ex08 の 2 本が赤・rc 1(`evidence/checks/c-entry-after-fix-runtime-revert-budget300.txt`)。既定の予算 60 秒では、load average 約 90 の
    下で前に並ぶ core-effects の組と repo 全体の行が予算を使い、例の検は「未測 0/32」と名指され rc 0
    (`evidence/checks/c-entry-after-fix-runtime-revert-default-budget.txt` — I3 の限界)。
- 直さなかった物: B の挙げた `doeff/do.py`(`@do`)と doeff-vm の `BindOpener` も `<-` の実行に関わるが、どれもすべての Program が使う
  汎用の実行時で、macro に固有の物ではない。例の検をこれらの契約の組にすると、核のどの変更にも例の検の時間を払わせる。これらの変更は
  それぞれの契約の組・逆依存と日次が測る(限界として §8 に記す)。

### 6.3 S1(b) の失敗ケース(盲検の外・設計者の確かめ)

例の dir を `examples/traverse` へ動かし表を直さない時(`evidence/checks/c-s1-moved-examples-red.txt`): table-guard の `EXAMPLES_DIR` も
直し忘れると「例が無い」で赤、`EXAMPLES_DIR` だけを直すと動かした 22 の path を名指して赤。§3 S1(b) の主張どおり。

## 7. 予測と実際

| シナリオ | 予測(§3) | 実際 | 差の原因 |
| --- | --- | --- | --- |
| S1(a) 例を足す | examples/ だけ | examples/ + 期待の出力に `…` を置く時は examples-check の `CASE_FAILURE_MUTANTS` に 1 行。表・table-guard・入口は変えない | §2 の examples-check の知識の書き落とし(c9e1622b5 の表)。共同の不変条件で、検が名指す |
| S1(b) 置き場を動かす | examples-check・table-guard・contract-table | 同じ(table-guard が 2 つの直し忘れを赤にした) | — |
| S2(a) macro の持ち主の source | macro-owner(doeff-hy の src)だけ・表は変えない | outcomes.py(doeff-core-effects)も macro の意味の半分。直す前は例の検を選ばなかった。直した後は outcomes.py の変更も表を変えずに拾う | 持ち主の境界の誤り → 表の行と table-guard で直した |
| S2(b) doeff-hy の検だけ | 例の検を選ばない | 選ばない(実測) | — |
| S3 負荷の高い機体 | 表と入口は変えない・収まらない検は未測 | 同じ。例・macro の差分では例の検は予算内で走った。outcomes.py の差分では load average 約 90 で例の検が未測になった | 入口の予算(既定 60 秒)と順(表の順)— 入口の持ち分 |

## 8. 実装の証拠・接続・限界

- 実装: `pyproject.toml` の `[[tool.doeff.contract-tests]]` の 3 か所(`packages/doeff-traverse/` の行に例の検・`packages/doeff-hy/src/doeff_hy/` の行・
  `packages/doeff-core-effects/doeff_core_effects/outcomes.py` の行)と、`tests/test_contract_table_follows_packages.py` の
  `test_changing_an_example_or_its_macros_selects_the_examples_check`(df9e62756 と c50541815)。
- 接続: 表は入口 `make test-changed`(`scripts/run_changed_tests.py`)が読む。table-guard は表の repo 全体の行でどの差分でも入口が走らせ、
  日次の root の段(`pytest tests`)でも走る。マージの列は検を走らせない(operator の裁定)— 入口を回すのは書き手。
- 限界:
  - I3: 予算 60 秒の内に収まらない時、例の検は未測と名指され rc 0。負荷の高い Pod では例・macro・outcomes.py の差分で、例の検かその後ろの検が
    未測になる(実測)。例の検を速くする仕事は依頼元の配り役が card にする(2026-10-12 06:24 JST の受け入れの文)。
  - `doeff/do.py`・doeff-vm の `BindOpener` など汎用の実行時の変更は、例の検を選ばない(§6.2)。
  - table-guard は macro-owner の src の中で macros.hy の 1 file と、実行時の半分の outcomes.py だけを確かめる。行の接頭辞を file 単位に狭める
    変更は気づかない。I2(doeff-hy の検だけでは選ばない)は検で固定していない(実測だけ)。
  - examples-check が例の source の文字列を持つ漏洩(§6.1)は、この変更の外として残す。
  - 型検査(pyright)と日次の全体検証は走らせていない(型は日次)。
