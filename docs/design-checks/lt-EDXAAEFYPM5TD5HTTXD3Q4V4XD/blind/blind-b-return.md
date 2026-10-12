## 結論

検査を通るのに責務を侵す実装を 1 つ作り、同じ設定で「検査は通る」「責務は侵している」の両方を実測しました。新しい例 `ex10_item_retry.hy` です。失敗した件をやり直す(retry する)ための handler を例の中に書いたもので、検は 52 passed・1 skipped で全部通ります(元の版は 48 passed・1 skipped)。

## 1. 機能の要求とコード

**要求(想定):** 「不安定な API で、失敗した件を数回やり直す例を足したい。件は取得してから解析するので、件の Program 全体をやり直す。ex06 のように 1 つの effect だけを retry すると、解析が新しい payload を見ない。何回目で通ったかも見せたい」

**置いた file:**
- `packages/doeff-traverse/examples/ex10_item_retry.hy`
- `packages/doeff-traverse/examples/expected/ex10_item_retry.txt`(例を走らせた標準出力そのまま。印は無し)
- 写し: `/tmp/ki-f8a5-blind-b/ex10_item_retry.hy`・`/tmp/ki-f8a5-blind-b/ex10_item_retry.txt`

**中心の部分:**
```hy
(setv _attempts {})            ; 件ごとの試行の数(印字に使う)
(defk attempt-item [f max-attempts symbol n]
  {:pre [(: f Callable) (: max-attempts int) (: symbol str) (: n int)] :post [(: % float)]}
  (setv (get _attempts symbol) n)
  (try (<- price (f symbol)) price
    (except [e Exception]
      (when (>= n max-attempts) (raise))
      (<- price (attempt-item f max-attempts symbol (+ n 1))) price)))

(defhandler retry-items [max-attempts]
  (Traverse [f items label]
    (<- coll (Traverse (fn [symbol] (attempt-item f max-attempts symbol 1)) items :label label))
    (resume coll)))
```
handler の組は `[(retry-items 3) try_handler flaky-quotes fail_handler (sequential)]` と、`(parallel 4)` の版です。

## 2. 本来の持ち主と、侵す契約

- **判断(件の失敗の方針)**
  - 本来の持ち主: traverse-lib(design §2「件の中の例外を件の失敗として記録して先へ進む」)。Traverse の docstring にも「Handler decides: … error strategy per item」とあります。
  - 移る先: 例の `retry-items`・`attempt-item`。「件の中の例外を max-attempts 回までは失敗にしない」という判断を例が持ちます。例外は traverse-lib の handler に届く前に、例の try/except が受けます。
- **状態(件ごとの履歴)**
  - 本来の持ち主: traverse-lib の Collection と HistoryEntry。HistoryEntry は event の値に `"retried"` を、欄に `attempt` を持ち、`Inspect` がそれを読む公開の口です。
  - 移る先: 例の module の dict `_attempts`。履歴の記録が 2 つになります。
  - 実測: MSFT は backend を 3 回呼び、例も「3」と印字します。ところが Inspect が返す履歴は `HistoryEntry(stage='quote', event='ok', detail=None, attempt=1)` です。NFLX も `attempt=1` の failed になっています。
- **知識(入れ子の Traverse の扱い)**
  - traverse-lib の handler は件の Program を自分の下で走らせるので、入れ子の Traverse も同じ handler に届きます(Traverse の docstring の Nesting の節)。
  - 例の handler は件の Program の周りに居ません。そのため From を 2 つ持つ for/do では、内側の件が retry されません。
  - 実測: 内側の件は backend を 1 回呼んだだけで `ConnectionError` の failed になりました(probe の (c))。加えて `:post float` のせいで、入れ子の外側の件は post 条件の誤りで失敗します(probe の (b))。

## 3. 各検査が拒まない理由(全部実測で PASSED)

- **test_example_runs[ex10]:** 終了コードは 0 で、標準出力は期待の出力と一致します。この検が見るのは出力だけで、方針の持ち主は見ません。
- **test_case_failure_shows_in_compared_output[ex10]:**
  - 子は `Traverse.__init__` を包みます。`retry-items` が新しい Traverse を作るので、子はもう一度包みます(外側の包み)。
  - 外側の包みの最初の件(AAPL)が retry の外で失敗し、出力が期待と合わなくなります。case-calls は 26、NO_CASES の外です。
  - 元の Traverse に入れた失敗(内側の包み)は MSFT の 1 回目に当たり、例の retry が吸い込みました。それでも検は出力の差だけを見るので通ります(`/tmp/ki-f8a5-blind-b/failing-child-ex10.txt`)。
- **test_example_runs_a_program[ex10]:** program-runs は 3 です。
- **test_example_uses_only_doeff_hy_macros[ex10]:** require は doeff-hy.macros だけで、defmacro・defreader・macroexpand もありません。この確かめは macro の定義と展開の引き出しを名で見るだけで、traverse-lib の effect(Traverse)を受ける handler の定義は見ません。
- **test_tables_name_examples_as_they_are:** 期待の出力が在り、NO_CASES は変わりません。
- **印・置かない物の 2 本:** `…`、path、Hy が付ける名、frame の行のどれも含みません。
- **残りの自己検(test_marks_count_only_what_ran など):** 例に依らないので通ります。test_broken_example_is_still_broken は BROKEN が空なので skip です。

まとめると、§5 の強制は出力・実行の数・macro の名を見るだけで、「どの module が件の失敗を判断するか」「件の履歴がどこに在るか」を見る検はありません。

## 4. 確かめる手順と期待の結果(どちらも実測済み)

1. `git -C /home/ubuntu/repos/doeff worktree add --detach /work/toolset/worktrees/doeff-wt-ki-f8a5-blind-b 86ecd1344` の後、`make sync`。
2. 上の 2 つの file を `examples/` と `examples/expected/` に置く。
3. 木の中で `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q` を走らせる。期待は 52 passed・1 skipped で、ex10 の 4 本が PASSED。記録は `/tmp/ki-f8a5-blind-b/pytest-ex10.txt`。
4. 違反は `uv run python -m hy /tmp/ki-f8a5-blind-b/probe_history.hy` で確かめる。期待は次のとおりで、記録は `/tmp/ki-f8a5-blind-b/probe-output.txt` です。
   - (a) MSFT が `_attempts=3`・`backend calls=3` なのに、lib の履歴は `event='ok' … attempt=1`。
   - (c) 入れ子の件は `backend calls: {'MSFT': 1}` で failed。
5. 補足の事実: `doeff_traverse/*.py` に retry の実装はありません。`"retried"` は collection.py の注釈に出てくるだけです。

## 5. 確かめていない前提・足りない情報

- **実験の木の場所:** `/tmp/ki-f8a5-blind-b/tree` は機体の hook(worktree-placement-guard)に止められたので、`/work/toolset/worktrees/doeff-wt-ki-f8a5-blind-b` に切りました。`/tmp/ki-f8a5-blind-b/` には実験の file だけを置いています。共有の木は書き換えていません。
- **違反という判断は解釈です:** design §2 の持ち分の表と lib の docstring から判断しました。design は「例が lib の effect の handler を定義すること」を明文では禁じていません。examples の持ち分「handler の組」に例の中の handler の定義が入るかどうかは、設計の書き手が決める事です。ex06 も、domain の effect の retry handler を例の中で定義しています。ex10 が違うのは、受けるのが traverse-lib の effect で、件の失敗の判断と件の履歴に及ぶ点です。
- **推測で未実測の事:** traverse-lib が後で retry を持ったり方針を変えたりしても、その変更は ex10 に届かず、検は緑のままになると見ています(S1 の「変えた側の module だけ」が崩れる)。
- **走らせていない検:** 同じ contract-tests の行に在る `test_traverse_answer_types.py`、doeff-hy-check(setv の警告)、pre-commit と semgrep、日次の全体検証。これらを通るとは言いません。
- **主の例ではない別の候補(実測済み):**
  - ex07 の時刻を `datetime.fromtimestamp` で作ると、期待の出力が機体の時間帯に依ります。
  - TZ 未設定(UTC)では 9 passed、`TZ=JST-9` では test_example_runs[ex07] が FAILED でした。
  - R3・S4 への反例になります(UNPLACEABLE は列挙なので、時間帯を知らない)。差分は `/tmp/ki-f8a5-blind-b/candidate-b-tz.diff` です。
  - この環境には `/usr/share/zoneinfo` が無く、`TZ=Asia/Tokyo` は黙って UTC として扱われます。そのため `JST-9` で確かめました。

WAIT: work — 呼び出し元の次の指示
