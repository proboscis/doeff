# 設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP — 結論・再現・修正(card ki-be596ac8276f)

完了の範囲: **実装完了まで**。盲検の前の主張は [design-before-blind.md](design-before-blind.md) に保存した(2026-10-11T14:56Z・盲検を
起こす前)。この本文はその主張を書き換えず、盲検の失敗例を試した結果と、直した境界・検・実行の記録を足す。

## 版

| 版 | 中身 |
| --- | --- |
| cf7ea9cc09a1da54c2bb4abbf984c0552818d837 | 基準版(L2062 の分かれ目) |
| a5d666e74be0b9556ae6fa224b30b50ca0478599(L2062) | この便の本体 1: 照らしが hy.core.hy_repr を読まない・一番外の読み込み 1 つの file の状態の写し(G・L・M)・code の読みを置き場に中身で覚える |
| 167d38ec290cf2e5bea1f260ac04f248de96fd80(L2066) | この便の本体 2: 歩みの digest の文字列を 1 度に hash・置き場の読みの形を型の集合で確かめる(綴りは同じ) |
| c135fa33011859378c94b4dd85f24fe6eac62772 → 9062ecd8436ccf086b3ea7aa9b30c3216f373aff(L2072) | 盲検 A の主の例の赤と直し・盲検 B の検 |
| aaa65d93f390dd94c1590b3cbb95f5993057191a → 54f1cdfa33cd2fca1988c8731fa959f8e1406f87(L2080) | 盲検 A の付録の赤と直し(読み込みの覚えの持ち主の thread と閉じ・code の読みの鍵の Hy の版)・linter の所見の直し |
| 67906435c3708455e175fb065a04bcc74144e8a8(L2081) | 読み込みの覚えの読みの口を構文木で縛る検(**実装の版** — 証拠の正常例はこの版か、doeff-hy の中身が同じ 54f1cdfa3) |

## 要件(固定・盲検の前と同じ)

1. test_emulated_screen.hy の読み込み(温まった後の process)で、照らしの足す CPU を、照らしを常に「今に合う」とした時との差 0.1 秒
   以内にする(card の受入 cac07f3cb — L2066 の時に依頼者が受け入れた)。
2. 不変条件 B1(packages/doeff-hy/architecture.hy)を破らない。D の条 1〜3 と別の top の package の値の検が緑のまま(受入 c65fb6ffb)。
3. 照らしの答えと digest の綴りを変えない。

## 盲検 A・B

入力・未加工の返答・起動の記録は [blind/](blind/)(meta = [blind/blind-meta.md](blind/blind-meta.md))。第 1 候補(codex・gpt-6-astra)は
この Pod の資格が無く、第 2 候補(Agent tool の新しい文脈の subagent・opus・xhigh)で起こした。A と B は同じ返事の中で同時に起こし、
互いの返答・設計者の自己評価・既知の失敗例を渡していない。実 model と effort の効きは観測していない。

## 失敗例の再現と修正

### A の主の例(S3・S4): 常駐の process で、読み込み済みの値の module の file を書き換えた後に作った記録

- **主張との食い違い**: S3 は「長く走る process で読み込みの間に file が変わっても、変える所はなし」と予想した。
- **再現**(probe = [evidence/probes/a_repro.py.txt](evidence/probes/a_repro.py.txt)): 常駐の process が tables を読み込んだ後(ROWS["b"] = 2)に
  tables.py を 20 に書き換え、同じ process が user_1 を compile し直すと、展開は process の中の古い表(2)を使い、記録の digest は今の
  file の文(20)から作られた。新しい process はその記録を今に合うと判じ、値 2 を使った(展開し直せば 20)。
  [A-repro-at-cf7ea9cc0.txt](evidence/a/A-repro-at-cf7ea9cc0.txt)・[A-repro-at-167d38ec.txt](evidence/a/A-repro-at-167d38ec.txt)
  の手順 3 = `{'compiled': [], 'values': {'user_1': 2}}`(対照の手順 4 = 20)。**基準版から在った**(この便が入れた物ではない)。
- **原因(知識の漏洩)**: 歩みは module の値を process の中から、値を覆う文と file の sha256 を今の disk から読み、2 つの時点を 1 つの
  digest に混ぜていた。「disk の今の中身 = module を実行した時の中身」という仮定は M の註 1 行にしか無く、どの責務も持っていなかった
  (共同の不変条件の持ち主が無い)。
- **修正**(L2072・赤の commit c135fa330): L が module の本体を実行する時に file の状態を覚え、M の歩みは file を digest に足す前と
  値を覆う文を読む前に、L に「実行した時と同じか」を聞く(L2080 で `loader_hooks.executed_source_is_current(path) -> bool` に整えた —
  状態の形(更新時刻 ns・大きさ)は L に隠す)。違えば import の途中と同じく digest を partial にし、覚えず、記録の行は None(新しい
  process は古いと判じて展開し直す)。B(bound_source)の口は変えない — A は「B の口が実行の時点の中身を受け取る形に変わる」と予想
  したが、M が B を呼ぶ前に確かめるので、B の「中身で引く純な読み」は隠したまま。
- **実際の波及**: L・M。予想(なし)との差は、持ち主の無かった共同の不変条件を L(実行した時の状態)と M(歩みの前の確かめ)に置いた
  ため。process の中の展開は古い値のまま(process の値と一致 — 正しい)で、防ぐのは古い値と新しい文の混じった記録が残る事。
- **検**: 赤 [red-A-at-c135fa330.txt](evidence/a/red-A-at-c135fa330.txt)(値 2)・直した版
  [A-repro-at-54f1cdfa3.txt](evidence/a/A-repro-at-54f1cdfa3.txt)(手順 3 = compile して 20)・直しを外す変異
  [mut-no-against-executed.txt](evidence/mut/mut-no-against-executed.txt)(赤 — 値 2)。

### B の例(S1): code の読みの entry を source の file の中身と関数の在処で引く形

- **再現**: B の差分 [B-diff-on-167d38ec.patch](evidence/b/B-diff-on-167d38ec.patch)(macro_use.py の code の読みの節だけ)は、盲検 B に
  提示した検を全部通った([B-diff-existing-tests.txt](evidence/b/B-diff-existing-tests.txt) — 60 passed)。それでも別の file の macro
  defhelper を替えて helper が compile し直された後、前の process の helper の読みを使い、使い手を今に合うと判じた
  ([B-probe-on-B-diff.txt](evidence/b/B-probe-on-B-diff.txt) の手順 3 = 値 1・基準 [B-probe-at-167d38ec.txt](evidence/b/B-probe-at-167d38ec.txt) = 10)。
- **主張との関係**: S1 の予想範囲(M の code の読みの節だけ)は当たったが、主張「entry の鍵は当たりやすさだけを決め、正しさは中の
  code の sha256 で決まる」を縛る検が無かった。範囲が狭いのは分離の成功ではなく、違反が検をすり抜けた事を示す。
- **修正**(c135fa330 → L2072): 検 `test_a_helper_built_by_a_changed_macro_of_another_file_is_stale` — main で緑、B の差分の上で赤
  ([B-guard-on-B-diff.txt](evidence/b/B-guard-on-B-diff.txt))。直した版の probe は 10([B-probe-at-54f1cdfa3.txt](evidence/b/B-probe-at-54f1cdfa3.txt))。

### A の付録 1(S3・S5): 読み込みの中で起きた thread と写した context が、読み込みの後も覚えの写しを使う

- **再現**(probe = [t_probe.py.txt](evidence/probes/t_probe.py.txt)): この Python(3.14.3 free-threaded)は `sys.flags.thread_inherit_context = 1`
  で、thread は起こした側の context を継ぐ。一番外の読み込みの中で起きた thread が、読み込みの後に reload した表(100)の下で user_1 を
  読むと、継いだ古い写しで覚えを確かめて今に合うと判じた([T-probe-at-54bd9355f.txt](evidence/appendix/T-probe-at-54bd9355f.txt) —
  `'compiled': [], 'value': 1`。本 thread の読みは 100)。基準版は thread でも 100
  ([T-probe-at-cf7ea9cc0.txt](evidence/appendix/T-probe-at-cf7ea9cc0.txt)・[thread-tests-at-cf7ea9cc0.txt](evidence/appendix/thread-tests-at-cf7ea9cc0.txt))。
  **この便(L2062 の読み込みの覚え)が入れた後退**。
- **主張との食い違い**: S5 は「覚えは context ごと。別の一番外の読み込みは自分の覚えを開く」と主張し、G の註にも「別の thread の
  import には効かない」と書いた。継いだ thread と写した context に値が残る事を見落とした。
- **修正**(L2080・赤の commit aaa65d93f): G の `ImportScope` が置いた thread(`_thread.get_ident`)と閉じたかを持ち、読みは
  `current_scope()`(置いた thread の中で、閉じる前だけ)を通す。L と M の置く口は閉じる時に閉じる。L2081 で、import_scope.py の外では
  `IMPORT_SCOPE` を set・reset の呼びにしか使わない事を構文木で縛った。
- **実際の波及**: G・L・M。予想(なし)との差は、G の公開契約の意図的な変更(読みの口 `current_scope()`・置いた口の `close()`)と、
  L・M の配線(閉じる・読みの口の差し替え)。覚えの寿命と持ち主の知識は G に集めた。main に同時に入った GC の card ki-de9d3593bbde
  (import_gc — 同じ包みで一番外の読み込みの間 GC の閾値を上げる)は thread ごとの数で持つので、継いだ thread の読み込みが自分の
  hold と release を組で持っても合う(test_import_gc を含む focused 123 passed)。
- **検**: 赤 [red-thread-at-aaa65d93f.txt](evidence/appendix/red-thread-at-aaa65d93f.txt)(thread・context の 2 本)・直した版
  [T-probe-at-54f1cdfa3.txt](evidence/appendix/T-probe-at-54f1cdfa3.txt)(100)・変異
  [mut-no-close.txt](evidence/mut/mut-no-close.txt)(閉じないと context が赤)・[mut-no-owner.txt](evidence/mut/mut-no-owner.txt)
  (置いた thread を確かめないと、読み込みの本体が終わらないうちに起きた thread が赤)・[mut-direct-read.txt](evidence/mut/mut-direct-read.txt)
  (直に読むと構文木の検が赤)。

### A の付録 2(S1・S4): code の読みの鍵に Hy の版が無い・読みの関数を別の file に移すと鍵が覆わない

- **確かめ**: code の読みの `called_modules` は hy.R の module の名を Hy の `hy.mangle` と `slashes2dots` で作るのに、entry の鍵
  (印・macro_use.py の中身・Python の版・在処・file)に Hy の版が無かった(鍵の組み立てを読んで確かめた)。版の違う Hy はこの Pod に
  無いので、Hy の版を変えた process で同じ entry を引く事は実行では示していない — 鍵の比べ(版を差し替えて entry の path が変わるか)で
  示した。
- **修正**(L2080): 鍵に `hy.__version__`。検 `test_the_code_reading_entry_is_keyed_by_the_hy_version` と、読みが大域の名で呼ぶ関数が全部
  `_own_digest` の覆う macro_use.py に在る事の検 `test_every_function_the_code_reading_calls_is_in_the_module_its_key_covers`。
- **変異**: 鍵から Hy の版を外す [mut-no-hy-key.txt](evidence/mut/mut-no-hy-key.txt)・`_mangle` を code_store.py へ移す
  [mut-moved-reading.txt](evidence/mut/mut-moved-reading.txt)(どちらも赤 — 後者は関数の名と移った先を言う)。
- **実際の波及**: M だけ(予想どおり)。

### A の付録 3(範囲外): 環境変数から組んだ dict を macro が読む

- **再現**(probe = [e_probe.py.txt](evidence/probes/e_probe.py.txt)): `(setv MODES {"a" (os.environ.get ...)})` を読む macro の使い手は、環境変数を
  替えた process でも 'plain' のまま今に合うと判じた(素の文字列の形 — 条 3 — は古いと判じて 'fancy')。
  [E-probe-at-cf7ea9cc0.txt](evidence/appendix/E-probe-at-cf7ea9cc0.txt)・[E-probe-at-54f1cdfa3.txt](evidence/appendix/E-probe-at-54f1cdfa3.txt)。
- **判断**: 基準版でも同じで、この便が入れた物でも、この便が変えた所でもない(辿れない大域の表を source の文で覆う規則 — bound_source
  と歩みの規則・card ki-38508e6e56fd 以前の決め)。B1 の条そのものの前からの穴として、この検証の範囲の外に置き、依頼者へ材料と一緒に
  渡した。この便の主張(S1〜S5)と受入 c65fb6ffb(条 1〜3 と別の top の package の値の検が緑)はこの穴に依らない。合格には数えない。

## 強制方法(実装後)

| 守る責務 | 強制方法と選んだ訳 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| B1: digest と照らしの答えを process をまたいで覚えない | 失敗ケースの検(子の process 2 つ以上で依った物を変え、古いと判じるか)— どの値を覚えてよいかは意味の判断で、静的には表せない | tests/test_macro_use_record.py の D の条の 4 本 | doeff-hy の pytest(着地の前の focused・日次の全体) | 新しい種類の覚えを足す便は条の文と検を読む必要がある。候補 (a) の素直な形は 4 本とも赤([mut-a-naive.txt](evidence/mut/mut-a-naive.txt)) |
| code の読みは code の中身で引く(S1) | 別の file の macro が作る helper の検 | `test_a_helper_built_by_a_changed_macro_of_another_file_is_stale` | 同上 | 盲検 B の形を縛る。中身で決まらない別の鍵の形は、同じ検か新しい検が要る |
| code の読みの鍵が読み方を覆う(S1・S4) | 鍵の Hy の版の検と、読みが呼ぶ関数の在処の検(関数を辿って `__module__` を比べる) | `test_the_code_reading_entry_is_keyed_by_the_hy_version`・`test_every_function_the_code_reading_calls_is_in_the_module_its_key_covers` | 同上 | 関数の中で import する外の library(Hy)は鍵の版が覆う前提。版を変えた Hy での実行は未検証 |
| 照らしだけが hy.core.hy_repr を読まない(S2・費用) | 3 つ目の process が読み込んでいない事の検 | `test_a_steady_check_process_does_not_import_hy_repr` | 同上 | 他の module が hy.repr を引く事は止めない([mut-hy-repr-import.txt](evidence/mut/mut-hy-repr-import.txt) で赤) |
| 利用者が hy.repr に関数を足した process は Hy の読みに置き場を使わない(S2) | 置き場に足さない事の検 | `test_a_custom_hy_repr_keeps_hy_readings_out_of_the_store` | 同上 | [mut-hy-repr-custom.txt](evidence/mut/mut-hy-repr-custom.txt) で赤 |
| 一番外の読み込み 1 つの写し・読み込みの間は読み直す(S3・費用) | stat の回数の検と、読み込みの間の変更に気づく検 | `test_one_import_stats_each_macro_file_once`・`test_a_file_changed_between_two_imports_is_noticed_by_the_second` | 同上 | 一番外の本体が長く走る process(本体で app を回す)では、本体の thread は本体が終わるまで 1 つの写しを使う(盲検の前から書いた限界) |
| 歩みの disk の読みは module を実行した時の file を写す(A) | 実行した時の状態は L だけが持ち、M は bool の口で聞く。常駐の process の検 | `loader_hooks.executed_source_is_current`・`macro_use._Walker._against_executed`・`test_a_record_made_after_the_file_of_a_loaded_module_changed_is_stale_in_a_new_process` | 同上 | 読みの口を通らずに読み込まれた module(包む前の起動・拡張)は確かめる材料が無く、確かめない |
| 覚えは置いた thread の中で、閉じる前だけ使う(S5) | G の `current_scope()` を読みの唯一の口にし、構文木の検で外の直の読みと別名を拒む。継いだ thread・写した context・本体の中の thread の検 | import_scope.py・`test_an_import_after_the_outer_import_ended_does_not_use_its_snapshot[thread・context]`・`test_a_thread_started_inside_a_running_import_does_not_share_its_snapshot`・`test_the_import_scope_variable_is_read_only_through_current_scope` | 同上 | 構文木の検は doeff_hy_bytecode_guard の Python の file だけを読む(package の外や `getattr` の文字列の読みは見ない) |
| digest の綴り | 長さを前に置いた並びの sha256 との一致の検 | `test_the_digest_is_the_sha256_of_the_length_prefixed_parts` | 同上 | 歩みが何を入れるかは縛らない |
| 置き場の entry の形 | 読んだ時に形を確かめ、違えば名指して除く | M `_facts_from_rows`・B `_binding_from_parts`・S `discard_entry` | 実行時 | 壊れた entry の検は無い |
| 外から書き換える属性の名・生の tuple を返さない | doeff-linter の DOEFF002・DOEFF006(基点に無い error を止める) | .pre-commit-config.yaml の doeff-linter の hook | commit の前の hook・着地の前の pre-commit | L2062・L2072 の時は基点の binary が無く数えられなかった(L2080 で初めて数え、直した) |
| 起動時の import の軽さ(G・L) | module の頭の註(起動時に読み込み済みの標準 library だけ — `_thread`・`contextvars`) | import_scope.py・loader_hooks.py の docstring | 人の読み | import の方向を静的に止める検は無い |

正常例は [tests/focused-at-54f1cdfa3.txt](evidence/tests/focused-at-54f1cdfa3.txt)(doeff-hy の 9 file・123 passed — L2081 の構文木の検の
他を全部含む)と、実装の版 67906435c でこの検証が足した検と覚え・hy.repr の検をまとめて走らせた
[tests/added-tests-at-67906435c.txt](evidence/tests/added-tests-at-67906435c.txt)(12 passed — 構文木の検を含む)。違反の例は
[evidence/mut/](evidence/mut/) の 9 つの変異(どれも狙った検が狙った文で赤 — 当てた差分・命令・終了の code を各 file に書いた・当てた後の
木は元に戻した)。

## 実際の波及と事前の予想

| シナリオ | 予想(盲検の前) | 実際 | 差の訳 |
| --- | --- | --- | --- |
| S1 storage | M(と S の末尾の定数) | 盲検 B の変更は M だけ(予想どおり)。付録 2 の直しも M だけ | 範囲は当たったが、主張を縛る検が無かった — 検を足した |
| S2 effects(hy.repr) | B の中で閉じる | 変更なし(盲検で失敗例は出なかった)。変異 2 つで検が守る事を示した | — |
| S3 effects(長く走る process) | なし | L・M(A の主の例 — 基準版から在った)・G・L・M(付録 1 — この便の後退) | 持ち主の無い共同の不変条件(実行した時の file)と、覚えの寿命の見落とし |
| S4 distribution | なし | 付録 2 の鍵(M)・A の主の例の記録が置き場を通って広がる形(L2072 で防いだ) | 鍵が読み方を全部は覆っていなかった |
| S5 concurrency | なし | G・L・M(付録 1) | free-threaded の 3.14 で thread が context を継ぐ事の見落とし |
| S6 hardware | 除外 | — | 照らしは機体に依らない。Python と Hy の版は鍵が覆う(Hy の版は L2080 で足した)。interpreter は 1 つで試せない |
| S7 simulation | 除外 | — | 照らしは時刻・乱数・外の service を読まない(file・環境・読み込み済みの module だけ) |

合格させるために予想を後から広げていない(上の「予想」は design-before-blind.md の表のまま)。

## 費用(要件 1 との関係)

- 受入の判断(L2066 の時・15 組の差 0.077 秒)は依頼者が済ませた。この検証は判断し直さない。
- L2072 の直し: 照らしの正味から照らしの中の GC を引くと、直しの有る無しで同じ(0.064〜0.097 秒と 0.068〜0.097 秒 —
  [sync-ab-gc.log](evidence/cost/sync-ab-gc.log))。process 全体の GC は 12 回・0.41〜0.49 秒で同じ([sync-ab-gcall.log](evidence/cost/sync-ab-gcall.log))。
  照らしの正味が 0.03 秒増えて見えたのは、GC の 1 回が照らしの中へずれたため。
- L2080 の直し: 照らし 799・歩み 17・覚えの確かめの stat 20・覚えを開いた回数 4 が直す前と同じ
  ([cost-walk-count-be59-base.txt](evidence/cost/cost-walk-count-be59-base.txt)・[cost-walk-count-be59.txt](evidence/cost/cost-walk-count-be59.txt))。
  照らしの正味 − GC の中央値は 0.117 秒と 0.123 秒(5 回ずつ・負荷 80〜90 — [ab-thread-scope.log](evidence/cost/ab-thread-scope.log))。
- 負荷 27〜52 の機体で受入の形を測ると、基点(167d38ec)も直した版も差 0.17〜0.23 秒(組ごとの差の標準偏差 0.23〜0.37 秒 —
  [accept-base-now.log](evidence/cost/accept-base-now.log)・[accept-livesync.log](evidence/cost/accept-livesync.log))。直しの有る無しで同じ
  様子なので機体の混み具合と読み、直しの費用とは数えない。測りの script は [evidence/probes/](evidence/probes/)。

## 未確認事項・限界

- 盲検の subagent の実 model と effort の効きは観測していない([blind/blind-meta.md](blind/blind-meta.md))。
- 型検査(pyright)は日次の段で、この検証では走らせていない。
- 版の違う Hy での code の読みの鍵の効きは、鍵の比べだけで示した(実行は未検証)。
- 一番外の本体が長く走る process では、本体の thread は本体が終わるまで 1 つの写しを使う(盲検の前から書いた限界。中で起きた thread
  は L2080 で自分の覚えを置く)。
- 付録 3(環境変数から組んだ dict)は範囲外として依頼者へ渡した。
- focused の 1 回目は、機体の負荷 97〜109 の時に子の process の 25 秒の時間切れで 2 本が落ちた
  ([focused-load-timeouts-at-e28e3e11c.txt](evidence/tests/focused-load-timeouts-at-e28e3e11c.txt))。単独の走らせ直しと、後の 2 回の focused
  (117 passed・123 passed)で緑。

## 証拠の索引

| path | 何か |
| --- | --- |
| evidence/probes/ | 再現の probe(a_repro・b_probe・t_probe・e_probe)・頭を書く走らせ役(run_probe.sh・run_pytest.sh)・変異の script(mutate.py)・費用の測り(net.py・walk_count.py・ab.sh・sync_ab.sh・accept.sh・accept_stats.py) |
| evidence/a/ | 盲検 A の主の例の再現(基準版・L2066・直した版)と赤の検 |
| evidence/b/ | 盲検 B の差分・提示した検の通過・B の検の赤・probe の値 |
| evidence/appendix/ | 付録 1(thread・context)と付録 3(環境変数の dict)の再現と赤 |
| evidence/mut/ | 違反の形の変異 9 つ(各 file の頭に変異の名・対象の版・命令・終了の code・当てた差分) |
| evidence/tests/ | focused の検の出力(L2072 の時・負荷の時間切れ・最終の版) |
| evidence/cost/ | 費用の測りの log |

頭に「頭は後から足した」とある file は、この会話の記録の命令を写した物(出力の本体は走らせた時の物のまま)。
evidence/probes/ の Python の script は、走らせた中身のまま `.py.txt` の名で置いた(repo の Python の lint の対象に入れない —
子の process の環境を組むために os.environ を読む)。走らせる時は `uv run --frozen python <名>.py.txt …`。
