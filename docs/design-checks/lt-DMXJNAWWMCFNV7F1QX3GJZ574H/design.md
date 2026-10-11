# 展開の木の置き場を読み手の版から外す — 設計と検証の記録

## この文書の目的

読み手が次の 3 つを判断できる状態にする。

1. doeff-effect-analyzer の Hy の展開の木の置き場が、どの責務の分け方で「analyzer の source が変わっても展開をやり直さない」を満たすか。
2. その分け方を、将来の実装者が破れないように何で守っているか(検の名・場所・走る経路・守れない所)。
3. 盲検 A・B が出した失敗例が、直す前に本当に成立し、直した後に成立しないか(実行の記録)。

- 依頼: lt-DMXJNAWWMCFNV7F1QX3GJZ574H(card ki-81f1c868203f・親 ki-d7f2e023c0d4)。
- 基準の版: doeff c1e7731d67cf0b6bbc60102a72fa4113c23b8dc9(最初の実装・本線・着地の列 doeff/L2057)。
- 直しの版: b606fb9d6999afc2e47deca80688001f905f8f76(branch wt/ki81-design-check-r2。この記録と同じ便で本線へ入れる)。証拠の実行は同じ木の caf81e66d(本文に Reverts の行を足す前の同じ変更)で行った。
- 完了の範囲: **実装完了まで**。最初の実装(c1e7731d)は設計検証の前に本線へ入った。設計検証は終わりの検査に求められて後から行い、
  盲検で成立した失敗例を直しの版で塞いだ。未接続の検査は無い(全部 pytest の契約の検で、日次の全体の pytest の母集団に入る)。
- 盲検の前に固定した主張: `design-before-blind.md`(sha256 a27fbad95bd208627a8d185a097e90d1c53a713d87f3eddfd78c17a87369b65a)。

## 全体の構成(前 ⇒ 今)

```
前(v8・c1e7731d より前)
  program_effects.py ── _compile_hy ──▶ <cache>/<sha(python,hy,reader=program_effects.py,RECORD_TAG,名,source)>/<macro の指紋>.pickle
                                         └ 1 つの項 = macro の記録 + 木 + 派生の値   → program_effects.py が 1 字変わると全 module を展開し直す

今(v10・caf81e66d)
  program_effects.py(読み手 R・A)                     hy_expansion.py(展開 X)
    _compile_hy ─①派生の項を読む─▶ <cache>/<sha(v10, 展開の鍵, reader=package の全 .py)>/<指紋>.pickle(macro の記録 + 派生の値と木)
        │ 外れ
        └─②_expansion(source, …, dir) ───────▶ X: 鍵を source から作る ─▶ <cache>/expansions/<sha(python,hy,expander=hy_expansion.py,RECORD_TAG,名,source)>/<指紋>.pickle
                                                  └ 項が無ければ _expand_hy で展開して書く(書けるのは X だけ)
        ③派生の値を作って ① の場所へ書く(②③は観測の区間の中)
  型検査用の展開(doeff_hy.static_view の中)の間は、①②③と答えの置き場(result_cache)を全部使わない。
```

## 要件(固定)

1. Hy の展開の木(と macro の記録)を、読み手(program_effects.py)の版に依らない鍵で置く。
2. analyzer の source だけを変えた後の 1 回目の解析で、Hy の展開(`_expand_hy`)が 0 回になり、答えが変える前と同じ(受入 c2d906709)。
3. 派生の値の形が変わった時に古い項を読まない守り(#2973)は、派生の値の側に残す。
4. 触らない: 置き場を無くす・閉じの検を外す・時間の予算で隠す・RECORD_TAG が変わった時に展開を捨てる事。

## 責務(直しの版)

| id | 責務 | 持つ知識 | 隠す知識 | 口 |
| --- | --- | --- | --- | --- |
| X | `hy_expansion.py` — 展開と展開の置き場 | 展開のやり方・展開の鍵(python・hy・自分の file の digest・RECORD_TAG・module の名・source の sha256)・項の形と名・型検査用の展開の判定 | 項の pickle の並び・`expansions/` の並び・置き場の読み書き(`_stored_expansion`・`_store_expansion`・`_read_expansion` は X の外から名指せない) | `_expansion(source, filename, module_name, directory)`・`_expand_hy`・`_expansion_key`・`_ExpansionKey.text()/.place()`・`_entry_name`・`_type_check_expansion` |
| R | program_effects.py の派生の置き場 | 派生の値の形・鍵(v10・展開の鍵・読み手の digest = package の全 .py)・読む順 | 派生の pickle の並び・塊の切り方 | `_compile_hy`・`_hy_cache_place`・`_hy_cache_dir` |
| A | 読み手(`_Reader`・`analyze_program`・`_body_facts` ほか) | 木から答えを読む規則 | 木の出所 | `analyze_program`・`module_ast` ほか |
| O | 外れの観測 | 外れ(展開・展開の項の読み・派生の値の作りと書き)の区間 | 観測者 | `observe_expansions` |
| C | result_cache.py | 答えの依る module の組 | 置き場の形 | `cached_result`(置き場の根は `_hy_cache_dir` — 型検査用の展開の間は None) |
| P | env_places.py | 環境変数の素の値 | 判断 | `tree_cache_setting` |

effects: X = Hy の compile・展開の項の書き(一時 file → `os.replace`)と読み。R = 派生の項の書きと読み。項は誰も消さない(前から同じ)。

## 変更シナリオ — 主張・盲検・実測

| id | 軸 | 変える要求 | 盲検の前の主張 | 盲検が出した失敗例 | 直す前の実測 | 直し | 直した後の実測 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | storage | 読み手を変える(派生の値の形・読む規則・読み手の file の分け方) | program_effects.py だけが変わり、X は変わらない。展開 0 回で派生の値だけ作り直す | A: 本体の事実の helper を `body_facts.py` へ分け、そこだけ変えると古い事実を読む | 成立: 同じ派生の置き場を読み `rewraps` が空。空の置き場では `prog` が入る(`evidence/A-before-split-on-c1e7731d.txt`) | 読み手の digest を package の全 .py に。派生の値を作る code が package と標準 library の外に無い事を検で守る | 新しい派生の置き場で `prog` が入り、展開 0 回(`evidence/A-after-split-on-fix.txt`)。検: 別 file の変更で派生を作り直す・同じ file なら別の場所でも同じ項・閉包の検(辿る数 70)とその失敗例 |
| S2 | storage | 展開のやり方・展開の項の形を変える | hy_expansion.py だけが変わる。X の digest が両方の鍵に入る | B: 読み手が木を加工(gensym の振り直し)して展開の置き場へ書くと、読み手の版に依る木が版に依らない鍵の下に残る | 成立: 既存の検 20 本 + B の違反の検 2 本 = 22 passed・ruff の新しい指摘なし(`evidence/B-before-D1-on-c1e7731d.txt`) | 「読む/展開して書く」を X の `_expansion` 1 関数に閉じ、鍵はその source から作る。置き場の関数を X の外が名指すと赤・X は analyzer の他の module を import しない(検) | 書く形は検が `program_effects.py` を名指して赤(`evidence/B-after-store-form.txt`)。読み手の側だけで加工する形は B の検の裏返しを含め 29 passed(`evidence/B-after-legit-form.txt`) |
| S3 | storage | 使った macro・Hy / Python の版・RECORD_TAG が変わる | code を変えずに展開し直す。使わない macro では外れない | A の別の候補: 型検査用の展開(`TYPE_CHECK_EXPANSION`)の木が普通の鍵で残る・普通の木を読む | 成立: 型検査用の展開が置いてあった普通の木を読み、展開 2 回(`evidence/before-fix-new-tests.txt`) | 型検査用の展開の間は 3 つの置き場を使わない | 検が緑(`evidence/after-fix-tests-verbose.txt`)。macro の変更の検も緑 |
| S4 | effects | 読み手が変わった後の「展開の項を読んで派生の値を作る」分の観測 | O は変わらない。当たりは観測しない | (盲検は出さず) | — | — | 書きを観測の外へ出した写しは検が赤(`evidence/S4-mutation.txt`)。観測の検は緑 |
| S5 | concurrency | 並べた process が同じ source を同時に外す | `os.replace` で書きかけを読まない。読めない項は外れ | (盲検は出さず) | — | — | 6 process 同時: 各 1 回展開・項は 1 つずつ・一時 file の残り 0・両方の項が読める(`evidence/S5-concurrent.txt`)。読めない項の検は緑 |
| S6 | distribution | 機体をまたいで置き場を分け合う | 適用外 | — | — | — | — |
| S7 | hardware | 機体の性質 | 適用外 | — | — | — | — |
| S8 | simulation | 決定的な模擬 | 適用外 | — | — | — | — |

### 予想と実測の差

- S1: 盲検の前は「program_effects.py だけが変わる」と予想した。A の分割の例では、実際に変わったのは `body_facts.py`(読み手の内部の
  分け方)で、R の鍵は program_effects.py 1 file だけを見ていたので、古い派生の値が読まれた。これは**知識の漏れ**(R の鍵が A の
  file の分け方に依っていた)で、公開契約の変更ではない。直しで R の鍵は「package の全 .py」になり、A がどう分けても R は変わらない。
  package の外へ出る分けは閉包の検が赤にする。
- S2: 盲検の前は「X の digest が鍵に入るので前の版の木を読まない」と主張したが、X の外(R)が X の置き場へ書けたので、
  X の digest が覆わない code が木を決められた。これは**知識の漏れ**(置き場に何を入れるかの判断が R へ出られた)。
  直しで書くのは X だけになり、X は analyzer の他の module を import しない。
- S3: 型検査用の展開の印は鍵にも記録にも無かった(前の v8 の鍵でも同じ — c1e7731d の前から在った穴)。X の中の判断で塞いだ。

## 強制方法(直しの版)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 展開の鍵に読み手の版が入らない(要件 1・2) | 契約の検: package を写し program_effects.py に 1 行足して読ませると、1 回目の解析で展開 0 回・答えの repr が同じ | `packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py::test_a_changed_reader_expands_nothing_again_and_answers_the_same` | 日次の全体の pytest(root の testpaths に analyzer の tests/python)・変更時の名指し | 動的な検。答えの同一は 1 つの module(ASKING)で見る |
| 派生の値は今の読み手が作る(要件 3・S1) | 読み手の digest = package の全 .py の名と中身。検: 別 file(handler_effects.py)の変更で派生を作り直す・同じ file なら別の場所でも同じ項・#2973 の古い項を読まない | `program_effects._reader_digest`・同 test file の `test_a_changed_module_of_the_reader_builds_what_is_derived_again`・`test_the_same_files_of_the_reader_elsewhere_read_the_same_entry`・`test_an_entry_written_by_another_reader_is_not_read` | 同上 | package の外の code は digest に入らない → 次の行で守る |
| 派生の値を作る code は package と標準 library の中(S1) | 契約の検(静的な辿り): `_derived` から code の読む global の名を辿り、package と標準 library の外の関数・class・module、外から import した名を名指す。失敗例(外の helper を差すと名指す)も検 | `test_what_is_derived_is_built_by_code_of_this_package`・`test_a_helper_outside_the_package_is_named` | 同上 | 属性の呼び(`obj.method()`)の先と、外から来た値(引数)は辿らない。今 辿る数は 70 |
| 展開の置き場に書くのは X だけ(S2) | 契約の検(AST): `_store_expansion`・`_stored_expansion`・`_read_expansion` を package の hy_expansion.py 以外が名指すと赤。失敗例(B の形)も検 | `test_only_the_expansion_module_reads_or_writes_its_store` | 同上 | 名指さずに `expansions/` の file を直に書く code は見ない(pickle と鍵の綴りを写す必要があり、現実的な実装の形ではない) |
| X の木を決める code は X の file の中(S2) | 契約の検(AST): hy_expansion.py が analyzer の他の module を import しない(関数の中の import も)。X の digest の検 | `test_the_expansion_module_imports_no_other_module_of_the_analyzer`・`test_a_changed_expansion_is_expanded_again` | 同上 | doeff_hy_bytecode_guard(macro の記録)の code の変更は RECORD_TAG を手で上げる約束(card の範囲外) |
| 型検査用の展開は置き場を使わない(S3) | X の `_type_check_expansion` を `_expansion` と `_hy_cache_dir` が読む。契約の検 | `test_a_type_check_expansion_is_neither_read_from_nor_written_to_a_store` | 同上 | — |
| macro の変更(S3) | 契約の検(前から) | `test_a_changed_body_of_the_used_macro_is_expanded_again`・`test_a_changed_unused_macro_is_read_from_the_cache` ほか | 同上 | — |
| 外れの観測(S4) | 契約の検 | `test_writing_the_cache_entry_is_inside_the_observed_miss`・`test_building_what_a_changed_reader_derives_is_inside_the_observed_miss`・`test_an_observer_sees_only_the_expansions_that_missed_the_cache`・doeff-hy-pytest の `test_budget.py` | 同上 | — |
| 書きかけ・壊れた項は外れ(S5) | 一時 file → `os.replace`・読めない項は None。契約の検 | `test_an_unreadable_cache_file_is_expanded_again_and_rewritten`・`test_an_unreadable_derived_entry_is_built_again_from_the_stored_tree` | 同上 | 同時の書きを撃つ検は無い(実験 `evidence/S5-concurrent.py` で 1 回確かめた) |
| 型・lint | ruff(変えた file で新しい指摘なし)・code-quality の fast の段 | repo の設定 | commit の hook(未導入の機体あり)・日次 | pyright の範囲(`doeff`・doeff-adr)に analyzer は入らない。責務の所有は型で表していない(package の中の私的な名の約束と契約の検で守る) |

## 盲検 A・B

- 起動の記録: `blind/blind-meta.md`。第 1 候補 gpt-6-astra / low は、この Pod の codex に資格が無く使えなかった。
  第 2 候補の Opus 5.5(Agent tool の `model: opus`・`effort: xhigh`)を、互いに見えない新しい文脈で同時に起こした。
- 入力: `blind/blind-a-input.md`・`blind/blind-b-input.md`(著者の自己評価・既知の穴・望む結論は入れていない)。
- 返答(未加工): `blind/blind-a-return.md`・`blind/blind-b-return.md`。
- 成立の判定: A・B とも写しの上で成立した(上の表)。A の別の候補(型検査用の展開)も成立した。

## 実行の記録(証拠)

| file | 中身 |
| --- | --- |
| `evidence/B-d1-on-c1e7731d.patch`・`evidence/B-d1-violation-tests.py` | B の差分 D1 と B の違反の検(B の返答のまま) |
| `evidence/B-before-D1-on-c1e7731d.txt` | 直す前: 22 passed・ruff の新しい指摘なし |
| `evidence/A-split.py`・`evidence/A-probe.py`・`evidence/A-before-split-on-c1e7731d.txt` | A の分割と再現。直す前: 同じ置き場で古い事実 |
| `evidence/before-fix-new-tests.txt` | c1e7731d の写しで新しい検: 3 failed / 5 passed(意図した訳) |
| `evidence/fix-caf81e66d.patch` | 直しの差分(c1e7731d → caf81e66d の analyzer) |
| `evidence/after-fix-tests-verbose.txt`・`evidence/after-fix-package.txt` | 直した後: 27 passed・182 passed |
| `evidence/A-after-split-on-fix.txt` | 直した後の A: 新しい派生の置き場で新しい事実 |
| `evidence/B-patch-on-fix.py`・`evidence/B-after-store-form.txt`・`evidence/B-inverted-tests.py`・`evidence/B-after-legit-form.txt` | 直した後の B: 書く形は赤・正しい形は 29 passed |
| `evidence/S4-mutation.txt` | S4 の失敗例: 1 failed(意図した検) |
| `evidence/S5-concurrent.py`・`evidence/S5-concurrent.txt` | S5 の実験 |
| `evidence/measure-toolset.txt` | 最初の実装の効き(toolset の組: 展開 107 → 0 回・91.2 → 11.3 秒・答えの digest 同じ) |

## 残る事(要件の外)

- 受入 c280fe28e(日次の記録)は、agora-controllers の doeff の pin が上がった後の日次で数える(依頼主が card に書いた)。
- 測りの script(ki-d7f2e023c0d4 の発言)は `program_effects._expand_hy` を差し替える。直しの版ではこの名が program_effects に無く、
  script は AttributeError で止まる。差し替える先は `hy_expansion._expand_hy`(card に書く)。
