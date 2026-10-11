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
