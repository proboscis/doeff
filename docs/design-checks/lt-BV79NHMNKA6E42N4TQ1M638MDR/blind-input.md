# 盲検の入力 — doeff-hy の handler の節の操作の表

対象: doeff の作業木(この file を置いた作業木)の HEAD = `10addc6473923419ef6acdb18dcb9044b87c7602`。実コードはこの作業木の中を読んでよい。
共有の checkout(`~/repos/doeff`)と他の作業木は変更しない。この作業木の中の変更と /tmp の下の file は自由(使い捨て)。

## 1. 背景

doeff-hy は Hy の macro で effect handler を書く仕組み。`defhandler` / `handle` の節は `(EffectType [欄 …] 本体 …)` の形で、本体は
節の操作のどれかで終わらなければならない(ADR-DOE-CORE-EFFECTS-003 R15):

| 操作 | 意味 |
| --- | --- |
| `(resume 値)` | 続き k を値で再開する(handler は残る) |
| `(transfer 値)` | 続き k を値で再開し、handler を外す |
| `(finish 値)` | 続きを捨て、handler を置いたスコープの答えを値にする |
| `(reperform effect)` | effect と続きを外の handler へ渡す |
| `(pass)` | 旧い形の reperform(DeprecationWarning) |
| `(raise 例外)` | Python の raise(書き換えない) |

effect の型は再開の扱いを宣言する(`doeff_core_effects.effects.Resumption`): Raise = NEVER(誰も再開しない)・Absent = ABSENT-AS-ONLY・
宣言なし = REQUIRED。Raise / Absent の節が続きを再開すると誤り、普通の effect の節を打ち切るには `:finish-reason "理由"` が要る。
検めは 3 つの時点: 展開の時(名前で分かる物 — SyntaxError)・handle / defhandler を評価した時(effect の型の宣言 — ClauseEndingError)・
実行の時(resume も finish もせずに節を抜けた — RuntimeError)。

## 2. 固定した要件

- R1: 節の操作の名と性質は、doeff-hy の 1 か所(表)だけに在る。
- R2: 表に操作を足すと、展開の時の「節のすべての道が終わるか」の検め・書き換え・node の import(実行時と型検査のための展開)・
  Raise / Absent の節の再開の検め(展開の時の名前の検めと、評価した時の型の検め)が、どれも追従する。
- R3: 読み手(handle.hy・clause_endings.hy)の片方へ表を介さずに名を足すと、検が赤になる。
- R4: 既存の handle / defhandler の展開(実行時・型検査のための展開)は変えない。

## 3. 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| clause-ops-table | 節の操作の表(`packages/doeff-hy/src/doeff_hy/clause_ops.hy`) | 操作の名・終わり方 `Ending`(RESUMES / FINISHES / FORWARDS / RAISES)・引数の数・実行時と型検査の展開の node の名・書き換え | 名の一覧そのもの(呼び手は表を写さず関数で引く) | `ClauseOp`・`Ending`・`CLAUSE-OPS`・`named-op`・`clause-op`・`ops-ending`・`node-names` |
| termination-check | 節のすべての道が操作で終わるか(`handle.hy` の `_terminates`・`_seq-terminates`・`_check-clause-terminates`・`clause_endings.hy` の `match-terminates?`・`try-terminates?`) | if / cond / match / try / when … の制御の形 | どの名が操作か(表に聞く) | `(_check-clause-terminates etype body)` → SyntaxError か None |
| clause-rewrite | 節の本体の操作を VM へ渡す form にする(`handle.hy` の `_rewrite-ops`・末尾の resume を transfer にする `_tail-resume-to-transfer`) | 本体の歩き方・末尾の位置 | 操作ごとの form(表の行の rewrite が持つ) | `(_rewrite-ops form)` |
| handler-imports | handler の展開の頭の import(`handle.hy` の `_vm-imports`) | handler 自身が使う Pass・WithHandler | 操作の node(表の nodes / static-nodes) | `(_vm-imports)` → import の form |
| clause-endings | 節の終わり方と effect の再開の宣言の照合(`clause_endings.hy` の `ops-in`・`clause-ops`・`check-clause-by-name`・`check-clause-endings`・`check-clause-endings-once`・`fell-through`) | 名前で分かる effect(Raise / Absent)・`Resumption` と `Ending` の照らし方・`ClauseEndingError` | どの名が再開する操作か(表の Ending に聞く) | 展開の時 SyntaxError・評価の時 ClauseEndingError・実行の時 RuntimeError |
| effect-resumption | effect の型の再開の宣言(`packages/doeff-core-effects/doeff_core_effects/effects.py` の `Resumption`・`resumption_of`) | Raise = NEVER・Absent = ABSENT-AS-ONLY・宣言なし = REQUIRED | — | `resumption_of(type)` |
| static-view | 型検査のための展開の切替と module の頭の補助の import(`packages/doeff-hy/src/doeff_hy/static_view.py`) | 切替の ContextVar・HANDLER_STATIC_NAMES | — | `static_view_enabled`・`static_view()` |
| bytecode-guard | 展開の記録と .pyc の照らし(`packages/doeff-hy/src/doeff_hy_bytecode_guard/`) | macro の閉包の digest・束縛の文 | — | 記録・照らし |
| regression-test | 検(`tests/test_handle_clause_ops.py`) | 表と読み手の対応 | — | pytest |

## 4. 変更シナリオと設計者の主張

- S1(effects): 続きへ例外を投げる操作 `(throw 例外)` → `TransferThrow` を足す。主張: 足すのは clause-ops-table の 1 行と rewrite の関数だけ。
  termination-check・clause-rewrite・handler-imports・clause-endings・static-view は変えない。Raise / Absent の節の throw は展開の時と評価の時の
  両方で断られ、型検査のための展開も TransferThrow を import する。前提: 新しい操作は `(名 引数 …)` の形で、終わり方が 4 つの Ending のどれか。
- S2(effects): 続きを捨ててスコープを値で終える新しい操作(例 `(abort 値)`)を足す。主張: clause-ops-table の 1 行(Ending.FINISHES)だけ。
  :finish-reason の要否は clause-endings が Ending で照らすので変えない。
- S3(effects): 既存の操作の展開の形を変える(例 型検査のための展開の resume の node を替える・pass を退役する)。主張: 行の rewrite・nodes・
  static-nodes を替えるか行を消すだけ。消した操作はどの読み手からも操作でなくなる。
- S4(storage): 表の行を変えた版で、前の版が書いた .pyc を使う。主張: 展開の記録は macro の閉包を名の大域と束縛の文で辿るので、表の行を
  変えると前の記録は今に合わないと判じられる。表と関係の無い行の変更では今に合うまま。bytecode-guard は変えない。

## 5. 強制方法(検)

| 守る事 | 検 |
| --- | --- |
| 名と性質は表 1 つ | `tests/test_handle_clause_ops.py::test_readers_name_operations_only_through_the_table` — handle.hy と clause_endings.hy に文字列か quote した記号で書いた綴りを拾い、表に無い名はどの読み手(`_terminates`・`clause_ops`・`_rewrite_ops` の 2 つの展開)も操作として扱わない |
| 表に足すと読み手が追従する | 同 file の `test_added_operation_*` 4 件 — 表に行 throw を足し(monkeypatch)、展開・実行・Raise / Absent の検め・2 つの展開の import を確かめる |
| 表のどの行も Ending どおりに照らされる | `test_every_operation_in_the_table_is_checked_by_its_ending` |
| 既存の展開の形 | `tests/test_handle_macro.py`・`tests/test_handle_scope.py`・`packages/doeff-hy/tests/test_tail_resume_tco.py`・`packages/doeff-hy/tests/test_static_check_defhandler.py`・ADR `docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy` の R15 の検 |

検の走らせ方(この作業木で): `uv run pytest -q tests/test_handle_clause_ops.py`(約 10 秒)。ADR の検は
`uv run pytest -q docs/adr/defadr_doeff_core_effects_003_absent_and_raise.hy -k "clause_must_end_explicitly or clause_endings_checked"`。
全体の検(`uv run pytest` を引数なし)は走らせない。
