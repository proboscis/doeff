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

以下が固定済みの要件・責務・公開契約・将来変更についての主張と、実コードと検の在りかです(あなたの作業木は /home/ubuntu/.worktrees/doeff-wt-blind-a-ki-fe7a)。

# 盲検 A・B に渡す共通の材料(設計検証 lt-0ZP7AYKXMDX83R8DKGFBA73DBJ)

## 対象の版と読める所

- repo: doeff。対象の版: commit 6a7f25105e14b7c4dd52a76652edf078661eb46c(基 fe951a108277a99761b2c888958b827d4eebfc82 の上の 2 commit)。
- あなた専用の作業木: `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-fe7a`(その版を detached で切り、`uv sync --group dev` 済み)。この作業木の中では file を足し・変え・
  消してよい(他の場所は読むだけ)。検は `cd /home/ubuntu/.worktrees/doeff-wt-blind-a-ki-fe7a && uv run pytest <path> …` で走らせる(Python は uv を通す。
  `.venv/bin/python` を直に呼ばない)。repo 全体の検(`uv run pytest` を引数なしで)は走らせない。
- 主に読む file:
  - `packages/doeff-traverse/tests/test_examples_run.py`(例の検 — この設計の対象)
  - `packages/doeff-traverse/tests/run_example_failing_a_case.py`(件の中で失敗させる子)
  - `packages/doeff-traverse/examples/*.hy` と `examples/expected/*.txt`
  - `packages/doeff-hy/src/doeff_hy/static_stub.py` の `hidden_name`(隠す名の定義)
  - `packages/doeff-hy/src/doeff_hy/macros.hy`・`outcome_forms.hy`(defk・`<-`・traverse・fnk・for/do の展開)
  - `pyproject.toml` の `[[tool.doeff.contract-tests]]`(変更の path の頭から、マージの前に走らせる検を選ぶ表)
  - `.agents/code-quality.d/doeff-traverse-tests.json`(検の module の依存の許し)
- 基との差: `git -C /home/ubuntu/.worktrees/doeff-wt-blind-a-ki-fe7a diff fe951a108 6a7f25105`。

## 要件(固定)

- R1: 展開の形を文字列で写して印字する例の file を examples/ に足すと、例の検が名指して赤になる。その失敗ケースの検が doeff main で緑。
  (背景: 例の検は既に、例の中の macro の定義・doeff-hy の外からの require・`hy.macroexpand` 等の呼び出しを断り、例が doeff の run で
  Program を走らせる事を実行の数で確かめている。展開を引き出さずに文字列で写して印字する例は、macro が変わっても赤にならず古い展開を
  教える。)
- R2: 答えを変えない doeff-hy の macro の変更で、examples/ の例と例の検は変わらない。
- R3: 今の例 9 本(ex01〜ex09)と期待の出力は書き換えずに緑のまま。

## 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(packages/doeff-hy/src/doeff_hy/macros.hy・outcome_forms.hy・handle.hy ほか)— macro の名と展開の規則 | 展開の中身(隠す別名 `_doeff_…`・Hy の gensym `_hy_gensym_…`・展開が module を引く `(__import__ "…" :fromlist …)`・形) | `(require doeff-hy.macros [名 …])` |
| hidden-names | `doeff_hy.static_stub.hidden_name(name) -> bool` — 公開の面に出さない名(`_hy_`・`_doeff_`・`__doeff_` で始まる名と MODULE_TAGS)の 1 か所の定義 | 接頭辞の表 | 関数 `hidden_name` |
| examples | examples/*.hy と examples/expected/<stem>.txt — 例ごとの data・handler の組・印字 | なし(読み物) | `python -m hy <例>` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | test_examples_run.py — 例の母集団(glob)・表(BROKEN・NO_CASES・MARKS・UNPLACEABLE)・出力の比べ方・macro の持ち主の検・展開の形の印の検 | 子の走らせ方・比べ方の中身 | pytest の検の名と断りの文 |
| failing-child | run_example_failing_a_case.py — 件の中で失敗させる子と実行の印(この変更で触らない) | 失敗の起こし方 | 標準エラーの `case-calls: N program-runs: M` |
| module-contract | .agents/code-quality.d/doeff-traverse-tests.json — 検の module の依存の許し | なし | code-quality の dependency-not-allowed |

## 公開契約

- C1 example-check → examples: 期待の出力に「展開の形の印」を置かない。印 = (a) `_` で始まる語(Hy の綴りの `-` を含む)w のうち
  `hidden_name(hy.mangle(w))` が真の物、(b) 語 `__import__`(展開が名を束ねずに module を引く手立て)。UNPLACEABLE の行
  「展開の形の印(Hy・doeff-hy の隠す名・展開の __import__)」と test_expected_output_places_nothing_unplaceable。
- C2 example-check ← macros: 例が使う doeff-hy の macro の使い所(例の `(require doeff-hy.macros [名 …])` の名を頭に持つ式)の 1 段の
  展開(`hy.macroexpand_1`・`hy.repr`)は、どれも印を持つ。持たない使い所は test_example_expansions_show_their_marks が
  `<行>: <macro>` で名指す。
- C3 example-check ← hidden-names: 隠す名の決まりは `hidden_name` を呼んで判じ、接頭辞を写さない。
- C4 失敗ケース: test_printed_expansion_copy_is_named — ex04_simple.hy に今の traverse の展開の文字列を print する 1 行を足した file を
  走らせ、その出力に UNPLACEABLE がちょうど「展開の形の印」を名指す。

## 変更シナリオと主張

### S1(effects): doeff-hy が答えを変えずに展開を変える

- 変更: (a) 隠す別名を改名する(例 `_doeff_traverse_Traverse` → `_doeff_tv_Traverse`)。(b) fnk の `do` の引きを `__import__` から
  module の頭の隠す別名へ替える。(c) 展開が隠す名も `__import__` も持たない macro を足す/変える。
- 主張: (a)(b) では examples と example-check は変わらない。(c) では test_example_expansions_show_their_marks が、その macro を使う例の
  使い所を名指して赤になり、直しは macros の展開に隠す名を持たせるか、example-check の印の種類を足す事(意図した契約 C1・C2 の変更)。
- 予想する範囲: (a)(b) macros だけ。(c) macros か、macros と example-check。

### S2(simulation): 例を 1 本足す・例が展開を写して印字する

- 変更: (a) 新しい例を足す。(b) 例に展開の写し(今の展開・古い展開・quote した form の `hy.repr`・model の `str`)を印字する行を足す。
- 主張: (a) は glob で全部の検に入り、表の変更は要らない(件の無い例なら NO_CASES)。(b) は期待の出力の検が file の名と「展開の形の印」
  を名指して赤。写しでない普通の印字は赤にならない。
- 予想する範囲: examples だけ。

### S3(effects): doeff-hy が隠す名の決まりを変える

- 変更: hidden-names の接頭辞を足す/変える。
- 主張: example-check は変わらない(`hidden_name` を呼ぶ)。
- 予想する範囲: hidden-names と macros。

### 適用しない軸

hardware・storage・distribution・concurrency — 検は 1 つの pytest の process とその子の process で、読む物は repo の file、書く物は
pytest の tmp_path だけ。子の並びの数と機体の検の枠は変えない。

## 検の実体と実行経路

- 検: `packages/doeff-traverse/tests/test_examples_run.py` の全部(特に test_expected_output_places_nothing_unplaceable・
  test_example_expansions_show_their_marks・test_printed_expansion_copy_is_named・test_unplaceable_table_catches_its_samples と、
  例ごとの test_example_runs・test_case_failure_shows_in_compared_output・test_example_runs_a_program・
  test_example_uses_only_doeff_hy_macros)。
- 実行: `uv run pytest packages/doeff-traverse/tests/test_examples_run.py`(全部で 約 20 秒)。
- 選ばれ方: pyproject.toml の `[[tool.doeff.contract-tests]]` で、prefix `packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/`・
  `packages/doeff-core-effects/doeff_core_effects/outcomes.py` の変更がマージの前にこの file を選ぶ。日次の全体でも走る。
- 依存の検: code-quality の dependency-not-allowed(module 契約 .agents/code-quality.d/doeff-traverse-tests.json の allow)。
