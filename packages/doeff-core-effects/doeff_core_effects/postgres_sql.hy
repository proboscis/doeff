;;; 汎用の SQL の effect(sql_effects.hy)の本物の PostgreSQL の答え手 postgres-sql-handler(agora-redesign #802 便 3)。psycopg 3 を呼んで値を
;;; 詰め替えるだけで、判断を持たない。psycopg はこの module の I/O の関数の中でだけ読む(psycopg の無い環境でも、書き換えと写しの純関数は
;;; 読めて検を撃てる)。
;;;   - 接続の貸し出し PostgresConnections は組み立ての側(composition root)が database の宣言(PostgresDatabase — 名と DSN)から作って渡し、
;;;     止める時に close する。要求 1 つ = 接続 1 本(doeff-records の置き場もこの貸し出しを使う — 同時の要求の transaction を 1 本の接続で
;;;     混ぜない)。接続は自動 commit・json / jsonb の欄は text で読む(値の正規化の決まり)。切れた接続と transaction の途中で返った接続は
;;;     返す時に捨てる / rollback する。
;;;   - 接続の上限(PostgresTimeouts・agora-redesign #1479): 接続は開く時の上限・TCP の keepalive・送った bytes の返事を待つ上限
;;;     (tcp_user_timeout)・文の上限(statement_timeout)を持つ。DB の pod が入れ替わると空きの接続は相手の居ない TCP のまま残り、上限が
;;;     無いと次の文が kernel の再送の限り(数十分)固まって許可を持ち続ける — 上限があれば文は OperationalError で落ち、SqlUnreachable を
;;;     答え、返す時に切れた接続として捨てられ、次の借りが新しく張る。
;;;   - 引数は中立の `:name` を `%(name)s` へ書き換え、文の `%` は `%%` にする(postgres-statement)。
;;;   - 失敗: engine の SQLSTATE をそのまま SqlFailed に。SQLSTATE の無い driver の誤りは類の表(DRIVER-CLASS-SQLSTATES — agora-controllers
;;;     services/record/handlers_wire.hy と同じ表)で類の code に。SQLSTATE の無い OperationalError / InterfaceError・接続できない時・
;;;     接続の例外の SQLSTATE(class 08 と 57P01・57P02・57P03)は SqlUnreachable(postgres-failure)。
;;;   - SqlTransaction = 接続 1 本で BEGIN → lock-key が在れば pg_advisory_xact_lock(hashtext(鍵))→ program → COMMIT(sql_transaction.hy)。
;;;   - scheduler を塞がない(agora-redesign #1215): postgres-sql-handler は driver の I/O(接続の許可を待つ・接続を開く・文を流す・COMMIT・
;;;     ROLLBACK・接続を返す)を scheduler の thread で撃たず、呼び 1 つに thread 1 本(offloaded_call.hy の ThreadPerCall)で回し、撃った task
;;;     だけが外から完了させる promise で待つ。遅い文の間も同じ run の他の task(待ち受けの /healthz・時計の刻み)は回る。同時に使う接続の
;;;     上限は PostgresConnections の許可(thread の間の錠)が持ち、許可を待つのも thread の中 — thread の数に上限を置かないので、許可を持つ
;;;     transaction の次の文が thread の空きを待って詰まることがない。手順(文・値の写し・transaction の段・取り消しの後始末)は
;;;     pooled-postgres-sql-handler と同じ offloaded-transaction と offloaded-statement を使い、違いは Executor と許可の待ち方だけ(pooled は呼び手の
;;;     pool と scheduler の semaphore)。外側に scheduled が要る(CreateExternalPromise と Wait)— session の値の置き場(state)は要らない。
;;;   - 通知(agora-redesign #3073): SqlNotify = pg_notify(channel, '')を流す(transaction の中なら同じ接続で — commit した時だけ届く)。
;;;     commit の後(transaction の外なら流した後)に、同じ process の呼び鈴をその場で鳴らす — 同じ process の書きの合図が NOTIFY の配達を
;;;     待たずに届く(仮想の時計の下で、合図より先に待ちの期限が来ない)。SqlHangNotice = channel ごとに LISTEN を張った接続 1 本を daemon の
;;;     thread で持つ待ち受け(PostgresListener)に呼び鈴(外の promise)を掛ける。待ち受けは通知を受けるたびに掛かっている呼び鈴を全部
;;;     鳴らし、切れたら繋ぎ直し、繋ぎ直した時にも鳴らす(切れていた間の通知は届かないので、待ち手に読み直させる)。張れていない間の
;;;     SqlHangNotice は SqlUnreachable。繋ぎ直しの間の秒は境界の答え手の中だけの時間で、呼び手には出ない。
;;;   - 取り消し(agora-redesign #2792): 文 1 つの effect も transaction と同じく「接続を借りる」と「流して返す」を別の仕事にする。許可を待って
;;;     いる間に取り消された要求は、許可が取れても文を流さずにすぐ返す(DB が止まった間に取り消された読みが、DB が戻った時にまとめて流れ、
;;;     後から来た要求を待たせない)。走り出した文は止めない。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(require doeff-hy.record [defrecord])
(import queue [Queue Empty])
(import threading)
(import time)
(import concurrent.futures [Executor])
(import dataclasses [dataclass field])
(import doeff [Program])
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice
                                        SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlParam SqlColumnType SqlText SqlPlaceholder split-statement checked-params
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects.sql_transaction [run-in-transaction])

;; SQLSTATE を持たない driver の誤りの類 → その類の SQLSTATE の class の code(psycopg が SQLSTATE から類を選ぶ表の逆)。
(val DRIVER-CLASS-SQLSTATES {"DataError" "22000" "IntegrityError" "23000" "ProgrammingError" "42000" "NotSupportedError" "0A000"
                             "InternalError" "XX000"})
;; SQLSTATE を持たなければ届かない(接続できない・切れた)と読む類。
(val UNREACHABLE-CLASSES #("OperationalError" "InterfaceError"))
;; SQLSTATE を持っていても届かない(接続が切れた・engine が接続を受けない)と読む code: class 08 = 接続の例外の全部(前方 2 文字)と、
;; 57P01 管理者による切断・57P02 crash による切断・57P03 起動中 / 停止中で接続を受けない(class 57 の他 — 57014 取り消し等 — は engine の答え)。
;; 業務の文の誤りではなく、時間を置いて撃ち直せば晴れる失敗なので SqlFailed にしない(agora-redesign #880 の裁定)。
(val UNREACHABLE-SQLSTATE-CLASS "08")
(val UNREACHABLE-SQLSTATES #("57P01" "57P02" "57P03"))
;; 同時に貸す接続の既定の上限(database ごと)。
(val DEFAULT-POOL-SIZE 8)
;; postgres-sql-handler が driver の I/O を回す Executor(呼び 1 つに thread 1 本 — 頭の註)。持ち物が無いので module に 1 つ。
(val DRIVER-THREADS (ThreadPerCall))

;; 待ち受けの接続が切れてから繋ぎ直すまでの秒(頭の註 — 境界の答え手の中だけの時間)。
(val LISTEN-RETRY-SECONDS 1)

;; 宣言の欄の型 → PostgreSQL の型。
(val POSTGRES-TYPES {SqlColumnType.INTEGER "bigint" SqlColumnType.FLOAT "double precision" SqlColumnType.TEXT "text"
                     SqlColumnType.BYTES "bytea" SqlColumnType.BOOLEAN "boolean" SqlColumnType.JSON "json"})


(defclass [(dataclass :frozen True :kw-only True)] PostgresDatabase []
  "PostgreSQL の database の宣言(name = 業務が SqlQuery に書く名・dsn = psycopg の接続文字列 — 資格を含むので repr に出さない)。"
  #^ str name
  #^ str dsn
  (setv dsn (field :repr False)))


(defrecord PostgresTimeouts
  "接続の上限(頭の註): 開く時(秒)・keepalive の最初の問いまでの無音(秒)・問いの間隔(秒)・答えの無い問いの数・送った bytes の返事を
   待つ上限(ミリ秒)・文の上限(ミリ秒 — None = 付けない)・transaction の途中で何もしない上限(ミリ秒 — None = 付けない)。
   transaction の途中の上限は、BEGIN の後に program が進まなくなった接続(返す finally に届かない — 2026-09-30 の着地の台帳で、
   pg_advisory_xact_lock を持ったまま 6 分半 idle in transaction で残り、後続の書きが全部 statement timeout で落ちた・agora-redesign #1846)を
   engine が切り、transaction を戻して錠を外すため。"
  (#^ int connect-seconds)
  (#^ int keepalive-idle-seconds)
  (#^ int keepalive-interval-seconds)
  (#^ int keepalive-count)
  (#^ int unacknowledged-milliseconds)
  (#^ (| int None) statement-milliseconds)
  (#^ (| int None) idle-transaction-milliseconds))


;; 既定の上限: 死んだ相手は keepalive で 10 + 5 × 3 = 25 秒・文を送った後は 15 秒で見つかる。文の上限 60 秒は記録の service の文(短い
;; 読み書きと、錠を待つ表の用意)より十分長い。transaction の途中で何もしない上限 30 秒は、記録の service の transaction(文の間は純粋な
;; 計算だけ)より十分長く、錠を持ったまま止まった接続が後続の書きを塞ぐ時間を 30 秒で切る(agora-redesign #1846)。
(val DEFAULT-TIMEOUTS (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10 :keepalive-interval-seconds 5 :keepalive-count 3
                                        :unacknowledged-milliseconds 15000 :statement-milliseconds 60000
                                        :idle-transaction-milliseconds 30000))


(defrecord PostgresStatement
  "psycopg へ渡す形に書き換えた文(text = `%(name)s` の文・params = 引数 — dict にするのは driver を呼ぶ 1 点だけ)。"
  (#^ str text)
  (#^ tuple params))


(defclass PostgresConnections []
  "接続の貸し出し(頭の註)。資源なので値の型ではない(中身を書き換え、同一性で扱う)。size = database ごとに同時に貸す接続の上限
   (pooled-postgres-sql-handler が scheduler の許可の数として読む)・timeouts = 開く接続に付ける上限。"

  (defn __init__ [self #^ tuple databases * [size DEFAULT-POOL-SIZE] [timeouts DEFAULT-TIMEOUTS]]  ; defk にできない: 資源の class の初期化
    "database の宣言の列と、database ごとに同時に貸す接続の上限と、接続の上限を受けるため。"
    (setv self.size size
          self.timeouts timeouts
          self.databases (dfor d databases d.name d)
          self.idle (dfor d databases d.name (Queue))
          self.permits (dfor d databases d.name (threading.BoundedSemaphore size))
          self.listeners {}
          self.listeners-lock (threading.Lock)))

  (defn #^ PostgresListener listener [self #^ str name #^ str channel]  ; defk にできない: 資源の待ち受けを引く(無ければ作って thread を起こす — VM の外の錠)
    "database name の channel の待ち受け(PostgresListener)を引くため(初めての channel なら作る)。"
    (with [_ self.listeners-lock]
      (setv key #(name channel))
      (when (not-in key self.listeners)
        (setv (get self.listeners key) (PostgresListener self name channel)))
      (get self.listeners key)))

  (defn #^ None ring-local [self #^ str name #^ str channel]  ; defk にできない: commit の後に答え手が呼ぶ(VM の外の錠)
    "同じ process で channel に掛かっている呼び鈴をその場で鳴らすため(待ち受けがまだ無ければ掛かっている呼び鈴も無い)。"
    (with [_ self.listeners-lock]
      (setv listener (.get self.listeners #(name channel))))
    (when (is-not listener None)
      (.ring listener)))

  (defn names [self]  ; defk にできない: 答え手の番(:when)で呼ぶ読み(Program を返すと真偽にならない)
    "この貸し出しが答える database の名の並びを読むため。"
    (tuple self.databases))

  (defn connection-options [self #^ str name]  ; defk にできない: 接続を開く thread の中で読む psycopg への引数(Program を返すと psycopg へ渡せない)
    "開く接続に付ける上限を libpq の接続の parameter の写像で読むため(connect の keyword に渡す — DSN に同じ名が在ればこちらが勝つ)。
     ただし options(-c を連ねた 1 本の値)は勝たせず、database name の DSN の options に engine の上限の -c を継ぎ足す — 勝たせると DSN の
     -c search_path などが黙って消える(agora-redesign #1771)。"
    (setv timeouts self.timeouts
          options {"connect_timeout" timeouts.connect-seconds
                   "keepalives" 1
                   "keepalives_idle" timeouts.keepalive-idle-seconds
                   "keepalives_interval" timeouts.keepalive-interval-seconds
                   "keepalives_count" timeouts.keepalive-count
                   "tcp_user_timeout" timeouts.unacknowledged-milliseconds})
    ;; engine の上限(-c を連ねる): 文の上限と、transaction の途中で何もしない上限(agora-redesign #1846)。None の上限は付けない。
    (setv bounds (lfor [setting value] [#("statement_timeout" timeouts.statement-milliseconds)
                                        #("idle_in_transaction_session_timeout" timeouts.idle-transaction-milliseconds)]
                       :if (is-not value None)
                       (.format "-c {}={}" setting value)))
    (when bounds
      (setv bound (.join " " bounds)
            dsn (. (get self.databases name) dsn)
            declared (when dsn
                       ;; DSN の読みは libpq の綴り(URI・key=value の両方)なので psycopg の読みに任せる(DSN が空なら読まない —
                       ;; psycopg は postgres の追加の依存で、接続を開く acquire と同じく使う時だけ読む)。
                       (import psycopg.conninfo [conninfo-to-dict])
                       (.get (conninfo-to-dict dsn) "options")))
      (setv (get options "options") (if declared (+ declared " " bound) bound)))
    options)

  (defn acquire [self #^ str name]  ; defk にできない: 資源の貸し出し(thread の間で blocking に待つ)
    "接続を 1 本借りるため(空きが無ければ開く・上限なら返されるまで待つ)。開けない時は psycopg の例外を通す。"
    (import psycopg)
    (import psycopg.types.string [TextLoader])
    (.acquire (get self.permits name))
    (try
      (try
        (.get-nowait (get self.idle name))
        (except [Empty]
          (setv connection (psycopg.connect (. (get self.databases name) dsn) :autocommit True
                                            #** (.connection-options self name)))
          (.register-loader connection.adapters "json" TextLoader)
          (.register-loader connection.adapters "jsonb" TextLoader)
          connection))
      (except [BaseException]
        (.release (get self.permits name))
        (raise))))

  (defn release [self #^ str name connection]  ; defk にできない: 資源の返却(with / finally から呼ぶ)
    "借りた接続を返すため(切れていれば捨てる・transaction の途中なら rollback し、できなければ捨てる)。"
    (import psycopg)
    (try
      (cond
        (or connection.closed connection.broken) (.close connection)
        (!= connection.info.transaction-status psycopg.pq.TransactionStatus.IDLE)
          (try
            (.rollback connection)
            (.put (get self.idle name) connection)
            (except [psycopg.Error] (.close connection)))
        True (.put (get self.idle name) connection))
      (finally (.release (get self.permits name)))))

  (defn close [self]  ; defk にできない: 組み立ての側が止める時に呼ぶ資源の後始末
    "空きの接続を全部閉じるため(貸している接続は返された時に閉じない — 止めた後に呼ぶ)。"
    (for [idle (.values self.idle)]
      (while (not (.empty idle))
        (.close (.get-nowait idle))))))


(defclass PostgresListener []
  "channel 1 つの待ち受け(頭の註): LISTEN を張った接続 1 本を daemon の thread で持ち、掛かっている呼び鈴(外の promise)を通知ごとに
   鳴らす。資源なので値の型ではない。listening = 今 LISTEN が張れているか・listened = 1 度でも張れたか・failure = 張れていない理由・
   settled = 最初の接続を試し終えた印。"

  (defn #^ None __init__ [self #^ PostgresConnections connections #^ str name #^ str channel]  ; defk にできない: 資源の class の初期化
    "待ち受けを作り、LISTEN の thread を起こすため。"
    (setv self.connections connections
          self.name name
          self.channel channel
          self.lock (threading.Lock)
          self.bells (set)
          self.listening False
          self.listened False
          self.failure None
          self.settled (threading.Event))
    (.start (threading.Thread :target self.listen :name "doeff-postgres-listen" :daemon True)))

  (defn #^ None ring [self]  ; defk にできない: 待ち受けの thread と commit の後の答え手が呼ぶ(VM の外)
    "掛かっている呼び鈴を全部鳴らして外すため(鳴った呼び鈴は True で完了する — WaitWithin の時間切れの None と見分ける)。"
    (with [_ self.lock]
      (setv bells (tuple self.bells))
      (.clear self.bells))
    (for [bell bells]
      (.complete bell True)))

  (defn #^ (| str None) hang [self #^ ExternalPromise bell]  ; defk にできない: driver の thread で回す(最初の接続を待つ)
    "1 度でも LISTEN が張れていれば呼び鈴を掛けて None を、1 度も張れていなければ理由の文を返すため(最初の接続を試し終えるまで待つ)。
     繋ぎ直しの最中に掛けた呼び鈴は、繋ぎ直した時に鳴る(その間の通知は届かないので、待ち手は鳴った後に読み直す)。"
    (.wait self.settled)
    (with [_ self.lock]
      (if self.listened
          (do (.add self.bells bell) None)
          (or self.failure "LISTEN が張れていない"))))

  (defn #^ None drop [self #^ ExternalPromise bell]  ; defk にできない: 答え手の節と取り消しの後始末が呼ぶ(VM の外の錠)
    "鳴らなかった呼び鈴を外すため(もう外れていれば何もしない)。"
    (with [_ self.lock]
      (.discard self.bells bell)))

  (defn #^ None listen [self]  ; defk にできない: daemon の thread の本体(blocking に通知を待つ)
    "LISTEN を張って通知ごとに呼び鈴を鳴らし、切れたら LISTEN-RETRY-SECONDS 置いて繋ぎ直すため(張った時にも鳴らす — 頭の註)。"
    (import psycopg)
    (import psycopg [sql])
    (while True
      (try
        (with [connection (psycopg.connect (. (get self.connections.databases self.name) dsn) :autocommit True
                                           #** (.connection-options self.connections self.name))]
          (.execute connection (.format (sql.SQL "LISTEN {}") (sql.Identifier self.channel)))
          (with [_ self.lock]
            (setv self.listening True
                  self.listened True
                  self.failure None))
          (.set self.settled)
          (.ring self)
          (for [_ (.notifies connection)]
            (.ring self)))
        (except [error psycopg.Error]
          (with [_ self.lock]
            (setv self.listening False
                  self.failure (str error)))
          (.set self.settled)
          (.ring self)
          (time.sleep LISTEN-RETRY-SECONDS))))))


(defk postgres-statement [statement params]
  {:pre [(: statement str) (: params tuple)] :post [(: % PostgresStatement)]
   :tags {:context "sql" :role "foundation"}}
  "中立の記法の文を psycopg の `%(name)s` の文へ書き換えるため(文の `%` は `%%` に・引数の名の食い違いは ValueError)。"
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (var text "")
  (for [part parts]
    (match part
      (SqlText :text piece) (:= text (+ text (.replace piece "%" "%%")))
      (SqlPlaceholder :name name) (:= text (+ text (.format "%({})s" name)))))
  (PostgresStatement :text text :params params))


(defk postgres-insert-statement [table columns]
  {:pre [(: table str) (: columns tuple)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows の executemany の文(位置の `%s`)を描くため。"
  (.format "INSERT INTO {} ({}) VALUES ({})" (! (checked-identifier table)) (.join ", " (! (checked-identifiers columns)))
           (.join ", " (* ["%s"] (len columns)))))


(defk postgres-failure [sqlstate class-names message]
  {:pre [(: sqlstate (| str None)) (: class-names tuple) (: message str)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "psycopg の例外(SQLSTATE・類の名の並び = MRO の名・文)を失敗の値へ写すため(頭の註)。postgres-sql-handler と
   pooled-postgres-sql-handler が共に使う読み分けの 1 点。"
  (cond
    (and (is-not sqlstate None) (or (.startswith sqlstate UNREACHABLE-SQLSTATE-CLASS) (in sqlstate UNREACHABLE-SQLSTATES)))
      (SqlUnreachable :reason message)
    (is-not sqlstate None) (SqlFailed :sqlstate sqlstate :reason message)
    (any (gfor name class-names (in name UNREACHABLE-CLASSES))) (SqlUnreachable :reason message)
    True (SqlFailed :sqlstate (next (gfor name class-names :if (in name DRIVER-CLASS-SQLSTATES) (get DRIVER-CLASS-SQLSTATES name)) None)
                    :reason message)))


(defk postgres-schema-statements [tables]
  {:pre [(: tables tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を PostgreSQL の DDL(CREATE TABLE / INDEX IF NOT EXISTS)に描くため。"
  (var statements [])
  (for [table tables]
    (val table-name (! (checked-identifier table.name)))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}{}" (! (checked-identifier column.name)) (get POSTGRES-TYPES column.type)
                                    (if column.nullable "" " NOT NULL"))))
    (when table.primary-key
      (.append definitions (.format "PRIMARY KEY ({})" (.join ", " (! (checked-identifiers table.primary-key))))))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({})" table-name (.join ", " definitions)))
    (for [index table.indexes]
      (.append statements (.format "CREATE {}INDEX IF NOT EXISTS {} ON {} ({})" (if index.unique "UNIQUE " "") (! (checked-identifier index.name))
                                   table-name (.join ", " (! (checked-identifiers index.columns)))))))
  (tuple statements))


(defk postgres-error [error]
  {:pre [(: error Exception)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "psycopg の例外を postgres-failure の入力に割るため。"
  (<- failure (postgres-failure (getattr error "sqlstate" None) (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
  failure)


(defk postgres-run [connection text params]
  {:pre [(: connection "psycopg の接続") (: text str) (: params (| tuple None))] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つを流して答えの値にするため(params = None は引数の書き換えを通さない文 — DDL と transaction の区切り)。
   connection は psycopg の接続(psycopg を読まない環境でも module を読めるよう、型を契約に書かない)。"
  (import psycopg)
  (try
    (with [cursor (.cursor connection)]
      (.execute cursor text (if (is params None) None (dfor p params p.name p.value)))
      (if (is cursor.description None)
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
          (do (val rows (! (normalized-rows (.fetchall cursor))))
              (SqlRows :rows rows :rowcount (len rows)))))
    (except [error psycopg.Error]
      (<- failure (postgres-error error))
      failure)))


(defk postgres-query [connection request]
  {:pre [(: connection "psycopg の接続") (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlQuery 1 つに答えるため。"
  (<- bound (postgres-statement request.statement request.params))
  (<- answer (postgres-run connection bound.text bound.params))
  answer)


(defk postgres-insert [connection request]
  {:pre [(: connection "psycopg の接続") (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows 1 つに executemany で答えるため。"
  (import psycopg)
  (<- (checked-rows request.columns request.rows))
  (<- text (postgres-insert-statement request.table request.columns))
  (if (not request.rows)
      (SqlRows :rows #() :rowcount 0)
      (try
        (with [cursor (.cursor connection)]
          (.executemany cursor text (list request.rows))
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None)))
        (except [error psycopg.Error]
          (<- failure (postgres-error error))
          failure))))


(defk postgres-ensure-tables [connection tables]
  {:pre [(: connection "psycopg の接続") (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を DDL に描いて順に流すため(最初の失敗で止める)。"
  (<- statements (postgres-schema-statements tables))
  (for [statement statements]
    (<- answer (postgres-run connection statement None))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


(defk postgres-begin [connection lock-key]
  {:pre [(: connection "psycopg の接続") (: lock-key (| str None))] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction を始め、lock-key が在れば同じ鍵の transaction を直列にする錠を取るため(成功は None)。"
  (<- began (postgres-run connection "BEGIN" None))
  (cond
    (not (isinstance began SqlRows)) began
    (is lock-key None) None
    True (do (<- locked (postgres-query connection (SqlQuery "" "SELECT pg_advisory_xact_lock(hashtext(:key))"
                                                             #((SqlParam :name "key" :value lock-key)))))
             (if (isinstance locked SqlRows) None locked))))


(defk postgres-control [connection statement]
  {:pre [(: connection "psycopg の接続") (: statement str)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の区切りの文(COMMIT・ROLLBACK)を流すため(成功は None)。"
  (<- answer (postgres-run connection statement None))
  (if (isinstance answer SqlRows) None answer))


;; channel へ合図を出す文(中立の記法)— 答え手は他の文と同じ postgres-query で流す(transaction の中なら commit した時だけ届く)。
(val NOTICE-STATEMENT "SELECT pg_notify(:channel, '')")


(defk postgres-lease [connections database]
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "psycopg の接続 | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "接続を 1 本借りるため(開けなければ SqlUnreachable)。psycopg は借りが誤りで終わった時の読み分けでだけ読む — driver を読むのは貸し出しの
   acquire なので、借りが通る道は psycopg の無い環境でも偽の貸し出しで撃てる(agora-redesign #2792 の検)。"
  (try
    (.acquire connections database)
    (except [error Exception]
      (import psycopg)
      (match error
        (psycopg.Error) (SqlUnreachable :reason (str error))
        _ (raise)))))


(deff lease-now [connections database]  ; defk にできない: driver の thread で回す入口(VM の外)
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "psycopg の接続 | SqlUnreachable")]}
  "driver の thread で接続を 1 本借りるため(許可を待つのもこの thread — 開けなければ SqlUnreachable)。"
  (run-detached (postgres-lease connections database)))


(deff return-abandoned [connections database leased]  ; defk にできない: driver の thread で回す後始末の入口
  {:pre [(: connections PostgresConnections) (: database str) (: leased "psycopg の接続 | SqlUnreachable")] :post [(: % "None")]}
  "取り消された待ち手に届かなかった接続を返すため(借りられなかった答え SqlUnreachable は返す物が無い)。"
  (when (not (isinstance leased SqlUnreachable))
    (.release connections database leased)))


(deff run-then-return [connections database leased claim work]  ; defk にできない: driver の thread で回す入口
  {:pre [(: connections PostgresConnections) (: database str) (: leased "psycopg の接続") (: claim "threading.Lock")
         (: work "(接続) → Program の callable")]
   :post [(: % "work の答え | None")]}
  "driver の thread の仕事 1 つで、借りた接続で work(接続 → Program)を流し、必ず返すため(文 1 つの effect の答え)。claim を先に取れた
   時だけ流す — 取れなければ、待ち手が文の始まる前に取り消され、offloaded-statement がもう接続を返している(答えは誰にも届かないので None)。"
  (when (.acquire claim :blocking False)
    (try
      (run-detached (work leased))
      (finally (.release connections database leased)))))


(defk offloaded-transaction [connections pool database program lock-key]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: lock-key (| str None))]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "接続 1 本を借りて program を 1 つの transaction で回し、必ず接続を返すため(各段の driver の I/O は pool の thread で — 頭の註)。
   postgres-sql-handler と pooled-postgres-sql-handler が共に使う。"
  (<- leased (offloaded pool (fn [] (lease-now connections database)) (fn [value] (return-abandoned connections database value))))
  ;; channels = この transaction が合図を出した channel(commit の後に同じ process の呼び鈴を鳴らす — 頭の註)。
  (val channels (set))
  (if (isinstance leased SqlUnreachable)
      leased
      (try
        (<- answer (run-in-transaction database program
                                       (fn [request] (offloaded pool (fn [] (run-detached (postgres-query leased request))) keep-nothing))
                                       (fn [request] (offloaded pool (fn [] (run-detached (postgres-insert leased request))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-detached (postgres-begin leased lock-key))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-detached (postgres-control leased "COMMIT"))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-detached (postgres-control leased "ROLLBACK"))) keep-nothing))
                                       :execute-notify (fn [request]
                                                         (.add channels request.channel)
                                                         (offloaded pool
                                                                    (fn [] (run-detached
                                                                             (postgres-query leased
                                                                                             (SqlQuery database NOTICE-STATEMENT
                                                                                                       #((SqlParam :name "channel"
                                                                                                                   :value request.channel))))))
                                                                    keep-nothing))))
        (when (not (isinstance answer #(SqlFailed SqlUnreachable)))
          (for [channel channels]
            (.ring-local connections database channel)))
        answer
        (finally
          (<- (offloaded pool (fn [] (.release connections database leased)) keep-nothing))))))


(defk offloaded-statement [connections pool database work]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: work "(接続) → Program の callable")]
   :post [(: % "work の答え | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つの effect を、transaction と同じく pool の仕事 2 つで答えるため(待つのは撃った task だけ — agora-redesign #2792)。
     1. 接続を借りる。許可を待っている間に取り消された要求は、許可が取れても文を流さず、return-abandoned ですぐ返す(DB が止まった間に
        取り消された要求が、DB が戻った時にまとめて文を流して後から来た要求を待たせない)。
     2. 文と返却を 1 つの仕事(run-then-return)で流す。走り出した文は止めず、終わった thread が返す(取り消された task は文の終わりを
        待たない)。借りた後で文の仕事が走り出す前に取り消されたら、finally が接続を返す — 返すのは claim(錠)を先に取った側の 1 度だけ。"
  (<- leased (offloaded pool (fn [] (lease-now connections database)) (fn [value] (return-abandoned connections database value))))
  (match leased
    (SqlUnreachable) leased
    _ (do (val claim (threading.Lock))
          (try
            (<- answer (offloaded pool (fn [] (run-then-return connections database leased claim work)) keep-nothing))
            answer
            (finally
              (when (.acquire claim :blocking False)
                (.submit pool return-abandoned connections database leased)))))))


(defk notified [connections pool database channel]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: channel str)]
   :post [(: % (| None SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の外の SqlNotify に答えるため(合図を流してから、同じ process の呼び鈴を鳴らす)。"
  (val request (SqlQuery database NOTICE-STATEMENT #((SqlParam :name "channel" :value channel))))
  (<- answer (offloaded-statement connections pool database (fn [leased] (postgres-query leased request))))
  (if (isinstance answer SqlRows)
      (do (.ring-local connections database channel)
          None)
      answer))


(defk hung-notice [connections pool database channel]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: channel str)]
   :post [(: % (| ExternalPromise SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlHangNotice に答えるため(待ち受けに呼び鈴を掛ける — 最初の接続を待つのは pool の thread。取り消されたら掛けた呼び鈴を外す)。"
  (val listener (.listener connections database channel))
  (<- bell ExternalPromise (CreateExternalPromise))
  (<- refused (offloaded pool (fn [] (.hang listener bell)) (fn [_] (.drop listener bell))))
  (if (is refused None)
      bell
      (SqlUnreachable :reason (.format "PostgreSQL の LISTEN が張れていない: {}" refused))))


(defhandler postgres-sql-handler [#^ PostgresConnections connections]
  ;; 引数に残す理由: 接続の貸し出しは組み立ての側が作って閉じる資源で(DSN と資格を持つ)、答える database の名で ClickHouse の答え手と
  ;; 同じ組に並べ分ける。
  "本物の PostgreSQL の答え手(頭の註 — scheduler を塞がない)。connections が宣言した database の名にだけ答える(他の名は外側へ回す)。"
  {:tags {:context "sql" :role "foundation"}}
  (SqlQuery [database statement params] :when (in database (.names connections))
    (val request (SqlQuery database statement params))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-query leased request))))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-insert leased request))))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-ensure-tables leased tables))))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (in database (.names connections))
    (<- answer (offloaded-transaction connections DRIVER-THREADS database program lock-key))
    (resume answer))
  (SqlNotify [database channel] :when (in database (.names connections))
    (<- answer (notified connections DRIVER-THREADS database channel))
    (resume answer))
  (SqlHangNotice [database channel] :when (in database (.names connections))
    (<- answer (hung-notice connections DRIVER-THREADS database channel))
    (resume answer))
  (SqlDropNotice [database channel bell] :when (in database (.names connections))
    (.drop (.listener connections database channel) bell)
    (resume None)))
