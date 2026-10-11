# 展開の木の置き場を読み手の版から外す — 盲検の前に固定した実装と主張

依頼: lt-DMXJNAWWMCFNV7F1QX3GJZ574H(card ki-81f1c868203f・親 ki-d7f2e023c0d4)。
対象版: doeff c1e7731d67cf0b6bbc60102a72fa4113c23b8dc9(本線・着地の列 doeff/L2057)。盲検に読ませる木は
`~/.worktrees/doeff-wt-ki81-design-check`(bca4c2f9a — c1e7731d の後の本線。analyzer の 3 file は c1e7731d と同じ中身)。
完了の範囲: 実装完了まで(本線に入った実装と検)。この文書は盲検 A・B を起こす前に書き、sha256 を報告に記録する。
経緯の申告: 実装は設計検証の前に本線へ入った(終わりの検査が設計検証の報告を求めたので、後から行う)。盲検で破れた所は
新しい便で直す。

## 固定した要件

1. Hy の展開の木(と macro の記録)を、読み手(program_effects.py)の版に依らない鍵で置く。
2. analyzer の source(program_effects.py)だけが変わった後の 1 回目の解析で、Hy の展開(`_expand_hy`)が 0 回になり、
   答えが変える前と同じ(受入 c2d906709)。
3. v5 の守り(#2973 — 派生の値の形が変わった時に古い項を読まない)は派生の値の側に残す。
4. 触らない: 置き場を切る・閉じの検を外す・時間の予算で隠す・RECORD_TAG が変わった時に展開を捨てる事。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口(package の中) |
| --- | --- | --- | --- | --- |
| X | `hy_expansion.py` — Hy の module の展開と、展開の木の置き場 | 展開のやり方(scratch の module・`hy_compile`・`macro_recording`)・展開の鍵 `_ExpansionKey`(python・hy・自分の source の digest `_expander_digest`・RECORD_TAG・module の名・source の sha256)・項の形(macro の記録 → 木の pickle)・項の名(macro の記録の指紋) | 項の中の pickle の並び・鍵の綴り・`expansions/` の dir の並び | `_expand_hy(source, filename, module_name) -> _Expanded`・`_expansion_key(source, module_name) -> _ExpansionKey`・`_ExpansionKey.text()/.place(dir)`・`_stored_expansion(place) -> _Expanded \| None`・`_store_expansion(place, expanded)`・`_entry_name(macros)` |
| R | `program_effects.py` の派生の値の置き場(`_compile_hy`・`_expanded`・`_TreeKey`・`_CachePlaces`・`_derived`・`_CachedTree`・`_ChunkedTree`・`_read_cached_tree`・`_write_cached_tree`・`_reader_digest`) | 派生の値(定義の索引・塊ごとの本体の節と本体の事実)の形・その置き場の鍵(展開の鍵 + 読み手の digest・形の印 `_TREE_KEY_FORMAT`)・読む順(派生の項 → 展開の項 → 展開) | 派生の値の pickle の並び・塊の切り方 | `_compile_hy(source, filename, module_name) -> _WholeTree \| _ChunkedTree`(`.find(function)`・`.tree`) |
| A | `program_effects.py` の読み手(`_Reader`・`analyze_program`・`_body_facts`・`_body_nodes`・`_find_function` ほか) | 木から effect の答えを読む規則 | 木がどこから来たか | `analyze_program`・`module_ast` ほか公開の関数 |
| O | 外れの観測(`observe_expansions`・`_EXPANSION_OBSERVERS`。使い手 = doeff-hy-pytest の budget.py) | 外れ(展開・展開の項の読み・派生の値の作りと書き)の間を観測者に知らせる | 観測者が誰か | `observe_expansions(observer) -> stop` |
| C | `result_cache.py` — 答えの置き場 | 答えが依る module の組(source を読んだ module・解析の module・そこから値を作った module を辿る) | 置き場の形 | `cached_result(identity, compute)` |
| P | `env_places.py` — 置き場の場所の環境変数 | `DOEFF_EFFECT_ANALYZER_CACHE` の素の値 | 判断(off など)は呼び手 | `tree_cache_setting()` |

X の effects: Hy の compile(macro を scratch の module に登録)・置き場の dir の作成と項の書き(一時 file → `os.replace`)・読み。
寿命: 項は disk に残り、誰も消さない(前から同じ)。R の effects: 派生の項の書きと読み。A・O・C・P は変えていない。

## 事前の主張(変更シナリオ)

- S1(storage・適用): 読み手の変更 — 派生の値の形(`_BodyFacts` の欄など)や読む規則を変える、program_effects.py の何かを変える。
  主張: 変えるのは program_effects.py(R・A)だけ。X は変えない。形の印を手で上げなくても、変えた後の 1 回目の解析は
  展開 0 回で、派生の値は今の読み手が作り直す(古い読み手の派生の項は読まない)。答えは読み手の意味が変わらなければ同じ。
  予想範囲: program_effects.py だけ。前提: 展開の木の中身を決める code は全部 X に在り、R・A は木を読むだけで、展開の項へ
  書く木を変えない。
- S2(storage・適用): 展開のやり方や展開の項の形を変える(`hy_compile` の渡し方・scratch の module の組み方・項の pickle の並び)。
  主張: 変えるのは hy_expansion.py(X)だけ。X の digest が両方の鍵に入るので、変えた後は展開の項も派生の項も外れ、
  前の版が作った木を読まない。R・A は変えない。予想範囲: hy_expansion.py だけ。
- S3(storage・適用): 木の中身を決める外の材料が変わる — 使った macro の本体・Hy / Python の版・RECORD_TAG。
  主張: code を変えずに、展開の項が外れて展開し直す(macro の記録の照らし・鍵)。使わない macro の変更では外れない。
  予想範囲: なし(変える module は無い)。
- S4(effects・適用): 外れの費用の観測。読み手が変わった後の「展開の項を読んで派生の値を作る」分も、展開と同じく
  観測の中に在る(予算が差し引く)。主張: O は変えない。当たりは観測しない。予想範囲: なし。
- S5(concurrency・適用): 日次の 3 つの session や並べた pytest が、同じ source を同時に外す。主張: どの書き手も一時 file から
  `os.replace` で置くので、読み手は書きかけの項を読まない。壊れた項・読めない項は外れとして扱い、展開し直して書き直す。
  鍵は要らない(同じ中身を 2 度書くだけ)。予想範囲: なし。
- S6(distribution・適用外): 置き場は機体ごと(`/work/state/doeff-effect-analyzer-cache`)で、機体をまたいで分け合う変更は
  この便に無い。分け合っても鍵は python の版の文字列を含み、内容で名指すので食い違いは無い見込みだが、測っていない。
- S7(hardware・適用外): 木は機体の性質に依らない(Python の AST と pickle の形は python の版で決まり、版は鍵に在る)。
- S8(simulation・適用外): analyzer は doeff の Program の外の道具で、時刻・乱数・外の世界の effect を持たない。置き場は
  中身の hash で決まる。

## 強制方法(今ある物)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 展開の鍵に読み手の版が入らない(S1) | 契約の検: program_effects.py の写しに 1 行足して `pe.__file__` に読ませ、1 回目の解析の `_expand_hy` が 0 回・答えの repr が同じ | `packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py::test_a_changed_reader_expands_nothing_again_and_answers_the_same` | 日次の全体の pytest(root の testpaths に analyzer の tests/python が在る)・変更時の名指し | 動的な検。`pe.__file__` 以外の道で program_effects の中身を鍵に入れる形は見ない |
| 展開のやり方が変わったら古い木を読まない(S2) | 契約の検: hy_expansion.py の写しに 1 行足すと展開し直す | 同 `test_a_changed_expansion_is_expanded_again` | 同上 | 展開の木の中身を決める code が X の外(R など)に置かれると、X の digest はそれを覆わない — 静的な強制は無い |
| 古い読み手の派生の項を読まない(#2973・S1) | 契約の検 | 同 `test_an_entry_written_by_another_reader_is_not_read` | 同上 | — |
| macro の変更・使わない macro(S3) | 契約の検 | 同 `test_a_changed_body_of_the_used_macro_is_expanded_again`・`test_a_changed_unused_macro_is_read_from_the_cache`・`test_a_changed_macro_module_that_a_required_macro_module_requires_is_expanded_again` | 同上 | RECORD_TAG は doeff-hy の側が手で上げる約束 |
| 外れの観測(S4) | 契約の検 | 同 `test_an_observer_sees_only_the_expansions_that_missed_the_cache`・`test_writing_the_cache_entry_is_inside_the_observed_miss`・`test_building_what_a_changed_reader_derives_is_inside_the_observed_miss`・`packages/doeff-hy-pytest/tests/test_budget.py` | 同上 | — |
| 読めない項は外れ(S5) | 契約の検 | 同 `test_an_unreadable_cache_file_is_expanded_again_and_rewritten`・`test_an_unreadable_derived_entry_is_built_again_from_the_stored_tree` | 同上 | 同時の書きそのものを撃つ検は無い |
| import の向き(X は R・A を知らない) | 無し(program_effects が X を module の頭で import するので、X が module の頭で program_effects を import すると循環の import で落ちる — 関数の中の import は止めない) | — | — | 静的な検査は無い |
| 型・lint | ruff・pyright(repo の設定)・code-quality の fast の段 | repo 根の pyproject.toml・pyrightconfig.json | commit の hook(未導入の機体あり)・日次 | 責務の所有は表せない |

## 実行の手順(名指し)

```
uv run --no-sync pytest packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py -q -p no:cacheprovider
uv run --no-sync pytest packages/doeff-effect-analyzer/tests/python packages/doeff-hy-pytest/tests/test_budget.py -q -p no:cacheprovider
```
