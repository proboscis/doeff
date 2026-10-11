## 結論

責務分離の失敗例が 1 つ見つかり、写しの木で再現しました。例のファイル(examples)だけを直し、出力は変えていない変更なのに、example-check の判定のコードを変えないと緑に戻りません。

---

## 1. 変更の要求と、要る理由

**要求:** 例の間で重複している `with-stack` を、共有の helper `examples/common/stack.hy` に寄せる。各例では `(import common.stack [with-stack])` の 1 行で使う。

**理由:** 同じ 5 行の `(defn with-stack …)` が 7 本の例に写されています(ex02・ex04_traverse_macro・ex05・ex06・ex07・ex08・ex09。grep で確認)。例は handler の組み方を教える読み物です。`scheduled` での包み方を変えるたびに 7 か所を直すのは、普通の整理の動機になります。規模は、新しいファイル 1 つ(10 行)と、各例の 5 行を 1 行に置き換えるだけです。

## 2. 関わる主張・前提と、予想に反して変わる物

**守った前提(実測):**
- §2 の examples の口「`python -m hy <file>` が 0 で終わり、標準出力が期待の出力に合う」は守れています。
  - ex07 の標準出力は直す前と 1 バイトも違いません。ex05 は秒の値だけが違います。
  - cwd を `/tmp` にして走らせても動きます。Hy 1.3.1 は script の dir を `sys.path[0]` に置くためです(probe で実測)。
- §1 の前提(handler は件の中の例外を件の失敗として記録する)には触れていません。
- §3 の前提(件の結果の行は `…` で伏せない)も守っています。期待の出力・`…` の位置・表 BROKEN / OUTPUT_NOT_COMPARED / CASE_FAILURE_MUTANTS は、どれも変えていません。
- helper は `examples/*.hy` の外(下の dir)に置いたので、例の母集団には入りません。`test_tables_name_examples_as_they_are` は緑のままです。

**予想に反して変わる物:**
- 赤になったのは、`test_case_failure_shows_in_compared_output[ex05_parallel_timing.hy]` と `[ex07_proboscis_ema_rewrite.hy]` です。
- 直すには、example-check の判定のコード(変異の写しを走らせる場の作り方)を変える必要があります。
- これは次の主張と食い違います。
  - §2「example-check は `…` の意味だけを知る」
  - §2 の隠す知識「下請けの関数」
  - S3「example-check の判じの code は変えない」
- 変わる責務は examples(意図した変更)と example-check(予想外の変更)の 2 つです。

**契約の適用範囲について:**
- 「1 つの例は、自分だけで完結する 1 ファイル」という前提は、§1〜§4 のどこにも契約として書かれていません。近いのは S3 の予想の波及「例 1 file + 期待の出力 1 file」(新しい例を足す場合の記述)だけです。
- 設計者がこれを前提のつもりでいたなら、この例は前提を変えるもので、契約の範囲外になります。
- ただしその場合も、前提は宣言されておらず、静的な検でも強制されていません。気づける経路は、`…` を持つ例でだけ、誤った案内の失敗が出ることだけです。

## 3. どの知識がどこへ漏れて波及するか

**漏れる物:**
- example-check の内部の判断「変異の写しを `tmp_path/<名>` に 1 ファイルだけ書いて走らせる」(test_examples_run.py 元の L207 `mutant = tmp_path / name`)が、examples への暗黙の規則として漏れています。
- その規則は「例は単独で別の dir へ動かしても走る(兄弟のファイルを import しない・読まない)」で、examples の口には書かれていません。

**走らせ方が 2 通りある:**
- `test_example_runs` は元の場所で走らせます(`_run_hy(EXAMPLES / name)`)。
- 変異の検は別の dir で走らせます。
- example-check が持つ知識「走らせ方」が 1 つに決まっていないことが、波及の原因です。

**関係のない 2 つの知識が結びつく(非対称):**
- この規則が効くのは、期待の出力に `…` を置いた例(CASE_FAILURE_MUTANTS の鍵)だけです。
- 同じ変更を `…` の無い ex06 に入れても緑のままでした(実測)。
- つまり examples 自身の判断「この印字は走るたびに変わる(`…` を置く)」が、無関係な書き方の制約「兄弟を import してよいか」を黙って切り替えています。

**区別:**

| 種類 | 当てはまるか | 理由 |
| --- | --- | --- |
| 公開契約の拡張 | いいえ | 出力・終了コード・印・表の行は変えていない |
| 共同の不変条件 | いいえ | CASE_FAILURE_MUTANTS と `…` の一致も、変異の元の文がちょうど 1 つ在ることも、緑のまま |
| 配線の変更 | 直す側の形はこれ | 写しの場に examples の dir ごと複製する。ただし引き金が examples の内部の変更で、変わるのが S3 が「変えない」とした判定のコードなので、隠すはずの知識の漏洩に当たる |

- もう 1 つの直し方は、examples の口に「1 ファイルで完結する」を足し、兄弟の import を静的に断る検を足すことです。これは公開契約の拡張になります。どちらにするかは、「例を走らせる環境」を誰が持つかを設計で決める必要があります。

**案内の誤り:**
- 失敗の文言は「変異が件の外で例を落とした — 件の中だけで失敗する変異を選ぶ」で、表を直すよう案内します。
- 実際の原因は import の行での `ModuleNotFoundError: No module named 'common'` で、変異を入れた文より前に落ちています。変異をどう選び直しても直りません。
- 誤った直し方(表を書き換える、または例を元の重複に戻す)へ誘導されやすくなっています。

## 4. 最小の変更例と再現の手順、見るべき結果

```sh
mkdir -p /tmp/lt4z5w-blind-a/tree
git -C /home/ubuntu/.worktrees/doeff-wt-dc-4z5w archive c9e1622b5 | tar -x -C /tmp/lt4z5w-blind-a/tree
cd /tmp/lt4z5w-blind-a/tree && make sync
uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q   # 実測: 31 passed, 1 skipped
```

**変更:**
1. `packages/doeff-traverse/examples/common/stack.hy` を新しく作る。

```hy
(import doeff_core_effects.scheduler [scheduled])
(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (h body)))
  (scheduled body))
```

2. ex05 の L97–101 と ex07 の L191–195 にある `(defn with-stack …)` を、`(import common.stack [with-stack])` に置き換える。

**見る結果(どれも実測):**
- 2 本の例は終了コード 0 で走ります。ex07 の出力の差は空です。
- 検の結果は `2 failed, 29 passed, 1 skipped` です。
  - 赤: `test_case_failure_shows_in_compared_output[ex05…]` と `[ex07…]`。
  - 理由: tmp_path の写しの import の行で `ModuleNotFoundError`。
  - 緑: `test_example_runs` の全部、`test_tables_name_examples_as_they_are`、`test_example_uses_only_doeff_hy_macros`。
- 同じ変更を ex06(`…` 無し)に入れても緑です。
- example-check を次のように直すと `31 passed` に戻ります。

```python
shutil.copytree(EXAMPLES, tmp_path / "examples")
mutant = tmp_path / "examples" / name
```

- この直しで検の意味が弱まっていないことも確かめました。ex05 の `…` を合計まで広げると、変異の検は「… が件の結果まで伏せている」で赤になります。

**記録(どれも `/tmp/lt4z5w-blind-a/` の下):**
- `refactor.diff`:例の変更(ex05・ex06・ex07 と helper)
- `fix-copytree.diff`:example-check を直した差分
- `after-refactor.log`:赤になった時のログ
- `baseline/`:直す前の例・出力・元の test ファイル
- 写しの木は今、例の変更を入れて test ファイルを元に戻した状態で、2 failed のままです。読める木は変えていません(`git status` は空)。

## 5. 確かめていない前提と、足りない情報

**実測した物:** 上の 4 に書いた結果すべて、Hy 1.3.1 の import の振る舞い、`with-stack` の重複 7 か所。写しの `.venv` の環境は Python 3.14t(free-threaded)と Hy 1.3.1 です。

**推測(走らせていない):**
- S3 の形の新しい例(`…` を持ち、`common.stack` を import する)を足しても、同じ経路で赤になり、判定のコードの変更が要るはずです。コードの経路が同じなので推測しましたが、走らせていません。
- 兄弟のデータファイル(`__file__` から引く `examples/data/*.csv` など)を読む例も、同じように落ちるはずです。
- 日次の段 `pytest packages/doeff-traverse/tests` でも同じ結果になるはずです。走らせたのは 1 ファイルだけで、同じ test ファイルなので同じと見ています。

**足りない情報:**
- 「1 例 = 自分で完結する 1 ファイル」が設計の意図した前提かどうか。盲検の後の design.md は、指示に従い読んでいません。
- 例の持ち主が、共有の helper より「例ごとにコピーして使える独立した読み物」を好むかどうか。重複を寄せる動機は私の判断で、実測した事実ではありません。

**調べたが主な例にしなかった物(どれも走らせていない):**
- CASE_FAILURE_MUTANTS は例のソースの文面を持っています。そのため、出力を変えずに変異の文を書き換えるだけで、表の書き直しが要ります。ただし assert の文言がこれを予期しているので、宣言済みの共同の不変条件と見て、失敗例には数えませんでした。
- `_PROGRAM_RUNNERS = {run, async_run}` は、doeff と doeff-hy の「件を走らせる口」の名前を写しています。走らせる関数や、走らせる形に展開される macro が増えると、example-check の変更が要ります。§4 の限界の欄で認められている事柄なので、主な例にはしませんでした。

失敗例は 1 つ見つかりましたが、探した範囲は上に書いたものだけです。他の変更について合格・安全だとは結論しません。

WAIT: work — 呼び出し元の次の指示
