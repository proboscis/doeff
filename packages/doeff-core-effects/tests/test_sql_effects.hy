;;; 汎用の SQL の effect(sql_effects.hy — agora-redesign #802 便 3)の検。
;;;   - I/O なしの答え手(sqlite-sql-handler)に、effect の約束を全部撃つ: 引数の書き換えと値の正規化・失敗の 2 型(SqlFailed の SQLSTATE の類・
;;;     SqlUnreachable)・transaction の約束 3 つ(最初の失敗で rollback して再開しない・禁じた effect と入れ子を断る・例外で rollback)・
;;;     DDL の宣言・宣言していない database を外側へ回す。
;;;   - 本物の答え手(postgres-sql-handler・clickhouse-http-sql-handler)は、方言の書き換えと答えの写しを純関数で撃つ。実 DB の結合の検は
;;;     DSN / URL の環境変数(DOEFF_SQL_TEST_POSTGRES_DSN・DOEFF_SQL_TEST_CLICKHOUSE_URL)が無ければ skip する。
;;;   - scheduler を塞がない答え手(pooled-postgres-sql-handler — #880 U2)は実 PG で: 同じ筋書きの答え・遅い問い合わせの横で別の task が進む・
;;;     transaction の錠の番号が旧い書き方の hashtext と同じ・取り消しで rollback して接続と許可を返す。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import os)
(import json)
(import importlib.util)
(import threading)
(import time)
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import decimal [Decimal])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.effects [Get])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed
                                        SqlUnreachable SqlSchemaApplied SqlTransactionMisuse SqlTable SqlColumn SqlColumnType SqlIndex
                                        normalized-value])
(import doeff_core_effects.sqlite_sql [sqlite-sql-handler sqlite-statement sqlite-failure])
(import doeff_core_effects.postgres_sql [postgres-sql-handler postgres-statement postgres-insert-statement postgres-failure
                                         postgres-schema-statements PostgresDatabase PostgresConnections])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn Gather Cancel TaskCancelledError])
(import doeff_core_effects.clickhouse_http_sql [clickhouse-http-sql-handler clickhouse-statement clickhouse-query-request
                                                clickhouse-insert-request clickhouse-rows clickhouse-written-rows clickhouse-failure
                                                clickhouse-schema-statements ClickHouseDatabase ClickHouseResponse ClickHouseParam])

(val DB "store")
(val POSTGRES-DSN (os.environ.get "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val CLICKHOUSE-URL (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_URL"))

;; 検の表: 主鍵つきの行の表と、一意の索引を持つ表。
(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT)
                                (SqlColumn :name "weight" :type SqlColumnType.FLOAT :nullable True)
                                (SqlColumn :name "blob" :type SqlColumnType.BYTES :nullable True)
                                (SqlColumn :name "flag" :type SqlColumnType.BOOLEAN :nullable True)
                                (SqlColumn :name "doc" :type SqlColumnType.JSON :nullable True))
                     :primary-key #("id")
                     :indexes #((SqlIndex :name "items_label" :columns #("label") :unique True))))


(defk seeded [database]
  {:pre [(: database str)] :post [(: % SqlSchemaApplied)]
   :tags {:context "sql" :role "program"}}
  "検の表を宣言して用意するため。"
  (<- applied (SqlEnsureTables database #(ITEMS)))
  applied)


(defk count-items [database]
  {:pre [(: database str)] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "items の行の数を読むため。"
  (<- answer SqlRows (SqlQuery database "SELECT count(*) FROM items" #()))
  (val counted (get answer.rows 0 0))
  (assert (isinstance counted int) counted)
  counted)


;; --- 引数の書き換えと値の正規化 --------------------------------------------------------------------------------------------------

(defk round-trip [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "閉じた集合の値を全部書いて読み戻す筋書き(sqlite と本物の PostgreSQL で同じ答えになる)。"
  (<- (seeded database))
  (<- inserted (SqlInsertRows database "items" #("id" "label" "weight" "blob" "flag" "doc")
                              #(#(1 "一" 1.5 b"\x00\xff" True "{\"a\": 1}") #(2 "二" None None False None))))
  (<- one (SqlQuery database "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 3) (SqlParam :name "label" :value "a:b"))))
  (<- read (SqlQuery database
                     "SELECT id, label, weight, blob, flag, doc, ':id' FROM items WHERE id >= :low AND label <> ':low' -- :ignored
                      ORDER BY id"
                     #((SqlParam :name "low" :value 1))))
  #(inserted one read))


(deftest test-sqlite-rewrites-neutral-params-and-normalizes-values
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (round-trip DB)))
  (val inserted (get answers 0))
  (val one (get answers 1))
  (val read (get answers 2))
  (assert (= inserted (SqlRows :rows #() :rowcount 2)) inserted)
  (assert (= one (SqlRows :rows #() :rowcount 1)) one)
  ;; literal と注釈の中の `:name` は引数でない・BOOLEAN は bool・BYTES は bytes・JSON は text。
  (assert (= read.rows #(#(1 "一" 1.5 b"\x00\xff" True "{\"a\": 1}" ":id") #(2 "二" None None False None ":id") #(3 "a:b" None None None None ":id")))
          read)
  (assert (= read.rowcount 3)))


(deftest test-params-must-match-the-statement
  (<- bound (sqlite-statement "SELECT :a, :a, x::text" #((SqlParam :name "a" :value 1))))
  (assert (= #(bound.text bound.values) #("SELECT ?, ?, x::text" #(1 1))) bound)
  (for [#(statement params) [#("SELECT :a" #()) #("SELECT 1" #((SqlParam :name "a" :value 1)))
                             #("SELECT :a" #((SqlParam :name "a" :value 1) (SqlParam :name "a" :value 2)))]]
    (try
      (<- (sqlite-statement statement params))
      (assert False (.format "断られていない: {}" statement))
      (except [ValueError])))
  (try
    (<- (sqlite-statement "SELECT :a" #((SqlParam :name "a" :value (json.loads "[1]")))))
    (assert False "閉じた集合の外の値が通った")
    (except [TypeError]))
  (assert (= (! (normalized-value (Decimal "2"))) 2)))


;; --- 失敗の 2 型 ------------------------------------------------------------------------------------------------------------

(defk failures [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "断り(一意の違反)・文の誤り・不達・戻った後の答えを集める筋書き。"
  (<- (seeded database))
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (1, 'x')" #()))
  (<- duplicate (SqlQuery database "INSERT INTO items (id, label) VALUES (1, 'y')" #()))
  (<- broken (SqlQuery database "SELEC 1" #()))
  (<- (SetSqlOutage database True))
  (<- down (SqlQuery database "SELECT 1" #()))
  (<- down-insert (SqlInsertRows database "items" #("id" "label") #(#(9 "z"))))
  (<- (SetSqlOutage database False))
  (<- back (SqlQuery database "SELECT 1" #()))
  #(duplicate broken down down-insert back))


(deftest test-sqlite-gives-failures-as-values
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (failures DB)))
  (val duplicate (get answers 0))
  (val broken (get answers 1))
  (val down (get answers 2))
  (val down-insert (get answers 3))
  (val back (get answers 4))
  (assert (and (isinstance duplicate SqlFailed) (= duplicate.sqlstate "23000")) duplicate)
  (assert (and (isinstance broken SqlFailed) (= broken.sqlstate "42000")) broken)
  (assert (isinstance down SqlUnreachable) down)
  (assert (isinstance down-insert SqlUnreachable) down-insert)
  (assert (= back (SqlRows :rows #(#(1)) :rowcount 1)) back))


(deftest test-sqlite-exception-classes-map-to-sqlstate-classes
  (assert (= (! (sqlite-failure #("IntegrityError" "DatabaseError" "Error") "UNIQUE constraint failed")) (SqlFailed :sqlstate "23000" :reason "UNIQUE constraint failed")))
  (assert (= (. (! (sqlite-failure #("DataError" "DatabaseError") "too big")) sqlstate) "22000"))
  (assert (= (. (! (sqlite-failure #("OperationalError" "DatabaseError") "database is locked")) sqlstate) "40001"))
  (assert (= (. (! (sqlite-failure #("OperationalError" "DatabaseError") "no such table: x")) sqlstate) "42000"))
  (assert (isinstance (! (sqlite-failure #("ProgrammingError" "DatabaseError") "Cannot operate on a closed database.")) SqlUnreachable)))


;; --- transaction の約束 ---------------------------------------------------------------------------------------------------

(defk insert-two [database log]
  {:pre [(: database str) (: log list)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 2 行を入れる program(log に進み具合を残す)。"
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (10, 'a')" #()))
  (.append log "一行目")
  (<- (SqlInsertRows database "items" #("id" "label") #(#(11 "b"))))
  (.append log "二行目")
  "入れた")


(defk fail-second [database log]
  {:pre [(: database str) (: log list)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "1 行目を入れた後に一意の違反を起こす program。例外を広く捕まえても、失敗の後は再開されないことを見る。"
  (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (20, 'c')" #()))
  (.append log "一行目")
  (try
    (<- (SqlQuery database "INSERT INTO items (id, label) VALUES (20, 'd')" #()))
    (.append log "失敗の後に再開された")
    (except [Exception]
      (.append log "失敗が例外で投げ込まれた")))
  "ここへは来ない")


(defk misuse [database program]
  {:pre [(: database str) (: program "Program")] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction が SqlTransactionMisuse で断られることを、断りの文にして返す筋書き。"
  (try
    (<- (SqlTransaction database program None))
    "断られていない"
    (except [error SqlTransactionMisuse]
      (str error))))


(defk ask-state []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で禁じた effect(状態の Get)を出す program。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (30, 'e')" #()))
  (<- (Get "key"))
  "来ない")


(defk nested []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で入れ子の transaction を出す program。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (31, 'f')" #()))
  (<- (SqlTransaction DB (count-items DB) None))
  "来ない")


(defk other-database []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で別の database へ問い合わせる program。"
  (<- (SqlQuery "other" "SELECT 1" #()))
  "来ない")


(defk raising []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "1 行を入れた後に例外を投げる program。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (40, 'g')" #()))
  (raise (RuntimeError "program の誤り")))


(defk transactions [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "transaction の約束 3 つを順に撃つ筋書き。"
  (<- (seeded database))
  (val committed-log [])
  (<- committed (SqlTransaction database (insert-two database committed-log) "lock-a"))
  (<- after-commit (count-items database))
  (val failed-log [])
  (<- failed (SqlTransaction database (fail-second database failed-log) None))
  (<- after-failure (count-items database))
  (<- refused-state (misuse database (ask-state)))
  (<- refused-nested (misuse database (nested)))
  (<- refused-other (misuse database (other-database)))
  (<- after-misuse (count-items database))
  (var raised "")
  (try
    (<- (SqlTransaction database (raising) None))
    (except [error RuntimeError] (:= raised (str error))))
  (<- after-raise (count-items database))
  #(committed (tuple committed-log) after-commit failed (tuple failed-log) after-failure refused-state refused-nested refused-other after-misuse
    raised after-raise))


(deftest test-sqlite-transaction-keeps-the-three-promises
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (transactions DB)))
  (val committed (get answers 0))
  (val committed-log (get answers 1))
  (val after-commit (get answers 2))
  (val failed (get answers 3))
  (val failed-log (get answers 4))
  (val after-failure (get answers 5))
  (val refused-state (get answers 6))
  (val refused-nested (get answers 7))
  (val refused-other (get answers 8))
  (val after-misuse (get answers 9))
  (val raised (get answers 10))
  (val after-raise (get answers 11))
  ;; commit した program の答えがそのまま返り、2 行が残る。
  (assert (= #(committed committed-log after-commit) #("入れた" #("一行目" "二行目") 2)))
  ;; (1) 最初の失敗で rollback し、program を再開しない(例外を投げ込まない — 広く捕まえる program でも続かない)。
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23000")) failed)
  (assert (= failed-log #("一行目")) failed-log)
  (assert (= after-failure 2) after-failure)
  ;; (2) 禁じた effect・入れ子・別の database は断られ、rollback される。
  (assert (in "Get" refused-state) refused-state)
  (assert (in "SqlTransaction" refused-nested) refused-nested)
  (assert (in "other" refused-other) refused-other)
  (assert (= after-misuse 2) after-misuse)
  ;; (3) program の例外は rollback して通す。
  (assert (= #(raised after-raise) #("program の誤り" 2))))


;; --- DDL の宣言と database の分け方 -------------------------------------------------------------------------------------------

(defk declared-twice []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "同じ宣言を 2 度流し(在れば何もしない)、一意の索引が効くことを見る筋書き。"
  (<- first (seeded DB))
  (<- second (seeded DB))
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (1, 'same')" #()))
  (<- duplicate-label (SqlQuery DB "INSERT INTO items (id, label) VALUES (2, 'same')" #()))
  #(first second duplicate-label))


(deftest test-sqlite-draws-the-schema-declaration
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (declared-twice)))
  (val first (get answers 0))
  (val second (get answers 1))
  (val duplicate-label (get answers 2))
  (assert (= first.statements
             #("CREATE TABLE IF NOT EXISTS items (id INTEGER NOT NULL, label TEXT NOT NULL, weight REAL, blob BLOB, flag BOOLEAN, doc TEXT, PRIMARY KEY (id))"
               "CREATE UNIQUE INDEX IF NOT EXISTS items_label ON items (label)"))
          first)
  (assert (= first second))
  (assert (and (isinstance duplicate-label SqlFailed) (= duplicate-label.sqlstate "23000")) duplicate-label))


(defk two-databases []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "2 つの database に同じ表を用意し、片方にだけ書く筋書き。"
  (<- (seeded "hot"))
  (<- (seeded "cold"))
  (<- (SqlQuery "hot" "INSERT INTO items (id, label) VALUES (1, 'x')" #()))
  #((! (count-items "hot")) (! (count-items "cold"))))


(deftest test-each-handler-answers-only-its-declared-databases
  ;; 内側の答え手は hot だけに答え、cold は外側の答え手へ回る(本物の PostgreSQL と ClickHouse を同じ組に並べる形)。
  (<- counts (with-handler [(state) (sqlite-sql-handler #("cold")) (sqlite-sql-handler #("hot"))] (two-databases)))
  (assert (= counts #(1 0)) counts))


;; --- 本物の答え手の書き換えと写し(純関数) ----------------------------------------------------------------------------------

(deftest test-postgres-rewrites-to-pyformat-and-escapes-percent
  (<- bound (postgres-statement "SELECT '%s :x', x::bigint, :x FROM t WHERE y LIKE 'a%' AND z = :z -- :x"
                                #((SqlParam :name "x" :value 1) (SqlParam :name "z" :value None))))
  (assert (= bound.text "SELECT '%%s :x', x::bigint, %(x)s FROM t WHERE y LIKE 'a%%' AND z = %(z)s -- :x") bound)
  (<- insert (postgres-insert-statement "items" #("id" "label")))
  (assert (= insert "INSERT INTO items (id, label) VALUES (%s, %s)") insert)
  (try
    (<- (postgres-insert-statement "items; DROP TABLE x" #("id")))
    (assert False "引用の要る名が通った")
    (except [ValueError])))


(deftest test-postgres-errors-map-to-the-two-failure-values
  (assert (= (! (postgres-failure "23505" #("UniqueViolation" "IntegrityError" "DatabaseError" "Error") "dup")) (SqlFailed :sqlstate "23505" :reason "dup")))
  (assert (= (! (postgres-failure None #("DataError" "DatabaseError" "Error") "NUL")) (SqlFailed :sqlstate "22000" :reason "NUL")))
  (assert (= (! (postgres-failure None #("OperationalError" "DatabaseError" "Error") "refused")) (SqlUnreachable :reason "refused")))
  (assert (= (! (postgres-failure None #("InterfaceError" "Error") "closed")) (SqlUnreachable :reason "closed")))
  (assert (= (! (postgres-failure None #("Error") "other")) (SqlFailed :sqlstate None :reason "other"))))


(deftest test-postgres-draws-the-schema-declaration
  (<- statements (postgres-schema-statements #(ITEMS)))
  (assert (= statements
             #("CREATE TABLE IF NOT EXISTS items (id bigint NOT NULL, label text NOT NULL, weight double precision, blob bytea, flag boolean, doc json, PRIMARY KEY (id))"
               "CREATE UNIQUE INDEX IF NOT EXISTS items_label ON items (label)"))
          statements))


(val CLICKHOUSE (ClickHouseDatabase :name "cold" :url "http://127.0.0.1:9/" :database "archive" :user "reader" :password "secret" :timeout 2.0))


(deftest test-clickhouse-rewrites-to-typed-params
  (<- bound (clickhouse-statement "SELECT :i, :s, :n, :b, :f, :y, ':i'"
                                  #((SqlParam :name "i" :value 7) (SqlParam :name "s" :value "a\tb\\c\n") (SqlParam :name "n" :value None)
                                    (SqlParam :name "b" :value True) (SqlParam :name "f" :value 1.5) (SqlParam :name "y" :value b"\x01"))))
  (assert (= bound.text "SELECT {i:Int64}, {s:String}, {n:Nullable(String)}, {b:Bool}, {f:Float64}, {y:String}, ':i'") bound)
  (assert (= bound.params #((ClickHouseParam :name "i" :text b"7") (ClickHouseParam :name "s" :text b"a\\tb\\\\c\\n")
                            (ClickHouseParam :name "n" :text b"\\N") (ClickHouseParam :name "b" :text b"true")
                            (ClickHouseParam :name "f" :text b"1.5") (ClickHouseParam :name "y" :text b"\x01")))
          bound.params)
  (<- request (clickhouse-query-request CLICKHOUSE "SELECT :i" #((SqlParam :name "i" :value 7))))
  (assert (= request.url (+ "http://127.0.0.1:9/?database=archive&default_format=JSONCompactEachRow"
                            "&output_format_json_quote_64bit_integers=0&wait_end_of_query=1&param_i=7"))
          request.url)
  (assert (= request.body b"SELECT {i:Int64}"))
  (assert (= (lfor h request.headers #(h.name h.value)) [#("X-ClickHouse-User" "reader") #("X-ClickHouse-Key" "secret")]))
  (assert (not-in "secret" (repr CLICKHOUSE)) "資格が repr に出た"))


(deftest test-clickhouse-insert-is-one-json-body
  (<- request (clickhouse-insert-request CLICKHOUSE "events" #("id" "body") #(#(1 "一") #(2 None))))
  (assert (in "query=INSERT+INTO+events+%28id%2C+body%29+FORMAT+JSONCompactEachRow" request.url) request.url)
  (assert (= request.body (.encode "[1, \"一\"]\n[2, null]\n" "utf-8")) request.body)
  (try
    (<- (clickhouse-insert-request CLICKHOUSE "events" #("body") #(#(b"\x00"))))
    (assert False "bytes の値が JSON に載った")
    (except [ValueError])))


(deftest test-clickhouse-answers-map-to-rows-and-failures
  (<- rows (clickhouse-rows b"[1,\"a\",null,true,1.5]\n\n[18446744073709551615,\"b\",\"c\",false,0]\n"))
  (assert (= rows #(#(1 "a" None True 1.5) #(18446744073709551615 "b" "c" False 0))) rows)
  (assert (= (! (clickhouse-written-rows "{\"read_rows\":\"0\",\"written_rows\":\"3\"}")) 3))
  (assert (is (! (clickhouse-written-rows None)) None))
  (<- failed (clickhouse-failure (ClickHouseResponse :status 404 :body b"Code: 60. DB::Exception: Unknown table\n" :exception-code "60" :summary None)))
  (assert (= failed (SqlFailed :sqlstate None :reason "Code: 60. DB::Exception: Unknown table")) failed)
  (<- gateway (clickhouse-failure (ClickHouseResponse :status 502 :body b"Bad Gateway" :exception-code None :summary None)))
  (assert (= gateway (SqlUnreachable :reason "HTTP 502: Bad Gateway")) gateway)
  (try
    (<- (clickhouse-rows b"[[1,2]]\n"))
    (assert False "配列の欄が閉じた集合を越えて通った")
    (except [TypeError])))


(deftest test-clickhouse-draws-the-schema-and-refuses-what-it-lacks
  (<- statements (clickhouse-schema-statements
                   #((SqlTable :name "events" :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER)
                                                          (SqlColumn :name "body" :type SqlColumnType.JSON :nullable True))
                               :primary-key #("id") :indexes #((SqlIndex :name "events_body" :columns #("body")))))))
  (assert (= statements
             #("CREATE TABLE IF NOT EXISTS events (id Int64, body Nullable(String), INDEX events_body (body) TYPE minmax GRANULARITY 1) ENGINE = MergeTree ORDER BY (id)"))
          statements)
  (<- unique (clickhouse-schema-statements #(ITEMS)))
  (assert (and (isinstance unique SqlFailed) (= unique.sqlstate "0A000")) unique))


(defk clickhouse-without-engine []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "transaction の断りと、届かない HTTP の答えを集める筋書き(127.0.0.1:9 は待ち受けが無い)。"
  (<- refused (SqlTransaction "cold" (count-items "cold")))
  (<- unreachable (SqlQuery "cold" "SELECT 1"))
  #(refused unreachable))


(deftest test-clickhouse-refuses-transactions-and-reports-unreachable
  (<- answers (with-handler [(clickhouse-http-sql-handler #(CLICKHOUSE))] (clickhouse-without-engine)))
  (val refused (get answers 0))
  (val unreachable (get answers 1))
  (assert (and (isinstance refused SqlFailed) (= refused.sqlstate "0A000")) refused)
  (assert (isinstance unreachable SqlUnreachable) unreachable))


;; --- 実 DB との結合(環境変数が無ければ skip) --------------------------------------------------------------------------------

(defk fresh [database]
  {:pre [(: database str)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "実 DB の検の表を消して作り直すため(前の走行の行を残さない)。"
  (<- dropped SqlRows (SqlQuery database "DROP TABLE IF EXISTS items" #()))
  dropped)


(defk postgres-journey []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "sqlite と同じ筋書きを本物の PostgreSQL で走らせる。"
  (<- (fresh DB))
  (<- trip (round-trip DB))
  (<- (fresh DB))
  (<- promises (transactions DB))
  #(trip promises))


(deftest test-postgres-answers-like-the-sqlite-handler
  {:skip-if (or (is POSTGRES-DSN None) (is (importlib.util.find-spec "psycopg") None))
   :skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN が無い(か psycopg が無い)"}
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (try
    (<- real (with-handler [(postgres-sql-handler connections)] (postgres-journey)))
    (finally (.close connections)))
  (<- memory (with-handler [(state) (sqlite-sql-handler #(DB))] (postgres-journey)))
  (val real-trip (get real 0))
  (val real-promises (get real 1))
  (val memory-trip (get memory 0))
  (val memory-promises (get memory 1))
  (assert (= (cut real-trip 1 None) (cut memory-trip 1 None)) #(real-trip memory-trip))
  ;; SQLSTATE は本物が細かい(23505)ので類の 2 文字で比べる。
  (val class-of (fn [answer] (if (isinstance answer SqlFailed) (cut (or answer.sqlstate "") 0 2) answer)))
  (assert (= (tuple (map class-of real-promises)) (tuple (map class-of memory-promises))) #(real-promises memory-promises)))


(defk clickhouse-journey []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "本物の ClickHouse に表を宣言し、投入し、引数つきで読む筋書き。"
  (<- (SqlQuery "cold" "DROP TABLE IF EXISTS events" #()))
  (<- (SqlEnsureTables "cold" #((SqlTable :name "events" :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER)
                                                                    (SqlColumn :name "body" :type SqlColumnType.TEXT :nullable True)
                                                                    (SqlColumn :name "ok" :type SqlColumnType.BOOLEAN))
                                          :primary-key #("id")))))
  (<- inserted (SqlInsertRows "cold" "events" #("id" "body" "ok") #(#(1 "a\tb" True) #(2 None False))))
  (<- read (SqlQuery "cold" "SELECT id, body, ok FROM events WHERE id >= :low AND body IS NULL OR body = :body ORDER BY id"
                     #((SqlParam :name "low" :value 2) (SqlParam :name "body" :value "a\tb"))))
  (<- broken (SqlQuery "cold" "SELECT * FROM no_such_table" #()))
  #(inserted read broken))


(deftest test-clickhouse-answers-over-http
  {:skip-if (is CLICKHOUSE-URL None)
   :skip-reason "DOEFF_SQL_TEST_CLICKHOUSE_URL が無い"}
  (val declared (ClickHouseDatabase :name "cold" :url (or CLICKHOUSE-URL "") :database (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_DATABASE" "default")
                                    :user (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_USER" "default")
                                    :password (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_PASSWORD" "") :timeout 10.0))
  (<- answers (with-handler [(clickhouse-http-sql-handler #(declared))] (clickhouse-journey)))
  (val inserted (get answers 0))
  (val read (get answers 1))
  (val broken (get answers 2))
  (assert (= inserted (SqlRows :rows #() :rowcount 2)) inserted)
  (assert (= read (SqlRows :rows #(#(1 "a\tb" True) #(2 None False)) :rowcount None)) read)
  (assert (and (isinstance broken SqlFailed) (in "no_such_table" broken.reason)) broken))


;; --- scheduler を塞がない PostgreSQL の答え手(pooled-postgres-sql-handler — agora-redesign #880 U2)。実 PG が要る(環境変数が無ければ skip)---

(defk pause [seconds]
  {:pre [(: seconds float)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "壁の時計で seconds 待つため(外から完了させる promise — 待つのはこの task だけ)。"
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer seconds (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  None)


(defk finished-at [statement]
  {:pre [(: statement str)] :post [(: % float)]
   :tags {:context "sql" :role "program"}}
  "文 1 つを流し、終わった拍の単調時計を答えるため。"
  (<- answer SqlRows (SqlQuery DB statement #()))
  (time.monotonic))


(defk ticks [count seconds]
  {:pre [(: count int) (: seconds float)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "seconds ごとに count 回刻み、刻んだ拍の単調時計を答えるため(scheduler が塞がれていれば遅れる)。"
  (var seen #())
  (for [_ (range count)]
    (<- (pause seconds))
    (:= seen (+ seen #((time.monotonic)))))
  seen)


(defk slow-beside-ticks []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "遅い問い合わせ 1 つと、許可(接続 1 本)を待つ問い合わせ 1 つの横で、別の task が刻めるかを見るため。"
  (<- slow (Spawn (finished-at "SELECT pg_sleep(1.0)")))
  (<- queued (Spawn (finished-at "SELECT 1")))
  (<- seen (ticks 5 0.05))
  (<- finished (Gather slow queued))
  #(seen (tuple finished)))


(defk advisory-lock-held []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で、この接続が持つ advisory lock の番号(bigint の鍵)を pg_locks から読むため。"
  (<- held SqlRows (SqlQuery DB (+ "SELECT ((classid::bigint << 32) | objid::bigint) FROM pg_locks "
                                   "WHERE locktype = 'advisory' AND pid = pg_backend_pid()") #()))
  (assert (= (len held.rows) 1) held.rows)
  (get held.rows 0 0))


(defk lock-numbers [key]
  {:pre [(: key str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "lock-key の transaction が取る錠の番号と、新しい書き方の SELECT hashtext(:k) の値を並べるため。"
  (<- held (SqlTransaction :database DB :program (advisory-lock-held) :lock-key key))
  (<- hashed SqlRows (SqlQuery DB "SELECT hashtext(:k)" #((SqlParam :name "k" :value key))))
  #(held (get hashed.rows 0 0)))


(defk insert-then-sleep []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 1 行入れてから長く眠るため(眠りの間に取り消される)。"
  (<- (SqlQuery DB "INSERT INTO pooled_cancel (id) VALUES (1)" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(1.5)" #()))
  None)


(defk cancel-mid-transaction []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "transaction の途中で task を取り消し、取り消しが届くか・入れた行が残らないか・許可が返って次の問い合わせが通るかを見るため。"
  (<- (SqlQuery DB "DROP TABLE IF EXISTS pooled_cancel" #()))
  (<- (SqlQuery DB "CREATE TABLE pooled_cancel (id bigint)" #()))
  (<- task (Spawn (SqlTransaction :database DB :program (insert-then-sleep) :lock-key "pooled-cancel")))
  (<- (pause 0.5))
  (<- (Cancel task))
  (var cancelled False)
  (try
    (<- (Wait task))
    (except [TaskCancelledError] (:= cancelled True)))
  (<- counted SqlRows (SqlQuery DB "SELECT count(*) FROM pooled_cancel" #()))
  #(cancelled (get counted.rows 0 0)))


(val POOLED-SKIP (or (is POSTGRES-DSN None) (is (importlib.util.find-spec "psycopg") None)))


(deftest test-pooled-postgres-answers-like-the-sqlite-handler
  {:skip-if POOLED-SKIP :skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN が無い(か psycopg が無い)"}
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (<- real (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (postgres-journey)))
    (finally (.close connections) (.shutdown pool)))
  (<- memory (with-handler [(state) (sqlite-sql-handler #(DB))] (postgres-journey)))
  (assert (= (cut (get real 0) 1 None) (cut (get memory 0) 1 None)) #(real memory))
  (val class-of (fn [answer] (if (isinstance answer SqlFailed) (cut (or answer.sqlstate "") 0 2) answer)))
  (assert (= (tuple (map class-of (get real 1))) (tuple (map class-of (get memory 1)))) #(real memory)))


(deftest test-pooled-postgres-does-not-block-the-scheduler
  {:skip-if POOLED-SKIP :skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN が無い(か psycopg が無い)"}
  ;; 接続 1 本: 遅い問い合わせが許可を持ち、2 つ目は許可を待つ。その間も別の task の刻みが遅れない。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (slow-beside-ticks)))
    (finally (.close connections) (.shutdown pool)))
  (val seen (get answer 0))
  (val slow-done (get answer 1 0))
  (val queued-done (get answer 1 1))
  (assert (= (len seen) 5))
  ;; 刻みは全部、遅い問い合わせ(1 秒)が終わる前に済む。
  (assert (< (max seen) slow-done) #(seen slow-done))
  ;; 2 つ目は許可(接続 1 本)が返るまで待った。
  (assert (>= queued-done slow-done) #(queued-done slow-done)))


(deftest test-pooled-postgres-takes-the-same-advisory-lock-as-before
  {:skip-if POOLED-SKIP :skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN が無い(か psycopg が無い)"}
  (import psycopg)
  (val keys #("records-writer" "agora-records-writer" "records-migrate" "会話-01J0000000000000000000000"))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (var numbers #())
  (try
    (for [key keys]
      (<- pair (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (lock-numbers key)))
      (:= numbers (+ numbers #(pair))))
    (finally (.close connections) (.shutdown pool)))
  ;; 旧い書き方(doeff-records の pg_sql.hy の lock-statement — 位置の %s に鍵を結ぶ)で同じ鍵の hashtext を読む。
  (val old (with [connection (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
             (tuple (gfor key keys (get (.fetchone (.execute connection "SELECT hashtext(%s)" #(key))) 0)))))
  (for [[key pair before] (zip keys numbers old)]
    ;; transaction が取った錠の番号 = 新しい書き方の hashtext(:k) = 旧い書き方の hashtext(%s)。
    (assert (= (get pair 0) (get pair 1) before) #(key pair before))))


(deftest test-pooled-postgres-rolls-back-and-returns-the-connection-on-cancel
  {:skip-if POOLED-SKIP :skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN が無い(か psycopg が無い)"}
  (import psycopg)
  ;; 接続 1 本: 取り消しの後の問い合わせが通れば、許可と接続が返っている。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (cancel-mid-transaction)))
    (val idle (list (. (get connections.idle DB) queue)))
    (assert (= (len idle) 1) idle)
    (assert (= (. (get idle 0) info transaction-status) psycopg.pq.TransactionStatus.IDLE))
    (finally (.close connections) (.shutdown pool)))
  ;; 取り消しが届き、transaction の中で入れた行は rollback で残らない。
  (assert (= answer #(True 0)) answer))
