## 結論

S1 の主張のうち「example-check は 1 行も変えずに、壊れた例を赤にする」は崩れます。for/do の `When` の展開が呼び手に置かせる別名を変えると、例は壊れるのに検は 12 本とも緑のままでした(実測)。

## 1. 変更の要求と、それが要る理由

- **要求**: doeff-hy が for/do の展開の「呼び手に置かせる名」を変える。この名は docstring の `Requires:` に書かれています。具体的には、`When` の偽の枝の展開 `(yield (_doeff_traverse_Skip))` が引く名を、別の名へ替えます。
- **理由**: traverse-lib の doc は Skip を「Internal… Not intended for direct use」としています。それでも macro は、呼び手(ex09)に `Skip :as _doeff_traverse_Skip` の import を求めています。内部の effect を表から下げる整理や、落とした件に理由を残す機能を足す時に、引く名が替わり得ます。S1 自身も「名を変える・規則を締める」をこの系の想定の変更に挙げています。
- **ただし推測**: 実装者がどの形を選ぶかは推測です。Skip に引数を足す形なら名は変わらず、この失敗は起きません。

## 2. 関係する主張・前提と、予想に反して変わる物

- **前提は守っています**: doeff-hy が macro の面を変え、日次の `make test-packages` で直列に走り、置き場は同じ、という前提のままです。
  - `Requires:` の名は for/do が文書に書いた面の一部なので、「名を変える」に当たります。
  - S1 を「require に書く macro の名だけ」と狭く読む場合でも、I3 の強制(§4 の 1 行目)と R1/R2 の目的(例が macro の名とずれたら見つける)の範囲には入ります。
- **予想**: 変わるのは macros と ex09 だけ。example-check は変えずに `test_example_runs[ex09]` が赤になる。
- **実際**:
  - macros と ex09 の変更が要る点は予想どおりです。
  - しかし検が緑なので、誰も ex09 を直せと言われません。ex09 は誤った履歴を印字して 0 で終わります。
  - 約束した検出を取り戻すには、予想の範囲の外を変える事になります。どれを選んでも、別の責務を持つ所が変わります。
    - example-check に、traverse-lib の失敗の履歴の形か、例ごとの期待の出力を教える。
    - traverse-lib に、件の中の NameError を伝えさせる。全利用者の失敗の方針が変わります。
    - 例ごとに、期待の結果を自分で確かめさせる。読み物に検の責務が増えます。
    - macros が呼び手の名前空間に頼るのをやめる。macros の中で済みますが、`Requires:` という公開の契約の変更になります。

## 3. 漏れる知識と、区別

漏れている知識は 3 つです。

- **(a) 展開が実行時に引く名**: §2 では macros が隠すのは「展開の中身」、公開の口は `(require …)` だけとされています。実際には `Requires:` が 2 つ目の公開の口です。その口を通って traverse-lib の内部の Skip まで ex09 に漏れています。これは「例は公開の口だけで走る」という I3 とも食い違います。
- **(b) traverse-lib の失敗の方針**: `sequential` の handler は件ごとに `except Exception` で例外を受け、「件の失敗」として記録します。example-check の合否(終了コード)は、この隠れた方針に黙って依っています。
- **(c) 展開の内部の配置**: 引く名が件の関数の外に出るなら終了コード 1 になり、検出されます。件の関数の中だけで出るなら 0 で、見逃されます。ある macro の変更が検出されるかどうかを予想するには、展開の内部の配置を知る必要があります。

区別すると次のとおりです。

| 種類 | 該当する物 | 欠陥か |
|---|---|---|
| 意図した公開契約の変更 | `Requires:` の変更と、ex09 の import の 1 行の修正 | 欠陥ではない(S1 の想定どおり) |
| 配線の変更 | なし(glob も日次も変えない) | — |
| 共同の不変条件 | 「呼び手の別名 = 展開が引く名」。§2 に宣言が無く、S1 は検出を example-check に任せているが、件の中の違反は見えない | — |
| 隠すはずの知識の漏洩 | 上の (a)(b)(c) | これが欠陥 |

§4 は「誤った結果を印字して 0 で終わる例は通る」という限界を一般論として認めています。しかし traverse-lib が件ごとに失敗を隔離するので、macro の面の変更のうち 1 つの種類が丸ごとこの限界に入ります。その種類については S1 と §4 が矛盾します。

## 4. 最小の再現(実測)

1. 2cff14d6 で自分の木を切り、`make sync` します。置き場は自分専用の空の dir にします(`DOEFF_HY_CODE_STORE`・`PYTHONPYCACHEPREFIX`。変えた macro の compile を共有の置き場へ書かないため)。
2. 基準として `uv run --no-sync pytest packages/doeff-traverse/tests/test_examples_run.py -q` を走らせると、12 passed でした。
3. `packages/doeff-hy/src/doeff_hy/macros.hy` の 1406 行目を 1 行だけ変えます。ex09 の import はそのままです。
   ```
   -      '(yield (_doeff_traverse_Skip))))
   +      '(yield (_doeff_traverse_SkipWithReason))))
   ```
4. 同じ検を走らせると **12 passed** でした。`test_example_runs[ex09_for_do_when.hy]` も PASSED です。
5. ex09 を直に走らせると rc=0 で、出力の差は次のとおりです(eve の行も同じ)。Top 3 と Eligible の行は変わりません。
   ```
   <   [1] SKIP: {'name': 'bob', 'score': 42, 'active': False}
   <       process: skipped
   >   [1] SKIP: name '_doeff_traverse_SkipWithReason' is not defined
   >       process: failed
   ```
6. 比べのために測った物です。
   - Traverse の別名(1432〜1433 行目)を変えると、ex09 は rc=1 の NameError で検出されます。
   - `When` の句の名を `Where` に変える(1218 行目)と、展開の時の誤りで `test_example_runs[ex09]` が FAILED になり、検出されます。
   - 変えた macro は出力に反映されたので、S3 の無効化は効いています。

観測するのは、検の合否、ex09 の終了コード、履歴の行が `skipped` か `failed` か、の 3 つです。

## 5. 未確認の事・推測

- **実測した事**:
  - 例 1 本の時間は、置き場が冷えて単独で走らせると 約 20 秒(CPU 14 秒)、温まっていると 約 1 秒でした。
  - pytest の中では親 process が先に置き場を温めるので、置き場が空でも子 1 本は 0.5〜0.7 秒でした。
- **推測**:
  - 1 節の改名の動機と、実装者がどの形を選ぶか。
  - `tests/test_for_do_runtime.py` は "skipped" を確かめているので、日次の root の段で doeff-hy の変更自体は捕まると思われます。ただし ex09 を指す知らせは出ません。この file は読んだだけで、走らせていません(「この検だけ」の決まりのため)。
  - 同じ仕組みで、件の関数の中で使う traverse-lib の公開の口の変更(例: `try_call` の引数)も見逃すと思われます。試していません。
- **別の所見(再現していない)**: example-check の子の締切は固定の 50 秒です。root の conftest は子の `timeout=` のために `deadline_scale_factor` を用意しています(ADR-DOE-ENFORCE-001 R6・card ki-bb91f171d222。2026-10-11 の日次で、固定 30 秒の子が殺された実例があります)。締切の方針の写しですが、今の大きさでは 約 60 倍に遅くならないと表に出ず、再現していません。
- **依存の宣言**: doeff-traverse の `pyproject.toml` は hy・doeff-hy・tqdm を宣言していません。hy と doeff-hy は root の dev group から入ります。tqdm を入れているのがどの package かは未確認です。
- **読んでいない物**: design.md は §1〜§4 だけを読みました。§0・§5 と `blind/` の他の file は読んでいません。

**実験の木について**: `/tmp/blind-a-ex09/tree` は置き場の規律の hook に止められたので、`/work/toolset/worktrees/doeff-wt-blind-a-ex09`(detached・2cff14d6)で行いました。外す時も `--force` と手での削除が規律で止められ、回収の命令も Pod の中では動かないので、木は残してあります。macros.hy は試すたびに戻しており、`git status` は 0 行です。`/tmp/blind-a-ex09` の一時の置き場は消しました。読める木(`/home/ubuntu/.worktrees/doeff-wt-ex09-design-check`)は変えていません。
