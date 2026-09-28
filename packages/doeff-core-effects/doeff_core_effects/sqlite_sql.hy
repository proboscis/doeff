;;; 汎用の SQL の effect(sql_effects.hy)の I/O なしの答え手 sqlite-sql-handler(agora-redesign #802 便 3)。stdlib の sqlite3 の memory の DB で
;;; 答える — 仮想の時計の模擬で、実 process も外の engine も起こさない。業務を知らない: 表は SqlEnsureTables で呼び手が宣言する。
;;;   - database の名ごとに `:memory:` の接続を 1 本(check_same_thread = False・自動 commit — transaction は BEGIN IMMEDIATE で明示)。
;;;     接続は session の値で、session の値の置き場(doeff_core_effects の state)は外側に要る。
;;;   - 引数は中立の `:name` を `?` へ書き換える(本物と同じ書き換えの道を通し、記法の誤りを模擬でも同じ所で断る)。
;;;   - 失敗は例外の類 → SQLSTATE の類の表(SQLITE-CLASS-SQLSTATES)で SqlFailed にする — 模擬で「断り(22 / 23)」と「一時的(40)」の
;;;     分岐を起こせるように。閉じた接続は SqlUnreachable。SetSqlOutage で database を不達にでき、その間は全部 SqlUnreachable。
;;;   - SqlTransaction の lock-key は使わない: 接続は 1 本で、transaction の中は同じ database の問い合わせと純粋な計算だけなので
;;;     scheduler の別の task が割り込まない(sql_transaction.hy)。
;;;   - 欄の型 BOOLEAN は sqlite に無いので、宣言の型の名で読み戻しを bool へ写す(本物の PostgreSQL と同じ値を返す)。
;;; 方言の差(sqlite で通らない PostgreSQL / ClickHouse の文)はこの答え手の外: 呼び手は両方で通る文を書く(#802 便 3 の案 A)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import sqlite3)
(import dataclasses [dataclass])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlColumnType SqlText SqlPlaceholder split-statement checked-params param-value
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects.sql_transaction [run-in-transaction])

;; 例外の類 → SQLSTATE の類(psycopg が SQLSTATE から類を選ぶ表の逆 — agora-controllers services/record/handlers_wire.hy の
;; DRIVER-CLASS-SQLSTATES と同じ類)。OperationalError は文で分ける(sqlite-failure)。
(val SQLITE-CLASS-SQLSTATES {"IntegrityError" "23000" "DataError" "22000" "InterfaceError" "22000" "ProgrammingError" "42000"
                             "NotSupportedError" "0A000" "InternalError" "XX000"})
;; 錠の取り合い(一時的)の SQLSTATE — serialization_failure。
(val BUSY-SQLSTATE "40001")
;; 文の誤り・無い表など OperationalError の残りの SQLSTATE。
(val OPERATIONAL-SQLSTATE "42000")

;; 宣言の欄の型 → sqlite の型の名(BOOLEAN は読み戻しを bool へ写すための名・JSON は text)。
(val SQLITE-TYPES {SqlColumnType.INTEGER "INTEGER" SqlColumnType.FLOAT "REAL" SqlColumnType.TEXT "TEXT" SqlColumnType.BYTES "BLOB"
                   SqlColumnType.BOOLEAN "BOOLEAN" SqlColumnType.JSON "TEXT"})

(sqlite3.register-converter "BOOLEAN" (fn [raw] (!= (int raw) 0)))


(defrecord SqliteStatement
  "sqlite へ渡す形に書き換えた文(text = `?` の文・values = 引数の値を `?` の順に)。"
  (#^ str text)
  (#^ tuple values))


(defrecord SqliteConnection
  "database の名と、その memory の DB の接続 1 本(session の値)。"
  (#^ str name)
  (#^ sqlite3.Connection connection))


(defk sqlite-statement [statement params]
  {:pre [(: statement str) (: params tuple)] :post [(: % SqliteStatement)]
   :tags {:context "sql" :role "foundation"}}
  "中立の記法の文を sqlite の `?` の文へ書き換えるため(引数の名の食い違いは ValueError)。"
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (var text "")
  (var values [])
  (for [part parts]
    (match part
      (SqlText :text piece) (:= text (+ text piece))
      (SqlPlaceholder :name name) (do (:= text (+ text "?"))
                                      (.append values (! (param-value params name))))))
  (SqliteStatement :text text :values (tuple values)))


(defk sqlite-failure [class-names message]
  {:pre [(: class-names tuple) (: message str)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "sqlite3 の例外(類の名の並び = MRO の名・文)を失敗の値へ写すため(頭の註の表)。"
  (val lowered (.lower message))
  (cond
    (in "closed database" lowered) (SqlUnreachable :reason message)
    (and (in "OperationalError" class-names) (or (in "locked" lowered) (in "busy" lowered))) (SqlFailed :sqlstate BUSY-SQLSTATE :reason message)
    (in "OperationalError" class-names) (SqlFailed :sqlstate OPERATIONAL-SQLSTATE :reason message)
    True (SqlFailed :sqlstate (next (gfor name class-names :if (in name SQLITE-CLASS-SQLSTATES) (get SQLITE-CLASS-SQLSTATES name)) None)
                    :reason message)))


(defk sqlite-schema-statements [tables]
  {:pre [(: tables tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を sqlite の DDL(CREATE TABLE / INDEX IF NOT EXISTS)に描くため。"
  (var statements [])
  (for [table tables]
    (val table-name (! (checked-identifier table.name)))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}{}" (! (checked-identifier column.name)) (get SQLITE-TYPES column.type)
                                    (if column.nullable "" " NOT NULL"))))
    (when table.primary-key
      (.append definitions (.format "PRIMARY KEY ({})" (.join ", " (! (checked-identifiers table.primary-key))))))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({})" table-name (.join ", " definitions)))
    (for [index table.indexes]
      (.append statements (.format "CREATE {}INDEX IF NOT EXISTS {} ON {} ({})" (if index.unique "UNIQUE " "") (! (checked-identifier index.name))
                                   table-name (.join ", " (! (checked-identifiers index.columns)))))))
  (tuple statements))


(defk sqlite-run [connection text values]
  {:pre [(: connection sqlite3.Connection) (: text str) (: values tuple)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "書き換えた文 1 つを流して答えの値にするため(行を返す文の rowcount は返した行の数)。"
  (try
    (val cursor (.execute connection text values))
    (if (is cursor.description None)
        (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
        (do (val rows (! (normalized-rows (.fetchall cursor))))
            (SqlRows :rows rows :rowcount (len rows))))
    (except [error sqlite3.Error]
      (<- failure (sqlite-failure (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
      failure)))


(defk sqlite-query [connection request]
  {:pre [(: connection sqlite3.Connection) (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlQuery 1 つに答えるため。"
  (<- bound (sqlite-statement request.statement request.params))
  (<- answer (sqlite-run connection bound.text bound.values))
  answer)


(defk sqlite-insert [connection request]
  {:pre [(: connection sqlite3.Connection) (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows 1 つに executemany で答えるため。"
  (<- (checked-rows request.columns request.rows))
  (val table (! (checked-identifier request.table)))
  (val names (! (checked-identifiers request.columns)))
  (val text (.format "INSERT INTO {} ({}) VALUES ({})" table (.join ", " names) (.join ", " (* ["?"] (len names)))))
  (try
    (val cursor (.executemany connection text (list request.rows)))
    (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
    (except [error sqlite3.Error]
      (<- failure (sqlite-failure (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
      failure)))


(defk sqlite-ensure-tables [connection tables]
  {:pre [(: connection sqlite3.Connection) (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を DDL に描いて順に流すため(最初の失敗で止める)。"
  (<- statements (sqlite-schema-statements tables))
  (for [statement statements]
    (<- answer (sqlite-run connection statement #()))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


(defk sqlite-control [connection statement]
  {:pre [(: connection sqlite3.Connection) (: statement str)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の区切りの文(BEGIN IMMEDIATE・COMMIT・ROLLBACK)を流すため(成功は None)。"
  (<- answer (sqlite-run connection statement #()))
  (if (isinstance answer SqlRows) None answer))


(defk with-connection [connections database]
  {:pre [(: connections tuple) (: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "database の memory の DB の接続が置き場に在ることを確かめ、無ければ開いて足した置き場を返すため。session の値は同じ答え手の全部で
   分け合う(鍵が module と答え手の名で決まる)ので、答え手ごとではなく database の名ごとに 1 本を持つ。宣言の型を読むのは BOOLEAN の読み戻しのため。"
  (if (any (gfor c connections (= c.name database)))
      connections
      (+ connections #((SqliteConnection :name database
                                         :connection (sqlite3.connect ":memory:" :check-same-thread False :isolation-level None
                                                                      :detect-types sqlite3.PARSE-DECLTYPES))))))


(defk connection-of [connections database]
  {:pre [(: connections tuple) (: database str)] :post [(: % sqlite3.Connection)]
   :tags {:context "sql" :role "foundation"}}
  "置き場から database の接続を引くため(with-connection の後に呼ぶ)。"
  (next (gfor c connections :if (= c.name database) c.connection)))


(defhandler sqlite-sql-handler [#^ tuple databases]
  ;; 引数に残す理由: 答える database の名で PostgreSQL / ClickHouse の答え手と同じ組に並べ分ける(Ask では組の中の区別が付かない)。
  "I/O なしの SQL の答え手(頭の註)。databases = 答える database の名の tuple(他の名の effect は外側へ回す)。"
  {:tags {:context "sql" :role "foundation"}}
  (session var connections #())
  (session var unreachable #())
  (SetSqlOutage [database down] :when (in database databases)
    (:= unreachable (if down
                        (tuple (sorted (| (set unreachable) #{database})))
                        (tuple (gfor name unreachable :if (!= name database) name))))
    (resume None))
  (SqlQuery [database statement params] :when (in database databases)
    (<- opened (with-connection connections database))
    (:= connections opened)
    (if (in database unreachable)
        (resume (SqlUnreachable :reason (.format "模擬の不達: {}" database)))
        (do (<- connection (connection-of connections database))
            (<- answer (sqlite-query connection (SqlQuery database statement params)))
            (resume answer))))
  (SqlInsertRows [database table columns rows] :when (in database databases)
    (<- opened (with-connection connections database))
    (:= connections opened)
    (if (in database unreachable)
        (resume (SqlUnreachable :reason (.format "模擬の不達: {}" database)))
        (do (<- connection (connection-of connections database))
            (<- answer (sqlite-insert connection (SqlInsertRows database table columns rows)))
            (resume answer))))
  (SqlEnsureTables [database tables] :when (in database databases)
    (<- opened (with-connection connections database))
    (:= connections opened)
    (if (in database unreachable)
        (resume (SqlUnreachable :reason (.format "模擬の不達: {}" database)))
        (do (<- connection (connection-of connections database))
            (<- answer (sqlite-ensure-tables connection tables))
            (resume answer))))
  (SqlTransaction [database program lock-key] :when (in database databases)
    (<- opened (with-connection connections database))
    (:= connections opened)
    (if (in database unreachable)
        (resume (SqlUnreachable :reason (.format "模擬の不達: {}" database)))
        (do (<- connection (connection-of connections database))
            (<- answer (run-in-transaction database program
                                           (fn [request] (sqlite-query connection request))
                                           (fn [request] (sqlite-insert connection request))
                                           (fn [] (sqlite-control connection "BEGIN IMMEDIATE"))
                                           (fn [] (sqlite-control connection "COMMIT"))
                                           (fn [] (sqlite-control connection "ROLLBACK"))))
            (resume answer)))))
