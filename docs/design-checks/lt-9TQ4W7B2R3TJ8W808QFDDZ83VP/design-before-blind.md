# 設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP — 盲検の前の主張(card ki-be596ac8276f)

完了の範囲: **実装完了まで**(doeff L2062 a5d666e74・L2066 167d38ec2 が main に在る。agora-controllers の pin は L6100・L6104)。
基準版: cf7ea9cc09a1da54c2bb4abbf984c0552818d837(L2062 の分かれ目)。実装版: 167d38ec290cf2e5bea1f260ac04f248de96fd80。

## 要件(固定)

- test_emulated_screen.hy の読み込み(2 つ目からの process・.pyc が温まった後)で、.pyc の記録の照らしが足す CPU を、照らしを常に
  「今に合う」とした時との差 0.1 秒以内にする(card ki-be596ac8276f の受入 cac07f3cb)。
- 古い展開を見逃さない不変条件 B1(packages/doeff-hy/architecture.hy)を破らない。D の条 1〜3 と、別の top の package の値の検が緑
  のまま(受入 c65fb6ffb)。B1 は「digest も照らしの答え(合う / 古い)も process をまたいで覚えない。覚えてよいのは中身で決まる
  純な読みだけ」。
- 照らしの答え(どの記録を古いと判じるか)を変えない。digest の値(綴り)を変えない — 変えると全部の .pyc と置き場の記録が古くなる。

## 責務(module)

| id | file | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G | doeff_hy_bytecode_guard/import_scope.py | 一番外の読み込み 1 つの間の覚えの持ち主 | 覚えの形(file の path → 状態・確かめた digest の覚え) | なし(形だけ) | `ImportScope`・`IMPORT_SCOPE`(ContextVar) | なし、値を持つだけ | context ごと。開いた口が閉じる | 標準 library しか import しない(起動時に読む) |
| L | doeff_hy_bytecode_guard/loader_hooks.py | SourceFileLoader の口の包み(読み・compile・本体の実行) | どの口でいつ照らすか・一番外の本体の実行の間だけ覚えを置く事 | 照らしの中身 | `install`・`record_is_current_here`・`file_sha256` ほか | 読みの口の file の読み書き・.pyc の書き | process | 一番外の `exec_module` が覚えを開け閉めする(入れ子は外の覚え)。起動時は軽い module だけ |
| M | doeff_hy_bytecode_guard/macro_use.py | 使った macro の記録の作り方と照らし方・閉包の digest の歩み・process の中の digest の覚えと確かめ・code の読み(CodeFacts)とその置き場の entry | digest の綴り(長さを前に置いた文字列の並びの sha256)・歩みの規則・覚えの確かめ方・code の読みの鍵(marshal の sha256)と entry の形 | 歩みの中・entry の形 | `record_is_current`・`macro_digest`・`value_digest`・`MacroRecording` | 歩みの中の import・file の stat と sha256・置き場の entry の読み書き | 覚えは process の間(digest)・置き場の entry は 7 日の掃除まで | B1・digest の綴り・覚えを使うのは依った file が覚えた時の状態のままの時だけ(読み込み 1 つの中は 1 つの写し) |
| B | doeff_hy_bytecode_guard/bound_source.py | 辿れない大域を覆う source の文の読みと、その置き場の鍵 | Hy と Python の読み方・鍵の欄(Hy の版と hy.repr の登録の表) | 読みの形 | `binding_of` | source の読み・置き場の entry の読み書き | process の覚え・置き場の entry | 鍵は読みを変える物を全部持つ。hy.core.hy_repr が読み込まれていなければ既定の表の印(読み込まない) |
| S | doeff_hy_bytecode_guard/code_store.py | 置き場の並び・entry の読み書き・壊れた entry の除き | dir の並び・種類の末尾(.code・.imports・.binding・.facts)・書き方(一時 file → rename) | 各種類の中身の形(使い手が持つ) | `store_dir`・`content_key`・`entry_path`・`read_entry`・`write_entry`・`discard_entry` | dir の作成・file の読み書き・時刻の更新 | entry は 7 日使われなければ掃除が消す | 書きかけを読ませない |

## 変更シナリオと事前の主張

| id | 軸 | 変える要求 | 主張(盲検の前) | 予想の範囲 | 変えない |
| --- | --- | --- | --- | --- | --- |
| S1 | storage | code の読みの置き場の形を変える(entry の単位を source の file ごとにする・CodeFacts に欄を足す) | M の code の読みの節(FACTS_TAG・`_facts_entry`・`_write_facts`・`_facts_from_rows`)だけを変える。entry の鍵は当たりやすさだけを決め、正しさは中の code の sha256 で決まるので、形を変えても照らしの答えと digest は変わらない。形の違う entry は名指して除き、読み直す | M(と、新しい種類が要れば S の末尾の定数 1 行) | G・L・B・digest の綴り |
| S2 | effects | 利用者の code が hy.repr の登録の表に関数を足す・Hy が hy.core.hy_repr を遅れて読み込む | 鍵の判断は B の `_hy_reading` 1 か所。Hy 自身の外の関数が登録された process は Hy の読みに置き場を使わない。読み込まれていない process は既定の表の印(読み込まない)。M・L・G は変えない | なし(B の中で閉じる) | M・L・G・S |
| S3 | effects | 長く走る process(開発の繰り返し・遅れて Hy の module を読む worker)で、読み込みと読み込みの間に macro の依る file が変わる | 一番外の読み込み 1 つの中では file の状態は 1 つの写し(1 度だけ stat)。次の一番外の読み込みは読み直すので、覚えた digest を使わず辿り直し、古いと判じる。覚えの開け閉めは L の `_scoped_exec_module` 1 か所、確かめは M の `_confirmed`・`_state_now` | なし | 全部 |
| S4 | distribution | 置き場(DOEFF_HY_CODE_STORE)を作業木と版をまたいで共有する(zeus の数十の作業木・日次) | 置き場に入るのは中身で決まる読み(source の文の読み・code の読み)だけ。別の作業木の別の中身は別の鍵か、code の sha256 が違って当たらない。digest も照らしの答えも置き場に入らないので、条 1〜3 と別の top の package の値の検は緑のまま | なし | 全部 |
| S5 | concurrency | free-threaded の Python 3.14 で、thread が並べて Hy の module を import する(thread は context を継ぐ) | 覚えは context ごと(ContextVar)。継いだ thread は同じ覚えの dict を共有するが、dict の書きは値が同じ(同じ file の状態・同じ覚え)で、照らしの答えは変わらない。別の一番外の読み込みは自分の覚えを開く | なし | 全部 |
| S6 | hardware | 別の機体・別の Python の作り(free-threaded か否か・版) | 除外: 照らしは機体に依らない。Python と Hy の版は鍵(sys.version・Hy の版・marshal の bytes)が覆い、別の版では当たらないだけ。この Pod の interpreter は 1 つで試せない | — | — |
| S7 | simulation | 決定的な模擬の上で照らしを走らせる | 除外: 照らしは時刻・乱数・外の service を読まない(file の中身・環境・読み込み済みの module だけ)。模擬の対象の層に無い | — | — |

## 強制方法(事前)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| B1(digest・照らしの答えを process をまたいで覚えない) | 失敗ケースの検(子の process 2 つ以上で、依った物を変えて古いと判じる事を確かめる) | tests/test_macro_use_record.py の条 1〜3・別の top の package の値の検 | doeff-hy の pytest(着地の前の focused・日次の全体) | 静的には表せない(どの値を覚えてよいかは意味の判断)。新しい種類の覚えを足す便は条の文と検を読む必要がある |
| 照らしだけが hy.core.hy_repr を読まない | 3 つ目の process が読み込んでいない事の検 | test_a_steady_check_process_does_not_import_hy_repr | 同上 | 他の module が hy.repr を引く事は止めない |
| code の読みを作り直さない | 3 つ目の process が定数を読まない事の検 | test_a_steady_check_process_does_not_read_code_facts_again | 同上 | 数え方は `_constant_text` の呼びの回数(名の変更で検が壊れる — 壊れれば赤で気づく) |
| 読み込み 1 つの写し・読み込みの間は読み直す | 使い手 6 つの読み込みで stat 1 回の検と、読み込みの間の変更に気づく検 | test_one_import_stats_each_macro_file_once・test_a_file_changed_between_two_imports_is_noticed_by_the_second | 同上 | 一番外の本体の実行が長く走る(本体の中で app を回す)process では写しが長く残る |
| digest の綴り | 長さを前に置いた並びの sha256 と一致する事の検 | test_the_digest_is_the_sha256_of_the_length_prefixed_parts | 同上 | 歩みが何を入れるかは縛らない(入れ方だけ) |
| 置き場の entry の形 | 読んだ時に形を確かめ、違えば名指して除く(実行時) | M `_facts_from_rows`・B `_binding_from_parts`・S `discard_entry` | 実行時 | entry の壊れの検はまだ無い(この検証で試す) |
| 起動時の import の軽さ(G・L) | module の頭の import の規則(標準 library だけ)を註で書く | import_scope.py・loader_hooks.py の docstring | 人の読み・既存の起動の検(test_bytecode_guard の .pth の検) | import の方向を静的に止める検は無い |
