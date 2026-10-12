# 盲検の前の主張(依頼 lt-XNEJ6RS2KTXW2VWP165DW6FEMD・card ki-9a93072b861e)

対象の版: doeff `e76b005506f605df68ef52d2c4c4157d25280df3`(main。5454330f5 の直しを含む)。
完了の範囲: **実装の完了まで**(5454330f5 と e76b00550 が main に在る)。この設計検証で足す検は「予定」と書き、実行した物だけを `checks` に入れる。

## 受入条件
1. 柵の handler の節(`(EffectBase [] :when (not (isinstance effect X)) …)`)を通る X の効果が外へ出る事を、effect-analyzer が module の段の節でも関数の中の節でも読む。
2. 番(`:when`)が class だけからは決まらない節は、従来どおり節が答える(欄の番 `(Ask [key] :when (= key …))` を「通るかもしれない」と読むと closure の検が大量に赤くなる — agora-controllers の番つき節 170 件のうち 145 件がこの形)。

## 責務(modules)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 | 節が「届いた効果を答えず外へ渡す」条件の表し方と判定(`program_effects.py` の `ClassTest`・`NotTest`・`AllTest`・`AnyTest`・`UnknownTest`・`PassTest`、`Clause.passes`・`Clause.passes_on`) | 届いた効果の class から 渡す / 答える / 不明 の 3 値を出す算術(isinstance は部分類で真・上位の class で不明・無関係で偽、not / and / or の 3 値論理)。不明は「答える」へ畳む | 番の綴り・AST・scope | `PassTest.holds_for(arrived) -> bool \| None`、`Clause.passes_on(arrived) -> bool` |
| M2 | 番の読み(`handler_effects.py` の `_guards_in`・`_yielded_call_on`・`_sent_on_guards`・`_pass_test`・`_read_clauses`) | 節の suite の直下の `if` で本体が効果への yield 1 つのものを番の候補とする形の契約。その呼びが `Pass` かは scope で解いた class が `doeff_vm.Pass` かで判じる。番の if は節として数えない | doeff-hy の展開の綴り(別名・gensym の名)・`Pass` の import の仕方 | `_read_clauses(...) -> _ReadClauses`(`Clause.passes` を詰める) |
| M3 | 効果の流れ(`program_effects.py` の `pass_through`) | handler の列を内から外へ通して何が出るかの計算。`passes_on` が True の効果は節で消さず、届いたまま(出どころも保って)外へ出す | 節の中身の読み方・AST・番の綴り | `pass_through(inner, handlers, include) -> Residual`。節には `handles`・`passes_on`・`emits` しか問わない |
| M4 | doeff-hy の節の展開(`handle.hy` の `_build-clause`)。**変更していない** | 番の綴り: `(if (not 番) (yield (Pass effect k)) 本体)` を節の suite の直下に置く | analyzer の読み方 | 展開した Python の AST |
| M5 | 解析結果の保存(`result_cache.py` と `Clause.__getstate__`) | analyzer の版(source の digest)を鍵に結果を覚える。`Clause` は pickle で往復する | 番の意味 | `Clause.__getstate__` の dict |

## 副作用・寿命
M1〜M3 は純粋(I/O なし・時計なし)。M2 は AST と import 済み module の globals を読むだけ。寿命は M1 が値(frozen dataclass)、M2 が 1 回の読み、M3 が 1 回の計算。解放の要る資源はない。

## 変更シナリオと事前の主張(失敗例探しの前)

| id | 軸 | 変わる要求 | 変わる責務 | 変わらない責務・契約 | 局所化できる理由 | 前提・限界 |
|---|---|---|---|---|---|---|
| S1 | effects | doeff-hy が展開の綴りを変える(`Pass` の別名・gensym の名・補助の import の置き方) | M4 だけ | M1・M2・M3・M5 | M2 は名でなく scope で解いた class が `doeff_vm.Pass` かで判じ、M3 は `passes_on` しか見ない | 別名が scope で解ける事。番の if の形(suite 直下・本体が効果への yield 1 つ)を変えるなら M2 の形の契約の変更(契約の拡張) |
| S2 | effects | 番に class テストの新しい書き方が増える(`type(effect) is X`・`effect.__class__ in (…)`) | M1(variant を 1 つ)と M2(`_pass_test` の 1 分岐) | M3・M4・M5 | M3 は `PassTest` の中身を知らず `passes_on` だけを呼ぶ | テストが届いた効果の class だけで決まる事 |
| S3 | storage | 解析結果の保存。`Clause` に欄 `passes` を足した | M1 と M5(`__getstate__` に 1 項) | M2・M3 | 保存の鍵が analyzer の source の digest で、欄の無い古い保存は `passes=None`(従来の読み)として読める | 古い保存が混ざる経路は鍵が防ぐ。pickle を別の process が読む時は analyzer の版が同じ |
| S4 | concurrency | 閉じの解析が複数の thread で同じ handler を読む | なし | M1〜M5 全部 | 番の読みは 1 回の `_read_clauses` の局所値。node ごとに覚える `_DISPATCHES` は scope に依らない構造だけで、`Pass` かの判定は scope ごと(覚えない) | 既存の `_DISPATCHES` の cache の扱いは変えていない |
| S5 | simulation | 模擬の環境が複合の番の柵(`or` した isinstance と欄の `and`)を足す | なし(M2 は `or`・`and`・`not` を読める) | M1〜M5 全部 | class で決まる部分だけで確実に渡る効果は外へ出て、欄で決まる部分は不明 = 従来どおり節が答える | 欄の部分の読みは範囲外 |
| S6 | distribution | 単一の機体から cluster の複数の Pod へ | 対象外 | — | analyzer は source と import 済み module だけを読む純粋な計算で、機体をまたぐ共有の状態を持たない。結果の cache は鍵が source の digest | 配布は dotfiles・agora-controllers の既存の更新手順 |
| S7 | hardware | 機体の差 | 対象外 | — | 静的な読みで、機体の資源に依らない | — |

## 予想する変更範囲(失敗例探しの前)
- S1: 変わるのは M4 の file(`handle.hy`)だけ。analyzer の file は変えない。
- S2: `program_effects.py` に variant 1 つと `handler_effects.py` の `_pass_test` に 1 分岐。`pass_through` の行は変えない。
- S3: `Clause.__getstate__` の 1 項と欄の既定値だけ。

## 強制方法(責務ごと)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M2 が綴りでなく class で Pass を判じる(S1) | 契約の検: macro 展開の柵(module の段・関数の中)と、別名の `Pass` で書いた手書きの dispatcher の両方で柵が読める事 | `packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py`(実施済み・12 本) | effect-analyzer の pytest、doeff の変えた所の検 | doeff-hy が番の if の形を変えると赤になる(それが契約の変更の合図) |
| M1 の 3 値の算術 | 純粋なデータの真理値表の検(AST なし) | **予定**: 同じ file に足す | 同上 | — |
| M3 が節の中身を知らない(境界) | 手で組んだ `Clause`(`passes` つき)だけで `pass_through` が正しく流す検(AST も reader も通さない) | **予定**: 同じ file に足す | 同上 | `pass_through` が AST を触る退行は、この検が手組みの節で赤にする |
| M1 の union の網羅 | `PassTest` は閉じた型の和で、各 variant が `holds_for` を持つ。持たない variant を和に足すと型検査が拒む | `program_effects.py`(型)。**予定**: 拒む実験を 1 回 | pyright(日次の型検査) | 実行時には効かない |
| M5 の保存の往復 | pickle の往復と古い形(欄なし)の読みの検 | **予定**: 同じ file に足す | 同上 | 別 process の読みは鍵の検が別に守る |
| 番の候補が Pass でない呼びを番にしない | 形は番の候補でも `Pass` に解けない呼びは番として数えず従来どおり分かれ目として読む検 | **予定**: 同じ file に足す | 同上 | — |
| 汎用の package に応用の語を書かない | semgrep の規則(既存) | `.semgrep.yaml`、`tests/test_semgrep_gate.py` | 着地の門・日次 | — |

## 検査の実体(盲検 B へ渡す)
- `uv run pytest packages/doeff-effect-analyzer/tests/python -q`(146 passed が基準)。
- `uv run semgrep --metrics=off --config .semgrep.yaml packages/doeff-effect-analyzer/python <変えた file> --error`(35 規則・0 件)。
- `uv run ruff check <変えた file>`(この package は変更前から 13 件の指摘を持つ・増やさない)。
- `uv run pyright <変えた file>`(変更前から 7 件・増やさない)。
- 着地の門(merge queue): 変えた所の focused 検・衝突の印の走査・follow-install。

## 前提(予測できない物は予測しない)
未知の将来をすべて予測できるとは主張しない。doeff-hy が番の if の形そのもの(suite の直下・本体が効果への 1 つの yield)を変える変更は M2 の形の契約の変更で、この主張の範囲外。
