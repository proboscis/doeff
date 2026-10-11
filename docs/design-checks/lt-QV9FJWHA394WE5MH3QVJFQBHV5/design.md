# 設計検証 lt-QV9FJWHA394WE5MH3QVJFQBHV5 — doeff-traverse の例を走らせる検

## 0. 完了の範囲

**実装完了まで**(設計だけではない)。実装は doeff の main に入った 2cff14d6a353e31b3e894c36fde37d698fb8e344(L2108・2026-10-12 05:01 JST)。
この報告は、実装の後に作った設計の記録である。依頼が class=investigate なのに、実装を別の依頼に分けず同じ会話でマージまで進めた
(依頼文の「直し」の節に、直し方が書いてあった)。順が逆なので、§3 の主張は盲検の前に固定した版(この file を最初に push した commit)を正とし、
盲検の後で書き換えない。

## 1. 受入の条件(依頼 lt-QV9FJWHA394WE5MH3QVJFQBHV5 の本文から)

- R1: `packages/doeff-traverse/examples/ex09_for_do_when.hy` の require を、今の macro の名に合わせる(main で `(require … fold)` の所で落ちていた)。
- R2: 例が走る事を確かめる検を置く。例を全部走らせる検がもし在れば、その検に入れる(在らなかった)。

固定した事実(2cff14d6 の親 6ee49cc2 で実測):

- `fold` macro は fa37eaaf1(2026-04-07「Remove fold macro」)で消えた。同じ commit から `defk` の `:pre`/`:post` が必須で、handler の形の引数 `[effect k]` を defk で書く事も断る。
- examples/ の 12 本のうち 11 本が落ちていた。fold で落ちたのは ex04_traverse_macro・ex07・ex08・ex09 の 4 本、defk の契約が無くて落ちたのは ex01・ex02・ex03・ex04_debug2・ex04_simple・ex05・ex06 の 7 本。走ったのは ex04_debug の 1 本だけ。
- 例を走らせる検は、どの母集団にも無かった。

## 2. 責務(module)

| id | 何か | 持つ知識・判断 | 隠す物 | 公開の口 | 作用 | 寿命 |
| --- | --- | --- | --- | --- | --- | --- |
| macros | `packages/doeff-hy/src/doeff_hy/macros.hy` | 在る macro の名と展開の規則(defk の契約の必須・handler の形の拒否・for/do・traverse) | 展開の中身 | `(require doeff-hy.macros [名 …])` | 展開の時だけ(compile 時) | compile 1 回 |
| traverse-lib | `packages/doeff-traverse/doeff_traverse/` | effect の型(Traverse・Reduce・Skip …)と handler(sequential・fail-handler …) | Collection の中身 | effect の class・handler の関数 | なし(effect は handler が解く) | Program 1 回 |
| examples | `packages/doeff-traverse/examples/*.hy` | 例ごとの data・handler の組・印字 | なし(読み物) | `python -m hy <file>` が 0 で終わる | stdout・stderr への印字。ex05・ex08 は実時間の sleep、ex08 は tqdm | process 1 つ |
| example-check | `packages/doeff-traverse/tests/test_examples_run.py`(今回 新規) | 例の母集団(examples/*.hy の glob)・BROKEN の表(file の名 → 理由)・走らせ方(subprocess・`sys.executable -m hy`・timeout 50 秒)・合否(走る例は 0、表の例は 0 以外) | 下請けの関数(`_examples`・`_run_example`) | pytest の id `test_example_runs[<file>]`・`test_broken_example_is_still_broken[<file>]` | 子 process を起こす(env を受け継ぐ)・例の file を読む | 検 1 つ。子は終わるか timeout で殺される |
| daily-gate | `Makefile` の `test-packages`・`.agents/land-queue.toml` の gate.full | いつ・どの母集団を走らせるか | — | 日次の全体検証の段 packages | — | 日次 1 回 |
| bytecode-store | `doeff_hy_bytecode_guard`・`DOEFF_HY_CODE_STORE` | compile 済みの Hy の置き場と、macro が変わった時の無効化 | 置き場の鍵 | loader の hook | 置き場の読み書き | 置き場は走行をまたいで残る |

不変の条件:

- I1(example-check): examples/ の `.hy` はどれも、`test_example_runs` か `test_broken_example_is_still_broken` のちょうど片方に入る。
- I2(example-check): BROKEN の行は、在る file を名指し、その例は今も 0 以外で終わる。
- I3(examples): 例は、今の macro と traverse-lib の公開の口だけで最後まで走り、0 で終わる。

## 3. 事前の主張(盲検の前に固定)

### S1(軸 effects・適用): doeff-hy が macro の面を変える

- 変更: doeff-hy が macro を消す・名を変える・規則を締める(fa37eaaf1 の fold の削除と defk の契約の必須化と同じ形)。
- 主張: 変更は macros と、それを使う例の file に収まる。example-check は 1 行も変えずに、壊れた例ごとに `test_example_runs[<file>]` を赤にし、compile の誤りを出力に出す。直すのはその例の file だけ。後回しにするなら BROKEN に 1 行足す。traverse-lib・daily-gate は変えない。
- 前提: 検は日次の `make test-packages` で走る。マージの列は検を走らせない(operator の裁定 land-gate-runs-no-tests)ので、見つかるのはマージの後の次の日次。
- 予想の範囲: macros + 影響を受ける例。example-check は変えない。

### S2(軸 effects・適用): 例を足す・直す・消す

- 変更: (a) 新しい例 ex10 を足す。(b) BROKEN の例を直す(例 ex04_traverse_macro を Reduce で書き直す)。(c) 例を消す・名を変える。
- 主張: (a) example-check は変えない(glob が拾う)。(b) 直した例は `test_broken_example_is_still_broken[<file>]` で赤になり、BROKEN の 1 行を外すまで残る。検の変更はその 1 行だけ。(c) 表に残った行は `is_file` の確かめで赤になる。
- 予想の範囲: examples の 1 file(+ BROKEN の 1 行)。

### S3(軸 storage・適用): compile 済みの Hy が置き場に残る

- 変更: 例を一度走らせて、compile 済みの物が `DOEFF_HY_CODE_STORE` に残った後に、例が require する macro を消す・変える。
- 主張: example-check は置き場の無効化を持たない。子 process は env を受け継いで同じ置き場を使い、bytecode-store が macro の変更を見て compile し直すので、古い緑にならない(検は赤になる)。example-check も examples も変えない。
- 前提: bytecode-store の無効化は、require する macro を定義した file の変更を見る。

### 軸ごとの不適用

- hardware: 例は CPU だけを使う script で、機体に依る code が無い。1 本 約 1 秒で、時間の上限は子の timeout 50 秒と pytest の検ごとの上限(root の ini の 60 秒 × 負荷の倍率)が持つ。
- distribution: 検は同じ機体の上で子 process を起こすだけで、例どうしも機体をまたいで話さない。日次は機体 1 つの上で走る。
- concurrency: 日次の `make test-packages` は直列で走る(`-n` を付けない)。例は互いに独立した process で、共に触る物は compile の置き場だけ。置き場への同時の書き込みは bytecode-store の持ち分で、この変更の持ち分ではない。
- simulation: 検は終了コードだけを見て、印字の中身を比べない。時間・乱数の決定性は主張しない(実時間の sleep を使う ex05・ex08 は BROKEN に在る)。

## 4. 強制方法

| 守る責務 | 強制方法と選んだ理由 | 実装の在り処 | 実行の経路 | 限界 |
| --- | --- | --- | --- | --- |
| I3: 例は今の macro と lib で走る | 例を実際に走らせる。Hy の例は pyright の対象外で、macro の名は展開の時にしか解けないので、静的な検査では表せない | `test_example_runs`(`packages/doeff-traverse/tests/test_examples_run.py`) | 日次の全体検証の段 packages → `make test-packages` → `pytest packages/doeff-traverse/tests` | マージの前には走らない(マージの後の日次で見つかる)。終了コードだけを見るので、誤った結果を印字して 0 で終わる例は通る |
| I1: どの例も検に入る | 母集団を glob で取り、手で写した一覧を持たない | `_examples()` | 同上 | `.hy` の外(`.py` の例)は入らない(今の examples/ に `.py` は無い) |
| I2: BROKEN の表が今の実際と合う | 表の例を走らせ、今も落ちる事を確かめる(直した例が表に残ると赤) | `test_broken_example_is_still_broken` | 同上 | 理由の文は照らさない — 表の理由と違う原因で落ちていても赤にならない |
| examples は traverse-lib の公開の口だけを使う | 強制しない(例は読み物で、内部へ触っても走れば通る) | — | — | 静的に表していない |

## 5. 検証の計画

- S1 正: 直した ex09 で `test_example_runs[ex09_for_do_when.hy]` が緑。負: 直す前の ex09(6ee49cc2 の版)で同じ検が `HyRequireError: Could not require name fold` で赤。
- S2 正: 新しい例を足すと、検を変えずに `test_example_runs[<新しい例>]` が集まり緑。負: BROKEN の例を走るようにすると `test_broken_example_is_still_broken` が赤。表に在って file が無い行も赤。
- S3 正: 置き場が温まった状態で、変えていない例は緑のまま。負: 置き場が温まった後に require する macro を消すと、同じ例が赤。
- 盲検 A・B の失敗例を、同じ検で再現する。

## 6. 盲検の結果・再現・直し(盲検の後に足した節 — §1〜§5 は 8f4e8b69 の版のまま)

起動の記録は [blind/blind-meta.md](blind/blind-meta.md)。

### 6.1 盲検 A — 件の中で壊れる macro の変更を、終了コードだけの検は見逃す

- 返答: [blind/blind-a-return.md](blind/blind-a-return.md)
- 再現(2cff14d6・[evidence/repro/a-mutated-check.txt](evidence/repro/a-mutated-check.txt)・`a-ex09-before.txt`・`a-ex09-mutated.txt`): macros.hy の 1406 行目(When の展開が引く名)を変えた木で、検は 12 passed。ex09 は終了コード 0 のまま、bob・eve の行を `skipped` ではなく `failed` と印字した。**成立。**
- 原因: traverse-lib の `sequential` は、件の中の例外を件の失敗として記録して先へ進む。§3 の S1 の主張は「例が壊れれば終了コードが 0 以外になる」を暗に前提にしていたが、件の中の壊れは終了コードに出ない。example-check の合否が traverse-lib の失敗の方針に黙って依っていた(隠すはずの知識の漏れ)。
- 直し(d524b2fa): 走る例の標準出力を `examples/expected/<stem>.txt` と比べる。
  - 期待の出力は例の持ち分(例と同じ dir に置き、例を変えたら一緒に書き直す)。example-check が持つのは「標準出力が一致するか」だけで、traverse-lib の失敗の履歴の形は知らない。
  - ex04_debug は macro の展開(gensym の名を含む)を印字する debug 用なので、`OUTPUT_NOT_COMPARED` に理由つきで置いた。比べると macros の隠す中身が期待の出力へ漏れる。
  - 期待の出力が無い走る例は赤。表と期待の出力の file が今の例と合う事も確かめる(`test_tables_name_examples_as_they_are`)。
- 再検証: c3(置き場が温まった後に A の変更を当てると、ex09 が出力の違いで赤)。
- A の別の所見(締切): 子の締切の固定 50 秒は、root の conftest の `deadline_scale_factor`(ADR-DOE-ENFORCE-001 R6)が持つ締切の方針の写しだった。d524b2fa で fixture を使い、`CHILD_DEADLINE_SECONDS × 係数` にした。遅い機体での赤は再現していない(今は 1 本 約 1 秒で、約 60 倍遅くならないと出ない)。
- A の所見のうち、この依頼の範囲外として直さない物:
  - for/do の展開が呼び手の名前空間の別名に頼る事(docstring の `Requires:` の import。traverse-lib の内部の Skip を含む)。これは doeff-hy の公開の契約の形。直した検は、その壊れを例の出力の違いとして捕まえる。
  - doeff-traverse の `pyproject.toml` が hy・doeff-hy・tqdm を宣言しない事(例は workspace の dev group の環境で走る)。

### 6.2 盲検 B — 消えた macro を例の中で定義し直すと検を通る

- 返答: [blind/blind-b-return.md](blind/blind-b-return.md)
- 再現(2cff14d6・[evidence/repro/b-shim-check.txt](evidence/repro/b-shim-check.txt)): ex04 に旧い fold の defmacro を写し、BROKEN の行を消した木で 12 passed。持ち主の `(require doeff-hy.macros [fold])` は HyRequireError。**成立。**
- 原因: I3 の強制は「例を走らせる」だけで、macro の出どころを見分けない。macro の持ち主(macros)の知識が、例の中に 2 つ目の定義元として移る。
- 直し(d524b2fa): 例を Hy の reader で読み、macro を定義する form(`defmacro`・`defmacro!`・`defreader`)と、`doeff_hy` の外の package からの require を断る(`test_example_uses_only_doeff_hy_macros`)。
- 直しの直し(2 回):
  - d524b2fa の確かめは、`read_many` の結果を Python の list のまま歩き、form の中へ降りていなかった。B の差分を見逃して緑だった([evidence/checks/c6a-b-shim-missed-by-d524b2fa.txt](evidence/checks/c6a-b-shim-missed-by-d524b2fa.txt))。1a3d45f4 で form を 1 つずつ歩くように直し、違反を含む inline の source を拾う事の検(`test_macro_check_sees_nested_forms`)を足した。今の例には違反が無いので、本物の例だけでは中身の無い緑と区別できないため。
  - 1a3d45f4 の require の確かめは、素の名(Symbol)だけを module と見ていた。Hy 1.3.1 の reader は `a.b` を `(. a b)` と読むので、shim を `examples/support/` へ移した変形(`require support.fold_shim`)を見逃した([evidence/checks/c6b0-b-shim-module-missed-by-1a3d45f4.txt](evidence/checks/c6b0-b-shim-module-missed-by-1a3d45f4.txt))。16b8936e で点の form の頭を package と読むようにした。
- 再検証: c6(例の中の defmacro → 赤)・c6b(support の module の require → 赤)・c6c(歩き方を d524b2fa の形へ戻すと inline の検が赤)。
- B の所見のうち範囲外: defk の中に `(_doeff_traverse_Reduce (fnk …) …)` を書くと UnboundLocalError になる(fnk が入れる import と traverse の展開の `_doeff_do` がぶつかる)。doeff-hy の不具合の候補で、私は再現していない。

### 6.3 予測と実際の波及

| シナリオ | 予測(§3) | 実際 |
| --- | --- | --- |
| S1 | macros + 影響を受ける例。example-check は変えない | 直した後(16b8936e)は予測どおり(c2・c3)。直す前(2cff14d6)は件の中で壊れる変更を見逃した(A)。直しで example-check に「標準出力を比べる」を足し、examples に例ごとの期待の出力を足した。example-check は traverse-lib の失敗の形を知らないまま |
| S2 | 例 1 file(+ BROKEN の 1 行) | 例 1 file + その期待の出力 1 file(+ BROKEN の 1 行)。期待の出力は直しで増えた例の持ち分(c4・c5)。例の中で macro を定義する直し方は断る(c6・c6b) |
| S3 | 何も変えない | 予測どおり(c1 の 2 回目・c3) |

### 6.4 残る限界(強制していない事)

- マージの前には走らない(日次の段 packages だけ)。
- 期待の出力を比べない例(ex04_debug)は、終了コードだけを見る。
- 標準出力に出ない壊れ(印字しない値の誤り)は見えない。
- 例が traverse-lib の公開の口だけを使う事(内部の module を import しない事)は強制しない。for/do の `Requires:` 自体が内部の Skip の import を求めるため。
- 実行時に呼ぶ require(`hy.macros.require` など)は確かめない。
- BROKEN の理由の文は照らさない(表の理由と違う原因で落ちていても赤にならない)。

### 6.5 実装と検査の経路

| 何 | 在り処 | 版 |
| --- | --- | --- |
| 例の検(4 種 + inline の検) | `packages/doeff-traverse/tests/test_examples_run.py` | 16b8936e |
| ex09 の期待の出力 | `packages/doeff-traverse/examples/expected/ex09_for_do_when.txt` | d524b2fa |
| 走る所 | 日次の全体検証の段 packages(`make test-packages`)。マージの列は検を走らせない | — |

## 7. main の進みへの載せ直しと、最後の版 37ff3b9a

- §6 の直し(d524b2fa・1a3d45f4・16b8936e)は branch `wt/ex09-design-check` の上で行った。その間に main では、別の会話が ex01〜ex08 を今の macro へ書き直し(4527ff917・06859fa8f — card ki-043ea763c926・ki-3a05af12bdeb)、BROKEN の表を空にした。
- 72ce60d2: 06859fa8 の上に 16b8936e の検を載せ、BROKEN を空にした。走る例を 2 回ずつ走らせ、標準出力が同じだった 9 本に期待の出力を置いた。毎回変わる ex04_debug2(関数の番地)・ex05(実時間)と、展開を印字する ex04_debug は `OUTPUT_NOT_COMPARED` に置いた(ex04_debug の gensym の番号は、2cff14d6 の時の `_6` が 06859fa8 では `_2` になっていた)。
- 72ce60d2 の c1 は、作った木と別の path の木(再現用の木)で ex07 が赤だった([evidence/at-72ce60d2/c1-all-green.txt](evidence/at-72ce60d2/c1-all-green.txt))。ex07 は件の失敗の履歴に traceback を印字し、そこに checkout の絶対 path と doeff-core-effects の行番号が入る。path を置き換えても lib の行番号(lib の隠す中身)が期待の出力に残るので、比べない側へ移した(37ff3b9a)。同じ形を置いた時点で捕まえるため、期待の出力に checkout の path が在れば赤にする確かめを足した(c7)。
- 最後の版 37ff3b9a で c1〜c7 を測り直した(手順 = [evidence/measure.sh.txt](evidence/measure.sh.txt))。16b8936e と 72ce60d2 で測った記録は [evidence/at-16b8936e/](evidence/at-16b8936e/)・[evidence/at-72ce60d2/](evidence/at-72ce60d2/) に残す。

### 7.1 最後の版の表

| 表 | 行 |
| --- | --- |
| BROKEN | なし |
| OUTPUT_NOT_COMPARED | ex04_debug(展開)・ex04_debug2(番地)・ex05(実時間)・ex07(traceback の path と lib の行番号) |
| 期待の出力 | ex01・ex02・ex03・ex04_simple・ex04_traverse_macro・ex06・ex08・ex09 の 8 本 |

### 7.2 残る限界(§6.4 に足す)

- 12 本のうち 4 本は標準出力を比べず、終了コードと macro の出どころだけを見る。この 4 本では、件の中の壊れ(盲検 A の形)は見えない。
- 期待の出力の path の確かめは、置いた木の path だけを探す。別の木の path を含む出力は、日次の木で外れて気づく。

### 7.3 実装と検査の経路(最後の版)

| 何 | 在り処 | 版 |
| --- | --- | --- |
| 例の検(例が走り標準出力が合う・表の例は今も落ちる・macro の出どころ・inline の確かめ・表と期待の出力の整合と path) | `packages/doeff-traverse/tests/test_examples_run.py` | 37ff3b9a |
| 期待の出力 8 本 | `packages/doeff-traverse/examples/expected/*.txt` | 72ce60d2 |
| 走る所 | 日次の全体検証の段 packages(`make test-packages`)。マージの列は検を走らせない | — |

### 7.4 マージの前の載せ直し

マージの列へ載せる前に、main が 7613a2147(doeff-hy の fnk・do! の展開の変更 — 盲検 B が範囲外の所見に挙げた UnboundLocalError の直しに当たる)へ進んだので、その上へ載せ直した(code の commit は fd88198e・218272f9)。
`packages/doeff-traverse` の木は 37ff3b9a と同じ(`git diff 37ff3b9a 218272f9 -- packages/doeff-traverse` が 0 行)。載せ直した版でも例の検は全部緑だった([evidence/checks/c1r-rebased-green.txt](evidence/checks/c1r-rebased-green.txt))。c2〜c7 は 37ff3b9a で測った物で、載せ直した版では測り直していない。
