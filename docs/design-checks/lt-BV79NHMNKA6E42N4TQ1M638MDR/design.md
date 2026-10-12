# 設計検証 lt-BV79NHMNKA6E42N4TQ1M638MDR — handler の節の操作の表(card ki-b4c015d8ba1f)

## この文書の目的

読み手が、次の 3 つを辿れる事:

- doeff-hy の handler の節の操作(resume・transfer・finish・reperform・pass・raise)の名と性質を、どこに置き、誰が読むか。
- その分け方が、操作を足す・変える・消す・展開の記録を使い回す、の変更に耐えるという主張と、盲検 A・B がそれを破った所。
- 破られた所の直しと、直した後に正常例が通り、失敗例が狙った理由で赤になる実行記録。

## 1. 完了の範囲と版

| 項目 | 内容 |
| --- | --- |
| 範囲 | 実装完了まで(設計・実装・検・通常の変更時の検の経路への接続) |
| 盲検の対象の版 | doeff main `10addc6473923419ef6acdb18dcb9044b87c7602`(L2175 で着地した最初の実装) |
| 盲検の後の直し | branch `wt/design-check-b4c0` の commit `6744ae538`(10addc647 の上・この文書と同じ登記で main へ入れる) |
| 盲検の前の主張 | `design-pre-blind.md`(sha256 `cc296f87243151bd32a7f275565ea240725eb484d47a4ddec1204874bc12ca24`・盲検の前に commit 009575a25 で push) |
| 盲検の入力 | `blind-input.md`(sha256 `0c3e1759f0562db42ecc3afb6adfad1e159478e49d411297f061832447e711e3` — 盲検の前の主張から、自己評価と既知の限界を外した物) |

## 2. 固定した要件

- R1: handler の節の操作の名と性質は、doeff-hy の 1 か所(表)だけに在る。
- R2: 表に操作を足すと、展開の時の「節のすべての道が終わるか」の検め・書き換え・node の import(実行時と型検査のための展開)・
  Raise / Absent の節の再開の検め(展開の時の名前の検めと、評価した時の型の検め)が、どれも追従する。
- R3: 読み手の片方へ表を介さずに名を足すと、検が赤になる。
- R4: 既存の handle / defhandler の展開(実行時・型検査のための展開)は変えない。

## 3. 構成(直した後)

```
packages/doeff-hy/src/doeff_hy/clause_ops.hy  ── 節の操作の表 CLAUSE-OPS(行 = ClauseOp)
   name・ending(Ending)・arity・returns・tail・nodes・static-nodes・rewrite
   引く口: named-op / clause-op / ops-ending / node-names
        │
        ├──▶ handle.hy
        │      _terminates             … 頭の名が表に在れば節の終わり(clause-op)
        │      _tail-resume-to-transfer … 末尾の操作を行の tail へ替える(resume → transfer)
        │      _rewrite-ops            … 行の rewrite へ渡し、returns の行には「再開した」の印を立てる
        │      _vm-imports             … 表の nodes / static-nodes と handler 自身の Pass を import
        │
        └──▶ clause_endings.hy
               ops-in / clause-ops      … 節が使う操作の名(clause-op)
               check-clause-by-name     … 展開の時: Raise / Absent × Ending.RESUMES、普通の effect × Ending.FINISHES
               check-clause-endings     … 評価の時: effect の型の Resumption × Ending(ops-ending)

tests/test_handle_clause_ops.py ── doeff_hy の全 .hy / .py に表の外の操作の名が無い事・表に足す / 消すと読み手が追従する事
```

## 4. 責務

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| clause-ops-table | 節の操作の表(`clause_ops.hy`) | 名・Ending・arity・returns(続きの後に節へ戻る)・tail(末尾で替える操作)・node・rewrite | 名の一覧 | `ClauseOp`・`Ending`・`CLAUSE-OPS`・`named-op`・`clause-op`・`ops-ending`・`node-names` |
| termination-check | 節のすべての道が操作で終わるか(`_terminates` など) | 制御の形 | どの名が操作か | `_check-clause-terminates` |
| clause-rewrite | 本体の操作を VM へ渡す form にする(`_rewrite-ops`・`_tail-resume-to-transfer`)と、実行の時の検めの印 `_doeff_clause_resumed` | 本体の歩き方・末尾の位置・印の名 | 操作ごとの form・末尾の替え先(表) | `_rewrite-ops` |
| handler-imports | handler の頭の import(`_vm-imports`) | handler 自身の Pass・WithHandler | 操作の node(表) | `_vm-imports` |
| clause-endings | 節の終わり方と effect の再開の宣言の照合(`clause_endings.hy`) | Raise / Absent の名・Resumption と Ending の照らし方 | 再開する操作の名(表の Ending) | SyntaxError・ClauseEndingError・RuntimeError |
| effect-resumption | effect の型の再開の宣言(`doeff_core_effects.effects`) | NEVER / ABSENT-AS-ONLY / REQUIRED | — | `resumption_of` |
| static-view | 型検査のための展開の切替(`static_view.py`) | ContextVar・HANDLER_STATIC_NAMES | — | `static_view()` |
| bytecode-guard | 展開の記録と .pyc の照らし | macro の閉包の digest・束縛の文 | — | 記録・照らし |
| regression-test | 検(`tests/test_handle_clause_ops.py`) | 表と読み手の対応・表の外の名の禁止 | — | pytest |

effects: どれも展開の時の form の組み立てと検めだけで、外の世界の副作用を持たない。lifetime: 資源を持たない(表は読み込みの時に作る不変の tuple)。

## 5. 変更シナリオ — 盲検の前の主張と実際

| シナリオ | 盲検の前の主張(予想の範囲) | 実際 | 差の理由 |
| --- | --- | --- | --- |
| S1(effects)続きへ例外を投げる操作 throw を足す | clause-ops-table の 1 行と rewrite だけ。他は変えない | TransferThrow だけの throw は主張どおり。resume と同じ扱いの throw(非末尾 ResumeThrow・末尾 TransferThrow — 盲検 A)は、10addc647 では handle.hy の末尾の替えと印の約束も変える必要があった。直した後は表の 2 行(throw・transfer-throw)だけで足せる | 「末尾で替える先」と「続きの後に節へ戻るか」という操作の性質が表の外(handle.hy の名の直書きと resume の行の rewrite の中)に在った = 隠すはずの知識の漏れ。表の欄(tail・returns)に移した(公開契約 ClauseOp の意図した拡張) |
| S2(effects)続きを捨てる新しい操作を足す | clause-ops-table の 1 行(Ending.FINISHES)だけ | 主張どおり(:finish-reason の要否は clause-endings が Ending で照らす)。盲検は S2 を破らなかった | — |
| S3(effects)既存の操作の展開を変える・退役する | 行の rewrite / nodes / static-nodes を替えるか行を消すだけ | 10addc647 では、名の集まりを写した新しい読み手(盲検 B)が退役に追従しないまま検を通った。盲検 A は「型検査の展開の resume の node を替えても末尾の resume には効かない(transfer の行が展開する)」を指摘 — 表の中の 2 行で済み、範囲の外へは漏れない | 表の外の名の写しを禁じる検が無かった(R3 の検は 3 つの既知の読み手の振る舞いしか見なかった)。全 module の綴りを拾う検を足した |
| S4(storage)表の行を変えた版で前の .pyc を使う | 前の記録は今に合わないと判じられる。bytecode-guard は変えない | 主張どおり(関係の無い行の変更では今に合うまま・行と rewrite を変えると古いと判じる)。盲検は S4 を探していない | — |
| 不適用の軸 | hardware・distribution・concurrency・simulation | — | 展開は 1 process の compile の時の form の組み立てで、機体・並行・時刻・乱数に触らない |

## 6. 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 名と性質は表 1 つ(R1・R3) | doeff_hy の全 `.hy`(Hy の reader で文字列と quote した記号)と全 `.py`(ast の文字列の定数)を拾い、表の名と同じ綴りを表の外に書かない。外す綴りは理由つきの 1 行(macros.hy の "raise" = Python の文の頭) | `tests/test_handle_clause_ops.py::test_no_module_writes_an_operation_name_outside_the_table` | root の pytest(4 時間に 1 度の全体の回の root の段)・手の焦点の検 | 綴りを組み立てる(`(+ "res" "ume")`)・doeff_hy の外の module に写す・まだ表に無い名を写す(表に足した時に赤になる)は拾わない |
| 読み手は表に無い名を操作と扱わない | handle.hy と clause_endings.hy に書いた綴りごとに、`_terminates`・`clause_ops`・`_rewrite_ops`(2 つの展開)が表どおりに振る舞う | `test_readers_name_operations_only_through_the_table` | 同上 | 読み手は 2 module |
| 表に足すと読み手が追従する(R2) | 表に行を足し(monkeypatch)、展開・実行・Raise / Absent の検め(展開の時・評価の時)・2 つの展開の import・末尾の替え・「再開した」の印を確かめる | `test_added_operation_*`(4 件)・`test_added_returning_operation_marks_the_clause_resumed`・`test_added_operation_with_a_tail_is_replaced_at_the_tail` | 同上 | 行の returns を誤って宣言すると(戻る node なのに False)実行の時に RuntimeError — 黙っては通らないが、展開の時には分からない |
| 表から消すと操作でなくなる | 行を消し、3 つの読み手と展開の時の SyntaxError を確かめる | `test_a_removed_operation_is_no_longer_an_operation_anywhere` | 同上 | — |
| 行の整合 | tail の先は同じ arity の戻らない行・rewrite の無い行は戻らず替えない | `test_table_rows_are_consistent` | 同上 | — |
| Ending どおりの照合 | 表の行ごとに Raise / Absent / 普通の effect の節を展開する | `test_every_operation_in_the_table_is_checked_by_its_ending` | 同上 | — |
| 展開を変えない(R4) | 253 file × 2 つの展開の前後の比べ | `evidence/r4-expand-compare.txt` | 1 回の実測(常設ではない) | 常設の検は既存の展開の形の検(test_handle_macro・test_static_check_defhandler・test_tail_resume_tco)が一部を見る |
| 型 | `ClauseOp` は凍結の dataclass(欄の型の注記)・`Ending` は StrEnum | `clause_ops.hy` | 実行時は dataclass の作りだけ | 欄の型は実行の時に検めない。handle.hy が module の頭で import するので defrecord(macros を require)を使えない |

## 7. 盲検 A・B

起動の記録は `evidence/blind-meta.md`(sha256 `737849d968ed…`)。要約: 第 1 候補 gpt-6-astra はこの環境の起動手段(Agent tool)で選べず、
第 2 候補 `opus`・effort `xhigh` で、A・B を新しい文脈で同時に起こした(fork・resume なし・互いの返答は渡していない)。観測した model と
effort は未確認。A = agentId a7e8b44a4afdfc9c1(520 秒)・B = agentId a174fea40db5986ca(1,028 秒)。

| 盲検 | 依頼文 | 返答(未加工) | 失敗例 |
| --- | --- | --- | --- |
| A | `evidence/blind-a-prompt.md`(be79823d…) | `evidence/blind-a-reply.md`(984105cc…) | resume と同じ扱いの throw: (a) 末尾の替えが handle.hy に名で直に在る (b) 「再開した」の印の約束が resume の行の rewrite の中に在る (c) 名を直に書く直しを R3 の検が見逃す |
| B | `evidence/blind-b-prompt.md`(876c3df6…) | `evidence/blind-b-reply.md`(56fcd4e1…) | handle.hy に名の集まりを写した新しい読み手(続き k を 2 度使う検め)が、表への追加(throw)と退役(pass)に追従しないまま全部の検を通る |

## 8. 再現と修正

| 記録 | 版 | 命令 | 終了 | 観測 |
| --- | --- | --- | --- | --- |
| `evidence/a-repro-before.log` | 10addc647 | `uv run python -I evidence/blind-a/exp_throw.py.txt` | 0 | 印を立てない ResumeThrow の throw は RuntimeError「どれにも届かずに抜けた」。印を写すと走るが末尾の throw は ResumeThrow のままで、生成器が effect 10 / 100 / 400 回で +12 / +102 / +402(末尾の resume は +2) — A の (a)(b) 成立 |
| `evidence/a-hardcoded-tco-test-before.log` | 10addc647 + `blind-a/hardcoded_tco.patch` | `uv run pytest -q tests/test_handle_clause_ops.py` | 0 | 14 passed — 名を直に書く直しを検が拒まない(A の (c) 成立) |
| `evidence/b-failure-test-before.log` | 10addc647 + `blind-b/failure.diff` | 同上 | 0 | 12 passed — B の差分を検が拒まない |
| `evidence/b-failure-demo-before.log` | 同上 | `uv run python -I -W ignore::DeprecationWarning evidence/blind-b/demo.py.txt` | 0 | 表に throw を足すと新しい検めだけが追従せず one-shot violation、pass を消しても新しい検めは pass を k を使う操作と数える — B 成立 |
| 直し | 6744ae538 | — | — | ClauseOp に returns・tail を足し、handle.hy の印と末尾の替えを表から読む。全 module の綴りの検を足した(§6) |
| `evidence/after-test.log` | 6744ae538 | `uv run pytest -q tests/test_handle_clause_ops.py tests/test_handle_scope.py packages/doeff-hy/tests/test_tail_resume_tco.py` | 0 | 77 passed(resume と同じ扱いの throw が表の 2 行で足せる・退役・行の整合を含む) |
| `evidence/after-related-test.log` | 6744ae538 | `uv run pytest -q tests/test_handle_macro.py packages/doeff-hy/tests/test_static_check_defhandler.py` | 0 | 37 passed |
| `evidence/after-adr-r15-test.log` | 6744ae538 | ADR-DOE-CORE-EFFECTS-003 の R15 の検 4 件(-k) | 0 | 4 passed |
| `evidence/b-failure-test-after.log` | 6744ae538 + `blind-b/failure.diff` | `uv run pytest -q tests/test_handle_clause_ops.py` | 1 | `test_no_module_writes_an_operation_name_outside_the_table` だけが赤(21 passed) — B の差分を狙った理由で拒む |
| `evidence/a-hardcoded-tail-test-after.log` | 6744ae538 + `evidence/a-hardcoded-tail-after.diff`(表に throw・transfer-throw を足し、末尾の替えを handle.hy に名で直に書く) | 同上 | 1 | `test_no_module_writes_an_operation_name_outside_the_table` が赤。ほかの 4 件の赤は、差分が足した行が検の fixture の行と同じ名でぶつかった物(狙いの検めではない) |
| `evidence/a-returns-flag-after.log` | 6744ae538 | `uv run python -I evidence/scripts/returns_flag.py.txt` | 0 | ResumeThrow の行を returns=False と誤って宣言すると RuntimeError(黙って通らない)、True なら答え -1 |
| `evidence/s4-storage-after.log` | 6744ae538 | `uv run python -I evidence/scripts/s4_storage.py.txt <作業木>` | 0 | 変えない・関係の無い註 → 今に合う(current True)。resume の行の nodes・transfer の rewrite を変える → 古い(current False) |
| `evidence/r4-expand-compare.txt` | 10addc647 ⇔ 6744ae538 | `evidence/scripts/expand_snapshot.py.txt`(12 本に分けて) | — | 253 file × 2 で、違うのは変えた handle.hy 自身の展開だけ |

## 9. 予測と実際の変更範囲

| シナリオ | 予測(盲検の前) | 実際の直し | 区別 |
| --- | --- | --- | --- |
| S1・S3 | clause-ops-table だけ | clause-ops-table(欄 returns・tail を足す)・clause-rewrite(印と末尾の替えを表から読む)・regression-test | 盲検の前の主張は、10addc647 では成り立たなかった(盲検 A の (a)(b)・盲検 B)。直しは、漏れていた知識を表へ移す 1 回の公開契約の拡張で、直した後は S1(resume と同じ扱いの throw を含む)・S3 が表だけで済む事を検で確かめた。予想の範囲を後から広げてはいない — 盲検の前の主張(design-pre-blind.md)は書き換えずに残す |
| S2・S4 | clause-ops-table だけ / 変更なし | 変更なし | 主張どおり |

## 10. 未確認・限界

- 盲検の model と effort は要求値(opus・xhigh)で、実際の値は観測できなかった。第 1 候補 gpt-6-astra は起動手段が無く試していない。
- 全 module の綴りの検は、doeff_hy の package の中の `.hy` / `.py` だけを見る。doeff-linter(Rust)の `business_fakes.rs` には節の操作の名の
  match が残る(言語が違い表から読めない — card ki-b4c015d8ba1f の範囲の外)。綴りを組み立てる書き方は拾わない。
- 行の returns の誤った宣言は、展開の時には分からず、実行の時の RuntimeError で止まる(黙っては通らない)。
- 全体の検・型検査(pyright・doeff-hy-check の全 module)は走らせていない(4 時間に 1 度の全体の回が測る)。test_macro_use_record の 2 件は
  機体の負荷で subprocess が時間切れになる(直す前の版でも同じ — 最初の実装の時に確かめた)。
- 品質検査(code-quality --scope changed・fast の段)は passed。型は含まない。
