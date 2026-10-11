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
