# doeff-records

版つきの行・変更の列・追記の列を扱う、doeff の effect と handler。

利用者の Program は下の effect だけを使う。置き場が memory か PostgreSQL かは、composition root が選ぶ handler で決まる。
書きの許可(誰がどの欄を書けるか・状態の語彙・終端の行・上限・承認)と保持の規則は、表の定義(`RecordsSchema`)として
composition root で渡す。書き手の名は effect の引数ではなく、handler を組む時に渡す。

## 公開 effect

| effect | 入力 | 成功の答え | 失敗の答え(値で返す) |
|---|---|---|---|
| `ReadRow(table, key)` | 表・キー | `Row(key, value, version)` か `Missing()` | `Unreachable` |
| `ListRows(table, where, fields, cursor, limit)` | 表・索引の欄の等号の AND・返す欄・前の頁の位置・上限 | `Page(rows, next_cursor, epoch, sequence)` | `Reset`・`Unreachable`・`NotIndexed` |
| `PutRow(table, key, value, expect, approval)` | 表・キー・欄の差分・期待(`ExpectAbsent` / `ExpectVersion(n)` / `ExpectAny`)・承認(任意) | `Written(version, value)` | `Conflict(current)`・`Refused(reason)`・`Unreachable` |
| `WatchChanges(tables, cursor, timeout, limit)` | 表の列・位置・待つ秒 | `Changes(items, cursor)` | `Reset(epoch)`・`Unreachable` |
| `AppendEvent(stream, idempotency_key, body)` | 追記の列・冪等キー・本文 | `Appended(sequence)`(同じキーの再送は前の番号) | `Refused`・`Unreachable` |
| `ReadEvents(stream, after, limit)` | 追記の列・この番号より後・上限 | `Events(items, last_sequence)` | `Unreachable` |

lease(取る・延ばす・返す・書きの柵)はこの package に作らない。doeff-cluster の `LeaseOp` / `HeldLease`
(`doeff_cluster.semaphore_model`)をそのまま使う。

型は `doeff_records.values`(定義・期待・答え)と `doeff_records.effects`(effect)にある。

- 行のキーは定義の `key_fields` の順の文字列の tuple。行の値は欄 → JSON の値の dict で、キーの欄を含む。
- 版(`version`)は生まれた行が 1 で、書くたびに 1 増える。
- `ListRows` の頁はキーの綴り(`admission.key_text`)の順。最初の頁の `epoch` と `sequence` から `WatchChanges` を始めると、
  一覧の後の変更を取りこぼさない。
- `WatchChanges` は確定した変更を、番号の順にちょうど 1 回ずつ返す(断られた書き・衝突した書きは出ない)。位置の `epoch` が置き場の版と
  違えば `Reset` を返すので、一覧から読み直す。
- 例外で上がるのは組み立ての誤り(定義に無い表・列を名指した = `UndeclaredTable`)と実装の誤りだけ。

## 表の定義

`TableDecl(name, key_fields, writers, indexes, state_field, states, terminal, initial, operator_paths, retention, size_budget)`

- `writers`: 欄 → その欄を書いてよい書き手の名の tuple。定義した欄はこれで全部(載っていない欄への書きは断る)。
  行を作ることはキーの欄を書くことなので、キーの欄の書き手が行を作ってよい書き手になる。値の変わらない欄は照らさない。
- `states` / `terminal` / `initial` / `state_field`: 状態の語彙。生まれる行で状態の欄が無ければ `initial` を置く。終端の行はもう書けない。
- `operator_paths`: 書くのに承認の要る欄。承認の確かめ方は handler を組む時に渡す(既定はどの承認も認めない)。
- `retention`: `KeepForever()`(消さない)か `KeepFor(seconds)`(終端になってから秒の後に消し、変更の列に `RowRemoved` を出す)。
- `size_budget`: 行の値の JSON(正規の綴り・UTF-8)の byte の上限。

追記の列は `StreamDecl(name, writers, retention, size_budget)`(`KeepFor` は積んでから秒の後に消す)。

## handler

- `doeff_records.memory.memory_records_handler(store, writer)` — `MemoryStore(schema)` の dict の上で答える。模擬環境・手元の
  1 process・単体の検に使う。同じ `MemoryStore` を別の `writer` の handler で包めば、1 つの置き場を複数の書き手が使う形になる。
- `doeff_records.pg.pg_records_handler(host, writer)` — `PgRecordsHost(connection, schema, prefix=...)` の PostgreSQL の表で答える。
  接続(psycopg 3・自動 commit)は composition root が開いて渡す(psycopg は extra `pg`)。表は状態の行の表(`state_rows`)と
  追記の表(`append_rows`)と同じ列の形で、変更の列(`row_changes`)と置き場の版(`store_epoch`)を足す。書きは置き場ごとの
  advisory lock で直列にする(番号の順と commit の順を揃えるため — 理由は `pg_sql.hy` の頭の註)。

どちらの handler も時刻を doeff-time の `GetTime` で読み、`WatchChanges` の待ちは `Delay` で眠る。仮想の時計
(`sim_time_handler`)の下では保持の期限も待ちも一瞬で進む。

判断(期待・書きの許可・保持・索引・頁)は `doeff_records.admission` の純関数ちょうど 1 つで、handler はどれもそれを呼ぶ。

## 適合の筋書き

`doeff_records.laws` は、どの handler の組も満たすべき法を Program として持つ(公開)。

| 法 | 確かめること |
|---|---|
| `law_stale_put_conflicts` | 古い版の `PutRow` は `Conflict`・衝突は行を変えない |
| `law_committed_changes_appear_once_in_order` | 確定した変更は `WatchChanges` にちょうど 1 回・順序どおり |
| `law_epoch_change_resets` | 置き場の版が変わると `Reset`・読み直した一覧から続けられる |
| `law_undeclared_writes_are_refused` | 定義に無い書き手・欄・状態・上限・承認・終端の行・キーの書き換えは `Refused` で、行を変えない |
| `law_transient_rows_expire` | `KeepFor` の終端の行は期限で消え、`KeepForever` の行は消えない |
| `law_indexed_list_equals_filtered_scan` | 索引の `ListRows` は全件を読んで絞った結果と同じ |
| `law_append_is_idempotent` | 同じ冪等キーの再送は前の番号・別の本文は `Refused` |
| `law_watch_waits_for_a_change` | `WatchChanges` は変更が来るまで `timeout` まで待つ |

使い方: `LAW_SCHEMA` の定義で置き場を作り、`LawHarness(as_writer)`(書き手の名と Program → その書き手の handler で包んだ
Program)を法に渡す。法は答えを順に並べた list を返すので、2 つの handler の組で同じ法を回して list を比べれば、答えが同じことも
確かめられる(`SHARED_LAWS` は時間を進めない法)。置き場の版を進める検の口は `doeff_records.faults.AdvanceStoreEpoch`(公開 effect ではない)。

## 検

```sh
uv run pytest packages/doeff-records/tests -q
# PostgreSQL の法も走らせる時(使い捨ての置き場を立てて):
docker run -d --rm --name records-pg -e POSTGRES_PASSWORD=pw -p 55433:5432 postgres:17-alpine
DOEFF_RECORDS_TEST_PG_DSN=postgresql://postgres:pw@127.0.0.1:55433/postgres \
  uv run --with psycopg pytest packages/doeff-records/tests -q
```

env `DOEFF_RECORDS_TEST_PG_DSN` が無い時、PostgreSQL の検は skip と表示する(緑とは数えない)。
