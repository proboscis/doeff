;;; PostgreSQL の handler — 公開 effect 9 つに PostgreSQL の表で答える(本番の置き場)。
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
;;; (pg_sql.writer-lock-key — 旧い版と同じ錠の番号)の中で流す。transaction の中では SqlQuery / SqlBatch と純粋な計算だけを出す(時刻は前に
;;; 読んで渡す)。版の繰り上げ・回収・刈り取りの transaction は既定の形(文 1 つずつ — writing)のまま。
;;; 書きの往復(#3605 — 記録の service と DB が別の機体に在ると、DB への往復 1 回が書き 1 回の待ちに乗る): PutRow・PutRows・AppendEvent だけが
;;; 往復をまとめる transaction(SqlTransaction の batched — batched-writing)を選び、DB へ 2 往復で書く。1 往復目 = 判じるための読みの束
;;; (置き場の頭・錠つきの行 | 冪等キーの引き・鍵の覚え・組の期限を過ぎた出来事 —
;;; BEGIN と書きの錠は答え手が同じ往復の先頭に流す)、2 往復目 = 書きの束(片付け・行の書きと変更の列 | 出来事の書き — 合図と COMMIT も同じ
;;; 往復)。判じる事(Conflict・ExpectAbsent・断り・期限を過ぎた物の片付け)は 1 往復目の答えで決め、片付けの文は 2 往復目の先頭に入れる
;;; (往復を増やさない)。断り・衝突の時も 2 往復目(片付けと COMMIT)を流す。
;;; 読みの手順(行・一覧・変更の列・追記の読み)は SqlQuery を 1 文ずつ流す(自動 commit — 旧い版と同じ断面)。
;;; 変化の待ち(WatchChanges・WatchEvents — #3073): 書きの錠の transaction は置き場の通知の channel へ合図(SqlNotify)を出す(commit した時
;;; だけ届く — 巻き戻した書きの合図は届かない。batched の書きでは答え手が COMMIT と同じ往復で流す)。待ちは呼び鈴(SqlHangNotice)を掛けてから読み、静かなら呼び鈴か
;;; timeout を待って読み直す(watching.wait-for-signal)。読み直しの間隔で起きない。合図は中身を運ばず、待ち手が変更の列から読み直す。
;;; 待ち受けの接続が繋ぎ直した時も呼び鈴が鳴るので、その間の書きも読み直しで拾う。
;;; 合図と呼び鈴は名で絞る(#3688 — 前は書き 1 回が置き場の待ち手を全部起こした): 名 = 表は "table:" + 表の名・列は "stream:" + 列の名
;;; (notice-topics の 1 か所で綴る — 表と列が同じ綴りでも混ざらない)。書きは触った名を合図し(行の書き = その表・追記 = その列・保持の
;;; 刈り = 期限の在る表とその列)、待ちは待つ名で呼び鈴を掛ける(WatchChanges = 頼んだ表・WatchEvents = その列)。版の繰り上げと変更の
;;; 刈り取り(PruneChanges)は全部の待ち手に関わる(版と床は全部の表の位置を変える — 名の分からない合図 None)。memory の置き場の呼び鈴と
;;; 同じ分け方(memory.hy の ring-bells)。
;;; 読み手の窓の read-modify-write の競合(同じ読み手の位置を 2 つの要求が同時に読んで進める)は旧い版と同じで、ここでは直さない
;;; (#880 の構成のレビュー A4)。
;;; 保持の期限(#3561): 読みの効果(ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd・ReadEventByKey)は回収を流さず、期限を
;;; 過ぎた行と出来事を読みの文の条件で除く(回収と同じ境 — row-expiry・event-expiry・pg_sql の頭の註)。読み 1 回が流すのは読みの文だけ。
;;; 回収(期限を過ぎた行を消して変更の列に「消えた」を積み、出来事を冪等キーの覚えへ移す — purge-expired)は SweepExpired(手入れの係)の
;;; 時だけ流す。時間で起きて回収する loop は持たない。
;;; 書き(PutRow・PutRows・AppendEvent — #3605 の D)は回収を流さず、書きの transaction の文だけを流す — 記録の service と DB が別の機体に
;;; 在ると文 1 つが往復 1 回で、書きのたびの回収の候補の読み(1 + 期限つきの列の数)が書きの待ちに乗っていた。答えは回収の後の書きと同じに
;;; 保つ: 書きが触る物だけを同じ transaction で片付けてから判じる — 行の書きは錠つきで読んだ今の行が期限を過ぎていればその行を消して
;;; 「消えた」を積む文を 2 往復目に入れ、無い行として判じる(locked-row)。追記は冪等キーの単位(出来事ごとに数える列は鍵の出来事・組で
;;; 数える列は鍵の組)の期限を過ぎた出来事を 1 往復目に読み、それを捨てて鍵の覚えへ移した後の断面で前の使いを判じ、捨てる文と覚えの文を
;;; 2 往復目に入れる(earlier-use)。触らない行と出来事は SweepExpired まで置き場に残り(読みには出ない)、その行の「消えた」も SweepExpired が積む。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetTime])
(import doeff_core_effects.scheduler [ExternalPromise])
(import doeff_core_effects.sql_effects [SqlQuery SqlBatch SqlTransaction SqlNotify SqlHangNotice SqlDropNotice SqlRows SqlFailed
                                        SqlUnreachable])
(import doeff_records.values [RecordsSchema TableDecl StreamDecl KeepFor ByKeySuffix Row Missing Page Written WrittenRows RowChanged
                              RowRemoved Changes Appended Conflict NotIndexed RetiredKey EventsMoved EventsQuiet StreamEnd StreamEmpty
                              EventAbsent EventRetired
                              StreamTail StreamTailEmpty
                              Event Events Reset WatchCursor ListCursor Refused Unreachable RowsConflict RowsRefused])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd
                               ReadEventByKey])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.event_source [RECORDS-SIGNAL-SOURCE ReadSignalSource])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [AppendReplay judge-expect judge-put judge-put-rows judge-append row-expired? retention-cutoff-ms
                                 where-refusal listed-row key-text key-from-text canonical-json next-watch-sequence epoch-ms body-digest])
(import doeff_records.watching [wait-for-signal moved-of])
(import doeff_records.pg_sql [Statement RowExpiry EventExpiry TailRead DEFAULT-PREFIX checked-prefix writer-lock-key migrate-lock-key
                              schema-statements drop-statements
                              store-head-statement watch-head-statement read-row-statement lock-row-statement list-rows-statement
                              terminal-rows-statement upsert-row-statement delete-row-statement append-change-statement
                              changes-statement advance-epoch-statement forget-changes-statement prune-changes-statement find-event-statement
                              insert-event-statement read-events-statement stream-end-statement event-by-key-statement expire-events-statement
                              expire-event-groups-statement touched-events-statement delete-events-statement expiring-events-statement
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


(defrecord LockedRow
  "書きの判定に渡す今の行(1 往復目の錠つきの読みから — locked-row): current = 今の行(無い行・期限を過ぎて片付ける行は None)/
   cleanup = 期限を過ぎた行を消して「消えた」を積む文(2 往復目の先頭に流す — 片付けが要らなければ空)。"
  (#^ (| Row None) current)
  (#^ (get tuple #(Statement ...)) cleanup))


(defrecord StagedRow
  "判定を通った 1 行の書き(staged-row): statements = 行の書きと変更の列に 1 つ積む文(2 往復目に流す)/ written = その書きの答え。"
  (#^ (get tuple #(Statement ...)) statements)
  (#^ Written written))


(defrecord EarlierUse
  "追記の冪等キーの前の使い(1 往復目の読みから — earlier-use): previous = 生きた出来事 | 保持の期限で消した鍵の覚え | None /
   cleanup = 追記が触る単位の期限を過ぎた出来事を捨てて鍵の覚えへ移す文(2 往復目の先頭に流す — 片付けが要らなければ空)。"
  (#^ (| Event RetiredKey None) previous)
  (#^ (get tuple #(Statement ...)) cleanup))


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


(defk in-transaction [database lock-key program [batched False]]
  {:pre [(: database str) (: lock-key str) (: program Program) (: batched bool)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を錠 lock-key の transaction 1 つで流すため(届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる)。batched =
   往復をまとめる transaction を選ぶか(既定 False — 文 1 つずつ・頭の註)。"
  (<- answer (SqlTransaction database program :lock-key lock-key :batched batched))
  (match answer
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ answer))


(defk batch-rows [database statements commit]
  {:pre [(: database str) (: statements tuple) (: commit bool)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "書きの transaction の中で、文の並びを DB への 1 往復でまとめて流し(commit なら同じ往復の最後に COMMIT も)、文ごとの行の tuple を
   返すため(頭の註)。往復が落ちれば答え手が transaction を戻して SqlTransaction の答えにする(program は再開されない)。"
  (<- answers (SqlBatch database (tuple (gfor statement statements (SqlQuery database statement.text statement.params))) :commit commit))
  (tuple (gfor answer answers answer.rows)))


(defk committed [store statements answer]
  {:pre [(: store PreparedStore) (: statements tuple) (: answer (| Written WrittenRows Appended Conflict Refused RowsConflict RowsRefused))]
   :post [(: % (| Written WrittenRows Appended Conflict Refused RowsConflict RowsRefused))]
   :tags {:context "records" :role "foundation"}}
  "書きの 2 往復目: statements(片付けと書き)を合図と COMMIT と同じ往復で流し、書きの答え answer を返すため(頭の註 — 断り・衝突の時も
   片付けと COMMIT を流す)。"
  (<- (batch-rows store.database statements True))
  answer)


(defk notice-channel [store]
  {:pre [(: store PreparedStore)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel の名(表の名の接頭辞ごとに 1 つ — 同じ database の別の置き場の書きで起きない)。"
  (.format "{}changes" store.prefix))


(defk notice-topics [tables streams]
  {:pre [(: tables tuple) (: streams tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表 tables と列 streams を、合図と呼び鈴の名の並び(表 = \"table:\" + 表の名・列 = \"stream:\" + 列の名 — 名の順・重ねない)にするため
   (頭の註 — 書きの合図の名と待ちの呼び鈴の名をこの 1 か所で綴る)。"
  (tuple (sorted (| (sfor table tables (+ "table:" table)) (sfor stream streams (+ "stream:" stream))))))


(defk notice-raised [notified]
  {:pre [(: notified (| None SqlFailed SqlUnreachable))] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "SqlNotify の答えの失敗を、置き場の例外(StoreUnreachable・RecordsSqlFailed)にするため(signalled と signalled-ahead が共に使う)。"
  (match notified
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ None))


(defk signalled [store topics program]
  {:pre [(: store PreparedStore) (: topics (| tuple None)) (: program Program)] :post [(: % "program の答え")]
   :tags {:context "records" :role "foundation"}}
  "program を流した後、同じ transaction の中で置き場の通知の channel へ、名 topics(None = 全部に関わる)の合図を出すため(commit した時だけ
   届く — 頭の註)。"
  (<- answer program)
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel topics))
  (<- (notice-raised notified))
  answer)


(defk signalled-ahead [store topics program]
  {:pre [(: store PreparedStore) (: topics (| tuple None)) (: program Program)] :post [(: % "program の答え")]
   :tags {:context "records" :role "foundation"}}
  "batched の transaction の中で、置き場の通知の channel へ名 topics の合図を出してから program を流すため(答え手は合図を覚えて COMMIT と
   同じ往復で流し、commit した時だけ届く — 頭の註。program が commit の束で終わるので、合図は program の前に出す)。"
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel topics))
  (<- (notice-raised notified))
  (<- answer program)
  answer)


(defk writing [store topics program]
  {:pre [(: store PreparedStore) (: topics (| tuple None)) (: program Program)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を置き場の書きの錠の transaction で流し、同じ transaction で名 topics(None = 全部に関わる)の書きの合図を出すため(旧い版の
   lock-statement と同じ錠の番号 — pg_sql.hy の頭の註)。文 1 つずつの transaction(回収・刈り取り・版の繰り上げ)。"
  (<- key (writer-lock-key store.prefix))
  (<- answer (in-transaction store.database key (signalled store topics program)))
  answer)


(defk batched-writing [store topics program]
  {:pre [(: store PreparedStore) (: topics tuple) (: program Program)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "書きの効果(PutRow・PutRows・AppendEvent)の program を、往復をまとめる(batched)書きの錠の transaction で流し、同じ transaction で書きが
   触る名 topics の合図を出すため(頭の註の「書きの往復」— 錠の番号は writing と同じ)。"
  (<- key (writer-lock-key store.prefix))
  (<- answer (in-transaction store.database key (signalled-ahead store topics program) :batched True))
  answer)


(defk hung-bell [store topics]
  {:pre [(: store PreparedStore) (: topics tuple)] :post [(: % (| ExternalPromise Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel に、名 topics を待つ呼び鈴を掛けるため(待ち受けに届かなければ Unreachable)。"
  (<- channel (notice-channel store))
  (<- bell (SqlHangNotice store.database channel topics))
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
  (<- head (head-of rows))
  head)


(defk head-of [rows]
  {:pre [(: rows tuple)] :post [(: % StoreHead)]
   :tags {:context "records" :role "foundation"}}
  "store-head-statement の答えの行を置き場の頭にするため(読みの 1 文と、書きの 1 往復目の束が同じ綴りを使う)。"
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


(defk removed-row-statements [store table text version now-ms epoch]
  {:pre [(: store PreparedStore) (: table str) (: text str) (: version int) (: now-ms int) (: epoch int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた行 1 つ(版 version)を消し、変更の列に「消えた」を積む 2 文を作るため(回収 remove-row と、期限を過ぎた行へ書く書きの
   片付け locked-row が同じ 2 文を使う)。"
  (<- delete (delete-row-statement store.prefix table text version))
  (<- change (append-change-statement store.prefix table text version None now-ms epoch))
  #(delete change))


(defk remove-row [store table text version now-ms epoch]
  {:pre [(: store PreparedStore) (: table str) (: text str) (: version int) (: now-ms int) (: epoch int)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "回収(SweepExpired)の書きの錠の transaction の中で、保持の期限を過ぎた行 1 つを消し、変更の列に「消えた」を積むため。"
  (<- statements (removed-row-statements store table text version now-ms epoch))
  (<- (run-statements store.database statements))
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


(defk retired-keys [stream removed]
  {:pre [(: stream str) (: removed tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限で捨てる出来事(append_rows の行 seq at payload の組 removed)の冪等キーの覚え(RetiredKey — 番号と本文の指紋)を作るため
   (#3022 — 捨てる transaction が同じ transaction で鍵だけの表へ入れ、消した後の同じ鍵の追記も append-locked が同じ規則で判じる。出来事
   だけ消えて覚えが無い断面を作らない)。回収(retire-expired-events)と書きの片付け(retired-events-statements)が使う。"
  (var kept #())
  (for [record removed]
    (<- event (event-of stream record))
    (:= kept (+ kept #((RetiredKey :idempotency-key event.idempotency-key :sequence event.sequence
                                   :body-digest (body-digest event.body))))))
  kept)


(defk retire-expired-events [store stream before-at separator]
  {:pre [(: store PreparedStore) (: stream str) (: before-at int) (: separator (| str None))] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、列 stream の保持の期限を過ぎた出来事を捨て、同じ transaction で冪等キーの覚えへ移すため(回収 —
   retired-keys)。separator = None は出来事ごとに数える列・str は組で数える列の区切り。答え = 捨てた出来事の数。"
  (<- delete (if (is separator None)
                 (expire-events-statement store.prefix stream before-at)
                 (expire-event-groups-statement store.prefix stream before-at separator)))
  (<- removed (query-rows store.database delete))
  (<- kept (retired-keys stream removed))
  (when kept
    (<- retire (retire-keys-statement store.prefix stream kept))
    (<- (query-rows store.database retire)))
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
    ;; 合図の名 = 行を消し得る表(期限と終端の語の在る表 — 消す行は transaction の中で読み直すので、候補の表でなく、読み直しが読む表の全部)。
    (<- row-topics (notice-topics (tuple (gfor #(name decl) (sorted (.items store.schema.tables))
                                               :if (and (isinstance decl.retention KeepFor) decl.terminal)
                                               name))
                                  #()))
    (<- count (writing store row-topics (remove-expired-rows store now-ms)))
    (:= removed count))
  (for [#(name decl) (sorted (.items store.schema.streams))]
    (<- expiry (event-expiry decl now-ms))
    (when (is-not expiry None)
      (<- probe (expiring-events-statement store.prefix name expiry.before-at expiry.separator))
      (<- due (query-rows store.database probe))
      (when due
        (<- stream-topics (notice-topics #() #(name)))
        (<- (writing store stream-topics (retire-expired-events store name expiry.before-at expiry.separator))))))
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


(defk locked-row [store decl text records now-ms epoch]
  {:pre [(: store PreparedStore) (: decl TableDecl) (: text str) (: records tuple) (: now-ms int) (: epoch int)] :post [(: % LockedRow)]
   :tags {:context "records" :role "foundation"}}
  "書きの判定に渡す今の行を、1 往復目の行の錠(FOR UPDATE)つきの読み records から判じるため(書きの transaction の中)。読んだ行が刻
   now-ms で保持の期限を過ぎた終端の行なら(回収と同じ判定 admission.row-expired?)、回収と同じくその行を消して変更の列に「消えた」を積む
   文を片付けに添え(2 往復目の先頭に流す)、無い行として返す — 書きは回収の後と同じく無い行として判じる(#3605 の D・頭の註)。
   epoch = 「消えた」に刻む置き場の版。"
  (when (not records)
    (return (LockedRow :current None :cleanup #())))
  (val record (get records 0))
  (<- row (row-of record))
  (when (row-expired? decl row.value (int (get record 3)) now-ms)
    (<- cleanup (removed-row-statements store decl.name text row.version now-ms epoch))
    (return (LockedRow :current None :cleanup cleanup)))
  (LockedRow :current row :cleanup #()))


(defk staged-row [store origin-host writer table text current value now-ms epoch]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: table str) (: text str) (: current (| Row None))
         (: value FrozenMap) (: now-ms int) (: epoch int)]
   :post [(: % StagedRow)]
   :tags {:context "records" :role "foundation"}}
  "判定を通った 1 行の書き(行の書きと変更の列に 1 つ積む文 — 2 往復目に流す)と、その答えを作るため(PutRow と PutRows の書きを 1 つにし、
   版と番号の採り方を揃える)。"
  (val version (if (is current None) 1 (+ current.version 1)))
  (val payload (canonical-json value))
  (<- upsert (upsert-row-statement store.prefix table text payload version now-ms writer origin-host epoch))
  (<- change (append-change-statement store.prefix table text version payload now-ms epoch))
  (StagedRow :statements #(upsert change) :written (Written version value)))


(defk put-row-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRow) (: now-ms int)]
   :post [(: % (| Written Conflict Refused))]
   :tags {:context "records" :role "foundation"}}
  "PutRow の書きの錠の中の手順を 2 往復で流すため(頭の註): 1 往復目に置き場の頭と今の行を錠つきで読み、判定し、2 往復目に片付けと
   (通れば)行の書きを COMMIT と同じ往復で流す。"
  (val decl (store.schema.table ask.table))
  (val text (key-text ask.key))
  (<- head-statement (store-head-statement store.prefix))
  (<- lock-statement (lock-row-statement store.prefix decl.name text))
  (<- read (batch-rows store.database #(head-statement lock-statement) False))
  (<- head (head-of (get read 0)))
  (<- found (locked-row store decl text (get read 1) now-ms head.epoch))
  (val conflict (judge-expect ask.expect found.current))
  (when conflict
    (<- collided (committed store found.cleanup conflict))
    (return collided))
  (val verdict (judge-put decl found.current ask.key ask.value))
  (when (isinstance verdict Refused)
    (<- refused (committed store found.cleanup verdict))
    (return refused))
  (<- staged (staged-row store origin-host writer ask.table text found.current verdict.value now-ms head.epoch))
  (<- written (committed store (+ found.cleanup staged.statements) staged.written))
  written)


(defk put-rows-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRows) (: now-ms int)]
   :post [(: % (| WrittenRows RowsConflict RowsRefused))]
   :tags {:context "records" :role "foundation"}}
  "PutRows の束を全部か 0 で、行の数に依らず 2 往復で書くため(頭の註): 1 往復目に置き場の頭と全部の行を錠つきで読み(期限を過ぎた行は
   片付けて無い行として — locked-row)、判定(admission.judge-put-rows)が全部通った時だけ、2 往復目に片付けと束の順の書き・変更を COMMIT と
   同じ往復で流す(錠の中なので番号は束の中で続く)。往復の途中の失敗は transaction ごと戻る。"
  (for [write ask.writes] (store.schema.table write.table))
  (val texts (tuple (gfor write ask.writes (key-text write.key))))
  (<- head-statement (store-head-statement store.prefix))
  (var reads #(head-statement))
  (for [#(write text) (zip ask.writes texts :strict True)]
    (<- lock-statement (lock-row-statement store.prefix write.table text))
    (:= reads (+ reads #(lock-statement))))
  (<- read (batch-rows store.database reads False))
  (<- head (head-of (get read 0)))
  (var found #())
  (for [#(write text records) (zip ask.writes texts (cut read 1 None) :strict True)]
    (<- one (locked-row store (store.schema.table write.table) text records now-ms head.epoch))
    (:= found (+ found #(one))))
  (val cleanup (tuple (gfor one found statement one.cleanup statement)))
  (val currents (tuple (gfor one found one.current)))
  (val verdict (judge-put-rows store.schema ask.writes currents))
  (when (not (isinstance verdict tuple))
    (<- refused (committed store cleanup verdict))
    (return refused))
  (var statements cleanup)
  (var written #())
  (for [#(write text current admitted) (zip ask.writes texts currents verdict :strict True)]
    (<- staged (staged-row store origin-host writer write.table text current admitted.value now-ms head.epoch))
    (:= statements (+ statements staged.statements))
    (:= written (+ written #(staged.written))))
  (<- rows (committed store statements (WrittenRows written)))
  rows)


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


(defk tail-reads [store streams now-ms]
  {:pre [(: store PreparedStore) (: streams (get tuple #(str ...))) (: now-ms int)] :post [(: % (get tuple #(TailRead ...)))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges が末尾を名指した列 streams の読みの材料(pg_sql.TailRead の組 — 名指した順)を刻 now-ms で作るため(#3718 — 期限の境は
   ReadStreamEnd と同じ event-expiry)。宣言に無い列は UndeclaredTable。"
  (var reads #())
  (for [name streams]
    (<- expiry (event-expiry (store.schema.stream name) now-ms))
    (:= reads (+ reads #((TailRead :stream name :expiry expiry)))))
  reads)


(defk tails-of [streams columns]
  {:pre [(: streams (get tuple #(str ...))) (: columns tuple) (= (len columns) (* 2 (len streams)))]
   :post [(: % (get tuple #((| StreamTail StreamTailEmpty) ...)))]
   :tags {:context "records" :role "foundation"}}
  "watch-head-statement の答えの行の 4 つ目からの列(名指した列ごとに seq・at の 2 つ — 生きている出来事の無い列は NULL)を、名指した順の
   tails(StreamTail | StreamTailEmpty)にするため(#3718)。"
  (var tails #())
  (for [#(stream #(sequence at)) (zip streams (zip (cut columns 0 None 2) (cut columns 1 None 2) :strict True) :strict True)]
    (:= tails (+ tails #((match sequence
                            None (StreamTailEmpty :stream stream)
                            _ (StreamTail :stream stream :sequence (int sequence) :at (int at)))))))
  tails)


(defk pg-watch-scan [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask WatchChanges) (: now-ms int)] :post [(: % (| Changes Reset))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges の 1 回ぶんの読み(待たない)を流すため。行の今の値が刻 now-ms で保持の期限を過ぎた終端の行である行の変わりは、回収の
   前でも出さない(文の条件・頭の註)。頼んだ表が空なら文を流さない(`IN ()` は文にできない — WatchChanges は空の組を作る時に断るので、
   今は起きない)。名指した列(ask.streams)の末尾は、置き場の頭と同じ 1 文(watch-head-statement — 同じ断面)で読み、変更の列は頭の番号
   までを読む(変更と末尾が同じ断面の物になる・往復は増えない — #3718)。"
  (for [name ask.tables] (store.schema.table name))
  (<- reads tuple (tail-reads store ask.streams now-ms))
  (<- head-statement Statement (watch-head-statement store.prefix reads))
  (<- head-rows tuple (query-rows store.database head-statement))
  (<- head StoreHead (head-of head-rows))
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
  (<- tails tuple (tails-of ask.streams (tuple (cut (get head-rows 0) 3 None))))
  (Changes (tuple items) (WatchCursor head.epoch (next-watch-sequence (tuple items) ask.limit head.head)) tails))


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defk touched-reads [store decl expiry idempotency-key]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: expiry (| EventExpiry None)) (: idempotency-key str)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "追記の 1 往復目に足す、追記が触る組の期限を過ぎた出来事の読み(0 か 1 文)を作るため(#3605 の D): 組で数える列で期限の在る時だけ —
   組の他の鍵の出来事は鍵の引きでは分からない。出来事ごとに数える列は鍵の引き(find-event)の刻で判じるので文を足さない。"
  (if (and (is-not expiry None) (is-not expiry.separator None))
      #((! (touched-events-statement store.prefix decl.name expiry.before-at expiry.separator idempotency-key)))
      #()))


(defk retired-events-statements [store stream removed]
  {:pre [(: store PreparedStore) (: stream str) (: removed tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "追記が触る単位の期限を過ぎた出来事(1 往復目に読んだ行 removed)を捨てて冪等キーの覚えへ移す文を作るため(2 往復目の先頭に流す —
   回収と同じく、出来事だけ消えて覚えが無い断面を作らない・#3022)。捨てる物が無ければ空。"
  (when (not removed)
    (return #()))
  (<- kept (retired-keys stream removed))
  (<- delete (delete-events-statement store.prefix stream (tuple (gfor record removed (int (get record 0))))))
  (<- retire (retire-keys-statement store.prefix stream kept))
  #(delete retire))


(defk earlier-use [store decl expiry idempotency-key found kept grouped]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: expiry (| EventExpiry None)) (: idempotency-key str) (: found tuple) (: kept tuple)
         (: grouped tuple)]
   :post [(: % EarlierUse)]
   :tags {:context "records" :role "foundation"}}
  "冪等キーの前の使いと、追記が触る単位の片付けの文を、追記の 1 往復目の読み(書きの錠の中)から判じるため(#3605 の D — 書きは置き場の
   全部を回収しない。答えは回収の後の追記と同じ: 期限を過ぎた鍵は覚えで判じ、期限を過ぎた組に新しい鍵を積んでも組の古い出来事は読みに
   戻らない)。found = 鍵の生きた出来事の引きの行・kept = 鍵の覚えの引きの行・grouped = touched-reads の答え(組で数える列だけ)。
   捨てる物 = 出来事ごとに数える列は鍵の出来事が境の刻(event-expiry — 回収と読みと同じ境)以前に積まれていればそれ・組で数える列は組の
   読みの答え。前の使い = 捨てずに残る生きた出来事・無ければ鍵の覚え(在った覚えは書き換えない — 回収の ON CONFLICT DO NOTHING と同じ)・
   それも無く鍵の出来事を今捨てるならその覚え・どれも無ければ None。出来事を消して覚えを入れる刈りも同じ書きの錠の transaction なので、
   読みと書きの間に刈りは挟まらない。"
  (val stream decl.name)
  (var live None)
  (when found
    (<- event (event-of stream (get found 0)))
    (:= live event))
  (val removed (cond
                 (is expiry None) #()
                 (is-not expiry.separator None) (get grouped 0)
                 (and (is-not live None) (<= live.at expiry.before-at)) found
                 True #()))
  (<- cleanup (retired-events-statements store stream removed))
  (val removed-sequences (sfor record removed (int (get record 0))))
  (val previous (cond
                  (and (is-not live None) (not-in live.sequence removed-sequences)) live
                  kept (RetiredKey :idempotency-key idempotency-key :sequence (int (get kept 0 0)) :body-digest (get kept 0 1))
                  (is-not live None) (RetiredKey :idempotency-key live.idempotency-key :sequence live.sequence
                                                 :body-digest (body-digest live.body))
                  True None))
  (EarlierUse :previous previous :cleanup cleanup))


(defk append-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask AppendEvent) (: now-ms int)]
   :post [(: % (| Appended Refused))]
   :tags {:context "records" :role "foundation"}}
  "AppendEvent の書きの錠の中の手順を 2 往復で流すため(頭の註): 1 往復目に置き場の頭・冪等キーの引き・鍵の覚え・(組で数える列は)組の期限を
   過ぎた出来事を読み、前の使いを判じ(earlier-use)、2 往復目に片付けと(新しければ)出来事の書きを COMMIT と同じ往復で流す。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- head-statement (store-head-statement store.prefix))
  (<- find (find-event-statement store.prefix ask.stream ask.idempotency-key))
  (<- lookup (find-retired-key-statement store.prefix ask.stream ask.idempotency-key))
  (<- grouping (touched-reads store decl expiry ask.idempotency-key))
  (<- read (batch-rows store.database (+ #(head-statement find lookup) grouping) False))
  (<- head (head-of (get read 0)))
  (<- use (earlier-use store decl expiry ask.idempotency-key (get read 1) (get read 2) (cut read 3 None)))
  (val verdict (judge-append decl ask.body use.previous))
  (when (isinstance verdict Refused)
    (<- refused (committed store use.cleanup verdict))
    (return refused))
  (when (isinstance verdict AppendReplay)
    (<- replayed (committed store use.cleanup (Appended verdict.sequence True)))
    (return replayed))
  (val payload (canonical-json {"idempotencyKey" ask.idempotency-key "writer" writer "body" ask.body}))
  (<- insert (insert-event-statement store.prefix ask.stream now-ms payload origin-host head.epoch))
  (<- rows (batch-rows store.database (+ use.cleanup #(insert)) True))
  (Appended (int (get (get rows -1) 0 0)) False))


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


(defk pg-read-event-by-key [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadEventByKey) (: now-ms int)] :post [(: % (| Event EventAbsent EventRetired))]
   :tags {:context "records" :role "foundation"}}
  "ReadEventByKey に答えるため: 冪等キーの出来事と、回収で消した鍵の覚えを 1 文で引く(event-by-key-statement — 一意の索引の引き 2 つで、
   列を辿らない)。生きた出来事は Event・保持の期限を過ぎた出来事(回収の前)と覚えの鍵は EventRetired・どちらも無ければ EventAbsent。
   消した鍵の再送は新しい出来事を積まない(judge-append)ので、出来事の行と覚えの行は同じ鍵で両方は来ない。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- statement (event-by-key-statement store.prefix ask.stream ask.idempotency-key expiry))
  (<- records (query-rows store.database statement))
  (when (not records)
    (return (EventAbsent)))
  (val record (get records 0))
  ;; 覚えの行('retired')と、期限を過ぎた出来事の行(5 列目 = 過ぎたか)は、どちらも消えた出来事の番号で答える。
  (when (or (= (get record 0) "retired") (get record 4))
    (return (EventRetired (int (get record 1)))))
  (<- event (event-of ask.stream (tuple (cut record 1 4))))
  event)


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
  ;; 書きの節は触る名(表 | 列)の合図を、待ちの節は待つ名の呼び鈴を使う(頭の註 — notice-topics)。
  (PutRow [table key value expect]
    (<- now (GetTime))
    (<- topics (notice-topics #(table) #()))
    (<- answer (reached (batched-writing store topics (put-row-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (PutRows [writes]
    (<- now (GetTime))
    (<- topics (notice-topics (tuple (gfor write writes write.table)) #()))
    (<- answer (reached (batched-writing store topics (put-rows-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    ;; 呼び鈴の名は頼んだ表だけ — 末尾を名指した列(streams)への追記では待ち手を起こさない(tails は答えを返す時点の末尾・#3718)。
    (<- topics (notice-topics (tuple tables) #()))
    (<- answer (wait-for-signal (fn [now-ms] (reached (pg-watch-scan store effect now-ms)))
                                (fn [] (hung-bell store topics)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (WatchEvents [stream after timeout]
    ;; 列の待ちは呼び鈴が鳴るたびの ReadEvents(limit 1)の読み直し(WatchChanges と同じ呼び鈴の仕組みで、待つ名はその列 — 頭の註)。
    (val once (ReadEvents stream :after after :limit 1))
    (<- topics (notice-topics #() #(stream)))
    (<- answer (wait-for-signal (fn [now-ms] (moved-after (reached (pg-read-events store once now-ms))))
                                (fn [] (hung-bell store topics)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- now (GetTime))
    (<- topics (notice-topics #() #(stream)))
    (<- answer (reached (batched-writing store topics (append-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- now (GetTime))
    (<- answer (reached (pg-read-events store effect (epoch-ms now))))
    (resume answer))
  (ReadStreamEnd [stream]
    (<- now (GetTime))
    (<- answer (reached (pg-read-stream-end store effect (epoch-ms now))))
    (resume answer))
  (ReadEventByKey [stream idempotency-key]
    (<- now (GetTime))
    (<- answer (reached (pg-read-event-by-key store effect (epoch-ms now))))
    (resume answer))
  (AdvanceStoreEpoch []
    ;; 検の口: 届かなければ StoreUnreachable を上げる(答えの型は int だけ — 旧い版と同じ)。版は全部の待ち手に関わる(名の分からない合図)。
    (<- epoch (writing store None (advance-epoch-locked store)))
    (resume epoch))
  (SweepExpired []
    (<- now (GetTime))
    (<- answer (reached (swept-count store (epoch-ms now))))
    (resume answer))
  (PruneChanges [keep-seconds]
    (<- now (GetTime))
    ;; 床は全部の表の待ち手の位置に関わる(名の分からない合図)。
    (<- answer (reached (writing store None (prune-locked store effect (epoch-ms now)))))
    (resume answer)))

