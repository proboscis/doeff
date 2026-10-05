;;; PostgreSQL の handler — 公開 effect 8 つに PostgreSQL の表で答える(本番の置き場)。
;;; PutRows は PutRow と同じ置き場の錠と transaction 1 つの中で、全部の行を検めてから書く(途中の失敗は transaction ごと戻る)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ(memory の handler と同じ関数)。
;;; 文は pg_sql.hy(純粋・中立の記法)で、この file はそれを doeff の汎用の SQL の effect(SqlQuery・SqlTransaction)に載せる
;;; Program だけを持つ(#880 U5・U6)。接続・driver・方言への書き換えは答え手(doeff_core_effects の
;;; postgres-sql-handler / pooled-postgres-sql-handler)の持ち物で、組み立ての側(composition root)がこの handler の外側に置く。
;;;
;;; 失敗の読み替え:
;;;   - 答え手の SqlUnreachable(接続できない・切れた — 読み分けは答え手の postgres-failure)を、公開 effect の答え Unreachable にする。
;;;   - 答え手の SqlFailed(engine の失敗 — 文と表の形の食い違い)は実装の誤りとして RecordsSqlFailed を上げる(旧い版で psycopg の
;;;     接続の失敗でない例外を上げたのと同じ扱い)。
;;; 書きの手順(PutRow・PutRows・AppendEvent・版の繰り上げ・期限切れの回収・刈り取り)は SqlTransaction の lock-key = 置き場の書きの錠
;;; (pg_sql.writer-lock-key — 旧い版と同じ錠の番号)の中で流す。transaction の中では SqlQuery と純粋な計算だけを出す(時刻は前に読んで渡す)。
;;; 読みの手順(行・一覧・変更の列・追記の読み)は SqlQuery を 1 文ずつ流す(自動 commit — 旧い版と同じ断面)。
;;; 変化の待ち(WatchChanges・WatchEvents — #3073): 書きの錠の transaction は最後に置き場の通知の channel へ合図(SqlNotify)を
;;; 出す(commit した時だけ届く — 巻き戻した書きの合図は届かない)。待ちは呼び鈴(SqlHangNotice)を掛けてから読み、静かなら呼び鈴か
;;; timeout を待って読み直す(watching.wait-for-signal)。読み直しの間隔で起きない。合図は中身を運ばず、待ち手が変更の列から読み直す。
;;; 待ち受けの接続が繋ぎ直した時も呼び鈴が鳴るので、その間の書きも読み直しで拾う。
;;; 読み手の窓の read-modify-write の競合(同じ読み手の位置を 2 つの要求が同時に読んで進める)は旧い版と同じで、ここでは直さない
;;; (#880 の構成のレビュー A4)。
;;; 保持の期限(#3561): 読みの効果(ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd)は回収を流さず、期限を
;;; 過ぎた行と出来事を読みの文の条件で除く(回収と同じ境 — row-expiry・event-expiry・pg_sql の頭の註)。読み 1 回が流すのは読みの文だけ。
;;; 回収(期限を過ぎた行を消して変更の列に「消えた」を積み、出来事を冪等キーの覚えへ移す — purge-expired)は SweepExpired(手入れの係)の
;;; 時だけ流す。時間で起きて回収する loop は持たない。
;;; 書き(PutRow・PutRows・AppendEvent — #3605 の D)は回収を流さず、書きの transaction の文だけを流す — 記録の service と DB が別の機体に
;;; 在ると文 1 つが往復 1 回で、書きのたびの回収の候補の読み(1 + 期限つきの列の数)が書きの待ちに乗っていた。答えは回収の後の書きと同じに
;;; 保つ: 書きが触る物だけを同じ transaction で片付けてから判じる — 行の書きは錠つきで読んだ今の行が期限を過ぎていればその行を消して
;;; 「消えた」を積み、無い行として判じる(locked-row)。追記は冪等キーの単位(出来事ごとに数える列は鍵の出来事・組で数える列は鍵の組)の
;;; 期限を過ぎた出来事を捨てて鍵の覚えへ移してから、前の使いを引く(retire-touched-events)。触らない行と出来事は SweepExpired まで置き場に
;;; 残り(読みには出ない)、その行の「消えた」も SweepExpired が積む。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetTime])
(import doeff_core_effects.scheduler [ExternalPromise])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlNotify SqlHangNotice SqlDropNotice SqlRows SqlFailed
                                        SqlUnreachable])
(import doeff_records.values [RecordsSchema TableDecl StreamDecl KeepFor ByKeySuffix Row Missing Page Written WrittenRows RowChanged
                              RowRemoved Changes Appended Conflict NotIndexed RetiredKey EventsMoved EventsQuiet StreamEnd StreamEmpty
                              Event Events Reset WatchCursor ListCursor Refused Unreachable RowsConflict RowsRefused])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.event_source [RECORDS-SIGNAL-SOURCE ReadSignalSource])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [AppendReplay judge-expect judge-put judge-put-rows judge-append row-expired? retention-cutoff-ms
                                 where-refusal listed-row key-text key-from-text canonical-json next-watch-sequence epoch-ms body-digest])
(import doeff_records.watching [wait-for-signal moved-of])
(import doeff_records.pg_sql [Statement RowExpiry EventExpiry DEFAULT-PREFIX checked-prefix writer-lock-key migrate-lock-key
                              schema-statements drop-statements
                              store-head-statement read-row-statement lock-row-statement list-rows-statement
                              terminal-rows-statement upsert-row-statement delete-row-statement append-change-statement
                              changes-statement advance-epoch-statement forget-changes-statement prune-changes-statement find-event-statement
                              insert-event-statement read-events-statement stream-end-statement expire-events-statement
                              expire-event-groups-statement expire-touched-events-statement expiring-events-statement
                              retire-keys-statement find-retired-key-statement])

(val MODULE-TAGS {:context "records" :role "foundation"})


(defclass RecordsSqlFailed [RuntimeError]
  "engine が答えた失敗(SqlFailed)— 文か表の形の食い違いで、実装の誤り。sqlstate = SQLSTATE(5 文字か None)。"
  (defn #^ None __init__ [self #^ (| str None) sqlstate #^ str reason]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "PostgreSQL が断った({}): {}" sqlstate reason))
    (setv self.sqlstate sqlstate)))


(defclass StoreUnreachable [Exception]
  "文 1 つが置き場に届かなかった(SqlUnreachable)。handler の節の入口(reached)が捕まえて Unreachable の答えにする —
   Program の奥の手順から 1 つずつ答えを返して回らないための印で、handler の外へは出ない(用意と版の繰り上げを除く)。")


(defrecord PreparedStore
  "表を用意し終えた置き場: database = SqlQuery に書く database の名 / schema = 宣言 / prefix = 表の名の接頭辞(検め済み)。
   作るのは prepare-records-store だけで、pg-records-handler はこれを受け取る — 表を用意せずに handler は組めず、handler は要求ごとに
   表を用意し直さない。"
  (#^ str database)
  (#^ RecordsSchema schema)
  (#^ str prefix))


(defrecord StoreHead
  "置き場の頭: epoch = 置き場の版 / floor = 変更の列の最も古い番号の手前(これより前の位置は Reset)/ head = 最新の変更の番号。"
  (#^ int epoch)
  (#^ int floor)
  (#^ int head))


(defrecord ExpiredRow
  "保持の期限を過ぎた行 1 つ: table = 表の名 / record = state_rows の行(key payload version updated_ms)。"
  (#^ str table)
  (#^ tuple record))


;; --- 文を流す ----------------------------------------------------------------------------------------------

(defk query-rows [database statement]
  {:pre [(: database str) (: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "文 1 つを流して行を返すため(届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる)。
   transaction の中では失敗の答えは program へ戻らない(答え手が rollback して SqlTransaction の答えにする)。"
  (<- answer (SqlQuery database statement.text statement.params))
  (match answer
    (SqlRows :rows rows) rows
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))))


(defk in-transaction [database lock-key program]
  {:pre [(: database str) (: lock-key str) (: program Program)] :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を錠 lock-key の transaction 1 つで流すため(届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる)。"
  (<- answer (SqlTransaction database program :lock-key lock-key))
  (match answer
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ answer))


(defk notice-channel [store]
  {:pre [(: store PreparedStore)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel の名(表の名の接頭辞ごとに 1 つ — 同じ database の別の置き場の書きで起きない)。"
  (.format "{}changes" store.prefix))


(defk signalled [store program]
  {:pre [(: store PreparedStore) (: program Program)] :post [(: % "program の答え")]
   :tags {:context "records" :role "foundation"}}
  "program を流した後、同じ transaction の中で置き場の通知の channel へ合図を出すため(commit した時だけ届く — 頭の註)。"
  (<- answer program)
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel))
  (match notified
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ answer))


(defk writing [store program]
  {:pre [(: store PreparedStore) (: program Program)] :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を置き場の書きの錠の transaction で流し、同じ transaction で書きの合図を出すため(旧い版の lock-statement と同じ錠の番号 —
   pg_sql.hy の頭の註)。"
  (<- key (writer-lock-key store.prefix))
  (<- answer (in-transaction store.database key (signalled store program)))
  answer)


(defk hung-bell [store]
  {:pre [(: store PreparedStore)] :post [(: % (| ExternalPromise Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel に呼び鈴を掛けるため(待ち受けに届かなければ Unreachable)。"
  (<- channel (notice-channel store))
  (<- bell (SqlHangNotice store.database channel))
  (match bell
    (SqlUnreachable :reason reason) (Unreachable (.format "PostgreSQL に届かない: {}" reason))
    _ bell))


(defk dropped-bell [store bell]
  {:pre [(: store PreparedStore) (: bell ExternalPromise)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "鳴らなかった呼び鈴を外すため。"
  (<- channel (notice-channel store))
  (<- (SqlDropNotice store.database channel bell))
  None)


(defk reached [program]
  {:pre [(: program Program)] :post [(: % "program の答え | Unreachable") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を流し、置き場に届かなかった事を Unreachable の答えにするため(公開 effect の答えの境目 — handler の節が呼ぶ)。"
  (try
    (<- answer program)
    answer
    (except [error StoreUnreachable]
      (Unreachable (.format "PostgreSQL に届かない: {}" error)))))


(defk run-statements [database statements]
  {:pre [(: database str) (: statements tuple)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "文の列を順に流すため(用意と後片付け)。"
  (for [statement statements]
    (<- (query-rows database statement)))
  None)


;; --- 用意と後片付け -----------------------------------------------------------------------------------------

(defk prepare-records-store [database schema prefix]
  {:pre [(: database str) (: schema RecordsSchema) (: prefix str)] :post [(: % PreparedStore)]
   :tags {:context "records" :role "foundation"}}
  "表を用意する(移行)— process ごとに 1 度、composition root が要求を受ける前に流す。文は移行の錠の transaction の中で流す:
   同じ置き場を同時に用意する別の process(replicas > 1・入れ替えの重なり)は錠を待ち、先の commit の後で IF NOT EXISTS が
   何もしない(錠が無いと同時の CREATE INDEX IF NOT EXISTS が競って遅れた方が UniqueViolation)。
   届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる(起動を止める)。"
  (<- checked (checked-prefix prefix))
  (<- statements (schema-statements checked schema))
  (<- key (migrate-lock-key checked))
  (<- (in-transaction database key (run-statements database statements)))
  (PreparedStore :database database :schema schema :prefix checked))


(defk drop-records-tables [store]
  {:pre [(: store PreparedStore)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "検の後片付け: この置き場の接頭辞の表を消す(検の解釈器だけが呼ぶ)。"
  (<- statements (drop-statements store.prefix))
  (<- (run-statements store.database statements))
  None)


;; --- 行の綴り ----------------------------------------------------------------------------------------------

(defk store-head [store]
  {:pre [(: store PreparedStore)] :post [(: % StoreHead)]
   :tags {:context "records" :role "foundation"}}
  "置き場の頭を読むため。"
  (<- statement (store-head-statement store.prefix))
  (<- rows (query-rows store.database statement))
  (val record (get rows 0))
  (StoreHead :epoch (int (get record 0)) :floor (int (get record 1)) :head (int (get record 2))))


(defk decoded-value [payload]
  {:pre [(: payload str)] :post [(: % FrozenMap)]
   :tags {:context "records" :role "foundation"}}
  "state_rows / row_changes の payload(JSON の綴り)を行の値(深く凍らせた写像)にするため。DB から読む境界はここ 1 か所。"
  (frozen-json-object (json.loads payload) "state_rows の payload"))


(defk row-of [record]
  {:pre [(: record tuple)] :post [(: % Row)]
   :tags {:context "records" :role "foundation"}}
  "state_rows の行(key payload version …)を Row にするため。"
  (Row (key-from-text (get record 0)) (! (decoded-value (get record 1))) (int (get record 2))))


(defk change-of [record]
  {:pre [(: record tuple)] :post [(: % (| RowChanged RowRemoved))]
   :tags {:context "records" :role "foundation"}}
  "row_changes の行(seq ledger key version payload at)を RowChanged | RowRemoved にするため。"
  (val payload (get record 4))
  (val key (key-from-text (get record 2)))
  (if (is payload None)
      (RowRemoved (get record 1) key (int (get record 0)))
      (RowChanged (get record 1) key (int (get record 3)) (! (decoded-value payload)) (int (get record 0)) (int (get record 5)))))


(defk event-of [stream record]
  {:pre [(: stream str) (: record tuple)] :post [(: % Event)]
   :tags {:context "records" :role "foundation"}}
  "append_rows の行(seq at payload)を Event にするため。"
  (val decoded (json.loads (get record 2)))
  (Event stream (int (get record 0)) (get decoded "idempotencyKey") (get decoded "body") (get decoded "writer") (int (get record 1))))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defk row-expiry [decl now-ms]
  {:pre [(: decl TableDecl) (: now-ms int)] :post [(: % (| RowExpiry None))]
   :tags {:context "records" :role "foundation"}}
  "表 decl の行の保持の期限の境(読みの文の条件 — pg_sql.RowExpiry)を、刻 now-ms で作るため(None = 期限の無い表・終端の語の無い表 —
   読みの文に条件を足さない)。境の刻は admission.retention-cutoff-ms(回収の row-expired? と同じ 1 つ)。"
  (val cutoff (retention-cutoff-ms decl now-ms))
  (if (and (is-not cutoff None) decl.terminal)
      (RowExpiry :table decl.name :state-field decl.state-field :terminal decl.terminal :before-at cutoff)
      None))


(defk event-expiry [decl now-ms]
  {:pre [(: decl StreamDecl) (: now-ms int)] :post [(: % (| EventExpiry None))]
   :tags {:context "records" :role "foundation"}}
  "追記の列 decl の出来事の保持の期限の境(pg_sql.EventExpiry — 境の刻と組で数える列の区切り)を、刻 now-ms で作るため(None = 期限の
   無い列)。読みの文の条件と回収の文(purge-expired)がこの 1 つを使う。境の刻は admission.retention-cutoff-ms。"
  (val cutoff (retention-cutoff-ms decl now-ms))
  (if (is cutoff None)
      None
      (EventExpiry :before-at cutoff
                   :separator (match decl.retention-group
                                (ByKeySuffix :separator text) text
                                _ None))))


(defk expired-rows [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "期限を過ぎた行(ExpiredRow)の列(表の名の順・鍵の順)を読むため。判断は admission.row-expired?。
   終端の語が空の表は文を流さない(`IN ()` は文にできない — 宣言は KeepFor の表に終端の語を求めるので、今は起きない)。"
  (var found [])
  (for [#(name decl) (sorted (.items store.schema.tables))]
    (when (and (isinstance decl.retention KeepFor) decl.terminal)
      (<- statement (terminal-rows-statement store.prefix name decl.state-field decl.terminal))
      (<- records (query-rows store.database statement))
      (for [record records]
        (<- value (decoded-value (get record 1)))
        (when (row-expired? decl value (int (get record 3)) now-ms)
          (.append found (ExpiredRow :table name :record (tuple record)))))))
  (tuple found))


(defk remove-row [store table text version now-ms epoch]
  {:pre [(: store PreparedStore) (: table str) (: text str) (: version int) (: now-ms int) (: epoch int)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、保持の期限を過ぎた行 1 つ(版 version)を消し、変更の列に「消えた」を積むため(回収 remove-expired-rows と、
   期限を過ぎた行へ書く書きの片付け locked-row が同じ 2 文を使う)。"
  (<- delete (delete-row-statement store.prefix table text version))
  (<- (query-rows store.database delete))
  (<- change (append-change-statement store.prefix table text version None now-ms epoch))
  (<- (query-rows store.database change))
  None)


(defk remove-expired-rows [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、期限を過ぎた行を読み直して消し、変更の列に「消えた」を積むため。答え = 消した行の数。"
  (<- head (store-head store))
  (<- expired (expired-rows store now-ms))
  (for [row expired]
    (<- (remove-row store row.table (get row.record 0) (int (get row.record 2)) now-ms head.epoch)))
  (len expired))


(defk remember-retired-keys [store stream removed]
  {:pre [(: store PreparedStore) (: stream str) (: removed tuple)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、保持の期限で捨てた出来事(append_rows の行 seq at payload の組 removed)の冪等キーの覚え(番号と本文の
   指紋)を同じ transaction で鍵だけの表へ入れるため(#3022 — 消した後の同じ鍵の追記も append-locked が同じ規則で判じる。出来事だけ消えて
   覚えが無い断面を作らない)。回収(retire-expired-events)と書きの片付け(retire-touched-events)が使う。"
  (var kept #())
  (for [record removed]
    (<- event (event-of stream record))
    (:= kept (+ kept #((RetiredKey :idempotency-key event.idempotency-key :sequence event.sequence
                                   :body-digest (body-digest event.body))))))
  (when kept
    (<- retire (retire-keys-statement store.prefix stream kept))
    (<- (query-rows store.database retire)))
  None)


(defk retire-expired-events [store stream before-at separator]
  {:pre [(: store PreparedStore) (: stream str) (: before-at int) (: separator (| str None))] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、列 stream の保持の期限を過ぎた出来事を捨て、冪等キーの覚えへ移すため(回収 — remember-retired-keys)。
   separator = None は出来事ごとに数える列・str は組で数える列の区切り。答え = 捨てた出来事の数。"
  (<- delete (if (is separator None)
                 (expire-events-statement store.prefix stream before-at)
                 (expire-event-groups-statement store.prefix stream before-at separator)))
  (<- removed (query-rows store.database delete))
  (<- (remember-retired-keys store stream removed))
  (len removed))


(defk purge-expired [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "期限を過ぎた行を消して変更の列に「消えた」を積み、期限を過ぎた出来事を捨てて冪等キーの覚えへ移すため(SweepExpired の掃除 — 読みは
   回収を待たずに期限を自分で見て、書きは自分が触る物だけを片付ける・頭の註)。候補が無ければ錠を取らない(行も出来事も、候補の読み 1 文の
   後に、候補が在る時だけ書きの錠の transaction を開く)。答え = 消した行の数。"
  (<- candidates (expired-rows store now-ms))
  (var removed 0)
  (when candidates
    (<- count (writing store (remove-expired-rows store now-ms)))
    (:= removed count))
  (for [#(name decl) (sorted (.items store.schema.streams))]
    (<- expiry (event-expiry decl now-ms))
    (when (is-not expiry None)
      (<- probe (expiring-events-statement store.prefix name expiry.before-at expiry.separator))
      (<- due (query-rows store.database probe))
      (when due
        (<- (writing store (retire-expired-events store name expiry.before-at expiry.separator))))))
  removed)


;; --- 行 --------------------------------------------------------------------------------------------------

(defk pg-read-row [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadRow) (: now-ms int)] :post [(: % (| Row Missing))]
   :tags {:context "records" :role "foundation"}}
  "ReadRow に答えるため(刻 now-ms で保持の期限を過ぎた終端の行は、回収の前でも Missing — 文の条件・頭の註)。"
  (val decl (store.schema.table ask.table))
  (<- expiry (row-expiry decl now-ms))
  (<- statement (read-row-statement store.prefix ask.table (key-text ask.key) expiry))
  (<- records (query-rows store.database statement))
  (if records (! (row-of (get records 0))) (Missing)))


(defk pg-list-rows [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ListRows) (: now-ms int)] :post [(: % (| Page Reset NotIndexed))]
   :tags {:context "records" :role "foundation"}}
  "ListRows に答えるため(刻 now-ms で保持の期限を過ぎた終端の行は、回収の前でも頁に出さない — 文の条件・頭の註)。"
  (val decl (store.schema.table ask.table))
  (<- head (store-head store))
  (when (and (is-not ask.cursor None) (!= ask.cursor.epoch head.epoch))
    (return (Reset head.epoch head.floor)))
  (val refusal (where-refusal decl ask.where))
  (when refusal (return refusal))
  (<- expiry (row-expiry decl now-ms))
  (<- statement (list-rows-statement store.prefix ask.table
                                     (if (is ask.cursor None) None ask.cursor.after-key)
                                     (dfor #(name value) (.items ask.where) name (canonical-json value))
                                     ask.limit
                                     expiry))
  (<- records (query-rows store.database statement))
  (val taken (cut records ask.limit))
  (var rows [])
  (for [record taken]
    (<- row (row-of record))
    (.append rows (listed-row decl ask.fields row)))
  (Page (tuple rows)
        (if (> (len records) ask.limit) (ListCursor head.epoch (get (get taken -1) 0)) None)
        head.epoch
        head.head))


(defk locked-row [store decl text now-ms epoch]
  {:pre [(: store PreparedStore) (: decl TableDecl) (: text str) (: now-ms int) (: epoch int)] :post [(: % (| Row None))]
   :tags {:context "records" :role "foundation"}}
  "書きの判定に渡す今の行(無ければ None)を、行の錠(FOR UPDATE)つきで読むため(書きの transaction の中で呼ぶ)。読んだ行が刻 now-ms で
   保持の期限を過ぎた終端の行なら(回収と同じ判定 admission.row-expired?)、回収と同じくその行を消して変更の列に「消えた」を積み、None を
   返す — 書きは回収の後と同じく無い行として判じる(#3605 の D・頭の註)。epoch = 「消えた」に刻む置き場の版。"
  (<- statement (lock-row-statement store.prefix decl.name text))
  (<- records (query-rows store.database statement))
  (when (not records)
    (return None))
  (val record (get records 0))
  (<- row (row-of record))
  (when (row-expired? decl row.value (int (get record 3)) now-ms)
    (<- (remove-row store decl.name text row.version now-ms epoch))
    (return None))
  row)


(defk store-row [store origin-host writer table text current value now-ms epoch]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: table str) (: text str) (: current (| Row None))
         (: value FrozenMap) (: now-ms int) (: epoch int)]
   :post [(: % Written)]
   :tags {:context "records" :role "foundation"}}
  "判定を通った 1 行を書き、変更の列に 1 つ積むため(PutRow と PutRows の書きを 1 つにし、版と番号の採り方を揃える)。"
  (val version (if (is current None) 1 (+ current.version 1)))
  (val payload (canonical-json value))
  (<- upsert (upsert-row-statement store.prefix table text payload version now-ms writer origin-host epoch))
  (<- (query-rows store.database upsert))
  (<- change (append-change-statement store.prefix table text version payload now-ms epoch))
  (<- (query-rows store.database change))
  (Written version value))


(defk put-row-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRow) (: now-ms int)]
   :post [(: % (| Written Conflict Refused))]
   :tags {:context "records" :role "foundation"}}
  "PutRow の書きの錠の中の手順(今の行を読み、判定し、通れば書く)を流すため。"
  (val decl (store.schema.table ask.table))
  (val text (key-text ask.key))
  (<- head (store-head store))
  (<- current (locked-row store decl text now-ms head.epoch))
  (val conflict (judge-expect ask.expect current))
  (when conflict (return conflict))
  (val verdict (judge-put decl current ask.key ask.value))
  (when (isinstance verdict Refused) (return verdict))
  (<- written (store-row store origin-host writer ask.table text current verdict.value now-ms head.epoch))
  written)


(defk put-rows-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRows) (: now-ms int)]
   :post [(: % (| WrittenRows RowsConflict RowsRefused))]
   :tags {:context "records" :role "foundation"}}
  "PutRows の束を全部か 0 で書くため: 書きの錠の中で全部の行を錠つきで読み(期限を過ぎた行は消して無い行として — locked-row)、
   判定(admission.judge-put-rows)が全部通った時だけ束の順に書いて変更を積む(錠の中なので番号は束の中で続く)。書きの途中の失敗は
   transaction ごと戻る。"
  (for [write ask.writes] (store.schema.table write.table))
  (val texts (tuple (gfor write ask.writes (key-text write.key))))
  (<- head (store-head store))
  (var currents #())
  (for [#(write text) (zip ask.writes texts :strict True)]
    (<- current (locked-row store (store.schema.table write.table) text now-ms head.epoch))
    (:= currents (+ currents #(current))))
  (val verdict (judge-put-rows store.schema ask.writes currents))
  (when (not (isinstance verdict tuple)) (return verdict))
  (var written [])
  (for [#(write text current admitted) (zip ask.writes texts currents verdict :strict True)]
    (<- one (store-row store origin-host writer write.table text current admitted.value now-ms head.epoch))
    (.append written one))
  (WrittenRows (tuple written)))


(defk table-expiries [store tables now-ms]
  {:pre [(: store PreparedStore) (: tables tuple) (: now-ms int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表 tables のうち保持の期限の在る表の境(RowExpiry の組 — 頼んだ表の順)を刻 now-ms で作るため(変更の列の読みの条件)。"
  (var expiries #())
  (for [name tables]
    (<- expiry (row-expiry (store.schema.table name) now-ms))
    (when (is-not expiry None)
      (:= expiries (+ expiries #(expiry)))))
  expiries)


(defk pg-watch-scan [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask WatchChanges) (: now-ms int)] :post [(: % (| Changes Reset))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges の 1 回ぶんの読み(待たない)を流すため。行の今の値が刻 now-ms で保持の期限を過ぎた終端の行である行の変わりは、回収の
   前でも出さない(文の条件・頭の註)。頼んだ表が空なら文を流さない(`IN ()` は文にできない — WatchChanges は空の組を作る時に断るので、
   今は起きない)。"
  (for [name ask.tables] (store.schema.table name))
  (<- head (store-head store))
  (val cursor ask.cursor)
  (when (or (!= cursor.epoch head.epoch) (< cursor.sequence head.floor) (> cursor.sequence head.head))
    (return (Reset head.epoch head.floor)))
  (var items [])
  (when ask.tables
    (<- expiries (table-expiries store (tuple ask.tables) now-ms))
    (<- statement (changes-statement store.prefix cursor.sequence head.head ask.tables ask.limit expiries))
    (<- records (query-rows store.database statement))
    (for [record records]
      (<- change (change-of record))
      (.append items change)))
  (Changes (tuple items) (WatchCursor head.epoch (next-watch-sequence (tuple items) ask.limit head.head))))


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defk retire-touched-events [store decl idempotency-key found now-ms]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: idempotency-key str) (: found (| Event None)) (: now-ms int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "追記が触る単位(出来事ごとに数える列は冪等キーの出来事・組で数える列は鍵の組)の保持の期限を過ぎた出来事を、回収と同じく捨てて冪等キーの
   覚えへ移すため(追記の書きの錠の中・#3605 の D — 書きは置き場の全部を回収しない。答えは回収の後の追記と同じ: 期限を過ぎた鍵は覚えで
   判じ、期限を過ぎた組に新しい鍵を積んでも組の古い出来事は読みに戻らない)。found = 鍵の生きた出来事(find-event の答え・無ければ None)。
   文を流すのは捨てる物が在り得る時だけ: 組で数える列は毎回(組の他の鍵の出来事は鍵の引きでは分からない)・出来事ごとに数える列は found が
   境の刻(event-expiry — 回収と読みと同じ境)以前に積まれた時だけ(新しい鍵の追記と、期限の前の鍵の再送は文を足さない)。
   答え = 捨てた出来事の番号の組。"
  (<- expiry (event-expiry decl now-ms))
  (val touched (and (is-not expiry None)
                    (or (is-not expiry.separator None)
                        (and (is-not found None) (<= found.at expiry.before-at)))))
  (when (not touched)
    (return #()))
  (<- delete (expire-touched-events-statement store.prefix decl.name expiry.before-at expiry.separator idempotency-key))
  (<- removed (query-rows store.database delete))
  (<- (remember-retired-keys store decl.name removed))
  (tuple (gfor record removed (int (get record 0)))))


(defk earlier-use [store decl idempotency-key now-ms]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: idempotency-key str) (: now-ms int)] :post [(: % (| Event RetiredKey None))]
   :tags {:context "records" :role "foundation"}}
  "冪等キーの前の使いを引くため(追記の書きの錠の中で呼ぶ): 追記が触る単位の期限を過ぎた出来事を先に捨てて覚えへ移し
   (retire-touched-events — 刻 now-ms)、生きた出来事が残っていればそれ・無ければ保持の期限で出来事を消した鍵の覚え(鍵だけの表を主鍵で
   1 行 — #3022)・どちらも無ければ None。出来事を消して覚えを入れる刈りも同じ書きの錠の transaction なので、引きの間に刈りは挟まらない。"
  (val stream decl.name)
  (<- find (find-event-statement store.prefix stream idempotency-key))
  (<- found (query-rows store.database find))
  (var live None)
  (when found
    (<- event (event-of stream (get found 0)))
    (:= live event))
  (<- removed (retire-touched-events store decl idempotency-key live now-ms))
  (when (and (is-not live None) (not-in live.sequence removed))
    (return live))
  (<- lookup (find-retired-key-statement store.prefix stream idempotency-key))
  (<- kept (query-rows store.database lookup))
  (if kept
      (RetiredKey :idempotency-key idempotency-key :sequence (int (get kept 0 0)) :body-digest (get kept 0 1))
      None))


(defk append-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask AppendEvent) (: now-ms int)]
   :post [(: % (| Appended Refused))]
   :tags {:context "records" :role "foundation"}}
  "AppendEvent の書きの錠の中の手順(触る単位の期限を過ぎた出来事を片付けて冪等キーの前の使いを引き、判定し、新しければ積む)を流すため。"
  (val decl (store.schema.stream ask.stream))
  (<- head (store-head store))
  (<- previous (earlier-use store decl ask.idempotency-key now-ms))
  (val verdict (judge-append decl ask.body previous))
  (when (isinstance verdict Refused) (return verdict))
  (when (isinstance verdict AppendReplay) (return (Appended verdict.sequence)))
  (val payload (canonical-json {"idempotencyKey" ask.idempotency-key "writer" writer "body" ask.body}))
  (<- insert (insert-event-statement store.prefix ask.stream now-ms payload origin-host head.epoch))
  (<- inserted (query-rows store.database insert))
  (Appended (int (get (get inserted 0) 0))))


(defk pg-read-events [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadEvents) (: now-ms int)] :post [(: % Events)]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents に答えるため(刻 now-ms で保持の期限を過ぎた出来事は、回収の前でも出さない — 文の条件・頭の註)。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- statement (read-events-statement store.prefix ask.stream ask.after ask.limit expiry))
  (<- records (query-rows store.database statement))
  (var items [])
  (for [record records]
    (<- event (event-of ask.stream record))
    (.append items event))
  (Events (tuple items) (if items (. (get items -1) sequence) ask.after)))


(defk pg-read-stream-end [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadStreamEnd) (: now-ms int)] :post [(: % (| StreamEnd StreamEmpty))]
   :tags {:context "records" :role "foundation"}}
  "ReadStreamEnd に答えるため: 列の生きた出来事の max(seq) を 1 文で読む(刻 now-ms で保持の期限を過ぎた出来事は回収の前でも数えない —
   ReadEvents と同じ条件。生きた出来事が無ければ StreamEmpty)。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- statement (stream-end-statement store.prefix ask.stream expiry))
  (<- records (query-rows store.database statement))
  (val last (get (get records 0) 0))
  (if (is last None) (StreamEmpty) (StreamEnd (int last))))


(defk moved-after [program]
  {:pre [(: program Program)] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents(limit 1)の答えの Program を WatchEvents の 1 回ぶんの答えにするため。"
  (<- answer program)
  (<- moved (moved-of answer))
  moved)


(defk advance-epoch-locked [store]
  {:pre [(: store PreparedStore)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の中で置き場の版を 1 進め、変更の列を忘れるため。答え = 新しい版。"
  (<- advance (advance-epoch-statement store.prefix))
  (<- rows (query-rows store.database advance))
  (<- forget (forget-changes-statement store.prefix))
  (<- (query-rows store.database forget))
  (int (get (get rows 0) 0)))


;; --- 手入れ ------------------------------------------------------------------------------------------------

(defk prune-locked [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask PruneChanges) (: now-ms int)] :post [(: % Pruned)]
   :tags {:context "records" :role "foundation"}}
  "変更の列が際限なく伸びないように、keep-seconds より古い変更を消して floor を上げるため(書きの錠の中 — 読み手の断面と揃える)。"
  (<- statement (prune-changes-statement store.prefix (- now-ms (int (* 1000 ask.keep-seconds)))))
  (<- rows (query-rows store.database statement))
  (val record (get rows 0))
  (Pruned (int (get record 0)) (int (get record 1))))


(defk swept-count [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % Swept)]
   :tags {:context "records" :role "foundation"}}
  "SweepExpired の答え(消した行の数)を作るため。"
  (<- removed (purge-expired store now-ms))
  (Swept removed))


;; --- handler ------------------------------------------------------------------------------------------------

(defhandler pg-records-handler [#^ PreparedStore store #^ str writer #^ str origin-host]
  ;; 引数に残す理由: store は用意し終えた置き場(prepare-records-store の答え — 表の用意を済ませた証)で、置き場ごとに違う。
  ;; writer は呼び手が名乗った書き手の名(要求ごとに違う — 書き手の名を effect の引数にしない)。origin-host は行に刻む機体の名で、
  ;; 組み立ての側の設定。
  "PostgreSQL の置き場の答え手(頭の註)。SqlQuery / SqlTransaction を外側の答え手へ出す。"
  {:tags {:context "records" :role "foundation"}}
  ;; 源の工場の問い(ReadSignalSource)には本番の源 RECORDS-SIGNAL-SOURCE で答える(#3127 — 源の WatchChanges・WatchEvents はこの置き場が答える)。
  (ReadSignalSource []
    (resume RECORDS-SIGNAL-SOURCE))
  ;; 読みの節は回収を流さず、刻を読みの文の条件へ渡す。書きの節(PutRow・PutRows・AppendEvent)も回収を流さず、書きの transaction の中で
  ;; 自分が触る物だけを片付ける。回収は SweepExpired だけ(頭の註・#3605 の D)。
  (ReadRow [table key]
    (<- now (GetTime))
    (<- answer (reached (pg-read-row store effect (epoch-ms now))))
    (resume answer))
  (ListRows [table where fields cursor limit]
    (<- now (GetTime))
    (<- answer (reached (pg-list-rows store effect (epoch-ms now))))
    (resume answer))
  (PutRow [table key value expect]
    (<- now (GetTime))
    (<- answer (reached (writing store (put-row-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (PutRows [writes]
    (<- now (GetTime))
    (<- answer (reached (writing store (put-rows-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    (<- answer (wait-for-signal (fn [now-ms] (reached (pg-watch-scan store effect now-ms)))
                                (fn [] (hung-bell store)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (WatchEvents [stream after timeout]
    ;; 列の待ちは呼び鈴が鳴るたびの ReadEvents(limit 1)の読み直し(WatchChanges と同じ呼び鈴 — 頭の註)。
    (val once (ReadEvents stream :after after :limit 1))
    (<- answer (wait-for-signal (fn [now-ms] (moved-after (reached (pg-read-events store once now-ms))))
                                (fn [] (hung-bell store)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- now (GetTime))
    (<- answer (reached (writing store (append-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- now (GetTime))
    (<- answer (reached (pg-read-events store effect (epoch-ms now))))
    (resume answer))
  (ReadStreamEnd [stream]
    (<- now (GetTime))
    (<- answer (reached (pg-read-stream-end store effect (epoch-ms now))))
    (resume answer))
  (AdvanceStoreEpoch []
    ;; 検の口: 届かなければ StoreUnreachable を上げる(答えの型は int だけ — 旧い版と同じ)。
    (<- epoch (writing store (advance-epoch-locked store)))
    (resume epoch))
  (SweepExpired []
    (<- now (GetTime))
    (<- answer (reached (swept-count store (epoch-ms now))))
    (resume answer))
  (PruneChanges [keep-seconds]
    (<- now (GetTime))
    (<- answer (reached (writing store (prune-locked store effect (epoch-ms now)))))
    (resume answer)))

