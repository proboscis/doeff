# 設計: 番(:when)が確実に外へ渡す効果を、その節が答えたと読まない

依頼 lt-XNEJ6RS2KTXW2VWP165DW6FEMD(class=investigate)・card ki-9a93072b861e・対象のリポジトリ doeff。

## この文書の目的

読み手が次の 3 つを、実行記録を辿って確かめられるようにする。

1. effect-analyzer が柵(`:when (not (isinstance effect X))`)の節を通る X の効果を空と読んでいた原因と、その直しの責務の分け方。
2. 将来の変更(展開の綴りの変更・番の新しい書き方・保存・並行・模擬の柵)が、どの責務に収まるか。1 回目の実装と事前の主張が、盲検の失敗例と実測で外れた所と、その直し。
3. 責務の取り違えを、どの検・型が拒むか。拒めない所はどこか。

## 1. 完了の範囲

**実装の完了まで。** 次の 3 つの commit が doeff の main に入る(1・2 は着地済み、3 はこの記録と同じ便で運ぶので着地の sha は事前に書けない)。

| # | commit | 内容 |
|---|---|---|
| 1 | 5454330f5 | 番を節の欄 `Clause.passes` に読み、確実に渡る効果を節で消さない |
| 2 | e76b00550 | 番の if が yield する `Pass` を、綴りでなく scope で解いた class で判じる(同じ時に入った 4f7dd1d49 が展開の `Pass` を別名にして、1 の版は main で赤だった) |
| 3 | この記録の便 | 盲検 A の失敗例の直し(`Clause.before_guard`)と、盲検 A・B で足した検・この記録 |

受入条件は card ki-9a93072b861e の 2 条(card に記録済み)に加え、この調べで固めた 2 条。

| 条 | 内容 |
|---|---|
| R1 | 柵の節を通る X の効果が外へ出る事を analyzer が読む(module の段・関数の中のどちらでも) |
| R2 | 番が class だけからは決まらない節(欄の番)は従来どおり節が答える。「通るかもしれない」と読むと closure の検が大量に赤くなる(agora-controllers の番つき節 170 件のうち 145 件が欄の番) |

## 2. 現状の障害と原因

```
 Program ── Nap ──▶ [ 柵 handler ] ──▶ 外の handler
                      │ 総受け節 (EffectBase)
                      │   if not 番 : yield Pass(effect, k)   ← Nap はここで外へ渡る
                      │   else      : raise ...               ← Tick は止まる
   直す前の analyzer:  pass_through が「最初に class が合う節」を取り、その節が答えたと数える
                      → Nap も Tick も「答えられた」→ 残り(外へ出る効果)= 空
```

| 見たもの | 実測(直す前) |
|---|---|
| 柵(module の段・関数の中)で外へ出る効果 | 空。本当は Nap |
| 正の番 `:when (isinstance effect Tick)` | 全部の効果が答えられた事になる(Nap・Zap も消える) |
| 原因 1 | `pass_through` が番を読まない |
| 原因 2 | 番の if(`not not isinstance(effect, Nap)`)が、本体 `Pass` の余計な入れ子の節として読まれ、総受けの節の後ろに回って使われない |

## 3. 責務(modules)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 | 節が「届いた効果を答えず外へ渡す」条件の表し方と判定、渡す前に節が出す効果(`program_effects.py` の `ClassTest`・`NotTest`・`AllTest`・`AnyTest`・`UnknownTest`・`PassTest`、`Clause.passes`・`Clause.passes_on`・`Clause.before_guard`・`Clause.performed_before_guard`) | 届いた効果の class から 渡す / 答える / 不明 の 3 値を出す算術。不明は「答える」へ畳む。渡す効果にも節が番の前に出す効果が付く | 番の綴り・AST・scope | `PassTest.holds_for(arrived) -> bool \| None`、`Clause.passes_on(arrived) -> bool`、`Clause.performed_before_guard() -> ProgramEffects` |
| M2 | 番の読み(`handler_effects.py` の `_guards_in`・`_yielded_call_on`・`_sent_on_guards`・`_pass_test`・`_ahead_of`・`_read_clauses`) | 節の suite の直下の `if` で本体が効果への yield 1 つのものを番の候補とする形の契約。その呼びが `Pass` かは scope で解いた class が `doeff_vm.Pass` かで判じる。番の if は節として数えない。番の if より前の文を `before_guard` にする | doeff-hy の展開の綴り(別名・gensym の名)・`Pass` の import の仕方 | `_read_clauses(...) -> _ReadClauses` |
| M3 | 効果の流れ(`program_effects.py` の `pass_through`) | handler の列を内から外へ通して何が出るかの計算。渡す効果は節で消さず出どころを保って外へ出し、その時の節の仕事は `before_guard`、答える時は `emits` | 節の中身の読み方・AST・番の綴り | `pass_through(inner, handlers, include) -> Residual`。節には `handles`・`passes_on`・`performed_before_guard`・`emits` しか問わない |
| M4 | doeff-hy の節の展開(`packages/doeff-hy/src/doeff_hy/handle.hy` の `_build-clause`・`expansion_names.py`)。**変更していない** | 番の綴り: `(if (not 番) (yield (Pass effect k)) 本体)` を節の suite の直下に置く。番が session の値を読む時は、その取り出し(`Get`・初期化・`Put`)を番の前に置く。`Pass` は `_doeff_Pass` の別名で書く | analyzer の読み方 | 展開した Python の AST |
| M5 | 解析結果の保存(`result_cache.py` と `Clause.__getstate__`) | 答えは、analyzer が読んだ module と analyzer 自身の module の内容を鍵にして覚える。`Clause` は pickle で往復する | 番の意味 | `Clause.__getstate__` の dict |

副作用: M1〜M3 は純粋(I/O・時計なし)。M2 は AST と import 済み module の globals を読むだけ。寿命: M1 は値(frozen dataclass。`before_guard` は初めて問う時に読み、保つ)、M2 は 1 回の読み、M3 は 1 回の計算。解放の要る資源はない。

## 4. 変更シナリオ・事前の主張・実測の波及

事前の主張(`claims-before-blind.md`、sha256 は `input-hashes.txt`)は盲検の前に保存した。ここでは主張と実測を並べる。事前の主張を実測に合わせて書き換えていない。

| id | 軸 | 変わる要求 | 事前の予想の範囲 | 実測の範囲 | 差の理由 |
|---|---|---|---|---|---|
| S1 | effects | doeff-hy が展開の綴りを変える | M4 だけ | M4 の 1 行(`expansion_names.py` の `PREFIX`)。analyzer は無変更で柵の検 34 本が緑 | 予想どおり。ただし**1 回目の実装(5454330f5)は予想を満たさなかった**: 綴り `Pass` で見分けていたので、実際に 4f7dd1d49 の別名化で main の検 7 本が赤になった(M2 が隠すはずの綴りを知っていた漏洩)。e76b00550 で直した |
| S2 | effects | 番に class テストの新しい書き方(`issubclass(type(effect), X)`) | M1 の variant 1 つ+M2 の 1 分岐 | M2 の `_pass_test` の 1 分岐だけ。`pass_through` の行は差分に無い | 予想より小さい: 既存の `ClassTest` の別の綴りだったので M1 に variant が要らなかった。意味が新しい variant なら M1 も変わる(予想の範囲内) |
| S3 | storage | `Clause` に欄 `passes`・`before_guard` を足した | M1 と M5(`__getstate__`) | `Clause` の欄と `__getstate__`(M1・M5 は同じ class)。欄の無い古い保存は passes なし・before_guard なしとして読める | 予想どおり。保存の鍵は analyzer の module の内容を含み、直す前の答えは再利用されない(実測) |
| S4 | concurrency | 複数 thread が同じ handler を読む/同じ定義を呼びごとに別の束縛で読む | 変更なし | 変更なし。thread 16 本が同じ答え | 予想どおり。ただし主張「番の読みは呼びごとの束縛で読む」を守る検が無かった(盲検 B、5 節) |
| S5 | simulation | 模擬の環境が複合の番の柵を足す | 変更なし | 変更なし。確実に渡る効果だけが外へ出て、欄の部分は不明 = 従来どおり | 予想どおり |
| S6 | distribution | 単一の機体から cluster へ | 対象外 | — | analyzer は source と import 済み module だけを読む純粋な計算で、機体をまたぐ共有の状態を持たない |
| S7 | hardware | 機体の差 | 対象外 | — | 静的な読みで、機体の資源に依らない |
| S8 | effects(盲検 A が足した) | 番が session の値を読む柵(保守の門) | 事前の主張には無かった(S5 の「変更なし」の延長で、変わるのは無いと読んでいた) | **M1・M2・M3・M5 が変わった**(盲検 A の予想と同じ)。M4 は無変更 | 事前の主張が外れた。M3 の `continue` が「渡す効果に対して節は何も走らない」という M4 の展開の並びに依存した暗黙の前提を持っていた(隠すはずの知識の漏洩)。前提を `Clause.before_guard` のデータにして明示し、M3 は問い口だけを使う(6 節) |

## 5. 盲検 A・B と再現

起動の記録は `blind-meta.md`、入力は `blind-input.md`、返答は `blind-A-response.md`・`blind-B-response.md`(受け取った文面のまま)。

| | 失敗例 | 設計者の再現(実行記録) | 結果 |
|---|---|---|---|
| A | 柵の節が session の値を読む番を持つと、番の前に節が出す効果(`Get`・初期化の `ReadFlag`・`Put`)が、番が渡す効果では捨てられる。`pass_through` が `passes_on` 真なら節の効果を数えずに `continue` していた | 盲検 A の入力を `make_package` の形に直して走らせた: `evidence/expA-probe-on-e76b00550.log` | **成立。** analyzer の答えは `['Nap']` だけ、節の `emits` は `Get`・`Put`・`ReadFlag`。実際には番の前に走る。直す前(6533804cf)は Nap が抜けて Get/Put/ReadFlag が残り、今回の変更は穴を塞がず移していた |
| B | 番の読みを定義の node ごとに 1 回だけ覚える性能の修正(既存の 4 つの cache と同じ書き方)。番が引数の class を見る時、最初に読んだ呼びの値に固まる | 盲検 B の入力を走らせた: 現行コードで 3 本緑(`expB-probe-on-e76b00550.log`)、差分を当てた mutant で赤(`expB-probe-on-mutant.log`)。mutant に既存の検 146 本・semgrep・ruff・pyright を当てた: 全部通る(`expB-existing-tests-on-mutant.log`・`expB-checks-on-mutant.log`) | **成立。** 責務違反(scope ごとの判断が大域の memo に移る)は既存の検査を全部通る。守る検が無かった |

## 6. 修正履歴

| 時点 | 内容 | 根拠 |
|---|---|---|
| 1 回目 5454330f5 | 番を `Clause.passes` に読む。番の if は名が `Pass` の呼びで見分けた | 検 10 本 → 11 本。analyzer の Python の検 145 passed |
| main の事故 | 同じ時に入った 4f7dd1d49 が展開の `Pass` を `_doeff_Pass` にして、着地した版で柵の検 7 本が赤(18:41 → 19:02 JST) | `evidence/incident-5454330f5-landed-version-guarded-tests.log` |
| 2 回目 e76b00550 | 呼びの名を scope で解いた class が `doeff_vm.Pass` かで判じる。別名の `Pass` で書く手書きの dispatcher の検を足す | 146 passed。検 12 本 |
| 盲検 A の直し(この便) | `Clause.before_guard`(番の if より前の文が出す効果)を足し、`pass_through` は渡す効果にそれを流す。`_ahead_of` が番の if より前の文を切り出す | `evidence/expA-probe-after-fix.log`: `['Get', 'Nap', 'Put', 'ReadFlag']` |
| 盲検 B の直し(この便) | 呼びごとの束縛で番を読む事を守る検 `test_a_guard_reads_the_values_the_call_gives_it` を足す。mutant(定義ごとの memo)で赤・実装で緑 | `evidence/new-B-test-on-mutant-v2.log` |
| 境界の検(この便) | 3 値の真理値表(15 行)・手組みの `Clause` だけで `pass_through` が流れる事・保存の往復と古い形・Pass でない呼びは番でない事・exact class の検は不明・thread | `test_guarded_clauses.py`(34 本) |

## 7. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M2 が綴りでなく class で `Pass` を判じる(S1) | 契約の検: macro 展開の柵(module の段・関数の中)と、別名の `Pass` で書いた手書きの dispatcher の両方で柵が読める事。別名の頭を変えて(`PREFIX`)も緑 | `packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py` | effect-analyzer の pytest・doeff の変えた所の検・着地の門 | doeff-hy が番の if の形を変えると赤になる(それが契約の変更の合図)。形が変わらない別名・gensym は拾える |
| M1 の 3 値の算術 | 純粋なデータの真理値表の検(AST なし)15 行 | 同 file の `test_a_guard_test_decides_by_the_class_that_arrives` | 同上 | — |
| M3 が節の中身を知らない(境界) | 手で組んだ `Clause`(`passes`・`before_guard` つき)だけで `pass_through` が流す検。reader も AST も通らない | 同 file の `test_pass_through_sends_on_what_a_clause_says_without_reading_any_source` | 同上 | `pass_through` が AST を触る退行は、手組みの節で赤になる。節の取り出し口の追加はこの検の更新を要する |
| 番の読みを覚えない(S4・盲検 B) | 同じ定義を呼びごとの束縛で 2 回読む検(mutant で赤) | 同 file の `test_a_guard_reads_the_values_the_call_gives_it` | 同上 | 定義の外の呼び手の束縛の渡し方が変わる退行は別の検が守る |
| 番の前の効果が渡す効果とともに出る(盲検 A) | session の値を読む柵の検(直す前に赤) | 同 file の `test_what_a_clause_does_before_its_guard_leaves_with_the_effect_the_guard_sends_on` | 同上 | 番の前に効果を出す展開が他の形(suite の直下でない)になると読めない |
| `PassTest` の和が閉じている | 各 variant が `holds_for` を持つ。持たない variant を和に足すと pyright が 4 か所で拒む(実測 `evidence/pyright-union-without-holds-for.log`) | `program_effects.py` の型 | 日次の型検査(commit の hook では撃たない) | 実行時には効かない。この package の pyright は変更前から 7 件の既存の指摘を持つ(増やしていない) |
| 保存(S3) | pickle の往復と古い形(欄なし)の検・保存の鍵が analyzer の module の内容を含む事の実測 | `test_a_clause_stored_before_its_guard_was_read_performs_nothing_before_it`・`evidence/expS3-stale-result-cache.log` | 同上 | 鍵が analyzer の module を含むのは `compute` から静的に辿れる所まで(`test_result_cache.py` が既存で守る) |
| Pass でない呼びを番にしない | `Relay(effect, k)` を yield する入れ子の if は番でなく従来どおり読む検 | `test_a_nested_branch_that_yields_something_other_than_pass_is_not_a_guard` | 同上 | — |
| 汎用の package に応用の語を書かない | semgrep の規則(既存の 35 規則) | `.semgrep.yaml`・`tests/test_semgrep_gate.py` | 着地の門・日次 | — |

## 8. 実装の証拠(正常例・失敗例)

| 検 | 正常例 | 失敗例(期待した拒否を観測) |
|---|---|---|
| S1 | 別名の頭を `_dx_` に替えても柵の検 34 本緑(`expS1-alias-prefix-changed.log`) | 1 回目の読みは `_doeff_Pass` で 7 本赤(`incident-5454330f5-landed-version-guarded-tests.log`) |
| S2 | 新しい書き方を `_pass_test` に足すと読める(`expS2-after.log`)。型: 全 variant が `holds_for` を持つ版は 7 件(基準) | 足す前は不明 = 節が答える(`expS2-before.log`)。`holds_for` の無い variant は pyright が拒む(`pyright-union-without-holds-for.log`) |
| S3 | 何も変えない analyzer の写しは保存した答えを使う(`expS3-stale-result-cache.log` 5 節)。pickle の往復の検 | analyzer の module を変えると再解析し、直す前の答えを使わない(同 2〜4 節) |
| S4 | 現行コードで呼びごとの束縛を読む 3 本緑・thread 16 本が同じ答え | mutant は既存の全検査を通りながら、新しい検が赤(`new-B-test-on-mutant-v2.log`) |
| S5 | 複合の番の柵で Tick だけ外へ出る検・実際の柵 6 つの読み(`scan-guards-agora-controllers.log`)・nodeboot の closure 検が最終の版で緑(`nodeboot-closure-final-code.log`) | 欄の番は節が答える(「通るかもしれない」と読むと gap になる 145 件の形)の検 |
| S8 | 直した後 `['Get', 'Nap', 'Put', 'ReadFlag']`(`expA-probe-after-fix.log`) | 直す前の読みで新しい検 3 本が赤(`new-tests-on-e76b00550.log`) |

測定: analyzer の Python の検 168 passed(`final-analyzer-pytest.log`。基準 134 → 146 → 168)。semgrep 0 件・ruff 13 件(変更前と同じ)・pyright 7 件(同)・doeff-cluster の closure 検 10 passed(`final-static-checks.log`)。

## 9. 限界と測っていない物

- **agora-controllers の closure の検**は、1 回目の版で 10 ファイル(emulated_daily_verify・intake_api・kanban_api・kanban_system・listener_loops・daily_verify_runner・item_entries・records_job・notice_broker_choice・nodeboot)を測り、最終の版では nodeboot だけを測り直した。赤は `test_emulated_daily_verify` の 1 本だけで、変更前の analyzer でも同じ版の組で同じに赤(analyzer と無関係)。冷えた cache では 60 秒の上限で時間切れの赤が 2 本出て、2 回目で通った。**測っていない**: apply_reception・record・runtime_env・turns_closure(2 本)・cloud_run_tests_tools・records_closure(規則の「触った所の検だけ」に従い広げていない)。
- 柵の factory の引数で渡す class(`defhandler fence-of [passable]` の `passable`)は、節を読む時に束ねられず不明のまま = 従来どおり節が答える。引数を handler の読みへ束ねる別の変更が要る。
- 複合の番の欄の部分(`(isinstance effect.key str)` など)は不明 = 従来どおり。
- `type(effect) is X` のような exact class の検は不明として読み、渡さない(安全側)。新しい variant を足す時は、`arrived` が「その class ちょうど」か「上限」かを決める必要がある(盲検 A の指摘。今は足していない)。
- 盲検 A・B は gpt-6-astra(優先 1)を起こす手段がなく、Opus 5.5(要求値 `opus`・effort `xhigh`)で代用した。実際に効いた model・effort は観測できない。
- 型検査は commit では撃たず、上の pyright の数は手の名指しの 1 回の実測。

## 10. 参照

`blind-meta.md`・`blind-input.md`・`claims-before-blind.md`・`input-hashes.txt`・`evidence/`(実行記録・差分・probe の source は `.txt` で置いた)。
