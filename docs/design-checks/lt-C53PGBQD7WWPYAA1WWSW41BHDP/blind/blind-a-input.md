あなたは責務分離に対する失敗例を生成してください。
このシステムで合理的に求められうる将来の機能追加・運用変更を1つ挙げ、
それを実装すると複数の責務を持つモジュールの変更が必要になる例を具体化してください。
一般的な設計レビューや承認判定は求めていません。

元の主張の前提を守っているかを示してください。前提を変更する例なら、
契約の適用範囲外であることを明示してください。ファイルが複数変わるだけでは欠陥ではありません。
意図した公開契約の拡張、配線の変更、共同の不変条件と、隠すはずの知識の漏洩を区別してください。
不要に極端な規模、無関係な要求、意図的な破壊は使わないでください。

返答には次を含めてください。
1. 現実的な変更要求と、その要求が必要になる理由。
2. 関係する元の主張・前提と、予想に反して変わる責務・モジュール・契約。
3. どの知識・判断がどこへ漏れるために変更が波及するか。契約拡張ならその区別。
4. 最小の具体的な入力・変更例と再現手順、観測すべき結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
失敗例が見つからない場合は探索した範囲と不足を述べ、合格・安全とは結論しないでください。

以下が固定済みの要件・責務・公開契約・将来変更についての主張と、実コードの在りかです(あなたの作業 dir は /tmp/ki3497-blind-A/)。

# 盲検 A・B に渡す共通の材料(設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP)

## 対象の版と読める所

- repo: doeff。対象の版: commit cf27cee9896a83edeb2a9547d274afe7d9f43911。
- その版の写し(読むだけ): `/tmp/ki3497/snap-cf27cee98/` — 中に `packages/doeff-traverse/`(src・examples・tests)、`packages/doeff-hy/src/`(macro)、`pyproject.toml`、`tests/test_contract_table_follows_packages.py`。
- 主に読む file: `packages/doeff-traverse/tests/test_examples_run.py`(例の検)、`packages/doeff-traverse/examples/*.hy` と `examples/expected/*.txt`、`packages/doeff-traverse/README.md` の「Examples」の節、`packages/doeff-hy/src/doeff_hy/macros.hy`・`outcome_forms.hy`(traverse・`<-` の展開)、`pyproject.toml` の `[[tool.doeff.contract-tests]]` の表(変更の path の頭からマージの前に走らせる検を選ぶ)。

## 要件(固定)

- R1: doeff-traverse の examples/ に在った debug 用の 2 本(ex04_debug = traverse macro の展開を `hy.repr (hy.macroexpand …)` で印字する、ex04_debug2 = 「macro が作る形を手で組む」と書いて関数の repr を印字する)を残すか外すかを決める。
- R2: 外すなら doeff の main で 2 本と、それを出力を比べずに通していた表 OUTPUT_NOT_COMPARED の行が無い。(決め: 外した。表と、表のための検も外した。)
- R3: examples/ の file は、どれも Program を走らせて結果を印字する読み物で、標準出力を期待の出力と比べられる。macro の展開や関数の番地のような doeff-hy・Hy の中身だけを印字する debug 用の file は examples/ に置けない。

## 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(macros.hy・outcome_forms.hy)— 在る macro の名と展開の規則 | 展開の中身(gensym の名・展開の形・Hy の無名関数の番号) | `(require doeff-hy.macros [名 …])` |
| traverse-lib | doeff-traverse の effect と handler(件の失敗の方針・件の履歴) | handler の frame・行番号 | effect の class・handler の関数 |
| examples | examples/*.hy と expected/<stem>.txt — 例ごとの data・handler の組・印字、期待の出力の中で走るたびに変わる所(印 …) | なし(読み物) | `python -m hy <examples>/<file>` が 0 で終わり、標準出力が期待に合う |
| example-check | tests/test_examples_run.py — 例の母集団(glob)・表 BROKEN と CASE_FAILURE_MUTANTS・印 ANY の意味・期待の出力に置かない物・件を走らせる口の名・合否 | 下請けの関数 | pytest の id |
| premerge-selection | pyproject.toml の選びの表と、その表の検 tests/test_contract_table_follows_packages.py | 選び方の組み立て | 表の行(path の頭 → 検の file) |

## 強制方法(設計者の宣言)

| 守る事 | 強制 | 経路 |
|---|---|---|
| E1: examples/ の file は Program を走らせる(R3) | `test_example_runs_a_program[<例>]`: 例の form(入れ子も・quote/quasiquote の中は除く)に、頭が件を走らせる口(`run`・`async_run`・`doeff.run` の形)の呼び出しが 1 つ以上在る。口の名は `_PROGRAM_RUNNERS`。拾い方は inline の検 `test_runner_call_check_sees_nested_forms` で確かめる | マージの前の選び(pyproject の表: `packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/`・`doeff_core_effects/outcomes.py` の変更 → この file)と日次の段 packages |
| E2: 走る例は全部、標準出力を期待と比べる(比べない表は無い) | `test_example_runs[<例>]` と `test_tables_name_examples_as_they_are` | 同上 |
| E3: 期待の出力に Hy の付ける名を置かず、… を置いた例には件の中で失敗する変異がある | `test_tables_name_examples_as_they_are`・`test_case_failure_shows_in_compared_output` | 同上 |
| E4: 消した 2 本の名を使い手が引かない | 4 つの repo の main と doeff の ADR で名を引いた | 1 回の確かめ |

検の実行: `uv run pytest packages/doeff-traverse/tests/test_examples_run.py`(例 1 本は子 process で `python -m hy` — 1〜9 秒)。

## 変更シナリオと設計者の主張

### S1(effects): macros の展開の中身が変わる(例の答えは変わらない)

- 要求: traverse・`<-` の展開が使う gensym の名や展開の形を変える。
- 主張: 変えるのは macros だけ。examples・期待の出力・example-check は 1 行も変えず、例の検は緑のまま。展開の中身を印字したり手で写したりする file を examples/ に戻すと、E1・E3 が断る。
- 前提: 例の答え(件の結果)が変わらない変更である事。答えが変わる変更は、期待の出力を書き直す正当な契約の変更。

### S2(simulation): 例を 1 本足す

- 要求: 新しい読み物の例(Program を走らせ、件の結果を印字する)を examples/ に足す。走るたびに変わる所が在れば … と変異を置く。
- 主張: 変えるのは examples(例と期待の出力)だけ(… を置く例は CASE_FAILURE_MUTANTS の 1 行も)。出力を比べずに通す除外の行は要らないし、作れない。
- 主張(反対側): Program を走らせず、展開や関数の repr を印字するだけの debug 用の file を examples/ に足すと、期待の出力の中身に依らず E1 が赤にする。
- 前提: 例は README の一覧に載る読み物である事。

## 守る事(道具の使い方)

- 写しと repo の file を書き換えない。書いてよいのは `/tmp/ki3497-blind-A/` の下だけ。
- pytest の全数の実行、他の会話への連絡、git の push・commit をしない。
- 実行していない検査を「通った」と断定しない。推測と実測を分けて書く。
