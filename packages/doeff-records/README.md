# doeff-records

版つきの行・変更の列・追記の列を扱う、doeff の effect と handler。

利用者の Program は下の effect だけを使う。置き場が memory か PostgreSQL かは、composition root が選ぶ handler で決まる。
書きの許可(誰がどの欄を書けるか・状態の語彙・終端の行・上限・承認)と保持の規則は、表の定義(`RecordsSchema`)として
composition root で渡す。書き手の名は effect の引数ではなく、handler を組む時に渡す。

## 公開 effect

| effect | 入力 | 成功の答え | 失敗の答え(値で返す) |
|---|---|---|---|
| `ReadRow(table, key)` | 表・キー | `Row(key, value, version)` か `Missing()` | `Unreachable` |
| `ListRows(table, where, fields, cursor, limit, descending_by, where_any)` | 表・索引の欄の等号の AND・返す欄・前の頁の位置・上限(1 以上 `LIST_LIMIT_MAX` = 5,000 以下 — 外は作る時に `ValueError`)・上から読む欄(下の註)・索引の欄の値の組(下の註) | `Page(rows, next_cursor, epoch, sequence)` | `Reset`・`Unreachable`・`NotIndexed` |
| `PutRow(table, key, value, expect, idempotency_key=None)` | 表・キー・欄の差分・期待(`ExpectAbsent` / `ExpectVersion(n)` / `ExpectAny`)・書き 1 つの身元の冪等キー(下の「行の書きの冪等キー」)。書き手の名は欄に無く、handler を組む時に身元から入る | `Written(version, value, position, replayed)`(同じ冪等キー・同じ本文の送り直しは前の答えを `replayed=True` で) | `Conflict(current)`・`Refused(reason)`・`Unreachable` |
| `WatchChanges(tables, cursor, timeout, limit, streams)` | 表の列・位置・待つ秒・上限・末尾を知りたい追記の列(既定 = 無し) | `Changes(items, cursor, tails)`(`tails` = 名指した列ごとの末尾 `StreamTail(stream, sequence, at)` か `StreamTailEmpty(stream)` — 名指した順・名指さなければ空) | `Reset(epoch, floor)`・`Unreachable` |
| `AppendEvent(stream, idempotency_key, body)` | 追記の列・冪等キー・本文 | `Appended(sequence)`(同じキーの再送は前の番号) | `Refused`・`Unreachable` |
| `ReadEvents(stream, after, limit)` | 追記の列・この番号より後・上限 | `Events(items, last_sequence)` | `Unreachable` |
| `ReadStreamEnd(stream)` | 追記の列 | 答えの分け方は `defeffect` で宣言している(`:failure [Unreachable]`・`:value [StreamEmpty]`・`:absent` は無し)。成功 = `StreamEnd(sequence)`(列に今ある、保持の期限を過ぎていない最後の出来事の番号)。値 = `StreamEmpty()`(そういう出来事が 1 つも無い — 番号 0 と混ぜない。不在にはせず、呼び手が `StreamEnd` と分けて扱う) | 失敗 = `Unreachable`(handler は今までどおり値で答え、呼び手の `<-` が `Raise(Unreachable)` に変える。値で受けたい所は `on-raise` か `result` で包む) |
| `WatchEvents(stream, after=0, timeout=0.0)`(公開 effect の外の列の待ち — 置き場の handler が自分の待ち方で答える) | 追記の列・この番号より頭が進むのを待つ・待つ秒の上限(`after` は 0 以上の整数・`timeout` は 0 以上の秒 — 外は作る時に `ValueError`。`timeout` 0 は待たずに 1 回だけ見る) | 答えの分け方は `defeffect` で宣言している(`:failure [Unreachable]`・`:value [EventsQuiet]`・`:absent` は無し)。成功 = `EventsMoved()`(頭が `after` より進んだ — 出来事は運ばない。読むのは呼び手で、起きた後に `ReadEvents` で読む)。値 = `EventsQuiet()`(`timeout` まで進まなかった — `after` が頭より先でも誤りにしない。失敗にも不在にもせず、待つ呼び手が毎回 `EventsMoved` と分けて扱う) | 失敗 = `Unreachable`(handler は今までどおり値で答え、呼び手の `<-` が `Raise(Unreachable)` に変える。値で受けたい所は `on-raise` か `result` で包む) |
| `ReadEventByKey(stream, idempotency_key)` | 追記の列・冪等キー | `Event`(その鍵で積んだ出来事 — `ReadEvents` が出すのと同じ)か `EventAbsent()`(その鍵ではまだ積んでいない)か `EventRetired(sequence)`(積まれたが保持の期限を過ぎた — 消えた出来事の番号)。列を辿らず、置き場の (列, 冪等キー) の一意の索引で答える(#3750) | `Unreachable` |
| `PutRows(writes, idempotency_key=None)` | 書きの束 = `RowWrite(table, key, value, expect)`(欄と意味は `PutRow` と同じ)の空でない tuple・束 1 つの冪等キー。同じ表の同じキーが 2 度出る束は作る時に `ValueError` | `WrittenRows(items, replayed)`(束の順の `Written` — 各項の `position` は束の順に 1 つずつ続く。同じ冪等キー・同じ束の送り直しは前の答えを `replayed=True` で) | `RowsConflict(index, table, key, current)`・`RowsRefused(index, table, key, reason)`・`Unreachable` |
| `AppendEvents(appends)` | 追記の束 = `EventAppend(stream, idempotency_key, body)`(欄と意味は `AppendEvent` と同じ)の空でない tuple。同じ列の同じ冪等キーが 2 度出る束は作る時に `ValueError` | `AppendedEvents(items)`(束の順の、追記ごとの `Appended` か `Refused`) | `Unreachable`(束の全部が積まれたか、どれも積まれていない) |

lease(取る・延ばす・返す・書きの柵)はこの package に作らない。doeff-cluster の `LeaseOp` / `HeldLease`
(`doeff_cluster.shared.intent.semaphore_model`)をそのまま使う。

型は `doeff_records.values`(定義・期待・答え)と `doeff_records.effects`(effect)にある。

- 行のキーは定義の `key_fields` の順の文字列の tuple。行の値は欄 → JSON の値の凍らせた写像(doeff-hy の `FrozenMap` —
  中の object も `FrozenMap`・array は tuple まで深く凍らせる)で、キーの欄を含む。`Row` / `Written` / `RowChanged` の `value`、
  `PutRow.value`・`ListRows.where`・`ListRows.where_any`、出来事の本文は、作る時に受けた写像を凍らせる(作った後に変えられない)。JSON へ書く所は
  `doeff_hy.frozen.thaw_json` で dict / list へ戻す。
- 版(`version`)は生まれた行が 1 で、書くたびに 1 増える。
- 書きの答え `Written` の `position`(`WatchCursor(epoch, sequence)`)は、その書きが変更の列に積んだ変更の位置(`sequence` = `WatchChanges` の
  `RowChanged.sequence` と同じ番号・`epoch` = 置き場の版)。その位置から `WatchChanges` を読むと、その書きより後の変更だけが来る。memory・
  PostgreSQL・HTTP の口越しで同じ値(`law_a_write_names_its_change_position`)。HTTP の口の `written` の鍵 `position` は省ける鍵で、
  位置を持たない `Written`(読み直した行から作った答え)は `position = None`。
- `ListRows` の頁はキーの綴り(`admission.key_text`)の順。最初の頁の `epoch` と `sequence` から `WatchChanges` を始めると、
  一覧の後の変更を取りこぼさない。
- `ListRows` の絞りは 2 つの形を AND で重ねる。どちらも索引の欄(定義の `indexes` かキーの欄)だけで、他の欄は `NotIndexed`(欄の名の順)。

  | 絞り | 形 | 合う行 | 作る時の断り |
  |---|---|---|---|
  | `where` | 欄 → 値 | 欄の値が値と等しい行(欄どうしは AND) | 欄の名の綴りの外 |
  | `where_any` | 欄 → 値の組(JSON の値の空でない配列 — 凍らせて tuple) | 欄の値が組のどれかと等しい行(欄どうしは AND)。既定は空の写像 = 絞らない | 空の組・配列でない組(`TypeError`)・組の値の数が全部の欄の和で `WHERE_ANY_LIMIT`(1,000)を越える・`where` と同じ欄 |

  等しさは JSON の値としての等しさ(`admission` の `json-equal?` — 数は数で比べ `1` と `1.0` は等しく、`True` と `1` は等しくない)。値の組の
  頁を辿った全件は、組の値ごとの等号の `ListRows` の全件の和をキーの順に並べた物と同じ(法 `law_any_of_list_equals_the_union_of_equal_lists`)。
  呼び手は値の組 1 つを、値ごとの往復でなく 1 回の読み(組が上限を越える時は上限ごとに割った回数)で引く。PostgreSQL の置き場は組を
  `(表の欄の式) IN (…)` に展げ、宣言の索引の欄ごとの表の欄の式の索引(`(CASE WHEN ledger = '<表>' THEN ((payload::jsonb) -> '<欄>') END)` —
  `pg_sql.hy` の頭の註)で引く。2 つ以上の欄の絞り(等号 2 つ・等号と値の組・値の組と値の組)は欄ごとの索引を組んで(BitmapAnd)当たる行だけを
  読み、1 頁の所要は片方の欄だけに合う行の数でなく当たる行の数で決まる。
- `descending_by`(欄の名)を付けた `ListRows` は上から読む読み: 欄の値が整数の行だけを、その値の大きい順(同じ値はキーの綴りの逆順)に上から
  `limit` 件読む。続きが在れば `next_cursor` は頁の最後の行の (値, キーの綴り) を持つ位置(`ListCursor` の `after_value` と `after_key` —
  keyset)で、それを `cursor` に渡すと (値, キーの綴り) がその組より小さい行から次の頁を読む(キーが時刻の順でない表でも、新しい順に古い頁を
  送れる)。頁の間に書かれた行は、位置より小さい組なら後の頁に出て、大きい組なら出ない。上から読む読みの `cursor` は `after_value` を持つ
  位置だけ・キーの順の読みの `cursor` は `after_value` の無い位置だけ(作る時に `ValueError`)。HTTP の口の位置の鍵 `afterValue` は上から読む
  読みの位置の時だけ載る。`where`・`where_any` はそのまま効く。
- HTTP の口の本文(`list-rows`)は、`descendingBy` と `whereAny`(欄 → 値の組の配列)の鍵を使う読みの時だけ載せる — 使わない読みの本文は
  鍵を足す前と同じ。古い記録の service は知らない鍵を 400 で断るので、鍵を使う client より先に service を新しい版で起こす。
- HTTP の口の答えの本文は鍵を足すだけ: client の読み(`wire.hy` の `answer-object-of`)は答えの object の知らない鍵を読み捨てるので、
  service を先に新しい版で起こしても 1 版前の client は答えを読める(要求の本文の知らない鍵は前のとおり 400 で断る)。鍵を消す・綴りを
  変える・新しい鍵を必須にする変更はしない — `tests/test_wire_versions.hy` が凍らせた答えの見本(`tests/wire-answers/<日>.json`・足すだけ)で
  断る。答えに鍵を足した変更は、新しい日の見本の file を足す。
- 行の書きの冪等キー(card ki-78c6d663491f): `PutRow`・`PutRows` の `idempotency_key` は書き 1 つの身元。置き場(memory・PostgreSQL)は
  確定した鍵つきの書きの答え(`Written`・`WrittenRows` — 版・値・位置)と本文の指紋(表・鍵・値・期待)を、書きを確定するのと同じ錠・
  transaction で覚え、同じ鍵・同じ本文の次の書きには、期待も書きの判定も見ずに覚えた答えを `replayed=True` で返す(行にも変更の列にも
  触らない — 間に別の書き手が行を変えていても)。同じ鍵の別の本文は冪等キーの断り(`PutRow` は `Refused`・`PutRows` は束の 0 行目の
  `RowsRefused` — 断りの文は「冪等キー」を含む)。衝突・断りの答えは覚えない(書いていない)。鍵の無い書きと、別の鍵の書きは今までどおり
  期待で判じる(同じ値でも `Conflict` — 値の一致では書けたと読まない)。覚えは覚えた刻から `values.WRITE_KEY_MEMORY_SECONDS`(1 時間)の後の
  `SweepExpired` が外す(`Swept.write_keys`)。HTTP の client は鍵の無い書きに要求ごとの鍵を乱数で作り、届かない書きの撃ち直しに同じ鍵を
  見出し `X-Records-Idempotency-Key` で付ける — 答えだけが切れて確定した書きの撃ち直しは、自分の着いた 1 回目に負けず `Written` を受ける。
  待つ時間(`RequestPatience`)は覚えの長さから要求の期限を引いた秒より短く選ぶ。
- `PutRows` は全部か 0 で書く: 1 行でも期待が合わないか断られれば、1 行も書かない。判定は `PutRow` と同じで、全部の行の期待を
  先に見て(合わない行があれば束の順で最初の行の `RowsConflict`)、次に全部の行の書きの判定を見る(最初に断られた行の `RowsRefused`)。
  確定した束は変更の列に束の順で 1 行ずつ、続いた番号で積む。PostgreSQL の handler は `PutRow` と同じ置き場の lock と
  transaction 1 つの中で全部の行を検めてから書くので、書きの途中の失敗は transaction ごと戻る。
- `AppendEvents` は追記の束を置き場への 1 往復で積む(1 つの要求で原因と本体の 2 つの出来事を積む書き手が、書きの錠と
  往復を 2 回順に待たないため)。答えは束の順に `AppendEvent` を 1 つずつ、間に他の書きを挟まずに撃った答えと同じ: 追記ごとに冪等キーの
  再送は前の番号・別の本文と上限は `Refused` で、断られた追記は束の他の追記を止めない(`PutRows` と違い全部か 0 ではない)。今積んだ追記の
  番号は束の順に増える。PostgreSQL の handler は追記の数に依らず `AppendEvent` と同じ書きの錠の transaction 1 つ・2 往復で積む(法
  `law_append_events_answer_like_appends_in_order`)。
- `WatchChanges` は確定した変更を、番号の順にちょうど 1 回ずつ返す(断られた書き・衝突した書きは出ない)。位置の `epoch` が置き場の版と
  違えば `Reset` を返すので、一覧から読み直す。
- `WatchChanges` の `streams` に追記の列を名指すと、答えの `tails` に名指した順で列ごとの末尾が載る(最後の生きている出来事の番号と刻 —
  `ReadEvents` の最後の出来事の `sequence`・`at` と同じで、番号は `ReadStreamEnd` と同じ。保持の期限を過ぎた出来事は数えない。変更と同じ
  置き場の断面から読む・#3718)。tails は答えを返す時点の名指した列の末尾。列への追記では
  待ち手を起こさない — 起こしたい呼び手は列を源に別に待つ(`WatchEvents`)。宣言に無い列を名指すと、表と同じく `UndeclaredTable`。
- 保持の期限(`KeepFor`)を過ぎた行と出来事は、その刻からどの読みにも出ない: `ReadRow` は `Missing`、`ListRows` と `ReadEvents` は除き、
  `WatchChanges` は行の今の値が期限を過ぎた終端の行である、その行の変わり(`RowChanged`)を出さず、`WatchEvents` は動かず、`ReadStreamEnd` は
  数えず、`ReadEventByKey` は `EventRetired` を答える。読みは置き場を変えない(期限の判定は読みが持つ)。置き場から消して変更の列に `RowRemoved` を積み、出来事を冪等キーの覚えへ移す
  回収は、書き(`PutRow`・`PutRows`・`AppendEvent`)の前と `SweepExpired` の時だけ走る。だから期限を過ぎた行の `RowRemoved` は、期限の刻でも
  読みの時でもなく、期限の後の最初の書き(どの表・列への書きでもよい)か `SweepExpired` の時に積まれる(記録の service では手入れの係が
  `RECORDS_MAINTENANCE_SECONDS` ごと — 既定 60 秒 — に `SweepExpired` を撃つ)— 既に `WatchChanges` で行を受け取って写しを持つ読み手は、
  その時まで写しに行を持ち得る(#3561。前は読みを含むどの要求も答える前に回収していた)。
- 例外で上がるのは組み立ての誤り(定義に無い表・列を名指した = `UndeclaredTable`)と実装の誤りだけ。`UndeclaredTable` の欄
  `tables`・`streams` は定義に無いと分かった名(分からない時は空)— 読み手はどの表の断りかを欄で照らし、文の綴りを読まない。

## 表の定義

`TableDecl(name, key_fields, fields, indexes, descending, descending_within, state_field, states, terminal, initial, operator_paths, retention, size_budget)`

- `fields`: 欄の定義 `FieldDecl(name, writers)`(欄の名と、その欄を書く書き手の名の tuple)の tuple。定義した欄はこれで全部
  (載っていない欄への書きは断る)。`writers` は宣言だけで、置き場の書きの判断は書き手の名では断らない(#2994)。
  定義を尋ねる口は `decl.declares(name)`・`decl.writers_of(name)`・`decl.field_names()`。
- `descending` / `descending_within`: 速さだけの宣言(答えを変えないので repr に出さず、表の要約を変えない)。`descending` は
  `ListRows` の `descending_by` で上から読む欄で、PostgreSQL の置き場は欄ごとに並べる式の索引を張る。`descending_within` は
  等号の欄で絞って上から読む読み(`where` の等号 + `descending_by`)の欄の組 `DescendingWithin(equal, descending)` の tuple
  (`equal` は `indexes` の欄・`descending` は `descending` の欄)で、PostgreSQL の置き場は組ごとに等号の欄の式を前に置いた並べる式の
  索引を張り、絞りに合う行の 1 頁だけを辿る(組の無い読みは、絞りの欄の索引と上から読む欄の索引を組んで位置より下の当たる行を全部
  読み、並べ替える)。
- `states` / `terminal` / `initial` / `state_field`: 状態の語彙。生まれる行で状態の欄が無ければ(差分に無いか None なら)`initial` を置く。
  終端の行はもう書けない。
- `operator_paths`: operator の宣言の欄(宣言だけ — 置き場の書きの判断は読まない・#2994)。
- `retention`: `KeepForever()`(消さない)か `KeepFor(seconds)`(終端になってから秒の後に読みに出なくなり、その後の最初の書きか
  `SweepExpired` の回収が消して変更の列に `RowRemoved` を出す — 上の保持の期限の項)。
- `size_budget`: 行の値の JSON(正規の綴り・UTF-8)の byte の上限。

追記の列は `StreamDecl(name, writers, retention, size_budget)`(`KeepFor` は積んでから秒の後に読みに出なくなり、その後の回収で消す。消した出来事の冪等キーは
番号と本文の指紋だけを覚える — 消した後の同じキーの再送も、同じ本文なら前の番号・別の本文なら `Refused`。#3022)。
覚えをいつまで残すかは `key_memory`(`KeepFor` の列だけが既定から替えられる):
`RememberKeysForever()`(既定 — 外さない)・`RememberKeysFor(seconds)`(覚えた刻から少なくとも `seconds` 秒残し、その後の `SweepExpired` が外す)・
`RememberNoKeys()`(出来事が置き場を去る時に覚えを残さない — 期限の後に同じ鍵で送り直されない列)。外れた鍵(と覚えを残さない列の
去った鍵)の再送は、置き場がその鍵を知らないので新しい出来事になり、`ReadEventByKey` は `EventAbsent`。覚え方を限る書き手は「宣言した幅の
外では同じ鍵を送り直さない」を守る。送ってから外す列の覚えは、出来事が送り先に在ると確かめて外れるまで外れない(送り先に届かない間の
答えは変わらない)。覚えが外れるのは回収の時なので、長さを過ぎてから回収までの間の再送と `ReadEventByKey` はまだ覚えで答える(前の番号・
`EventRetired`)— 約束は「少なくとも長さの間は覚えで答える」。
`archive_before_retire=True`(`KeepFor` の列だけ)の列は、期限を過ぎた出来事を回収も書きも置き場から外さず、手入れの出来事の送りの関数が
送り先に在ると確かめた出来事だけを外す(下の「置き場の手入れ」— 読みは期限の刻から出さず、冪等キーの答えも印の無い列と同じ)。
置き場 1 つの定義は `RecordsSchema(tables, streams, operators)`(表の名 → `TableDecl`・列の名 → `StreamDecl` の凍らせた写像・
operator の主体の名の tuple。`operator_paths` の欄の書き手に operator の主体が 1 人も居ない宣言は、作る時に `ValueError`)。

## 表ごとの行の型で読み書きする(`doeff_records.typed`)

業務の Program は欄 → 値の写像を見ずに、表ごとの行の型(pydantic の `BaseModel` か dataclass)で読み書きする。
上の汎用の effect の上の Program なので、handler は増えない(memory・PostgreSQL・写しのどの handler の上でも同じに動く)。

| 口 | 答え |
|---|---|
| `RowType(table, model)` | 表 1 つの行の型(欄の名 = 表の定義の欄の名・alias が在れば alias。写しは pydantic の `TypeAdapter`) |
| `read_typed(row_type, key)` | `TypedRow(key, value, version)` か `Missing`・`Unreachable` |
| `list_typed(row_type, where=, cursor=, limit=, where_any=)` | `TypedPage(rows, next_cursor, epoch, sequence)` か `Reset`・`Unreachable`・`NotIndexed` |
| `put_typed(row_type, key, value, expect)` | `TypedWritten(version, value, position)` か `TypedConflict(current)`・`Refused`・`Unreachable` |
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
  保持の期限で消した出来事の冪等キーの覚え(`retired_keys` — 出来事を消す transaction が同じ transaction で入れる)と、行の書きの冪等キーの
  覚え(`write_keys` — 行の書きの transaction が同じ transaction で入れる)を足す。
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
| `POST /v1/records/list-rows` | `{table, where?, fields?, cursor?, limit?}`(`limit` は `effects.LIST_LIMIT_MAX` = 5,000 まで — `ListRows` が作る時に断り、越えた本文は 400 malformed。大きな一覧は続きの位置 `next_cursor` で頁に割る — ADR-DOE-RECORDS-002 R6 (f)) | `page` / `reset` / `notIndexed` |
| `POST /v1/records/put-row` | `{table, key, value, expect}`(`approval` は廃止 — null だけ読み飛ばし、値があれば 400)。冪等キーは本文でなく見出し `X-Records-Idempotency-Key`(古い service は読み捨てるので、client と service の入れ替えの順を問わない — put-rows も同じ) | `written`(冪等キーの送り直しの答えだけ `replayed: true`)/ `conflict` / `refused` |
| `POST /v1/records/watch-changes` | `{tables, cursor, timeout?, limit?, streams?}`(`streams` は列を名指す時だけ・1 つ以上 — 空の配列は 400。古い service は鍵を知らず 400 なので、名指す client より先に service を新しい版で起こす) | `changes`(`items, cursor` と、`streams` を名指した要求の答えだけ `tails` = 名指した順の `{kind: streamTail, stream, sequence, at}` / `{kind: streamTailEmpty, stream}` の列 — 名指さない要求の答えに鍵は無い)/ `reset` |
| `POST /v1/records/append-event` | `{stream, idempotencyKey, body}` | `appended` / `refused` |
| `POST /v1/records/read-events` | `{stream, after?, limit?, keyHead?}`(`keyHead` は冪等キーの頭で引く時だけ — 列の宣言の `key-lookup` の区切りより前が等しい出来事だけを読む。古い service は鍵を知らず 400 なので、頭で引く client より先に service を新しい版で起こす) | `events` / `notIndexed`(`key-lookup` を宣言していない列への頭の読み) |
| `POST /v1/records/put-rows` | `{writes: [{table, key, value, expect}, …]}`(1 つ以上・同じ表の同じキーは 1 度だけ — 外れれば 400) | `writtenRows`(`items` = `written` の列)/ `rowsConflict`(`index, table, key, current`)/ `rowsRefused`(`index, table, key, reason`) |
| `POST /v1/records/read-stream-end` | `{stream}` | `streamEnd`(`sequence`)/ `streamEmpty` |
| `POST /v1/records/read-event-by-key` | `{stream, idempotencyKey}` | `event`(`event` = `read-events` の `items` と同じ形の出来事 1 つ)/ `eventAbsent` / `eventRetired`(`sequence`) |
| `POST /v1/records/append-events` | `{appends: [{stream, idempotencyKey, body}, …]}`(1 つ以上・同じ列の同じ冪等キーは 1 度だけ — 外れれば 400。古い service は操作を知らず 404 なので、束で積む client より先に service を新しい版で起こす) | `appendedEvents`(`items` = 束の順の `appended` / `refused` の列) |
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
- 用意の告知(#3733): 用意の task は記録の handler を置いた後に `RecordsPrepared(address, seconds)`(結んだ宛先・用意の所要の秒)を
  1 度だけ出す — 告知が出たなら記録の操作に答えられる。告知は用意の task の終わりで、止めの道の外(止めの合図が先なら用意の task ごと
  取り消す)。本番の土台(`records-connected` の `printed-listening`)は 1 行を印字する。準備の報告を立てる使い手の土台は、自分の答え手を
  `printed-listening` より内側に置く。答え手が落ちれば用意の失敗と同じく run は例外で終わる。
- `GET /readyz` は用意の前は 503、用意の後は置き場に届くかの判断 `doeff_records.http_server.store_reach(serving)` で答える。判断は公開で、
  使い手の土台が準備の報告に同じ判断を撃てる。答えは閉じた型 `StoreReach` = `StoreReachable(pressure)`(届く — 詰まりの読みを持つ・200)/
  `StoreUnreachable(reason)`(`RecordsServing.readiness` が届かないと答えた・503)/ `StoreSilent(seconds)`(上限 1 秒の内に答えない・503)。
  `readiness` が None の置き場は届く。memory の置き場の選び(`memory-store-choice`)の `readiness` は、置き場全体の止まり(名の無い
  `faults.SetStoreOutage`)の間だけ届かない(名を限った止まりは届く — PostgreSQL の `SELECT 1` と同じ意味)。
- 計器(#2709): 要求の task は答えを送った直後に、要求の種(`write` = put-row・put-rows・append-event・append-events / `read` = 残りの記録の操作 /
  `other` = 記録の操作でない route)と実際に送った答えの status ごとの counter `records_requests_<種>_<status>` を doeff の `CountMetric` で
  1 つ数える(本文の断りの 400・答えの途中で落ちた 500 も同じ 1 か所)。答えの送りが例外になれば、標準の誤りへ 1 行名指して 500 internal を
  1 度だけ送り直し、500 として数える(送れなかった答えの status は数えない)。`GET /metrics` は身元を引く前に答え、`ReadMeter` の断面を
  `doeff_core_effects.meter_prometheus.render_prometheus` で描く(名は末尾に `_total`・label なし)。系列は種 3 × status(200 と断りの
  status)の 15 本で閉じていて、起動の時に全部を 0 で置く。置き場に届かなかった数 = `*_503_total`(表の用意の前と `/readyz` の不達を
  含む)・答えの途中で落ちた数 = `*_500_total`。`other` には kubelet の `/healthz`・`/readyz` と `/metrics` 自身の読みが入る。値は
  process の再起動で 0 に戻る(読み手は区間の差で数える)。計器の答え手は doeff の `memory-meter-handler`(差し替えの欄 `RecordsServing.meter` が在れば、その内側に被せる)。
- 計算の pool の負荷(ADR-DOE-RECORDS-002 R6 (g)): `GET /metrics` は描きの時に Compute の答え手の負荷(`ReadComputeLoad`)を読み、
  断面に足して描く(計器へは積まない): `records_compute_workers`(pool の thread の数 — `RecordsServing.compute_workers`)・`records_compute_queued`(束の読みの読み解きと綴りの計算のうち、pool の thread を待って列に並んで
  いる数 — 今の値)・`records_compute_running`(回っている数 — 今の値)・`records_compute_wait_seconds_sum` と `_count`(回り始めた計算が
  出されてから回り始めるまで待った秒の合計と回数 — 起動からの累計)・`records_compute_run_seconds_sum` と `_count`(回り終えた計算が回った秒の合計と回数 — 起動からの累計)。拍ごとに読んだ 2 つの断面の差で、その拍の待ちの平均(合計の差 ÷
  回数の差)と、列に並んでいた数の時間の平均(合計の差 ÷ 拍の秒)と、pool の利用率(回りの合計の差 ÷ 拍の秒 ÷ thread の数)が出る — 要求の遅れが跳ねた刻と同じ拍で並べて読む。その場で回す答え手
  (`inline-compute-handler` — 台本の検と模擬)は列を持たないので全部 0。
- 走っている木と表の要約(#2742): `GET /served` は、動いている process が走っている木の commit と世代(`RecordsServing.served` の
  `ServedBuild` — 使い手の入口が土台の実行の文脈から読んで渡す・無ければ `null`)と、配っている表の宣言の要約(`schemaDigests` =
  表の名 → 表の宣言の `repr` を utf-8 にした sha256)を答える。要約の評価は `doeff_records.schema_digest.schema_digests` の 1 か所で、
  使い手の木の data(使い手の repo が木ごとに書く表の要約の file)も同じ関数で書く。身元を引く前・表の用意を問う前に答えるので、
  置き場に届かない間も読める。計器の種は `other`。`TableDecl` の形が doeff の版で変わると、全部の表の要約が一度に変わる。
- scheduler の thread の仕事の分け(ADR-DOE-RECORDS-002 — `doeff_records.loop_work` の頭の註): 受けの loop・要求の task・書きの門・
  変化の待ちは 1 本の scheduler の thread の上で回るので、その thread は要求の受けと順の制御と、大きさが要求の本文か 1 行で決まる計算だけを
  持つ。置き場に在る行の数で大きさが決まる計算 — 束の読み(一覧の頁・変更の列・出来事の列と手入れの送りの読み)の行の読み解きと、
  束の読みの答え(`Page`・`Changes`・`Events` で行を 1 つ以上運ぶ答え)の綴り(JSON の文字列と UTF-8 の byte 列)— は doeff の
  `Compute` を出して thread の外で回す。**記録の service を起こす組み立ては `Compute` の答え手を置く**: 本番の土台の口
  `records-connected` と検の殻 `start_records_server` は thread の pool(`thread-pool-compute-handler` — free-threaded の Python では
  scheduler の thread と並んで走る)を自分で開いて閉じる。`serve_records` と `respond` を自分の土台で回す台本の検と模擬は
  `doeff_core_effects.inline_compute.inline-compute-handler` を置く(置かない組み立ては、行を運ぶ最初の束の読みで答え手の無い `Compute`
  として 500 internal になる)。PostgreSQL の handler を直に組む組み立ても同じ(読み解きの `Compute` は置き場の handler が出す)。
- 検と模擬の殻は `doeff_records.http_server.start_records_server(run(records_server_config(schema, handler_for, request_handlers=…)))`:
  入口の Program を別の thread の run で回し、表の用意の告知(`RecordsPrepared`)を待って `url` と `close()` を持つ `RunningServer` を返す
  (返った口は記録の操作に答えられる)。`handler_for` = 書き手の名 → 用意し終えた
  置き場の handler、`request_handlers` = 要求ごとの答えの外側に被せる handler の列(検の仮想の時計・SQL の答え手)、`meter` = 計器の
  答え手の差し替え(None = 既定 — 検が壊した計器を差す口)。束の読みの読み解きと綴りは殻が開く thread の pool で回る。

client の handler `doeff_records.http_client.http_records_handler(RecordsEndpoint(base_url, writer=…))` は、同じ公開 effect に口越しで
答える(書き手の名 `writer` を平文の見出し `X-Records-Writer`(綴りは `doeff_records.wire` の `WRITER-HEADER`)で送る。`Authorization` は送らない)。`404` は `UndeclaredTable` を上げる
(欄 = 要求が名指した名のうち断りの理由に載った物 — 断りの本文の形は変えない。理由の綴りは `wire.hy` の `undeclared-reason` と
`undeclared-refusal` の 1 か所)。`400` / `500` と、ほかの status(`401` / `403`・間の proxy の `502` など — status ごとの枝は持たない)は
一般の失敗で `WireError` を上げる(本文が契約の断りの形でない JSON の時だけ `WireMalformed`。口は `401` / `403` を出さない)。
`WatchChanges` の待ちは client の時計で回す(口へは待たない問い合わせだけを送る)。
置き場の止まり(#3557): 要求と答えの公開 effect 8 つは、届かない(`503` の `store-unavailable` を含む `Unreachable`)時に、待つ時間まで
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

- `SweepExpired()` → `Swept(rows, forgotten, write_keys)` — 保持の期限を過ぎた行を消して `RowRemoved` を積み、期限を過ぎた出来事を捨て、
  覚えた刻から `WRITE_KEY_MEMORY_SECONDS` を過ぎた行の書きの冪等キーの覚えを外し(`write_keys` = 外した数)、覚え方
  (`StreamDecl.key_memory`)の長さを過ぎた鍵の覚えを外す(`forgotten` = 外した覚えの数 — PostgreSQL は列ごとに覚えた刻の索引を辿り、
  500 個ずつ列の書きの錠の transaction を分ける。送ってから外す列は鍵の出来事が置き場に残る覚えを外さない)。回収はこの
  effect の時だけ走る(手入れの係が実行する)。読みは回収せず期限を自分で見る。書き(`PutRow`・`PutRows`・`AppendEvent`)も回収せず、
  自分が触る行と出来事だけを同じ transaction で片付けてから判じる — 期限を過ぎた行への書きはその行の `RowRemoved` を積んでから無い行として
  判じ、期限を過ぎた鍵への追記は鍵の覚えで答える(答えは回収の後の書きと同じ)。PostgreSQL の書き 1 回は書きの transaction の文だけを流す。
- `PruneChanges(keep_seconds)` → `Pruned(floor, removed)` — `keep_seconds` より古い変更を変更の列から消し、floor を上げる。
  floor より前の位置の `WatchChanges` は `Reset(epoch, floor)`(`WatchCursor(epoch, floor)` から読めば残った変更を頭から全部読める)。
- `ReadExpiredEvents(at, after, limit)` → `ExpiredEvents(items, next)` — 送ってから外す列(`StreamDecl.archive_before_retire`)の、
  刻 `at`(`GetTime` の時刻)の境で期限を過ぎた出来事(書きが冪等キーを覚えただけの出来事を含む)を、列の名の順・列の中は積んだ刻と番号の順に 1 頁
  (1 頁 = 1 つの列)。`next` = 続きの位置 `ExpiredCursor(stream, at, sequence)`(None = 読み終えた)。
- `RetireArchivedEvents(stream, at, sequences)` → `RetiredEvents(count)` — 番号 `sequences`(送り先に在ると確かめた出来事)のうち、
  刻 `at` の境で今も期限を過ぎた出来事だけを外し、冪等キーを覚える。
- `maintenance_loop(interval_seconds, keep_seconds, archive, ticks, archive_events)` — 手入れの係の本体(`ticks=None` で止めるまで)。
  `archive_events` = 出来事の送りの関数(期限を過ぎた出来事の頁 → `EventsArchived(sequences)` | `Unreachable` の Program・None = 送らない)。
  手入れの回は刻 T を 1 度読み、その境で期限を過ぎた出来事を頁で読んで送り、確かめた番号のうち頁に在った物だけを同じ刻 T の境で外す
  (読みと外しの間に新しく期限を過ぎた出来事は外れない)。送りが `Unreachable` の頁で回を終える(外さない)。結果
  `MaintenanceReport.events` = `EventsArchiveReport(sent, confirmed, unreached, retired, halted)`(送った・確かめた・届かなかった・外した数と、
  途中で終えた訳)。記録の service の手入れの計画 `MaintenancePlan` の `archive_events` がこの関数で、送ってから外す列を宣言した置き場に
  `archive_events` の無い手入れを立てると、`serve_records` は起動の前に `ValueError` で断る。

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

- `records-settings dsn-source` — DSN を返す Program `dsn-source` と env から設定の値 `RecordsSettings`(DSN・接頭辞・機体の名・接続の数・
  宛先・手入れ)を作る。接続 URL がどこに在るか(file・cluster の Secret など)は呼び手が決め、`records-settings` は知らない
- `pg-url-file-dsn dsn-of` — DSN を接続 URL の file から得る `dsn-source`(env `RECORDS_PG_URL_FILE` の path の file を `ReadText` で読み、
  `dsn-of` で DSN にする)。単独の入口 `serve-records-service` は `(records-settings (pg-url-file-dsn dsn-of))` を使う
- `records-serving schema settings choice` — 本体の設定 `RecordsServing` を作る。`choice` は置き場の選び `StoreChoice`(`doeff_records.store_choice` — 表の用意の作り手と /readyz の問い)で、PostgreSQL は `doeff_records.main` の `PG-STORE`、memory は `doeff_records.memory` の `memory-store-choice`
- `records-connected settings body` — 土台の口(待ち受け・名乗りと用意の告知の印字・PostgreSQL の答え手・束の読みの読み解きと綴りの
  `Compute` を回す計算の pool〔`COMPUTE-WORKERS` 本〕。接続と pool を開き、終われば閉じる)。
  外側は持たない。用意の告知(`RecordsPrepared`)に別の答えを持つ使い手は、`body` を自分の答え手で包んで渡す(印字より内側が先に答える)
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
| `law_descending_list_is_the_head_of_the_sorted_scan` | 上から読む `ListRows`(`descending_by`)は、全件を読んで欄の値が整数の行を並べた上から `limit` 件と同じで、続きは並べた行が `limit` 件を越える時だけ返す |
| `law_descending_pages_follow_the_sorted_scan` | 上から読む `ListRows` の `next_cursor` を辿った頁を連ねると、全件を (値, キー) の大きい順に並べた物と同じ(重なりも欠けも無い)。頁の間の書きは位置より小さい組だけが後の頁に出る。古い版の位置は `Reset` |
| `law_any_of_list_equals_the_union_of_equal_lists` | 値の組の `ListRows`(`where_any`)の頁を辿った全件は、組の値ごとの等号の `ListRows` の全件の和をキーの順に並べた物と同じ。上から読む読みを重ねた答えはその和の上から `limit` 件。索引でない欄は `NotIndexed` |
| `law_append_is_idempotent` | 同じ冪等キーの再送は前の番号・別の本文は `Refused` |
| `law_a_keyed_write_replays_its_answer` | 冪等キーつきの `PutRow`・`PutRows` の同じ鍵・同じ本文の送り直しは、間に別の書き手が行を変えていても(`ExpectAbsent`・`ExpectVersion`・`ExpectAny` のどれも)前の答え(版・値・位置)を `replayed` で返し、行を変えず変更を積まない。同じ鍵の別の本文は冪等キーの断り |
| `law_another_key_is_judged_by_its_expectation` | 同じ値でも別の鍵・鍵の無い書きは今までどおり期待で判じる(`Conflict`)。衝突した書きは覚えない |
| `law_write_keys_are_forgotten_after_their_memory` | 行の書きの冪等キーの覚えは `WRITE_KEY_MEMORY_SECONDS` の間は送り直しに前の答えを返し、過ぎた後の `SweepExpired` が外す(外れた鍵の書きは期待で判じる) |
| `law_watch_waits_for_a_change` | `WatchChanges` は変更が来るまで `timeout` まで待つ |
| `law_none_removes_a_field` | 差分の値 None はその欄を消し、行の値は None を持たない |
| `law_maintenance_prunes_and_sweeps` | 刈った変更より前の位置は `Reset`・floor の位置からは続けられ、行は消えない。回収は期限切れの行だけを 1 回消す |
| `law_append_events_answer_like_appends_in_order` | `AppendEvents` の答えは束の順に `AppendEvent` を撃った答えと同じ — 追記ごとの再送・断り・新しい番号を返し、断られた追記は他の追記を止めず、今積んだ番号は束の順に増え、列をまたぐ束も 1 つで積む |
| `law_put_rows_is_all_or_nothing` | `PutRows` は全部通る束だけを書き(束の順の `Written`)、期待のずれ 1 行・断り 1 行の束は 1 行も書かない。期待のずれを断りより先に答え、確定した束の変更は束の順に続いた番号で見える |
| `law_a_write_names_its_change_position` | `PutRow` の `Written` と `PutRows` の各項の `position` は、その書きの変更が `WatchChanges` に現れる番号と置き場の版。束の中は束の順に続き、最後の書きの位置は書いた直後の一覧の頭と同じ。位置から読む `WatchChanges` はその後の変更だけを返し、版を上げた置き場の書きは新しい版を名乗る |
| `law_expired_keys_are_remembered` | 保持の期限で出来事を消した後も冪等キーは忘れない: 同じ本文の再送は前の番号で列の出来事を増やさず、別の本文は `Refused` |
| `law_expired_records_are_unseen_before_a_sweep` | 保持の期限を過ぎた行と出来事(出来事ごと・組ごとの列)は、回収の前でも 6 つの読みのどれにも出ない。期限を過ぎた行の `RowRemoved` は別の表への書きでは積まれず、回収(`SweepExpired`)が 1 回だけ積む |
| `law_a_write_clears_the_expired_row_it_touches` | 回収されていない期限を過ぎた行への `PutRow`・`PutRows` は、その行を消して `RowRemoved` を積んでから無い行として判じる(`ExpectAbsent` は版 1 で生まれ、消えた行の版の `ExpectVersion` は `Conflict(Missing)`)— 回収の後の書きと同じ答え |
| `law_an_expired_key_answers_the_same_before_and_after_a_sweep` | 回収されていない期限を過ぎた冪等キーへの追記は、回収の後と同じ答え(同じ本文は前の番号・別の本文は同じ文の断り)。期限を過ぎた組に新しい鍵を積んでも、組の古い出来事は読みに戻らない |
| `law_only_confirmed_events_are_retired` | 送ってから外す列の期限を過ぎた出来事は、回収(`SweepExpired`)でも送り先に届かない回でも外れず、送り先に在ると確かめた出来事だけが外れる(出来事ごと・組ごとの列)。外す前も後も読みに出ず、冪等キーの答えは同じ |
| `law_retirement_keeps_the_read_boundary` | 送りの読みの後に期限を過ぎた出来事は、送りの関数がその番号を答えても同じ回の外しで外れない(外しの境は読みと同じ刻 T) |
| `law_a_write_keeps_the_unarchived_event_it_touches` | 送ってから外す列の期限を過ぎた鍵・組に触る追記は出来事を外さないが、答えは印の無い列と同じ(同じ本文は前の番号・別の本文は同じ文の断り・期限を過ぎた組の古い出来事は新しい鍵の追記で読みに戻らない) |
| `law_keys_are_forgotten_after_their_memory` | 覚えを長さで限った列の去った鍵は、長さの間は覚えで答え(前の番号・断り・`EventRetired`)、長さの後の回収で覚えが外れて再送は新しい出来事(`EventAbsent`)。覚えを残さない列の去った鍵の再送は、回収の後も、回収の前に書きが片付けた時も新しい出来事 |
| `law_unarchived_keys_are_kept_until_retired` | 覚え方を限った送ってから外す列の覚えは、送り先に届かない間は長さを過ぎても外れず答えも変わらない。確かめて外した後、残さない列の覚えは同じ外しで、長さの列の覚えは覚えた刻から長さの後の回収で外れる |

使い方: `LAW_SCHEMA` の定義で置き場を作り、`LawHarness(as_writer)`(書き手の名と Program → その書き手の handler で包んだ
Program)を法に渡す。法は答えを順に並べた list を返すので、2 つの handler の組で同じ法を回して list を比べれば、答えが同じことも
確かめられる(`SHARED_LAWS` は時間を進めない法のうち、前からの 6 つの effect だけで回る法 — `PutRows` を答えない handler の組でも回せる)。置き場の版を進める検の口は `doeff_records.faults.AdvanceStoreEpoch`(公開 effect ではない)。置き場に届かない状態を起こす・戻す検の口は `doeff_records.faults.SetStoreOutage`(届かない理由 detail と、対象の表と追記の列の名 names — None で全部)で、memory の置き場が答える: 届かない間、名に当たる公開 effect は `Unreachable(detail)` を答え、置き場を変えない(本番の記録の口が service に届かない時と同じ答え)。記録の service の不達を筋書きにする検と模擬は、業務の effect に答える偽の handler を書かず、この口で正典の置き場を届かなくする。置き場の一部だけの断りを起こす・外す検の口は `doeff_records.faults.AddStoreFault(StoreFault(...))` と `ClearStoreFaults(names)`(names と名が重なる故障を外す — None で全部)で、これも memory の置き場が答える。`StoreFault` は名 names(表と追記の列の名の frozenset)・操作 operation(`StoreOperation.READ` = `ReadRow`・`ListRows`・`WatchChanges`・`ReadEvents`・`ReadStreamEnd` / `StoreOperation.WRITE` = `PutRow`・`PutRows`・`AppendEvent`・`AppendEvents`)・答え answer(`Refused` か `Unreachable`)・lands(True なら書きを置き場に着けてから答えを差し替える — 書きは届いたが答えが切れた形)・matching(effect を受けて当たるかを返す関数 — None で名と操作に当たる全部)・times(当たる回数 — None で外すまで当たり続け、数を持つ故障は当たるたびに 1 減って尽きた拍に外れる。lands と組むと「確定した後で答えが 1 度だけ切れた」形になり、撃ち直す client の次の要求は置き場に届く — `tests/test_client_replays_a_landed_write.hy`)を持つ。`PutRows` は束の中に当たる表が 1 つでもあれば束ごと当たる。`AppendEvents` は束の追記ごとに、同じ追記の `AppendEvent` として当たる(置き場に着けない `Unreachable` の故障が 1 つでも当たれば束のどれも積まずに `Unreachable`・`Refused` の故障はその追記だけを断る)。故障は置いた順に探して最初の 1 つが答え、置き場全体の不達(`SetStoreOutage`)がそれより先に答える。doeff の実行の外で筋書きを組む使い手は、同じ書きの同期の口 `doeff_records.memory.memory-add-fault` / `memory-clear-faults` を使う。

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
