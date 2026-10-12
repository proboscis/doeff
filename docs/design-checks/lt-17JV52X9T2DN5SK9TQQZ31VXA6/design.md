# 設計検証 lt-17JV52X9T2DN5SK9TQQZ31VXA6 — 展開が作った値の class 名を印字する例を、doeff-traverse の例の検が名指す(card ki-b7c40391ac8e)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. どんな例が今までの検をすり抜けていたか(再現)と、直した後はどの検がどう名指すか・名指せない所はどこか。
2. 独立の評価(予備の評価 0・盲検 A・B)が崩した主張と、この変更で直した所・直さずに範囲の外へ出した所。
3. 次に頼む仕事の案。

## 全体の図(今 ⇒ 目標)

```
今(基 c33b881a9)                                       目標(branch wt/ki-b7c4-class-name-examples・3186205eb)
examples/ex10 の形(blind B・前の設計検証):               同じ例を足すと
  (print "built:" (type single) (type pairs))              test_example_output_survives_expansion_mutation[ex10] が名指して赤
  → 例の検 62 本が全部緑                                     (他の検は緑のまま — c1)
defk の関数を呼んだ値の class を印字する例(blind B):       同じ例を足すと変異の検が名指して赤(c11)
  (type (pipeline [1 2 3]))  → 全部緑

tests/test_examples_run.py(example-check)                 + 変異の検(例ごと)・表 MUTATED_MACROS / UNMUTATED_MACROS・
                                                            examples/ の下は例と期待の出力だけの検
(なし)                                                     tests/run_example_mutated_expansion.py(mutation-entrance):
                                                            defk・fnk・traverse・for/do の展開を答えを変えない形に変え、
                                                            例の file の展開だけに当てて例を走らせる
.agents/code-quality.d/doeff-traverse-tests.json           allow に doeff-hy-macros を足す(入口が macro の表を静的に import)
```

変異 = 値を作る macro の展開を、effect を全部外へ渡す handler の下で走らせる Program(WithHandler)に替える(defk・fnk は、定義した・作った関数を
呼んだ値を同じ形に替える)。run の後の答えは変わらず、作る値の class 名・型・属性だけが、macro の今の展開の形(Traverse の effect か Call か)に依らず必ず
WithHandler になる。値の形を印字する例は、macro を 1 字も変えていないのに出力が変わる — macro の持ち主が答えを変えずに展開を変えた時に赤になる事を、先に名指せる。

## 0. 範囲

- 依頼 lt-17JV52X9T2DN5SK9TQQZ31VXA6(class=investigate)。範囲は**設計と実装まで**(branch に commit・push 済み)。
- 実装済み: branch wt/ki-b7c4-class-name-examples(基 dc3385b9a4bb105937d9dad6671545f7b34c80f2)の 4 commit —
  1e06853f3(検と入口)・ec89495d4(入口の static import と module 契約)・91b9b8bbc(予備の評価 0 の指摘を直す)・3186205eb(盲検 B の指摘を直す: defk・fnk を変異の対象に)。
  この記録(docs/design-checks/lt-17JV52X9T2DN5SK9TQQZ31VXA6/)は、その次の commit で置く(検の file は変えない)。
- main に入れるのは実装の依頼(class dev)の担い手 — 計画の会話(class investigate)は merge の列に載せない決まり。card の受入 ccce86f83
  「失敗ケースが doeff main で緑」は、その land で満ちる(予定)。配備は要らない(検の file だけの変更)。

## 1. 要件(固定)

| id | 要件 |
|---|---|
| R1 | 展開を文字列で写さず、macro が作った値の形(class 名・型・属性)を実行時に印字する例を足すと、例の検がその例を名指して赤になる。その失敗ケースの検が doeff main で緑(受入 ccce86f83) |
| R2 | 答えを変えない doeff-hy の macro の変更では、今の examples/ の例も、今回足した検も変わらない(前の card が作った「展開の印」の検は、印が消える作り替えで名指して表の行を求める — §8) |
| R3 | 今の例 9 本と期待の出力は書き換えずに緑のまま |
| R4 | 変異は、例が import する Hy の lib の展開を変えず、通常の走りが読む bytecode の entry を変えず、作り直させない |
| R5 | 新しい種類の例(初めて使う macro・examples/ の下の lib)に出会ったら、検が名指して表への行か置き場の直しを求める |

## 2. 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(macros.hy ほか)— 名と展開の規則 | 展開の中身・作る値の class | `(require doeff-hy.macros [名 …])`。traverse・for/do は呼び手に別名の import を求める |
| hidden-names | `doeff_hy.static_stub.hidden_name` | 接頭辞の表 | 関数 |
| examples | examples/*.hy と expected/<stem>.txt | なし(読み物) | `python -m hy <例>` が 0 で終わり、標準出力が期待に合う |
| example-check | test_examples_run.py — 母集団・表・比べ方・macro の持ち主の検・展開の印の検・変異の検 | 子の走らせ方・比べ方・使い所の読み方 | pytest の検の名と断りの文 |
| mutation-entrance | run_example_mutated_expansion.py — 値を作る macro の展開を答えを変えない形に変えて例を走らせる | 変異の作り方・Hy の macro の呼び口(第 1 引数 `_hy_compiler` の扱い) | `python run_example_mutated_expansion.py <例の path> <macro の名>…` |
| failing-child | run_example_failing_a_case.py(触らない) | 失敗の起こし方 | 標準エラーの実行の印 |
| module-contract | .agents/code-quality.d/doeff-traverse-tests.json | なし | code-quality の dependency-not-allowed・dynamic-boundary |

どの macro の展開が実行時の値を作るかは macros の持ち分だが、変異の表(MUTATED_MACROS)は検の側が手で持つ — その漏れは表の検(C5)が名指して補う(§8 に限界)。

## 3. 公開契約(直した後)

| id | 契約 | 守る検 |
|---|---|---|
| C1 | 例は、MUTATED_MACROS(defk・fnk・traverse・for/do)の展開を変異させた下でも、期待の出力のまま(落ちない)。値の class 名・型・属性を印字する例は、変異の下で出力が変わる/落ちるので名指して赤 | test_example_output_survives_expansion_mutation |
| C2 | 変異は例の file を compile する時の展開だけに当たる。lib の展開は変えない | test_expansion_mutation_spares_the_modules_an_example_imports |
| C3 | 変異の後の姿は macro の今の展開の形に依らない(必ず WithHandler)。検の自己検は変異の後の姿だけを決める | test_expansion_mutation_changes_the_built_value_not_the_answer・…names_an_example_that_breaks_on_the_built_value・…follows_the_macro_not_how_the_example_requires_it |
| C4 | 例が使う macro は MUTATED_MACROS か UNMUTATED_MACROS(理由つき)に置く | test_example_macros_are_mutated_or_named |
| C5 | examples/ の下は、直下の例(.hy)と expected/ の期待の出力(.txt)だけ | test_examples_dir_holds_only_examples_and_their_expected_outputs |
| C6 | 入口は Hy の macro の呼び口を保つ(第 1 引数が `_hy_compiler` の macro には compiler を渡す)・包み方を知らない/表に無い macro は 0 以外で終わる | 全例の変異の子(defk が `_hy_compiler` を受ける)・確かめ c4・c7 |
| C7 | 包む関数は例の path を閉じ込めない(例は sys.argv[0] で見分ける)— bytecode の guard の digest が例ごとに変わらない | 確かめ c5・c14 |

## 4. 変更シナリオ — 盲検の前の主張(design-before-blind.md)と実測

| シナリオ | 盲検の前の主張・予想 | 実測 | 判定と理由 |
|---|---|---|---|
| S1 答えを変えない macro の変更(effects) | macros だけ。examples・expected・example-check・入口は変わらない | (a) traverse の一番外を Program で包む: 78 passed(c2)。(b) traverse が compiler を受ける: 78 passed(c7)。(c) traverse を恒久に何もしない handler の下に置く: 78 passed(c8)。(d) traverse の中身を内部の macro へ移し hy.R で呼ぶ: **8 failed**(c12)— 前の card が作った「1 段の展開に印が在る」検 6 本と自己検 2 本。今回足した検は 1 本も変わらない | (a)(b)(c) は主張どおり — ただし (b)(c) は評価 0 が初版の入口と自己検の穴(compiler を受ける macro を包めない・今の macro の値の決め打ち)を崩し、直した後の結果。(d) は**主張の誤り** — 隠すはずの「各 macro の 1 段の展開が印を持つ」という知識の漏れで、前の card の C2 の作りに在る(この変更が作った物ではない)。直さずに範囲の外へ(§9 F-A1) |
| S2 例を 1 本足す(既存の macro だけ) | 変わるのは examples と expected だけ。値の形を印字する例は検が名指す | 値の形を印字する例は名指して赤(c1・c11)。examples/ の下に lib を置く形は C5 が名指す(c9)。**traverse・for/do を使う新しい例は、品質検査の private-symbol で赤**(c13) | 名指しは主張どおり。品質検査は**主張の誤り** — 別名 `_doeff_traverse_Traverse` を呼び手が import する口の漏れ(前の card の F1)で、新しい traverse の例は基底の行か F1 の解消が要る。直さない(§9 F-F1) |
| S3 例が doeff-hy の macro を初めて使う | 変わるのは examples・expected と example-check の表だけ | do! を使う例は C4 が名指して赤(c3) | 主張どおり |
| S4 Hy / doeff-hy の macro の呼び口が変わる | mutation-entrance だけ | 名が表に無い/包み方を知らない macro は入口が 0 以外で終わる(c4)。compiler を受ける macro は入口が compiler を渡して包む(c7) | 主張どおり。初版の入口は compiler を受ける macro で落ちて例のせいにした(評価 0 の R2a)— 直した |
| S5 変異の走りが共有の bytecode 保存先を使う(storage) | 通常の走りが読む entry を変えず、作り直させない | 空の保存先: 今の例では中身が同一(c5)。lib が defk・traverse を使う場合は、変異の走りが lib の entry を**もう 1 つ**足す(別の digest)が、交互に何度走らせても増えず作り直しも無い(c14)。評価 0: 初版は包む関数が例の path を閉じ込めて entry が走りごとに増えた | 初版は**主張の誤り**(R4)— 直した(C7)。直した後の R4 は「通常の entry を変えず、増えるのは lib ごとに 1 度」へ改めた(§8) |
| S6 通常の走りと変異の走りが並行して走る(concurrency) | 互いの出力を汚さない | 4 本並行の子で 78 本が毎回同じ結果(c0・c6)。変異の子が例の隣に書く .pyc を、通常の走りが読み違えない(r5 = c15) | 主張どおり |
| hardware・distribution・simulation | 適用しない | — | 検は 1 つの pytest の process とその子。機体・クラスタに依る要求は無い |

## 5. 確かめの一覧(evidence/checks/・手順は evidence/measure.sh.txt — 版 3186205eb の検の作業木)

| id | 正常/失敗 | 確かめた事 | 結果 |
|---|---|---|---|
| c0 | 正常 | 例 9 本で検が全部緑 | 78 passed, 1 skipped(基: 62 passed) |
| c1 | 失敗 | 盲検 B(前の設計検証)の ex10 — traverse・for/do が作る値の class を印字 — を足す | 変異の検が ex10 だけを名指して赤(`-built: Traverse / Call` `+built: WithHandler / WithHandler`)・他の 83 本は緑 |
| c2 | 正常 | 答えを変えない macro の変更(前の設計検証の patch)| 78 passed |
| c3 | 失敗 | 表に無い macro(do!)を初めて使う例 | 表の検が名指して赤 |
| c4 | 失敗 | 包む macro の名が今の doeff-hy に無い | 入口が 0 以外で終わり表を合わせると名指す |
| c5 | 正常 | 空の保存先で通常/変異を比べる(今の例 ex09・lib が defk・traverse を使う場合) | ex09 は中身が同一(70 file)。lib の場面は件数同じ・中身が違う(digest)— c14 で安定を確かめた |
| c6 | 正常 | 検の file の所要 3 回 | 17.6 / 25.6 / 22.0 秒(基 19.6 秒・機体の負荷で揺れる) |
| c7 | 正常 | traverse が compiler を受ける macro(評価 0 の R2a) | 78 passed |
| c8 | 正常 | traverse を恒久に何もしない handler の下に置く(評価 0 の R2b) | 78 passed |
| c9 | 失敗 | 例の補助の lib を examples/support/ に置き、その lib が作った値の class を例が印字する(評価 0 の ex11) | C5 の検が名指して赤 |
| c10 | 失敗(限界の確認) | 内側の defhandler が受けた Traverse の中身(件の関数の形)を印字する例(評価 0 の ex10) | **84 passed — 緑のまま通る**。変異は一番外の値だけを包む限界(§8)を実測で確かめた |
| c11 | 失敗 | defk の関数を呼んだ値の class を印字する例 ex14(盲検 B)| 変異の検が ex14 だけを名指して赤・他の 83 本は緑 |
| c12 | 失敗 | traverse を内部の macro へ移し hy.R で呼ぶ(盲検 A)| 8 failed: 前の card の印の検 6 本と自己検 2 本。今回足した検は緑のまま(S1 (d)) |
| c13 | 失敗 | traverse の例を 1 本足す(ex04_simple の写し)| 例の検は緑。品質検査は private-symbol で赤(S2・§9 F-F1) |
| c14 | 失敗(保存先の交互実行) | 同じ保存先で通常/変異を交互に走らせる(どちらを先にしても) | 増えるのは 1 度だけ(+1 file)・以後は同一・作り直し無し |
| c15 | 失敗 | 変異の走りが書く .pyc を通常の走りが読み違えないか(repro/r5) | 通常は `Traverse / Call`・変異は `WithHandler / WithHandler`・何度交互にしても同じ |

直す前の再現: evidence/repro/r0(基の版で 62 passed)・r1(ex10 を足しても全部緑)・r2(答えを変えない macro の変更で ex10 だけ赤になる)・r3(盲検 B の ex14 が全部緑)・r4(ex14 が答えを変えない
defk の変更で赤になる)・r5(.pyc)。

## 6. 評価の経緯 — 予備の評価 0 → 修正 → 盲検 A・B → 修正

設計者(私)の自己検証の後、独立の評価を 2 段で行った(blind/blind-meta.md)。**予備の評価 0** は盲検 A・B ではない(1 つの文脈に両方の役を頼んだ)。直した後の版に、
skill の手順どおり**盲検 A・B を別々の新規文脈で**立てた。

| 出所 | 失敗例 | 再現 | 成立 | この変更での扱い |
|---|---|---|---|---|
| 評価 0 R2a | traverse が `_hy_compiler` を受けるだけで 9 本赤(入口が compiler を渡さず `NameError`・断りは例のせい) | 評価 0・私(9 failed) | 成立 | 直した(入口が compiler を渡して包む)。c7 |
| 評価 0 R2b | 自己検が「今の macro の値は WithHandler でない」と決め打ち | 評価 0・私(c2 の patch で 3 failed) | 成立 | 直した(変異の後の姿だけを決める・lib は通常の走りと比べる)。c8 |
| 評価 0 ex11 | examples/support/ の lib が作った値の class を例が印字 | 評価 0(94 passed → M1 で赤) | 成立 | 直した(C5)。c9 |
| 評価 0 R4 | 包む関数が例の path を閉じ込め、lib の記録が走りごとに増える | 評価 0 | 成立 | 直した(sys.argv[0])。c5・c14 |
| 評価 0 ex10 | 内側の handler が受けた Traverse の中身を印字 | 評価 0・私(c10) | 成立 | 範囲の外の限界(§8) |
| 評価 0 ex12・別名 | `(require … :macros […])`・`[traverse :as trav]` で静的な読みが誤る / `*` が偽の赤 | 評価 0(読み取りの結果のみ・私は未再現) | 未再現 | 限界として記録(§8・§9 F-A2) |
| 評価 0 R2c | 展開の `__import__` を `hy.I` に替えると印の検 3 本が赤 | 評価 0 | 成立 | 前の card の C2 が名指す設計(UNMARKED_EXPANSIONS)。限界(§8) |
| 盲検 B | defk の関数を呼んだ値の class を印字する例(「Program is DATA」を見せる)が全部緑 | 私(r3: 84 passed + code-quality passed)・答えを変えない defk の変更で赤(r4) | 成立 | 直した(defk・fnk を変異の対象に)。c11 |
| 盲検 B 副次 | traverse を使う新しい例は品質検査の private-symbol で赤 | 私(c13) | 成立 | S2 の予測を訂正・範囲の外(F-F1) |
| 盲検 A | traverse を内部の macro へ委ねる作り替えで印の検が赤 | 私(c12: 8 failed) | 成立 | 前の card の C2 の漏れ。範囲の外(F-A1)。今回足した検は不変 |

## 7. 強制方法と実装の在りか

| 守る事 | 強制方法 | 実装の在りか | 実行経路 |
|---|---|---|---|
| C1・C3 | 契約の検(例ごとに変異の子を走らせて出力を比べる)・自己検 | test_examples_run.py の `test_example_output_survives_expansion_mutation`・`_expansion_mutation_change`・自己検 3 本・入口 | `[[tool.doeff.contract-tests]]`(pyproject.toml)の prefix `packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/` がマージの前に選ぶ。日次の全体 |
| C2・C7 | 契約の検(tmp の lib)・入口の作り | `test_expansion_mutation_spares_the_modules_an_example_imports`・入口の `_mutated` | 同上 |
| C4・C5 | 契約の検(表・置き場) | `test_example_macros_are_mutated_or_named`・`test_examples_dir_holds_only_…` | 同上 |
| C6 | 契約の検(全例の変異の子)| 入口の `_takes_compiler`・`_MUTATIONS` | 同上 |
| 検の file の依存 | module 契約 | doeff-traverse-tests.json の allow `doeff-hy-macros` | code-quality の dependency-not-allowed・dynamic-boundary(入口は `import doeff_hy.macros` を静的に書く)|

型で表せない所: 「値の形を印字する」かは文の意味で、型や import の向きでは表せない — 変異の下の出力の比べで判じる契約の検に置いた。品質検査(fast の段)と
pyright(変えた 2 file)は通った。型検査の full の段は走らせていない(日次の全体)。

## 8. 残る限界(強制していない事)

- 変異は**一番外の値**だけを包む。内側の handler が受けた effect の中身(件の関数の形・items の型)を印字する例は見分けない(c10)。
- defhandler(handler を定義する文)と `<-`(束縛)が作る物の形は包めない(UNMUTATED_MACROS)。変異の包み方は入口の `_MUTATIONS` が知る 4 つだけ。
- 例が import する lib(examples/ の外の package)が macro で作った値は、変異の外(C2 の裏)。
- 表の検(C4)と前の card の印の検は、`(require doeff-hy.macros [名 …])` の綴りだけを読む。`*`・`:macros`・別名の綴りは読めない(`*` は前の card の持ち主の検が偽の赤にする)。
  変異そのものは macro の表に当たるので綴りに依らない(`…follows_the_macro_not_how_the_example_requires_it`)。
- R2 は、前の card の印の検(各 macro の 1 段の展開が印を持つ)が、段を足す作り替え(hy.R の委譲)で名指して赤になる分だけ外れる(c12)。
- R4: lib が変異する macro を使う時、変異の走りは lib の bytecode の entry を別の digest でもう 1 つ足す(1 度だけ)。通常の entry は変えず、作り直させない(c14)。
- 新しい例が traverse・for/do の別名を import すると、品質検査の private-symbol で赤(前の card の F1 の根 — c13)。
- 型検査の full の段・日次の全体は走らせていない。

## 9. 範囲の外の所見(次に頼む仕事の案)

- F-A1(doeff-traverse の例の検・前の card ki-fe7a2e60fdfb の物): 印の検 `test_example_expansions_show_their_marks` と自己検 `test_expansion_check_reads_uses_out_of_context`・
  `test_printed_expansion_copy_is_named` が、各 macro の「1 段の」展開に印が在る事に依る。答えを変えない委譲(hy.R)で 8 本が赤になる(c12)。案: 印を
  1 段でなく最後まで展開した形で見る / 自己検の source を macro の展開の印に依らない形にする。
- F-A2(同): 静的な読み `_doeff_hy_macro_names`・`_required_packages` が `*`・`:macros`・別名を読めない。案: example の require の形を Hy で評価した throwaway module の `_hy_macros` と、
  doeff-hy の表を identity で突き合わせ、別名を本名に戻す。
- F-F1(doeff-hy・前の card の F1): traverse・for/do が Traverse・Skip を呼び手の import でなく自分で引く。新しい traverse の例が private-symbol で赤になる根(c13)。
- F-M1(doeff-traverse の例の検): 変異の包み方を足す案 — 内側の handler が受ける effect の中身(件の関数の形)を変える変異・defhandler の定義を包む変異。

## 10. 未解決

無い(上の F-… は範囲の外の所見で、この依頼の完成の条件ではない)。
