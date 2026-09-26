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
| `PutRow(table, key, value, expect)` | 表・キー・欄の差分・期待(`ExpectAbsent` / `ExpectVersion(n)` / `ExpectAny`)。書き手の名は欄に無く、handler を組む時に身元から入る | `Written(version, value)` | `Conflict(current)`・`Refused(reason)`・`Unreachable` |
| `WatchChanges(tables, cursor, timeout, limit)` | 表の列・位置・待つ秒 | `Changes(items, cursor)` | `Reset(epoch)`・`Unreachable` |
| `AppendEvent(stream, idempotency_key, body)` | 追記の列・冪等キー・本文 | `Appended(sequence)`(同じキーの再送は前の番号) | `Refused`・`Unreachable` |
| `ReadEvents(stream, after, limit)` | 追記の列・この番号より後・上限 | `Events(items, last_sequence)` | `Unreachable` |
| `PutRows(writes)` | 書きの束 = `RowWrite(table, key, value, expect)`(欄と意味は `PutRow` と同じ)の空でない tuple。同じ表の同じキーが 2 度出る束は作る時に `ValueError` | `WrittenRows(items)`(束の順の `Written`) | `RowsConflict(index, table, key, current)`・`RowsRefused(index, table, key, reason)`・`Unreachable` |

lease(取る・延ばす・返す・書きの柵)はこの package に作らない。doeff-cluster の `LeaseOp` / `HeldLease`
(`doeff_cluster.semaphore_model`)をそのまま使う。

型は `doeff_records.values`(定義・期待・答え)と `doeff_records.effects`(effect)にある。

- 行のキーは定義の `key_fields` の順の文字列の tuple。行の値は欄 → JSON の値の凍らせた写像(doeff-hy の `FrozenMap` —
  中の object も `FrozenMap`・array は tuple まで深く凍らせる)で、キーの欄を含む。`Row` / `Written` / `RowChanged` の `value`、
  `PutRow.value`・`ListRows.where`、出来事の本文は、作る時に受けた写像を凍らせる(作った後に変えられない)。JSON へ書く所は
  `doeff_hy.frozen.thaw_json` で dict / list へ戻す。
- 版(`version`)は生まれた行が 1 で、書くたびに 1 増える。
- `ListRows` の頁はキーの綴り(`admission.key_text`)の順。最初の頁の `epoch` と `sequence` から `WatchChanges` を始めると、
  一覧の後の変更を取りこぼさない。
- `PutRows` は全部か 0 で書く: 1 行でも期待が合わないか断られれば、1 行も書かない。判定は `PutRow` と同じで、全部の行の期待を
  先に見て(合わない行があれば束の順で最初の行の `RowsConflict`)、次に全部の行の書きの判定を見る(最初に断られた行の `RowsRefused`)。
  確定した束は変更の列に束の順で 1 行ずつ、続いた番号で積む。PostgreSQL の handler は `PutRow` と同じ置き場の lock と
  transaction 1 つの中で全部の行を検めてから書くので、書きの途中の失敗は transaction ごと戻る。
- `WatchChanges` は確定した変更を、番号の順にちょうど 1 回ずつ返す(断られた書き・衝突した書きは出ない)。位置の `epoch` が置き場の版と
  違えば `Reset` を返すので、一覧から読み直す。
- 例外で上がるのは組み立ての誤り(定義に無い表・列を名指した = `UndeclaredTable`)と実装の誤りだけ。

## 表の定義

`TableDecl(name, key_fields, fields, indexes, state_field, states, terminal, initial, operator_paths, retention, size_budget)`

- `fields`: 欄の定義 `FieldDecl(name, writers)`(欄の名と、その欄を書いてよい書き手の名の tuple)の tuple。定義した欄はこれで全部
  (載っていない欄への書きは断る)。行を作ることはキーの欄を書くことなので、キーの欄の書き手が行を作ってよい書き手になる。
  値の変わらない欄は照らさない。定義を尋ねる口は `decl.declares(name)`・`decl.writers_of(name)`・`decl.field_names()`。
- `states` / `terminal` / `initial` / `state_field`: 状態の語彙。生まれる行で状態の欄が無ければ(差分に無いか None なら)`initial` を置く。
  終端の行はもう書けない。
- `operator_paths`: operator の宣言の欄。書けるのは、その欄の書き手(`writers`)であり、かつ `RecordsSchema.operators` に入る書き手だけ。
  書き手の名は handler を組む時に身元から入る値なので、effect の中身で operator を名乗ることはできない。
- `retention`: `KeepForever()`(消さない)か `KeepFor(seconds)`(終端になってから秒の後に消し、変更の列に `RowRemoved` を出す)。
- `size_budget`: 行の値の JSON(正規の綴り・UTF-8)の byte の上限。

追記の列は `StreamDecl(name, writers, retention, size_budget)`(`KeepFor` は積んでから秒の後に消す)。
置き場 1 つの定義は `RecordsSchema(tables, streams, operators)`(表の名 → `TableDecl`・列の名 → `StreamDecl` の凍らせた写像・
operator の主体の名の tuple。既定の空 = 誰も `operator_paths` の欄を書けない。`operator_paths` の欄の書き手に operator の主体が
1 人も居ない宣言は、作る時に `ValueError`)。

## 表ごとの行の型で読み書きする(`doeff_records.typed`)

業務の Program は欄 → 値の写像を見ずに、表ごとの行の型(pydantic の `BaseModel` か dataclass)で読み書きする。
上の汎用の effect の上の Program なので、handler は増えない(memory・PostgreSQL・写しのどの handler の上でも同じに動く)。

| 口 | 答え |
|---|---|
| `RowType(table, model)` | 表 1 つの行の型(欄の名 = 表の定義の欄の名・alias が在れば alias。写しは pydantic の `TypeAdapter`) |
| `read_typed(row_type, key)` | `TypedRow(key, value, version)` か `Missing`・`Unreachable` |
| `list_typed(row_type, where=, cursor=, limit=)` | `TypedPage(rows, next_cursor, epoch, sequence)` か `Reset`・`Unreachable`・`NotIndexed` |
| `put_typed(row_type, key, value, expect)` | `TypedWritten(version, value)` か `TypedConflict(current)`・`Refused`・`Unreachable` |
| `typed_change(row_type, change)` | `RowChanged` → `TypedRowChanged`(`RowRemoved` はそのまま) |

`put_typed` は行の全体の像を書く: 行の型の欄を全部載せ、値が None の欄は消す。値の変わらない欄は書き手の名簿で照らさないので、
読んだ行の自分の欄だけを変えた値(`model_copy(update=...)` / `dataclasses.replace`)を書けばよい。

## handler

- `doeff_records.memory.memory_records_handler(store, writer)` — `MemoryStore(schema)` の手元の表の上で答える。模擬環境・手元の
  1 process・単体の検に使う。同じ `MemoryStore` を別の `writer` の handler で包めば、1 つの置き場を複数の書き手が使う形になる。
- `doeff_records.pg.pg_records_handler(host, writer)` — `PgRecordsHost(connection, schema, unreachable_errors=..., prefix=...)` の
  PostgreSQL の表で答える。接続(psycopg 3・自動 commit)と接続の失敗の例外の型は composition root が渡す(psycopg は extra `pg`)。表は状態の行の表(`state_rows`)と
  追記の表(`append_rows`)と同じ列の形で、変更の列(`row_changes`)と置き場の版(`store_epoch`)を足す。書きは置き場ごとの
  advisory lock で直列にする(番号の順と commit の順を揃えるため — 理由は `pg_sql.hy` の頭の註)。

どちらの handler も時刻を doeff-time の `GetTime` で読み、`WatchChanges` の待ちは `Delay` で眠る。仮想の時計
(`sim_time_handler`)の下では保持の期限も待ちも一瞬で進む。

判断(期待・書きの許可・保持・索引・頁)は `doeff_records.admission` の純関数ちょうど 1 つで、handler はどれもそれを呼ぶ。

## 記録の service の HTTP の口

別の process(Python・TS・別の Hy)が同じ 7 つの操作を使うための口。`doeff_records.service.respond` は HTTP の要求 1 つを
答え 1 つにする Program で、身元 → 本文の読み → 宣言に在る表か → 公開 effect を実行する、の順だけを持つ(判断は記録の handler)。

| route | 本文 | 200 の答えの `kind` |
|---|---|---|
| `POST /v1/records/read-row` | `{table, key}` | `row` / `missing` |
| `POST /v1/records/list-rows` | `{table, where?, fields?, cursor?, limit?}` | `page` / `reset` / `notIndexed` |
| `POST /v1/records/put-row` | `{table, key, value, expect}`(`approval` は廃止 — null だけ読み飛ばし、値があれば 400) | `written` / `conflict` / `refused` |
| `POST /v1/records/watch-changes` | `{tables, cursor, timeout?, limit?}` | `changes` / `reset` |
| `POST /v1/records/append-event` | `{stream, idempotencyKey, body}` | `appended` / `refused` |
| `POST /v1/records/read-events` | `{stream, after?, limit?}` | `events` |
| `POST /v1/records/put-rows` | `{writes: [{table, key, value, expect}, …]}`(1 つ以上・同じ表の同じキーは 1 度だけ — 外れれば 400) | `writtenRows`(`items` = `written` の列)/ `rowsConflict`(`index, table, key, current`)/ `rowsRefused`(`index, table, key, reason`) |
| `GET /healthz` | — | `{status: "ok"}` |

- effect の答えの失敗(`Conflict`・`Refused`・`NotIndexed`・`Reset`・`Missing`・`RowsConflict`・`RowsRefused`)は 200 の本文の値。HTTP の断りは
  `{error, reason}` で、`400 malformed`(知らないキー・足りないキー・型の違う値)・`401 unauthorized`(身元が引けない)・
  `404 not-found`(宣言に無い表・知らない route)・`503 store-unavailable`(置き場に届かない = `Unreachable`)・`500 internal`。
- 綴り(JSON の欄の名・`kind`・位置と期待の形)の正本は `doeff_records.wire`。口と client は両方これを呼ぶ。
- 身元: `Authorization: Bearer <token>` を身元の名簿 `principals.json`(`{version: 1, principals: [{name, tokenSha256}]}`)で
  書き手の名へ引く(`doeff_records.principals`)。引いた名で記録の handler を組むので、書き手の名は effect の引数にならない。
  名簿に在っても表の宣言の書き手でなければ、書きは記録の判断が `Refused` にする。
- 口を開く部品は `doeff_records.http_server.start_records_server(RecordsServerConfig(...))`(標準の `http.server`)。
  PostgreSQL の置き場では要求ごとに接続を 1 本借りる(`doeff_records.pg_pool.PgHostPool`)。

client の handler `doeff_records.http_client.http_records_handler(RecordsEndpoint(base_url, token))` は、同じ公開 effect に口越しで
答える。`401` は書き(`PutRow`・`AppendEvent`)なら `Refused`、`PutRows` なら束の最初の行の `RowsRefused`、読みなら `Unreachable`。`404` は `UndeclaredTable` を上げる。
`WatchChanges` の待ちは client の時計で回す(口へは待たない問い合わせだけを送る)。

## 置き場の手入れ(`doeff_records.maintenance`)

公開 effect ではない 2 つの effect と、それを回す Program。memory と PostgreSQL の handler が答える。

- `SweepExpired()` → `Swept(rows)` — 保持の期限を過ぎた行を消して `RowRemoved` を積み、期限を過ぎた出来事を捨てる(読み書きの前にも
  同じ回収が走るが、誰も触らない置き場でも行が残らないように手入れの係が実行する)。
- `PruneChanges(keep_seconds)` → `Pruned(floor, removed)` — `keep_seconds` より古い変更を変更の列から消し、floor を上げる。
  floor より前の位置の `WatchChanges` は `Reset`(一覧から読み直す)。
- `maintenance_loop(interval_seconds, keep_seconds, ticks)` — 手入れの係の本体(`ticks=None` で止めるまで)。

## 記録の service を起動する(`doeff_records.main`)

置き場の宣言と接続の開き方は呼び手の系が持つので、呼び手の系の入口が `serve_records_service` を呼ぶ:

```hy
(import psycopg)
(import myapp.tables [SCHEMA])
(import doeff_records.main [serve-records-service])
(serve-records-service SCHEMA (fn [url] (psycopg.connect url :autocommit True))
                       #(psycopg.OperationalError psycopg.InterfaceError))
```

env(接続 URL の file・身元の名簿の file・接頭辞・port・手入れの間隔・変更の列に残す秒)の一覧と既定は `main.hy` の頭の註。
表は接頭辞(既定 `records_`)つきで、起動時に `CREATE ... IF NOT EXISTS` だけを流す(既存の表を消さない・変えない)。
SIGTERM / SIGINT で口を閉じて接続を返す。

## 適合の筋書き

`doeff_records.laws` は、どの handler の組も満たすべき法を Program として持つ(公開)。

| 法 | 確かめること |
|---|---|
| `law_stale_put_conflicts` | 古い版の `PutRow` は `Conflict`・衝突は行を変えない |
| `law_committed_changes_appear_once_in_order` | 確定した変更は `WatchChanges` にちょうど 1 回・順序どおり |
| `law_epoch_change_resets` | 置き場の版が変わると `Reset`・読み直した一覧から続けられる |
| `law_undeclared_writes_are_refused` | 定義に無い書き手・欄・状態・上限・operator の欄・終端の行・キーの書き換えは `Refused` で、行を変えない |
| `law_operator_paths_need_an_operator` | `operator_paths` の欄は operator の主体の書き手だけが書ける(欄の書き手でも operator でなければ `Refused`・operator でも欄の書き手でない欄は `Refused`)・他の欄は欄の書き手の定義どおり |
| `law_transient_rows_expire` | `KeepFor` の終端の行は期限で消え、`KeepForever` の行は消えない |
| `law_indexed_list_equals_filtered_scan` | 索引の `ListRows` は全件を読んで絞った結果と同じ |
| `law_append_is_idempotent` | 同じ冪等キーの再送は前の番号・別の本文は `Refused` |
| `law_watch_waits_for_a_change` | `WatchChanges` は変更が来るまで `timeout` まで待つ |
| `law_none_removes_a_field` | 差分の値 None はその欄を消し、行の値は None を持たない |
| `law_maintenance_prunes_and_sweeps` | 刈った変更より前の位置は `Reset`・floor の位置からは続けられ、行は消えない。回収は期限切れの行だけを 1 回消す |
| `law_put_rows_is_all_or_nothing` | `PutRows` は全部通る束だけを書き(束の順の `Written`)、期待のずれ 1 行・断り 1 行の束は 1 行も書かない。期待のずれを断りより先に答え、確定した束の変更は束の順に続いた番号で見える |

使い方: `LAW_SCHEMA` の定義で置き場を作り、`LawHarness(as_writer)`(書き手の名と Program → その書き手の handler で包んだ
Program)を法に渡す。法は答えを順に並べた list を返すので、2 つの handler の組で同じ法を回して list を比べれば、答えが同じことも
確かめられる(`SHARED_LAWS` は時間を進めない法のうち、前からの 6 つの effect だけで回る法 — `PutRows` を答えない handler の組でも回せる)。置き場の版を進める検の口は `doeff_records.faults.AdvanceStoreEpoch`(公開 effect ではない)。

## 検

```sh
uv run pytest packages/doeff-records/tests -q
# PostgreSQL の法も走らせる時(使い捨ての置き場を立てて):
docker run -d --rm --name records-pg -e POSTGRES_PASSWORD=pw -p 55433:5432 postgres:17-alpine
DOEFF_RECORDS_TEST_PG_DSN=postgresql://postgres:pw@127.0.0.1:55433/postgres \
  uv run --with psycopg pytest packages/doeff-records/tests -q
```

env `DOEFF_RECORDS_TEST_PG_DSN` が無い時、PostgreSQL の検は skip と表示する(緑とは数えない)。

法の検は 4 つの組で回す: `memory`・`pg`・`http-memory`・`http-pg`(後の 2 つは 127.0.0.1 に口を開き、client の handler で送る)。
`test_parity_memory_pg.hy` は全部の法の答えの列が memory の組と等しいこと(番号・版・epoch・時刻まで)を確かめる。
