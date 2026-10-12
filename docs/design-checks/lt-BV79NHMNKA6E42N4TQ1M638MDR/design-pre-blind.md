# 設計検証 lt-BV79NHMNKA6E42N4TQ1M638MDR — 盲検の前の主張(card ki-b4c015d8ba1f)

完了の範囲: **実装完了まで**。対象の実装は doeff main `10addc6473923419ef6acdb18dcb9044b87c7602`(L2175・親 `4973b99753cdf6dc50daeafff4b025c8ca75ca7d`)。
この文書は盲検 A・B を起こす前に書き、sha256 を `design-pre-blind.sha256` に残す。盲検の後の結論はこの文書を書き換えず、`design.md` に書く。

## 1. 固定した要件

- R1: handler の節の操作(resume・transfer・finish・reperform・pass・raise)の名と性質は、doeff-hy の 1 か所(表)だけに在る。
- R2: 表に操作を足すと、展開の時の「節のすべての道が終わるか」の検め・書き換え・node の import(実行時と型検査のための展開)・
  Raise / Absent の節の再開の検め(展開の時の名前の検めと、handle / defhandler を評価した時の型の検め)が、どれも追従する。
- R3: 読み手(handle.hy・clause_endings.hy)の片方へ表を介さずに名を足すと、検が赤になる。
- R4: 既存の handle / defhandler の展開(実行時・型検査のための展開)は変えない。

## 2. 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| clause-ops-table | 節の操作の表(`packages/doeff-hy/src/doeff_hy/clause_ops.hy`) | 操作の名・終わり方 `Ending`(RESUMES / FINISHES / FORWARDS / RAISES)・引数の数・実行時と型検査の展開の node の名・書き換え | 名の一覧そのもの(呼び手は表を写さず関数で引く) | `ClauseOp`・`Ending`・`CLAUSE-OPS`・`named-op`・`clause-op`・`ops-ending`・`node-names` |
| termination-check | 節のすべての道が操作で終わるか(`handle.hy` の `_terminates`・`_seq-terminates`・`_check-clause-terminates`・`clause_endings.hy` の `match-terminates?`・`try-terminates?`) | if / cond / match / try / when … の制御の形 | どの名が操作か(表に聞く) | `(_check-clause-terminates etype body)` → SyntaxError か None |
| clause-rewrite | 節の本体の操作を VM へ渡す form にする(`handle.hy` の `_rewrite-ops`・末尾の resume を transfer にする `_tail-resume-to-transfer`) | 本体の歩き方・末尾の位置 | 操作ごとの form(表の行の rewrite が持つ) | `(_rewrite-ops form)` |
| handler-imports | handler の展開の頭の import(`handle.hy` の `_vm-imports`) | handler 自身が使う Pass・WithHandler | 操作の node(表の nodes / static-nodes) | `(_vm-imports)` → import の form |
| clause-endings | 節の終わり方と effect の再開の宣言の照合(`clause_endings.hy` の `ops-in`・`clause-ops`・`check-clause-by-name`・`check-clause-endings`・`check-clause-endings-once`・`fell-through`) | 名前で分かる effect(Raise / Absent)・`Resumption` と `Ending` の照らし方・`ClauseEndingError` | どの名が再開する操作か(表の Ending に聞く) | 展開の時 SyntaxError・評価の時 ClauseEndingError・実行の時 RuntimeError |
| effect-resumption | effect の型の再開の宣言(`doeff_core_effects.effects` の `Resumption`・`resumption-of`) | Raise = NEVER・Absent = ABSENT-AS-ONLY・宣言なし = REQUIRED | — | `(resumption-of type)` |
| static-view | 型検査のための展開の切替と module の頭の補助の import(`static_view.py`) | 切替の ContextVar・HANDLER_STATIC_NAMES | — | `static-view-enabled`・`static_view()` |
| bytecode-guard | 展開の記録と .pyc の照らし(`doeff_hy_bytecode_guard`) | macro の閉包の digest・束縛の文 | — | 記録・照らし |
| regression-test | 検(`tests/test_handle_clause_ops.py`) | 表と読み手の対応 | — | pytest |

effects: どの module も展開の時の form の組み立てと検めだけで、外の世界の副作用を持たない(clause-endings の評価の時の検めは例外を上げるだけ)。
lifetime: 資源を持たない(表は module の読み込みの時に 1 度作る不変の tuple)。

## 3. 変更シナリオと主張(盲検の前)

### S1(effects): 続きへ例外を投げる操作 `(throw 例外)` → `TransferThrow` を足す

- 主張: 足すのは clause-ops-table の 1 行(`ClauseOp :name "throw" :ending Ending.RESUMES :arity 1 :nodes #("TransferThrow") :static-nodes #("TransferThrow") :rewrite …`)と、その rewrite の関数だけ。
  termination-check・clause-rewrite・handler-imports・clause-endings・static-view は変えない。Raise / Absent の節の throw は、展開の時
  (名前)と評価の時(別名の Raise・型の宣言)の両方で断られる。型検査のための展開も TransferThrow を import する。
- 予想の範囲: clause-ops-table(と、その操作を使う検を足すなら regression-test)。
- 前提: 新しい操作は節の操作の形 `(名 引数 …)` で書き、終わり方が 4 つの Ending のどれかに当たる。

### S2(effects): 続きを捨ててスコープを値で終える新しい操作(例 `(abort 値)`)を足す

- 主張: clause-ops-table の 1 行(`:ending Ending.FINISHES`)だけ。普通の effect の節は :finish-reason が要り、:finish-reason を書いたのに
  打ち切る操作が無い節は断られる — どちらも clause-endings が Ending で照らすので変えない。
- 予想の範囲: clause-ops-table。

### S3(effects): 既存の操作の展開の形を変える(例 型検査のための展開の resume を別の typed の node に替える・pass を退役する)

- 主張: 行の rewrite・nodes・static-nodes を替えるか、行を消すだけ。handler-imports は表から import の名を作るので変えない。
  行を消した操作は、どの読み手からも操作でなくなる(展開の時に「節が終わらない」SyntaxError)。
- 予想の範囲: clause-ops-table。

### S4(storage): 表の行を変えた版で、前の版が書いた .pyc(展開の結果の置き場)を使う

- 主張: 展開の記録(bytecode-guard)は macro の閉包を名の大域と束縛の文で辿るので、表の行(`(setv CLAUSE-OPS …)` の文と、そこから届く
  rewrite の関数)を変えると、前の記録は今に合わないと判じられる。表と関係の無い行の変更では今に合うまま。
- 予想の範囲: 変更なし(bytecode-guard は表を知らない)。

### 不適用の軸

- hardware: 展開は compile の時の form の組み立てで、機体に依らない。
- distribution: 展開と検めは 1 process の中の compile の時の処理で、機体をまたがない。
- concurrency: 表は読み込みの時に作る不変の tuple で、展開の時に書き換えない。
- simulation: 時刻・乱数・外の世界に触らない。

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 名と性質は表 1 つ(R1) | 読み手は表を関数(`clause-op`・`ops-ending`・`node-names`)で引く。handle.hy と clause_endings.hy に文字列か quote した記号で書いた綴りを全部拾い、表に無い名はどの読み手(`_terminates`・`clause-ops`・`_rewrite-ops` の 2 つの展開)も操作として扱わない事を検で確かめる | `tests/test_handle_clause_ops.py::test_readers_name_operations_only_through_the_table` | root の pytest(4 時間に 1 度の全体の回の root の段)・手の焦点の検 | 綴りを文字列でも quote でもない形(例 `(.join "" ["thr" "ow"])`・別 module の名の一覧)で書く読み手は拾わない。読み手は handle.hy と clause_endings.hy の 2 つに限る |
| 表に足すと読み手が追従する(R2) | 検で表に行 throw を足し(monkeypatch)、展開・実行・Raise / Absent の検め(展開の時・評価の時)・2 つの展開の import を確かめる | `tests/test_handle_clause_ops.py` の `test_added_operation_*` 4 件 | 同上 | 足した行が 4 つの Ending の外の性質(例 1 回だけ再開)を持つ時は、Ending を足す変更になり、clause-endings の照らしも変える(契約の拡張) |
| 表のどの行も Ending どおりに照らされる | 表の行ごとに Raise / Absent / 普通の effect の節で展開し、断る・通すを Ending で決める | `test_every_operation_in_the_table_is_checked_by_its_ending` | 同上 | — |
| 展開を変えない(R4) | 253 file × 2 つの展開の直す前後の比べ(1 回の実測・常設の検ではない) | 実行記録 | 1 回 | 常設の検ではない。既存の展開の形の検(test_handle_macro・test_static_check_defhandler・test_tail_resume_tco)が一部を常に見る |
| 型 | `ClauseOp` は凍結の dataclass(欄の型の注記つき)・`Ending` は StrEnum | `clause_ops.hy` | 実行時は dataclass の作りだけ(型は検めない) | 欄の型は実行の時に検めない |
