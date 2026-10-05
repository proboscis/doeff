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
| `WatchChanges(tables, cursor, timeout, limit)` | 表の列・位置・待つ秒 | `Changes(items, cursor)` | `Reset(epoch, floor)`・`Unreachable` |
| `AppendEvent(stream, idempotency_key, body)` | 追記の列・冪等キー・本文 | `Appended(sequence)`(同じキーの再送は前の番号) | `Refused`・`Unreachable` |
| `ReadEvents(stream, after, limit)` | 追記の列・この番号より後・上限 | `Events(items, last_sequence)` | `Unreachable` |
| `ReadStreamEnd(stream)` | 追記の列 | `StreamEnd(sequence)`(列に今ある、保持の期限を過ぎていない最後の出来事の番号)か `StreamEmpty()`(そういう出来事が 1 つも無い — 番号 0 と混ぜない) | `Unreachable` |
| `PutRows(writes)` | 書きの束 = `RowWrite(table, key, value, expect)`(欄と意味は `PutRow` と同じ)の空でない tuple。同じ表の同じキーが 2 度出る束は作る時に `ValueError` | `WrittenRows(items)`(束の順の `Written`) | `RowsConflict(index, table, key, current)`・`RowsRefused(index, table, key, reason)`・`Unreachable` |

lease(取る・延ばす・返す・書きの柵)はこの package に作らない。doeff-cluster の `LeaseOp` / `HeldLease`
(`doeff_cluster.shared.intent.semaphore_model`)をそのまま使う。

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
- 保持の期限(`KeepFor`)を過ぎた行と出来事は、その刻からどの読みにも出ない: `ReadRow` は `Missing`、`ListRows` と `ReadEvents` は除き、
  `WatchChanges` は行の今の値が期限を過ぎた終端の行である、その行の変わり(`RowChanged`)を出さず、`WatchEvents` は動かず、`ReadStreamEnd` は
  数えない。読みは置き場を変えない(期限の判定は読みが持つ)。置き場から消して変更の列に `RowRemoved` を積み、出来事を冪等キーの覚えへ移す
  回収は、書き(`PutRow`・`PutRows`・`AppendEvent`)の前と `SweepExpired` の時だけ走る。だから期限を過ぎた行の `RowRemoved` は、期限の刻でも
  読みの時でもなく、期限の後の最初の書き(どの表・列への書きでもよい)か `SweepExpired` の時に積まれる(記録の service では手入れの係が
  `RECORDS_MAINTENANCE_SECONDS` ごと — 既定 60 秒 — に `SweepExpired` を撃つ)— 既に `WatchChanges` で行を受け取って写しを持つ読み手は、
  その時まで写しに行を持ち得る(#3561。前は読みを含むどの要求も答える前に回収していた)。
- 例外で上がるのは組み立ての誤り(定義に無い表・列を名指した = `UndeclaredTable`)と実装の誤りだけ。`UndeclaredTable` の欄
  `tables`・`streams` は定義に無いと分かった名(分からない時は空)— 読み手はどの表の断りかを欄で照らし、文の綴りを読まない。

## 表の定義

`TableDecl(name, key_fields, fields, indexes, state_field, states, terminal, initial, operator_paths, retention, size_budget)`

- `fields`: 欄の定義 `FieldDecl(name, writers)`(欄の名と、その欄を書く書き手の名の tuple)の tuple。定義した欄はこれで全部
  (載っていない欄への書きは断る)。`writers` は宣言だけで、置き場の書きの判断は書き手の名では断らない(#2994)。
  定義を尋ねる口は `decl.declares(name)`・`decl.writers_of(name)`・`decl.field_names()`。
- `states` / `terminal` / `initial` / `state_field`: 状態の語彙。生まれる行で状態の欄が無ければ(差分に無いか None なら)`initial` を置く。
  終端の行はもう書けない。
- `operator_paths`: operator の宣言の欄(宣言だけ — 置き場の書きの判断は読まない・#2994)。
- `retention`: `KeepForever()`(消さない)か `KeepFor(seconds)`(終端になってから秒の後に読みに出なくなり、その後の最初の書きか
  `SweepExpired` の回収が消して変更の列に `RowRemoved` を出す — 上の保持の期限の項)。
- `size_budget`: 行の値の JSON(正規の綴り・UTF-8)の byte の上限。

追記の列は `StreamDecl(name, writers, retention, size_budget)`(`KeepFor` は積んでから秒の後に読みに出なくなり、その後の回収で消す。消した出来事の冪等キーは
番号と本文の指紋だけを残して忘れない — 消した後の同じキーの再送も、同じ本文なら前の番号・別の本文なら `Refused`。#3022)。
置き場 1 つの定義は `RecordsSchema(tables, streams, operators)`(表の名 → `TableDecl`・列の名 → `StreamDecl` の凍らせた写像・
operator の主体の名の tuple。`operator_paths` の欄の書き手に operator の主体が 1 人も居ない宣言は、作る時に `ValueError`)。

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
- `doeff_records.pg.pg_records_handler(store, writer, origin_host, poll_seconds)` — PostgreSQL の表で答える。文は doeff の汎用の
  SQL の effect(`SqlQuery`・`SqlTransaction`)で出すので、外側に SQL の答え手(`doeff_core_effects.postgres_sql.postgres_sql_handler` か
  scheduler を塞がない `doeff_core_effects.pooled_postgres_sql.pooled_postgres_sql_handler`)を置く。接続・driver(psycopg 3)・届かない時の
  読み分けは答え手が持ち、答え手の `SqlUnreachable` は公開 effect の答え `Unreachable` になる(engine の失敗 `SqlFailed` は実装の誤りとして
  `RecordsSqlFailed` を上げる)。`store` は `prepare_records_store(database, schema, prefix)`(Program)の答えで、表の用意(移行)は
  process ごとにこの 1 度だけ(移行専用の錠の transaction の中で流すので、複数の process が同時に用意しても UniqueViolation にならない)。
  表は状態の行の表(`state_rows`)と追記の表(`append_rows`)と同じ列の形で、変更の列(`row_changes`)と置き場の版(`store_epoch`)と、
  保持の期限で消した出来事の冪等キーの覚え(`retired_keys` — 出来事を消す transaction が同じ transaction で入れる)を足す。
  書きは置き場ごとの advisory lock(`SqlTransaction` の `lock_key` = 接頭辞 + `records-writer`)で直列にする(番号の順と commit の順を
  揃えるため — 理由は `pg_sql.hy` の頭の註)。

どちらの handler も時刻を doeff-time の `GetTime` で読み、`WatchChanges` の待ちは `Delay` で眠る。仮想の時計
(`sim_time_handler`)の下では保持の期限も待ちも一瞬で進む。

判断(期待・書きの許可・保持・索引・頁)は `doeff_records.admission` の純関数ちょうど 1 つで、handler はどれもそれを呼ぶ。

## 記録の service の HTTP の口

別の process(Python・TS・別の Hy)が同じ 8 つの操作を使うための口。`doeff_records.service.respond` は HTTP の要求 1 つを
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
| `POST /v1/records/read-stream-end` | `{stream}` | `streamEnd`(`sequence`)/ `streamEmpty` |
| `GET /healthz` | — | `{status: "ok"}` |
| `GET /metrics` | — | Prometheus の text(`text/plain; version=0.0.4`)— 身元を問わない |
| `GET /served` | — | `{commits: {<repo>: <sha>} \| null, instance: <世代> \| null, schemaDigests: {<表>: <sha256>}}` — 身元も表の用意も置き場も問わない(#2742) |

- effect の答えの失敗(`Conflict`・`Refused`・`NotIndexed`・`Reset`・`Missing`・`RowsConflict`・`RowsRefused`)は 200 の本文の値。HTTP の断りは
  `{error, reason}` で、`400 malformed`(知らないキー・足りないキー・型の違う値)・
  `404 not-found`(宣言に無い表・知らない route)・`503 store-unavailable`(置き場に届かない = `Unreachable`)・`500 internal`。
- 綴り(JSON の欄の名・`kind`・位置と期待の形)の正本は `doeff_records.wire`。口と client は両方これを呼ぶ。
- 書き手の名: 口は呼び手を断らない(#2988)。呼び手が `X-Records-Writer: <名>`(綴りの正本 = `doeff_records.wire.WRITER_HEADER`)で名乗れば、確かめずにその名を使う。
  名乗らない呼び手は `anonymous`(`doeff_records.principals`)。口は名簿の file も `Authorization` の見出しも読まない(#3008)。その名で記録の handler を組むので、書き手の名は effect の引数にならない。
  書き手の名は行と出来事に記録するだけで、記録の判断は書き手の名では断らない(`anonymous` の書きも通る・#2994)。
- 待ち受けは入口の Program `doeff_records.http_server.serve_records` 1 つで、1 つの run・1 つの scheduler の中で動く(#880 U7)。
  doeff の汎用の HTTP の待ち受けの effect(`HttpListen`・`HttpNextRequest`・`HttpReadBody`・`HttpRespond`・`HttpShutdown`)を出し、
  要求ごとに `Spawn` した task が答える(例外でも必ず答える — 答えていなければ 500 internal)。本文の上限(16 MiB)は `HttpReadBody` が
  読む前に判じる。表の用意は task で、口は先に開き、用意の前の記録の操作は 503 store-unavailable・`/healthz` は 200。用意が落ちれば
  run は例外で終わる。止めの合図(`StopRequested`)で `HttpShutdown` し、走り中の要求を待ってから終わる。
- 計器(#2709): 要求の task は答えを送った直後に、要求の種(`write` = put-row・put-rows・append-event / `read` = 残りの記録の操作 /
  `other` = 記録の操作でない route)と実際に送った答えの status ごとの counter `records_requests_<種>_<status>` を doeff の `CountMetric` で
  1 つ数える(本文の断りの 400・答えの途中で落ちた 500 も同じ 1 か所)。答えの送りが例外になれば、標準の誤りへ 1 行名指して 500 internal を
  1 度だけ送り直し、500 として数える(送れなかった答えの status は数えない)。`GET /metrics` は身元を引く前に答え、`ReadMeter` の断面を
  `doeff_core_effects.meter_prometheus.render_prometheus` で描く(名は末尾に `_total`・label なし)。系列は種 3 × status(200 と断りの
  status)の 15 本で閉じていて、起動の時に全部を 0 で置く。置き場に届かなかった数 = `*_503_total`(表の用意の前と `/readyz` の不達を
  含む)・答えの途中で落ちた数 = `*_500_total`。`other` には kubelet の `/healthz`・`/readyz` と `/metrics` 自身の読みが入る。値は
  process の再起動で 0 に戻る(読み手は区間の差で数える)。計器の答え手は doeff の `memory-meter-handler`(差し替えの欄 `RecordsServing.meter` が在れば、その内側に被せる)。
- 走っている木と表の要約(#2742): `GET /served` は、動いている process が走っている木の commit と世代(`RecordsServing.served` の
  `ServedBuild` — 使い手の入口が土台の実行の文脈から読んで渡す・無ければ `null`)と、配っている表の宣言の要約(`schemaDigests` =
  表の名 → 表の宣言の `repr` を utf-8 にした sha256)を答える。要約の評価は `doeff_records.schema_digest.schema_digests` の 1 か所で、
  使い手の木の data(使い手の repo が木ごとに書く表の要約の file)も同じ関数で書く。身元を引く前・表の用意を問う前に答えるので、
  置き場に届かない間も読める。計器の種は `other`。`TableDecl` の形が doeff の版で変わると、全部の表の要約が一度に変わる。
- 検と模擬の殻は `doeff_records.http_server.start_records_server(run(records_server_config(schema, handler_for, request_handlers=…)))`:
  入口の Program を別の thread の run で回し、`url` と `close()` を持つ `RunningServer` を返す。`handler_for` = 書き手の名 → 用意し終えた
  置き場の handler、`request_handlers` = 要求ごとの答えの外側に被せる handler の列(検の仮想の時計・SQL の答え手)、`meter` = 計器の
  答え手の差し替え(None = 既定 — 検が壊した計器を差す口)。

client の handler `doeff_records.http_client.http_records_handler(RecordsEndpoint(base_url, writer=…))` は、同じ公開 effect に口越しで
答える(書き手の名 `writer` を平文の見出し `X-Records-Writer`(綴りは `doeff_records.wire` の `WRITER-HEADER`)で送る。`Authorization` は送らない)。`404` は `UndeclaredTable` を上げる
(欄 = 要求が名指した名のうち断りの理由に載った物 — 断りの本文の形は変えない。理由の綴りは `wire.hy` の `undeclared-reason` と
`undeclared-refusal` の 1 か所)。`400` / `500` と、ほかの status(`401` / `403`・間の proxy の `502` など — status ごとの枝は持たない)は
一般の失敗で `WireError` を上げる(本文が契約の断りの形でない JSON の時だけ `WireMalformed`。口は `401` / `403` を出さない)。
`WatchChanges` の待ちは client の時計で回す(口へは待たない問い合わせだけを送る)。
置き場の止まり(#3557): 要求と答えの公開 effect 7 つは、届かない(`503` の `store-unavailable` を含む `Unreachable`)時に、待つ時間まで
置き場の戻りを待って同じ要求を撃ち直す。待つ時間は要求のたびに client だけが問う `ReadRequestPatience` で問い、戻りは合図の源と同じ
`AwaitRecordsBack` で待つ。組み立ては client の外側に待つ時間の答え手を必ず置き、名で選ぶ — 待つ秒
(`doeff_records.http_client.request_patience_handler(RequestPatience(seconds))`)か、待たない 0 秒(`doeff_records.http_client.records_unwaited`)。
置かない組み立ては最初の要求で、答え手の無い `ReadRequestPatience` として落ちる。待つ時間を越えたら、待った秒を名指した `Unreachable` を返す。
合図の源が止まりに耐える時間は別の問い `ReadSourcePatience`(`source_patience_handler(SignalSourcePatience(seconds))`)で、client は問わない —
同じ組の中で「client は待たない・源は耐える」を、handler を並べる位置に依らずに選べる。変化の待ち 2 つ(`WatchChanges`・`WatchEvents`)は
待たない — 合図の源が自分で越えて止まりの合図を出す。源は戻りの知らせの後に、同じ位置から待たない読み(`timeout` 0)を 1 回撃って戻りの
合図を出し、その答えの位置から long-poll に戻る(戻った後に書きが無くても long-poll の 1 回ぶんを待ち切らない)。源の始まりの読み
(`ListRows`・`ReadStreamEnd`)は client の待つ時間に乗る。

## 置き場の手入れ(`doeff_records.maintenance`)

公開 effect ではない 2 つの effect と、それを回す Program。memory と PostgreSQL の handler が答える。

- `SweepExpired()` → `Swept(rows)` — 保持の期限を過ぎた行を消して `RowRemoved` を積み、期限を過ぎた出来事を捨てる。回収はこの
  effect の時だけ走る(手入れの係が実行する)。読みは回収せず期限を自分で見る。書き(`PutRow`・`PutRows`・`AppendEvent`)も回収せず、
  自分が触る行と出来事だけを同じ transaction で片付けてから判じる — 期限を過ぎた行への書きはその行の `RowRemoved` を積んでから無い行として
  判じ、期限を過ぎた鍵への追記は鍵の覚えで答える(答えは回収の後の書きと同じ)。PostgreSQL の書き 1 回は書きの transaction の文だけを流す。
- `PruneChanges(keep_seconds)` → `Pruned(floor, removed)` — `keep_seconds` より古い変更を変更の列から消し、floor を上げる。
  floor より前の位置の `WatchChanges` は `Reset(epoch, floor)`(`WatchCursor(epoch, floor)` から読めば残った変更を頭から全部読める)。
- `maintenance_loop(interval_seconds, keep_seconds, ticks)` — 手入れの係の本体(`ticks=None` で止めるまで)。

## 記録の service を起動する(`doeff_records.main`)

置き場の宣言と接続 URL の file の読み方は呼び手の系が持つので、呼び手の系の入口が `serve_records_service` を呼ぶ:

```hy
(import myapp.tables [SCHEMA])
(import doeff_records.main [serve-records-service])
(defk dsn-of [text] {:pre [(: text str)] :post [(: % str)]} (.strip text))
(when (= __name__ "__main__")
  (sys.exit (run (with-handlers [subprocess-handler os-file-handler] (serve-records-service SCHEMA dsn-of)))))
```

`dsn-of` = 接続 URL の file の中身 → DSN の Program。`serve-records-service` は env と file を effect(`ReadEnvironment`・`ReadText`)で読む
Program で、答え = process の終わりの code。本番の土台(`records-foundation`)は scheduler・`await-handler`・`async-time-handler`・
`os-signal-stop-handler`・`aiohttp-http-server`・`pooled-postgres-sql-handler`(psycopg 3・自動 commit — image に psycopg が要る)。

自分の process の外側(scheduler・`await-handler`・`state`・時計・止めの合図 `StopRequested` の答え手)を持つ系は、単独の入口を使わずに
割った口を組む(#1280):

- `records-settings dsn-of` — env と file を読んで設定の値 `RecordsSettings`(DSN・接頭辞・機体の名・接続の数・宛先・手入れ)を作る
- `records-serving schema settings choice` — 本体の設定 `RecordsServing` を作る。`choice` は置き場の選び `StoreChoice`(`doeff_records.store_choice` — 表の用意の作り手と /readyz の問い)で、PostgreSQL は `doeff_records.main` の `PG-STORE`、memory は `doeff_records.memory` の `memory-store-choice`
- `records-connected settings body` — 土台の口(待ち受け・名乗り・PostgreSQL の答え手。接続と pool を開き、終われば閉じる)。外側は持たない
- `records-process foundation serving` — 本体。`(records-process (fn [body] (<自分の外側> (records-connected settings body))) serving)` と撃つ

`records-foundation` は単独の入口の土台の全部(外側 + `records-connected`)。

env(接続 URL の file・接頭辞・port・手入れの間隔・変更の列に残す秒)の一覧と既定は `main.hy` の頭の註。
表は接頭辞(既定 `records_`)つきで、起動時に `CREATE ... IF NOT EXISTS` だけを流す(既存の表を消さない・変えない)。
SIGTERM / SIGINT で口を閉じて接続を返す。

## 適合の筋書き

`doeff_records.laws` は、どの handler の組も満たすべき法を Program として持つ(公開)。

| 法 | 確かめること |
|---|---|
| `law_stale_put_conflicts` | 古い版の `PutRow` は `Conflict`・衝突は行を変えない |
| `law_committed_changes_appear_once_in_order` | 確定した変更は `WatchChanges` にちょうど 1 回・順序どおり |
| `law_epoch_change_resets` | 置き場の版が変わると `Reset`・読み直した一覧から続けられる |
| `law_undeclared_writes_are_refused` | 定義に無い欄・状態・上限・終端の行・キーの書き換えは `Refused` で、行を変えない。書き手の名では断らない |
| `law_transient_rows_expire` | `KeepFor` の終端の行は期限で読みに出なくなり、回収(`SweepExpired`)の後の変更に `RowRemoved` が 1 回出る。`KeepForever` の行は消えない |
| `law_indexed_list_equals_filtered_scan` | 索引の `ListRows` は全件を読んで絞った結果と同じ |
| `law_append_is_idempotent` | 同じ冪等キーの再送は前の番号・別の本文は `Refused` |
| `law_watch_waits_for_a_change` | `WatchChanges` は変更が来るまで `timeout` まで待つ |
| `law_none_removes_a_field` | 差分の値 None はその欄を消し、行の値は None を持たない |
| `law_maintenance_prunes_and_sweeps` | 刈った変更より前の位置は `Reset`・floor の位置からは続けられ、行は消えない。回収は期限切れの行だけを 1 回消す |
| `law_put_rows_is_all_or_nothing` | `PutRows` は全部通る束だけを書き(束の順の `Written`)、期待のずれ 1 行・断り 1 行の束は 1 行も書かない。期待のずれを断りより先に答え、確定した束の変更は束の順に続いた番号で見える |
| `law_expired_keys_are_remembered` | 保持の期限で出来事を消した後も冪等キーは忘れない: 同じ本文の再送は前の番号で列の出来事を増やさず、別の本文は `Refused` |
| `law_expired_records_are_unseen_before_a_sweep` | 保持の期限を過ぎた行と出来事(出来事ごと・組ごとの列)は、回収の前でも 6 つの読みのどれにも出ない。期限を過ぎた行の `RowRemoved` は別の表への書きでは積まれず、回収(`SweepExpired`)が 1 回だけ積む |
| `law_a_write_clears_the_expired_row_it_touches` | 回収されていない期限を過ぎた行への `PutRow`・`PutRows` は、その行を消して `RowRemoved` を積んでから無い行として判じる(`ExpectAbsent` は版 1 で生まれ、消えた行の版の `ExpectVersion` は `Conflict(Missing)`)— 回収の後の書きと同じ答え |
| `law_an_expired_key_answers_the_same_before_and_after_a_sweep` | 回収されていない期限を過ぎた冪等キーへの追記は、回収の後と同じ答え(同じ本文は前の番号・別の本文は同じ文の断り)。期限を過ぎた組に新しい鍵を積んでも、組の古い出来事は読みに戻らない |

使い方: `LAW_SCHEMA` の定義で置き場を作り、`LawHarness(as_writer)`(書き手の名と Program → その書き手の handler で包んだ
Program)を法に渡す。法は答えを順に並べた list を返すので、2 つの handler の組で同じ法を回して list を比べれば、答えが同じことも
確かめられる(`SHARED_LAWS` は時間を進めない法のうち、前からの 6 つの effect だけで回る法 — `PutRows` を答えない handler の組でも回せる)。置き場の版を進める検の口は `doeff_records.faults.AdvanceStoreEpoch`(公開 effect ではない)。置き場に届かない状態を起こす・戻す検の口は `doeff_records.faults.SetStoreOutage`(届かない理由 detail と、対象の表と追記の列の名 names — None で全部)で、memory の置き場が答える: 届かない間、名に当たる公開 effect は `Unreachable(detail)` を答え、置き場を変えない(本番の記録の口が service に届かない時と同じ答え)。記録の service の不達を筋書きにする検と模擬は、業務の effect に答える偽の handler を書かず、この口で正典の置き場を届かなくする。置き場の一部だけの断りを起こす・外す検の口は `doeff_records.faults.AddStoreFault(StoreFault(...))` と `ClearStoreFaults(names)`(names と名が重なる故障を外す — None で全部)で、これも memory の置き場が答える。`StoreFault` は名 names(表と追記の列の名の frozenset)・操作 operation(`StoreOperation.READ` = `ReadRow`・`ListRows`・`WatchChanges`・`ReadEvents`・`ReadStreamEnd` / `StoreOperation.WRITE` = `PutRow`・`PutRows`・`AppendEvent`)・答え answer(`Refused` か `Unreachable`)・lands(True なら書きを置き場に着けてから答えを差し替える — 書きは届いたが答えが切れた形)・matching(effect を受けて当たるかを返す関数 — None で名と操作に当たる全部)を持つ。`PutRows` は束の中に当たる表が 1 つでもあれば束ごと当たる。故障は置いた順に探して最初の 1 つが答え、置き場全体の不達(`SetStoreOutage`)がそれより先に答える。doeff の実行の外で筋書きを組む使い手は、同じ書きの同期の口 `doeff_records.memory.memory-add-fault` / `memory-clear-faults` を使う。

## 検

```sh
uv run --with psycopg pytest packages/doeff-records/tests -q
# 自分の PostgreSQL を指す時:
DOEFF_RECORDS_TEST_PG_DSN=postgresql://postgres:pw@127.0.0.1:55433/postgres \
  uv run --with psycopg pytest packages/doeff-records/tests -q
```

env `DOEFF_RECORDS_TEST_PG_DSN` が無い時は、tests の conftest が pytest の始めに使い捨ての PostgreSQL 16 を一時の dir(TMPDIR の下)に立てて
env を置き、終わりに止めて消す(部品 = `packages/doeff-core-effects/tests/postgres_support/disposable_postgres.py`・binary は pgserver の
wheel に同梱の物を uv の分けた環境で取る)。立てられない機体(uv が無い・download できない・initdb が失敗する)では、PostgreSQL の検は
「使い捨ての PostgreSQL を用意できない: <理由>」と skip に表示する(緑とは数えない)。

法の検は 4 つの組で回す: `memory`・`pg`・`http-memory`・`http-pg`(後の 2 つは 127.0.0.1 に口を開き、client の handler で送る)。
`test_parity_memory_pg.hy` は全部の法の答えの列が memory の組と等しいこと(番号・版・epoch・時刻まで)を確かめる。
