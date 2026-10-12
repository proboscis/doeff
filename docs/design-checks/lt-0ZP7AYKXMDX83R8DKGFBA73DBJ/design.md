# 設計検証 lt-0ZP7AYKXMDX83R8DKGFBA73DBJ — 展開の形を文字列で写して印字する例を、doeff-traverse の例の検が名指す(card ki-fe7a2e60fdfb)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. どんな例が今までの検をすり抜けていたか(再現)と、直した後はどの検がどう名指すか。
2. 盲検 A・B が崩した主張と、この変更で直した所・直さずに範囲の外へ出した所。
3. まだ守れていない所と、次に頼む仕事の案。

## 全体の図(今 ⇒ 目標)

```
今(基 fe951a108)                                      目標(branch wt/ki-fe7a-expansion-string-examples・e2c158f5e)
examples/ex04_simple.hy の写し                           同じ写し
  + (print "traverse の展開: (_doeff_traverse_Traverse      → test_expected_output_places_nothing_unplaceable が
           (_doeff_do (fn [x] …)) items)")                   file と「展開の形の印」を名指して赤
  → 例の検を全部通る(evidence/repro/r1)

tests/test_examples_run.py(example-check)             tests/test_examples_run.py(example-check)
  UNPLACEABLE「Hy が付ける名(_hy_anon_・_hy_gensym_)」     UNPLACEABLE「展開の形の印」= hidden_name の隠す名 + __import__
  (正規表現を検が持つ)                                     ├─ doeff_hy.static_stub.hidden_name(hidden-names)を呼ぶ
                                                          ├─ test_example_expansions_show_their_marks:
                                                          │    例の doeff-hy の macro の使い所を 1 つずつ文脈の外で
                                                          │    1 段展開し、どれも印を持つ事(UNMARKED_EXPANSIONS は除く)
                                                          ├─ test_unmarked_table_names_unmarked_macros(表の行の掃除)
                                                          ├─ test_expansion_check_reads_uses_out_of_context(仕組み)
                                                          └─ test_printed_expansion_copy_is_named(失敗ケース)
doeff-hy の macros.hy(macros)                          macros — 隠す名だけの改名・文脈の外の展開を断る変更では、
                                                          examples と example-check は変わらない(c4・c5)
.agents/code-quality.d/doeff-traverse-tests.json       allow に doeff-hy-static-stub を足す(module-contract)
```

## 0. 範囲

- 依頼 lt-0ZP7AYKXMDX83R8DKGFBA73DBJ(class=investigate)。範囲は**設計と実装まで**(branch に commit 済み)。
- 実装済み: branch wt/ki-fe7a-expansion-string-examples の e2c158f5e6bae3d4564390a3c666833281556900(基 fe951a108 の上の 6 commit —
  検の変更 3・module 契約 1・この記録 2)。この design.md と報告の JSON は、その次の commit で置く(検の file は変えない)。
- main に入れるのは実装の依頼(class dev)の担い手 — 計画の会話(class investigate)は merge の列に載せない決まり。card の受入
  cedbad616「失敗ケースが doeff main で緑」は、その land で満ちる(予定)。
- 出自: 設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP の design.md §6 の残る限界。

## 1. 要件(固定)

| id | 要件 |
|---|---|
| R1 | 展開の形を文字列で写して印字する例の file を足すと、例の検が名指して赤になる。その失敗ケースの検が doeff main で緑(受入 cedbad616) |
| R2 | 答えを変えない doeff-hy の macro の変更で、examples/ の例と例の検は変わらない |
| R3 | 今の例 9 本(ex01〜ex09)と期待の出力は書き換えずに緑のまま |

## 2. 責務(module)— 盲検 A で macros の口を正した

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(macros.hy・outcome_forms.hy ほか)— macro の名と展開の規則 | 展開の中身(defk の helper の名 `_doeff_outcomes`・gensym・`__import__` の引き・形) | `(require doeff-hy.macros [名 …])`。**traverse・for/do は呼び手に `(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])`(for/do の When は Skip も)を求める**(docstring の Requires — 盲検 A。盲検の前の表はこれを「隠す物」に入れていた) |
| hidden-names | `doeff_hy.static_stub.hidden_name(name) -> bool` — 公開の面に出さない名の 1 か所の定義 | 接頭辞の表 `_HIDDEN_PREFIXES` | 関数 `hidden_name` |
| examples | examples/*.hy と expected/<stem>.txt | なし(読み物) | `python -m hy <例>` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | test_examples_run.py — 母集団・表(BROKEN・NO_CASES・UNMARKED_EXPANSIONS・MARKS・UNPLACEABLE)・比べ方・macro の持ち主の検・展開の形の印の検 | 子の走らせ方・比べ方・使い所の読み方 | pytest の検の名と断りの文 |
| failing-child | run_example_failing_a_case.py(触らない) | 失敗の起こし方 | 標準エラーの実行の印 |
| module-contract | .agents/code-quality.d/doeff-traverse-tests.json | なし | code-quality の dependency-not-allowed |

## 3. 公開契約(直した後)

| id | 契約 | 守る検 |
|---|---|---|
| C1 | 期待の出力に「展開の形の印」を置かない。印 = `_` で始まる語 w(Hy の綴りの `-` を含む)のうち `hidden_name(hy.mangle(w))` が真の物と、語 `__import__` | test_expected_output_places_nothing_unplaceable・test_unplaceable_table_catches_its_samples |
| C2 | 例が使う doeff-hy の macro の使い所を 1 つずつ文脈の外で 1 段展開し、展開できた物はどれも印を持つ。印の無い展開の macro は UNMARKED_EXPANSIONS に理由つきで置く。いちばん外の使い所は文脈の外でも展開できる。入れ子の使い所が文脈の外で断られたら数えない(外の macro が文脈の中で展開する — 引き出して写せる形が無い) | test_example_expansions_show_their_marks・test_expansion_check_reads_uses_out_of_context |
| C3 | 隠す名の決まりは hidden-names を呼んで判じ、接頭辞を写さない | import `from doeff_hy.static_stub import hidden_name` と module 契約の allow |
| C4 | 失敗ケース: ex04_simple.hy に今の traverse の展開の文字列を print する 1 行を足した file は、走り・macro の持ち主の検を通り、出力に UNPLACEABLE がちょうど「展開の形の印」を名指す | test_printed_expansion_copy_is_named |
| C5 | UNMARKED_EXPANSIONS の行は、今の例が使う印の無い展開の macro だけを名指す | test_unmarked_table_names_unmarked_macros |

## 4. 変更シナリオ — 盲検の前の主張(design-before-blind.md)と実測

| シナリオ | 盲検の前の主張・予想 | 実測 | 判定と理由 |
|---|---|---|---|
| S1(a) 隠す名の改名(effects) | macros だけ | 本当に隠れた名(HELPERS-NAME `_doeff_outcomes` → `_doeff_oc`)は macros だけ・全部緑(c5)。`_doeff_traverse_Traverse` の改名は例 7 本の import の書き換えが要る・15 failed(c6・盲検 A) | **主張の誤り**。別名は traverse・for/do が呼び手に import を求める口で、隠す物ではなかった — この変更の前からの漏れで、この変更が作った物ではない。この card では直さない(§9 F1) |
| S1(b)(c) 展開の作り替え・印の無い展開 | (b) macros だけ (c) C2 が名指す | (c) 印 `__import__` を外すと C2 が `52: fnk` 等を名指す(c3)。文脈の外の `(<- …)` を断る答えを変えない変更(変異 M-A2)では、直す前は C2 と C4 が `HyMacroExpansionError` で 11 本落ちた(r3・盲検 A)。直した後は全部緑(c4) | 直す前は**主張の誤り**(検が「macro は文脈の外でも展開できる」という macros の中身に依っていた)。C2 を、入れ子の断りは数えない形に直した(e2c158f5e と、その前の c98c93b2b) |
| S2(a) 例を 1 本足す(simulation) | glob で全部の検に入り、表の変更は要らない(件の無い例は NO_CASES) | module の直下で val を使う例は、直す前は C2 が `30: val` で断り、直す手が無かった(r5・盲検 B)。直した後は UNMARKED_EXPANSIONS に理由つきの行を置けば緑(c7)、置かなければ名指して赤(c8)、使わない macro の行は赤(c9) | 直す前は**主張の誤り**。主張を「表の変更が要るのは、件の無い例(NO_CASES)と、印の無い展開の macro を初めて使う例(UNMARKED_EXPANSIONS)だけ — どちらも検が名指して求める」へ改めた |
| S2(b) 例が展開の写しを印字する | 期待の出力の検が file と「展開の形の印」を名指す | 今の展開の写し(c1)・古い展開の写し(c2)・今の展開そのもの(C4 の検)を名指す。例の他の検は緑のまま | 主張どおり。ただし盲検 B: 展開を写さずに、for/do が作った値の class 名を印字する例は検を全部通り、答えを変えない macro の変更で赤になった(r4)— 文字列の写しではないので R1 の外、R2 は守れていない(§8・§9 F2) |
| S3 隠す名の決まりを変える(effects) | example-check は変わらない | `_dh_` を `_HIDDEN_PREFIXES` に足しても検は変えずに全部緑(c10)。`_dh_helper` は足す前は印でなく、足した後は名指される(c11) | 主張どおり |
| hardware・storage・distribution・concurrency | 適用しない | — | 検は 1 つの pytest の process とその子。新しい検で子を走らせるのは C4 の 1 本(約 1 秒)だけ |

## 5. 確かめの一覧(evidence/checks/・手順は evidence/measure.sh.txt — c0〜c11 は版 e2c158f5e の検の作業木)

| id | 正常/失敗 | 確かめた事 | 結果 |
|---|---|---|---|
| c0 | 正常 | 直した版で例の検が全部緑 | 62 passed, 1 skipped |
| c1 | 失敗 | 今の traverse の展開を写して印字する例 | 期待の出力の検が file と「展開の形の印」を名指して赤・他の 7 本は緑 |
| c2 | 失敗 | 古い展開(外した ex04_debug2 の形)を写して印字する例 | 同上 |
| c3 | 失敗 | `__import__` を印から外す | C2 が ex02・ex03 の fnk の使い所を名指して赤 |
| c4a | (直す前) | 変異 M-A2 の下、c98c93b2b | 仕組みの検だけが、断られた使い所の一覧を固定していて赤 — 直した(e2c158f5e) |
| c4 | 正常 | 変異 M-A2(文脈の外の `(<- …)` を断る)の下 | 62 passed |
| c5 | 正常 | HELPERS-NAME の改名 | 62 passed |
| c6 | 失敗 | 呼び手が import する別名 `_doeff_traverse_Traverse` の改名 | 15 failed(例 7 本 × 2 と C4)— 主張 S1(a) の誤りの記録 |
| c7 | 正常 | val を使う例 + UNMARKED_EXPANSIONS の行 | 18 passed |
| c8 | 失敗 | val を使う例、行なし | C2 が `30: val` を名指して赤 |
| c9 | 失敗 | 例が使わない macro の行 | 表の検が「行を外す」と名指して赤 |
| c10 | 正常 | hidden_name に接頭辞 `_dh_` を足す | 検を変えずに 62 passed |
| c11 | 失敗 | `_dh_helper` を持つ写し | 足す前は印でなく、足した後は名指される |
| c12 | 正常 | code-quality(変えた所・fast の段)と module 契約の表の検 | passed・14 passed(型検査は含まない) |
| c13 | 正常 | pre-commit(semgrep・doeff-linter・基〜HEAD) | Passed |

直す前の再現: evidence/repro/r1(直す前の版で写しの例が全部通る)・r2(盲検 A の改名)・r3(盲検 A の M-A2 で C2 が落ちる)・
r4(盲検 B の ex10)・r5(盲検 B の val)。

## 6. 盲検 A・B

- 起動の記録: blind/blind-meta.md(第 1 候補 gpt-6-astra は Agent tool で選べず、opus・effort xhigh で代用 — 観測した model・effort は未確認)。
- 入力: blind/blind-a-input.md・blind-b-input.md(共通の材料 blind-common.md)。返答(未加工): blind-a-return.md・blind-b-return.md。

| 返答の失敗例 | 再現 | 成立 | この変更での扱い |
|---|---|---|---|
| A 主: 別名 `_doeff_traverse_Traverse` の改名で例 7 本が赤 | r2・c6(15 failed) | 成立 | 表を正した(§2)。根の直しは doeff-hy の変更で範囲の外(§9 F1) |
| A 付: 文脈の外の `(<- …)` を断る変更で C2・C4 が `HyMacroExpansionError` | r3(11 failed) | 成立 | C2 を直した。c4a の後もう 1 度直し、c4 で全部緑 |
| B 主: for/do が作った値の class 名を印字する ex10 が検を通り、答えを変えない macro の変更で赤 | r4(65 passed → 1 failed) | 成立 | 文字列の写しではない(R1 の外)。限界に書き、§9 F2 |
| B 付: val を使う例を C2 が直しようの無い形で断る | r5 | 成立 | UNMARKED_EXPANSIONS を足した(c7・c8・c9) |

## 7. 強制方法と実装の在りか

| 守る事 | 強制方法 | 実装の在りか | 実行経路 |
|---|---|---|---|
| C1 | 契約の検 | test_examples_run.py の `_shows_expansion`・`UNPLACEABLE`・`_unplaceable_in` | pyproject の `[[tool.doeff.contract-tests]]` の prefix `packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/`(macros と hidden_name の在りか)・`packages/doeff-core-effects/doeff_core_effects/outcomes.py` がマージの前に選ぶ。日次の全体 |
| C2・C5 | 契約の検(例ごと) | `MacroUse`・`_doeff_hy_macro_uses`・`_unexpanded`・`_unmarked`・`UNMARKED_EXPANSIONS` | 同上 |
| C3 | 公開の関数の import と module 契約 | `from doeff_hy.static_stub import hidden_name`・doeff-traverse-tests.json の allow | code-quality の dependency-not-allowed(c12) |
| C4 | 子を 1 本走らせる検 | test_printed_expansion_copy_is_named | 同上 |

型で表せない所: 「写し」かどうかは文の意味で、型や import の向きでは表せない — 印字の語で判じる契約の検に置いた。

## 8. 残る限界(強制していない事)

- 展開を写さずに、展開が作った値を実行時に覗いて印字する例(値の class 名・repr — 盲検 B の ex10)は見分けない。R2 はこの形で崩れ得る。
- 隠す名も `__import__` も持たない一部だけの写し・隠す名を公開の名へ言い換えた写しは見分けない。
- 写しを印字せず、source の文字列・註に置くだけの file は見分けない(期待の出力に出ない)。
- 例の source は、traverse・for/do の口として隠す別名 `_doeff_traverse_Traverse`・`_doeff_traverse_Skip` を import する(盲検 A)。
  その別名の改名は例 7 本へ波及し、source の隠す名を断る検も置けない(§9 F1 の後に置ける)。ex04_simple の `(import doeff [do :as _doeff-do])`
  は defk が自分で引くので要らない import だと盲検 A は読んだ(未確かめ)。
- lib の例外の文が隠す名(`__doeff_resumption__` など)を含み、例がそれを印字すると、期待の出力に置けない(MARKS で伏せられない)。
- C2 は 1 段の展開だけを見る。入れ子の使い所の文脈の中の展開(defk が書き換えた `<-` など)は、外の使い所の展開の一部として見る。

## 9. 範囲の外の所見(次に頼む仕事の案)

- F1(doeff-hy): traverse・for/do が Traverse・Skip を呼び手の import でなく自分で引く(fnk の `__import__` か defk の helper の import の形)。
  その後で例 7 本と tests・publications の別名の import を外し、例の source の隠す名を断る検を足せる。外の利用者(agora の
  book_rows.hy など)の import は残っても害は無い(使われない名)。
- F2(doeff-traverse の例の検): 答えを変えない macro の変異(一番外を Program で包む など)の下で例を走らせ、標準出力が変わらない事を
  確かめる検 — 盲検 B の ex10 の形(展開が作った値の型・repr の印字)を、文字列の印でなく振る舞いで名指す。
- F3(agora-controllers の道具): `long-command put` が `scripts/long_command.hy` の 377 行で `TypeError: TaskLimit.__init__() got an
  unexpected keyword argument 'limit'` を出して落ちる(2026-10-12 03:2x UTC にこの会話で実測)。

## 10. 未解決

無い。
