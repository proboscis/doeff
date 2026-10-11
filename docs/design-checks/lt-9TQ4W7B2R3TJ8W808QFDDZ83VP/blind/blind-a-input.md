# 盲検 A の入力(lt-9TQ4W7B2R3TJ8W808QFDDZ83VP)

```text
あなたは責務分離に対する失敗例を生成してください。
このシステムで合理的に求められうる将来の機能追加・運用変更を1つ挙げ、
それを実装すると複数の責務を持つモジュールの変更が必要になる例を具体化してください。
一般的な設計レビューや承認判定は求めていません。

元の主張の前提を守っているかを示してください。前提を変更する例なら、
契約の適用範囲外であることを明示してください。ファイルが複数変わるだけでは欠陥ではありません。
意図した公開契約の拡張、配線の変更、共同の不変条件と、隠すはずの知識の漏洩を区別してください。
不要に極端な規模、無関係な要求、意図的な破壊は使わないでください。

返答には次を含めてください。
1. 現実的な変更要求と、その要求が必要になる理由。
2. 関係する元の主張・前提と、予想に反して変わる責務・モジュール・契約。
3. どの知識・判断がどこへ漏れるために変更が波及するか。契約拡張ならその区別。
4. 最小の具体的な入力・変更例と再現手順、観測すべき結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
失敗例が見つからない場合は探索した範囲と不足を述べ、合格・安全とは結論しないでください。
```

## 材料(固定済み)

### 対象
- repo: doeff(Python と Hy の effect の枠組み)の package `doeff-hy` の `doeff_hy_bytecode_guard`(Hy の module の .pyc が、展開に使った macro の変更で古くなった時に compile し直させる見張り)。
- 版: 167d38ec290cf2e5bea1f260ac04f248de96fd80(この便の変更の基準版は cf7ea9cc09a1da54c2bb4abbf984c0552818d837)。
- 読む木(読むだけ・変えない): `/home/ubuntu/.worktrees/doeff-at-167d38ec`
- 実コード(path は読む木からの相対):
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/import_scope.py`(全体)
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py`(全体。特に `install`・`_scoped_exec_module`・`_checking_get_code`・`record_is_current_here`・`file_sha256`)
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py`(全体。照らし `record_is_current`・`_rows_are_current`・覚え `_remembered`・`_confirmed`・`_state_now`・`_unchanged`・code の読み `CodeFacts`・`_code_facts`・`CodeReadings`・`_code_sha256`・`_facts_entry`・`_stored_facts`・`_write_facts`・`_facts_from_rows`・歩み `_Walker`(`_feed`・`digest`・`function`・`_value`・`_bound` ほか))
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/bound_source.py`(特に `binding_of`・`_binding_entry`・`_hy_reading`)
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py`(全体)
  - `packages/doeff-hy/src/doeff_hy_bytecode_guard/records.py`(記録の形 `MacroRecord`)
  - 条: `packages/doeff-hy/architecture.hy` の頭の註の B1
  - 検: `packages/doeff-hy/tests/test_macro_use_record.py`(後半の「process をまたぐ覚え」と「照らしの固定費」の節)・`tests/test_bound_source_store.py`

### 背景と要件
- Hy の module の bytecode は source だけでなく、展開に使った macro にも依る。見張りは compile の時に「使った macro の閉包の digest」の記録を code に足し、.pyc を読む時(読みの口 `get_code`)に、使った macro を module 名から今の環境で引き直して digest を比べる(照らし)。違えば compile し直す。
- digest は macro の関数の code・名で引く大域・既定値・閉包の cell・class・module の属性などを辿った「歩み」の文字列の並びの sha256。生きた値(別の module から来た素の値・環境変数で決まる値)を含む。
- 条 B1: 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の module の file の中身・環境変数など file の外で決まる値)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。digest も照らしの答え(合う / 古い)も process をまたいで覚えない。process をまたいで覚えてよいのは、中身で決まる純な読み(辿れない大域を覆う source の文の読み・歩みが読む関数の code の読み)だけ。
- 費用の要件: 2 つ目からの process(.pyc が温まった後)で、照らしが足す CPU を、照らしを常に「今に合う」とした時との差 0.1 秒以内にする。
- この便の変更(実装済み):
  1. bound_source の置き場の鍵: hy.core.hy_repr(hy.repr の登録の表の持ち主)がまだ読み込まれていなければ、鍵は既定の表の印にし、表を見るために読み込まない。読み込まれていれば表の指紋。Hy 自身の外の関数が登録されていれば置き場を使わない。
  2. 一番外の読み込み 1 つの覚え: loader_hooks が SourceFileLoader の module の本体の実行の口(`exec_module`)を包み、一番外の実行の間だけ `IMPORT_SCOPE` に `ImportScope` を置く。照らしはその中で、覚えた digest の確かめに使う file の状態(更新時刻・大きさ)を file ごとに 1 度だけ stat し、確かめた覚えを 2 度確かめない。読み込みの外(道具が直に照らす)では照らし 1 回ごとに置く。
  3. code の読み(CodeFacts — digest の形・名の表・文字列の定数・import・hy.R の module・名で辿れない読みの広さ)を、置き場の種類 `.facts` に code の中身(`marshal.dumps(code)` の sha256)で覚える。entry は歩み 1 つ(macro か値の在処と、その module の file)ごとに、その歩みが使った読みを並べる。読み方の版(macro_use.py の file の sha256)と Python の版(sys.version)を鍵に入れる。
  4. 歩みの `_feed` は文字列を積むだけにし、`digest()` が長さ(8 byte の little endian)を前に置いた並びを 1 度に hash する(綴りは前と同じ)。

### 責務(module)
| id | file | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G | import_scope.py | 一番外の読み込み 1 つの間の覚えの持ち主 | 覚えの形(file の path → 状態・確かめた digest の覚え) | なし | `ImportScope`・`IMPORT_SCOPE` | なし | context ごと | 標準 library しか import しない |
| L | loader_hooks.py | SourceFileLoader の口の包み | どの口でいつ照らすか・一番外の本体の実行の間だけ覚えを置く事 | 照らしの中身 | `install`・`record_is_current_here`・`file_sha256` ほか | file の読み書き・.pyc の書き | process | 一番外の exec_module が覚えを開け閉めする |
| M | macro_use.py | 記録の作り方と照らし方・歩み・process の中の digest の覚え・code の読みとその置き場の entry | digest の綴り・歩みの規則・覚えの確かめ方・code の読みの鍵と entry の形 | 歩みの中・entry の形 | `record_is_current`・`macro_digest`・`value_digest`・`MacroRecording` | 歩みの中の import・stat・sha256・entry の読み書き | 覚えは process の間・entry は 7 日の掃除まで | B1・digest の綴り・覚えを使うのは依った file が覚えた時の状態のままの時だけ(読み込み 1 つの中は 1 つの写し) |
| B | bound_source.py | source の文の読みとその置き場の鍵 | 読み方・鍵の欄 | 読みの形 | `binding_of` | source の読み・entry の読み書き | process の覚え・entry | 鍵は読みを変える物を全部持つ |
| S | code_store.py | 置き場の並び・entry の読み書き・壊れた entry の除き | dir の並び・種類の末尾・書き方(一時 file → rename) | 各種類の中身の形 | `store_dir`・`content_key`・`entry_path`・`read_entry`・`write_entry`・`discard_entry` | file の読み書き | entry は 7 日 | 書きかけを読ませない |

### 将来の変更についての主張(前提つき)
| id | 変える要求 | 主張 | 予想の範囲 |
| --- | --- | --- | --- |
| S1 | code の読みの置き場の形を変える(entry の単位を source の file ごとに・CodeFacts に欄を足す) | M の code の読みの節だけを変える。正しさは中の code の sha256 で決まり、照らしの答えと digest は変わらない。形の違う entry は除いて読み直す | M(と S の末尾の定数) |
| S2 | 利用者の code が hy.repr の登録の表に関数を足す・Hy が hy.core.hy_repr を遅れて読み込む | 鍵の判断は B の `_hy_reading` 1 か所で閉じる | B |
| S3 | 長く走る process で、読み込みと読み込みの間に macro の依る file が変わる | 次の一番外の読み込みは file の状態を読み直し、辿り直して古いと判じる。開け閉めは L の 1 か所・確かめは M | なし |
| S4 | 置き場を作業木と版をまたいで共有する | 置き場に入るのは中身で決まる読みだけで、digest も照らしの答えも入らない。B1 の検は緑のまま | なし |
| S5 | free-threaded の Python 3.14 で thread が並べて import する(thread は context を継ぐ) | 覚えは context ごと。継いだ thread は同じ dict を共有するが値は同じ file の状態・同じ覚えで、答えは変わらない | なし |

前提: 照らしの正しさは「歩みが読んだ物が同じなら digest が同じ」に依る。置き場の entry は速さのためだけで、壊れていれば除いて作り直す。一番外の読み込みの中で file が書き換わる事は、1 つの写しとして扱う(次の読み込みで気づく)。
