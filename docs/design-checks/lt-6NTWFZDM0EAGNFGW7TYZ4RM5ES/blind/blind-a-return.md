S1(a)「新しい例 ex10 と期待の出力を足す」の前提を守ったままで、予想の範囲「examples/ だけ」が外れる失敗例が見つかりました。「表・table-guard・入口は変えない」という主張そのものは実測でも成り立ちます。外れるのは examples-check です。この file が例の source の一節を持っているためです。

## 1. 変更の要求とその理由
要求は「所要時間を印字する例 ex10 を足す」です。並列の速さや失敗の traceback を見せる例は、読み物として自然に増えます。検の docstring の決まりで、実時間・traceback・path のように走るたびに変わる所は期待の出力に `…` を置きます。比べる例 10 本のうち、ex05 と ex07 の 2 本はすでに `…` を使っています(実測)。

あわせて、既存の例の中だけの変更も扱います(ex07 の mock の値の更新、ex05 の式の書き換え)。

## 2. 関係する主張・前提と、予想に反して変わる物
- **成り立つ物:** S1(a) の「表・table-guard・change-entry は変えない」。前提の「table-guard は repo 全体の行に載り、どの差分でも走る」も守っています。I1 も成り立ち、入口は例の検を選びました。
- **外れる物:** 予想の範囲「(a) examples/ だけ」。examples-check(`packages/doeff-traverse/tests/test_examples_run.py`)の `CASE_FAILURE_MUTANTS` に行を足さないと赤になります。
- **設計の §2 の漏れ:** examples-check の「持つ知識」に `CASE_FAILURE_MUTANTS` がありません。設計の事実は cffba412b で測られていますが、この表は載せ直しの後の親 c9e1622b5(card ki-2d5b129f2ff2)で入りました。版ごとの有無は実測で、cffba412b は 0 件、c9e1622b5 と df9e627 には在ります。
- **複数の責務を持つ module:** examples-check は 1 file に次の 3 つを持っています。
  - 母集団の glob・走らせ方・出力の比べ方(仕組み)
  - 例ごとの登録(`BROKEN`・`OUTPUT_NOT_COMPARED`・`CASE_FAILURE_MUTANTS`)
  - macro の持ち主の方針(`_MACRO_OWNER`)

  このため、例(データ)を足したり直したりすると、仕組みの file も直すことになります。

## 3. どの知識がどこへ漏れるか(区別)
- **共同の不変条件(意図した物で、強制がある):** 「期待の出力に `…` を置いた例には、`CASE_FAILURE_MUTANTS` に行がある」。`test_tables_name_examples_as_they_are`(286 行)が名指して赤にするので、黙って壊れはしません。行が要ること自体は正しい設計で、誤りは設計がこれを範囲に数えていない点です。
- **隠すはずの知識の漏洩:** 行の中身は「例の source の中にちょうど 1 度在る文字列」と「それを件の中だけで失敗させる書き換え」です。どちらも例の内部(handler の式・mock の値)の知識です。検の docstring(14 行)は「どこが変わるかは例の持ち分で、検は印の意味だけを知る」と書いていますが、変異の表はこれに反します。そのため、出力を変えない例の中だけの書き換えでも、検の file を直すことになります(204 行の `source.count(original) == 1`)。
- **該当しない物:** 公開の契約の拡張でも配線の変更でもありません。表・入口・table-guard・reverse-deps・macro-owner・daily-gate は変わりません。

## 4. 再現の手順と観測(作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-A-6ntw`・df9e62756・`make sync` の後)

**A. 例を足す(S1(a))**
- `examples/ex10_timed_double.hy` は `ex04_simple.hy` の require・import・`double`・`pipeline` をそのまま写し、末尾だけを次に替えた物です。
  ```
  (import time [time])
  (setv t0 (time))
  (setv result (run (scheduled ((sequential) (fail_handler (try_handler (pipeline [1 2 3])))))))
  (print "Valid:" (. result valid_values) "in" (round (- (time) t0) 3) "s")
  ```
- `examples/expected/ex10_timed_double.txt` の中身は `Valid: [2, 4, 6] in …s` です。
- `uv run --no-sync pytest -q -m 'not e2e'` で `test_example_runs[ex10_timed_double.hy]` と `test_tables_name_examples_as_they_are` を走らせました。
  - 実測: 例は走り、期待の出力に合いました(passed)。
  - 実測: table の検は「期待の出力に … を置いた例に、件の中で失敗する変異が無い — CASE_FAILURE_MUTANTS に行を足す」、`{'ex10_timed_double.hy'}` で赤でした。
- 入口を load average 124 の下で 1 回走らせました(実測)。
  - 選びは、契約の組 `packages/doeff-traverse/` の 2 本と repo 全体の行の 2 本で、逆依存は 0 本でした。
  - 結果は赤 1(上の table の検)、未測 2(`test_daily_test_population.py`・`test_contract_table_follows_packages.py`、「上限 60 秒の内に集め終わらなかった」)です。
  - 要約の行は `land-focus-counts: selected=4 ran=1 red=1 unmeasured=2 unmeasured_reverse=0 rc=1 seconds=65.7 budget=60` でした。
- 緑に戻す最小の変更は、examples-check への 1 行 `"ex10_timed_double.hy": ("(* x 2)", "(// 6 (- x 2))")` です。これで table の検と `test_case_failure_shows_in_compared_output[ex10…]` が 2 passed になりました(実測)。

**B. 例の中だけの変更**
- ex05 の `(resume (* x 10))` を `(resume (* 10 x))` に書き換えました(出力は同じ)。
- ex07 の mock の `"MSFT" 300.0` を `310.0` にし、期待の出力の `[4] OK: (…, 300.0)` も合わせて直しました。
- 実測: `test_example_runs` は ex05・ex07 とも passed でした。一方 `test_case_failure_shows_in_compared_output` は 2 本とも赤でした。ex05 は「変異の元の文 '(resume (* x 10))' が ex05_parallel_timing.hy に 1 つだけ在るのではない — 変異を例に合わせて書き直す」と出し、ex07 も同じ形でした。直す先は examples-check の表しかありません。

実験の変更は全部、手で戻しました(`git status` は空)。共有の source は変えていません。

## 5. 未確認の前提・推測
- **推測(読み方次第の点):** 設計者が「examples/ だけ」を「選びの仕組み(表・入口・guard)は変わらない」という意味で書いたのなら、外れは範囲の書き方の誤りに縮みます。その場合でも、例の内部の文字列を検が持つという漏洩は残ります。
- **推測(未実測):** table の検は file の最後の検です。今回は例の検が 42.7 秒で最後まで届きましたが、負荷がさらに高いと束が打ち切られます。その時はこの赤が「未測」(rc 0)に変わり、マージの前には止まらない可能性があります。
- **推測:** 今後 `…` を使う例がどれだけ増えるかは分かりません(今は 2/10・実測)。
- **未確認:** 例ごとの知識の写しが他の file にも在るかは調べていません(全数検査はしていません)。
- **探索したが採らなかった候補(どれも未実測):**
  - 例が import する実行時の package(doeff-core-effects の scheduler・try-handler など)を変えても、例の検は選ばれません。ただしこれは I1 の範囲外(前提の変更)です。
  - doeff-conductor の `examples/k2_k3_pilot_workflow.hy` は doeff-hy.conductor を require し、`test_c7_pilot_workflow.py` が path で読みます。同じ型の 2 つ目の「木を歩く検」で、これも範囲外です。逆依存で選ばれるかは測っていません。
  - doeff-hy の src の行を file 単位に狭めると、macro-owner の内部の module の構成が表へ漏れます。これは設計の §4 がすでに限界として書いています。

この報告は合格や安全の判定ではありません。

WAIT: work — 呼び手の次の依頼
