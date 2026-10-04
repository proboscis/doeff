;;; 汎用の SQL の effect(sql_effects.hy — agora-redesign #802 便 3)の検。
;;;   - I/O なしの答え手(sqlite-sql-handler)に、effect の約束を全部撃つ: 引数の書き換えと値の正規化・失敗の 2 型(SqlFailed の SQLSTATE の類・
;;;     SqlUnreachable)・transaction の約束 3 つ(最初の失敗で rollback して再開しない・禁じた effect と入れ子を断る・例外で rollback)・
;;;     DDL の宣言・宣言していない database を外側へ回す。
;;;   - 本物の答え手(postgres-sql-handler・clickhouse-http-sql-handler)は、方言の書き換えと答えの写しを純関数で撃つ。実 DB の結合の検は
;;;     DSN / URL の環境変数(DOEFF_SQL_TEST_POSTGRES_DSN・DOEFF_SQL_TEST_CLICKHOUSE_URL)が無ければ skip する。
;;;   - scheduler を塞がない答え手(pooled-postgres-sql-handler — #880 U2)は実 PG で: 同じ筋書きの答え・遅い問い合わせの横で別の task が進む・
;;;     transaction の錠の番号が旧い書き方の hashtext と同じ・取り消しで rollback して接続と許可を返す。
;;;   - postgres-sql-handler も scheduler を塞がない(#1215)ことを実 PG で: 遅い問い合わせの横で別の task が進む・同じ lock-key の transaction
;;;     だけが直列で、違う鍵は並ぶ。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import os)
(import json)
(import importlib.util)
(import threading)
(import time)
(import socket)
(import collections.abc [Callable])
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import decimal [Decimal])
(import doeff [Program])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.effects [Get])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlParam SqlRows SqlFailed
                                        SqlUnreachable SqlSchemaApplied SqlTransactionMisuse SqlTable SqlColumn SqlColumnType SqlIndex
                                        normalized-value normalized-rows split-statement])
(import doeff_core_effects.sqlite_sql [sqlite-sql-handler sqlite-statement sqlite-failure])
(import doeff_core_effects.postgres_sql [postgres-sql-handler postgres-statement postgres-insert-statement postgres-failure
                                         postgres-schema-statements PostgresDatabase PostgresConnections PostgresTimeouts])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn Gather Cancel TaskCancelledError])
(import doeff_core_effects.clickhouse_http_sql [clickhouse-http-sql-handler clickhouse-statement clickhouse-query-request
                                                clickhouse-insert-request clickhouse-rows clickhouse-written-rows clickhouse-failure
                                                clickhouse-schema-statements ClickHouseDatabase ClickHouseResponse ClickHouseParam])
(import disposable_postgres [session-dsn session-postgres-skip-reason])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val CLICKHOUSE-URL (os.environ.get "DOEFF_SQL_TEST_CLICKHOUSE_URL"))
;; 実 PG の検の skip(DSN か psycopg が無い)。DSN は env が無ければ conftest が立てた使い捨ての PostgreSQL の物
;; (postgres_support/disposable_postgres.py — agora-redesign #2830)。立てられなかった時は、その理由を名指す。
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POOLED-SKIP-REASON (cond POSTGRES-SKIP-REASON POSTGRES-SKIP-REASON
                              (is (importlib.util.find-spec "psycopg") None) "psycopg が無い(uv run --with psycopg で足す)"
                              True ""))
(val POOLED-SKIP (bool POOLED-SKIP-REASON))

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


(deftest test-a-remembered-split-still-checks-each-call-and-keeps-values-in-order
  ;; 同じ文の 2 度目は覚えた割り(同じ tuple)を使う — それでも呼びごとの params は検め、値は `?` の順に並ぶ(agora-redesign #2423)。
  (val statement "SELECT :b, :a, :b, ':a' -- :c")
  (<- first (split-statement statement))
  (<- again (split-statement statement))
  (assert (is first again) "同じ文の 2 度目が覚えた割りを返していない")
  (<- bound (sqlite-statement statement #((SqlParam :name "a" :value 1) (SqlParam :name "b" :value "x"))))
  (assert (= #(bound.text bound.values) #("SELECT ?, ?, ?, ':a' -- :c" #("x" 1 "x"))) bound)
  (try
    (<- (sqlite-statement statement #((SqlParam :name "a" :value 1))))
    (assert False "覚えた文で params の欠けが通った")
    (except [ValueError])))


(deftest test-plain-rows-pass-through-and-other-values-are-still-normalized
  ;; 値の型が閉じた集合そのものの行は写すだけ・それ以外の型が混じる行は今までどおり normalized-value を通る(agora-redesign #2423)。
  (<- plain (normalized-rows [#(1 "a" 1.5 b"x" True None)]))
  (assert (= plain #(#(1 "a" 1.5 b"x" True None))) plain)
  (<- mixed (normalized-rows [#(1 (Decimal "2.5") (bytearray b"y"))]))
  (assert (= mixed #(#(1 2.5 b"y"))) mixed)
  (try
    (<- (normalized-rows [#(1 (object))]))
    (assert False "写す決まりの無い型が通った")
    (except [TypeError])))


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


(defk out-of-range [database]
  {:pre [(: database str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "INTEGER の範囲(64 bit)の外の整数を引数の bind と行の挿入の両方で渡す筋書き(agora-redesign #1234)。"
  (<- (seeded database))
  (<- bound (SqlQuery database "INSERT INTO items (id, label) VALUES (:id, 'x')" #((SqlParam :name "id" :value (** 2 63)))))
  (<- inserted (SqlInsertRows database "items" #("id" "label") #(#((** 2 63) "y"))))
  (<- after (SqlQuery database "SELECT count(*) FROM items" #()))
  #(bound inserted after))


(deftest test-sqlite-refuses-an-out-of-range-integer-like-postgres
  ;; 本物の PostgreSQL は bigint の範囲外を 22003(numeric_value_out_of_range)で断る。sqlite3 の bind は OverflowError(sqlite3.Error の仲間
  ;; ではない)を出すので、答え手が値にしないと呼び手の Program へ例外が抜け、模擬と本物の答えが食い違う(agora-redesign #1234)。
  (<- answers (with-handler [(state) (sqlite-sql-handler #(DB))] (out-of-range DB)))
  (val bound (get answers 0))
  (val inserted (get answers 1))
  (val after (get answers 2))
  (assert (and (isinstance bound SqlFailed) (= bound.sqlstate "22003")) bound)
  (assert (and (isinstance inserted SqlFailed) (= inserted.sqlstate "22003")) inserted)
  ;; 断った後も接続は使える(行は 1 つも入っていない)。
  (assert (= after (SqlRows :rows #(#(0)) :rowcount 1)) after))


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
  {:pre [(: database str) (: program Program)] :post [(: % str)]
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


(deftest test-postgres-connection-sqlstates-map-to-unreachable
  ;; 接続の例外(class 08 の全部)と 57P01・57P02・57P03(管理者による切断・crash による切断・起動中 / 停止中)は SQLSTATE を持っていても
  ;; 届かない(agora-redesign #880 の裁定)。
  (for [sqlstate ["08000" "08001" "08003" "08006" "08P01" "57P01" "57P02" "57P03"]]
    (<- answer (postgres-failure sqlstate #("OperationalError" "DatabaseError" "Error") "gone"))
    (assert (= answer (SqlUnreachable :reason "gone")) #(sqlstate answer)))
  ;; 反例: class 57 の他(57014 = 文の取り消し・57000)と、接続でない engine の答え(23505・40001・53300)は SqlFailed のまま。
  (for [sqlstate ["57014" "57000" "23505" "40001" "53300"]]
    (<- engine-answer (postgres-failure sqlstate #("OperationalError" "DatabaseError" "Error") "engine"))
    (assert (= engine-answer (SqlFailed :sqlstate sqlstate :reason "engine")) #(sqlstate engine-answer))))


;; --- 接続の上限(agora-redesign #1479)------------------------------------------------------------------------------------------
;; DB の pod が入れ替わった後、上限の無い接続は相手の居ない TCP のまま文を待ち続け、記録の service が 44 分固まった。

(deftest test-postgres-connections-carry-the-timeouts
  ;; 既定の貸し出しは、開く接続に接続・keepalive・送った bytes の返事・文の上限を付ける。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn ""))))
  (assert (= (.connection-options connections DB)
             {"connect_timeout" 5 "keepalives" 1 "keepalives_idle" 10 "keepalives_interval" 5 "keepalives_count" 3
              "tcp_user_timeout" 15000 "options" "-c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000"})
          (.connection-options connections DB))
  ;; 文の上限を付けない宣言(None)では options を渡さない(DSN の options を消さない)。
  (val unbounded (PostgresConnections #((PostgresDatabase :name DB :dsn ""))
                                      :timeouts (PostgresTimeouts :connect-seconds 2 :keepalive-idle-seconds 1 :keepalive-interval-seconds 1
                                                                  :keepalive-count 1 :unacknowledged-milliseconds 900
                                                                  :statement-milliseconds None
                                                                  :idle-transaction-milliseconds None)))
  (assert (not-in "options" (.connection-options unbounded DB)))
  (assert (= (get (.connection-options unbounded DB) "tcp_user_timeout") 900)))


(val PSYCOPG-SKIP (is (importlib.util.find-spec "psycopg") None))


(deftest test-postgres-connect-to-a-silent-server-answers-unreachable
  {:skip-if PSYCOPG-SKIP :skip-reason "psycopg が無い"}
  ;; 接続を受けるだけで何も答えない口(DB の pod の入れ替えの最中と同じ形 — TCP の握手は kernel が済ませ、PostgreSQL の起動の答えが来ない)へ
  ;; 開くと、上限(libpq の下限 2 秒)で SqlUnreachable が返る。反例 = 上限の無い接続は、口が閉じるまで答えを待ち続ける。
  (val silent (socket.create-server #("127.0.0.1" 0)))
  (val port (get (.getsockname silent) 1))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (.format "host=127.0.0.1 port={} dbname=x user=x" port)))
                                        :timeouts (PostgresTimeouts :connect-seconds 2 :keepalive-idle-seconds 1
                                                                    :keepalive-interval-seconds 1 :keepalive-count 1
                                                                    :unacknowledged-milliseconds 1000 :statement-milliseconds 1000
                                                                    :idle-transaction-milliseconds None)))
  (val started (time.monotonic))
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (finally (.close connections) (.close silent)))
  (val elapsed (- (time.monotonic) started))
  (assert (isinstance answer SqlUnreachable) answer)
  (assert (< elapsed 6) elapsed))


(defclass CuttingProxy []
  "実 PG の前に置く TCP の中継(検の殻)— cut で走っている接続を両側とも閉じる(DB の pod の入れ替えで口が途中で閉じた形)。"

  (defn #^ None __init__ [self #^ str host #^ int port]  ; defk にできない: 検の殻の資源(socket と thread)の初期化
    "中継の待ち受けを開き、受けた接続ごとに行き先へ繋いで流す thread を立てるため。"
    (setv self.target #(host port)
          self.listener (socket.create-server #("127.0.0.1" 0))
          self.port (get (.getsockname self.listener) 1)
          self.live []
          self.lock (threading.Lock))
    (.start (threading.Thread :target self.accept :daemon True)))

  (defn #^ None accept [self]  ; defk にできない: 検の殻の thread の target
    "受けた接続を行き先へ繋ぎ、両向きに流すため(待ち受けが閉じたら終わる)。"
    (while True
      (try
        (setv [client _] (.accept self.listener))
        (except [OSError] (return None)))
      (setv upstream (.connect-upstream self))
      (with [self.lock] (.extend self.live [client upstream]))
      (for [[a b] [[client upstream] [upstream client]]]
        (.start (threading.Thread :target self.pipe :args #(a b) :daemon True)))))

  (defn #^ socket.socket connect-upstream [self]  ; defk にできない: 検の殻の socket を開く
    "行き先へ繋ぐため。host が / で始まれば PostgreSQL の unix socket の dir へ繋ぐ(使い捨ての PostgreSQL は TCP で待ち受けない
     — agora-redesign #2830)。"
    (setv #(host port) self.target)
    (if (.startswith host "/")
      (do
        (setv upstream (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
        (.connect upstream (os.path.join host (.format ".s.PGSQL.{}" port)))
        upstream)
      (socket.create-connection self.target)))

  (defn #^ None pipe [self #^ socket.socket a #^ socket.socket b]  ; defk にできない: 検の殻の thread の target
    "a から読んだ bytes を b へ流すため(どちらかが閉じたら終わる)。"
    (try
      (while True
        (setv data (.recv a 65536))
        (when (not data) (break))
        (.sendall b data))
      (except [OSError] None)))

  (defn #^ None cut [self]  ; defk にできない: 検の殻の操作
    "走っている接続を全部、両側とも閉じるため(待ち受けは開いたまま — 次の接続は通す)。"
    (with [self.lock]
      (for [s self.live]
        (try (.shutdown s socket.SHUT-RDWR) (except [OSError] None))
        (.close s))
      (.clear self.live)))

  (defn #^ None close [self]  ; defk にできない: 検の殻の後始末
    "中継を止めるため。"
    (.cut self)
    (.close self.listener)))


(deftest test-postgres-recovers-after-the-connection-is-cut-mid-way
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 実 PG の反例(#1479 の受入 — 本番の DB の pod は入れ替えない): 中継の口で接続を途中で閉じると、その接続の次の文は
  ;; SqlUnreachable、その次の文は新しい接続で 25 秒以内に答える(切れた接続は返す時に捨てられ、次の借りが張り直す)。
  (import psycopg.conninfo [conninfo-to-dict make-conninfo])
  (val target (conninfo-to-dict (or POSTGRES-DSN "")))
  (val proxy (CuttingProxy (.get target "host" "127.0.0.1") (int (.get target "port" 5432))))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1"
                                                                                         :port (str proxy.port))))
                                        :size 1))
  (try
    (<- before (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (.cut proxy)
    (val cut-at (time.monotonic))
    (<- broken (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (<- after (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (val recovered-in (- (time.monotonic) cut-at))
    (finally (.close connections) (.close proxy)))
  (assert (isinstance before SqlRows) before)
  (assert (isinstance broken SqlUnreachable) broken)
  (assert (isinstance after SqlRows) after)
  (assert (< recovered-in 25) recovered-in))


(defk select-one []
  {:pre [] :post [(: % "SqlQuery の答え")] :tags {:context "sql" :role "program"}}
  "transaction の中身として 1 行を読むため(錠が取れたかだけを見る)。"
  (<- answer (SqlQuery DB "SELECT 1" #()))
  answer)


(defk locked-write-after-a-stalled-transaction [idle-milliseconds]
  {:pre [(: idle-milliseconds (| int None))] :post [(: % "SqlTransaction の答え")]
   :tags {:context "sql" :role "program"}}
  "transaction の途中で止まった接続(BEGIN と錠の後に何もしない — 返す finally に届かなかった形)の横で、同じ錠の transaction を撃つため
   (agora-redesign #1846)。idle-milliseconds = transaction の途中で何もしない上限(None = 付けない)。文の上限は 3 秒。"
  (val timeouts (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10 :keepalive-interval-seconds 5 :keepalive-count 3
                                  :unacknowledged-milliseconds 15000 :statement-milliseconds 3000
                                  :idle-transaction-milliseconds idle-milliseconds))
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2 :timeouts timeouts))
  (val stalled (.acquire connections DB))
  (.execute stalled "BEGIN")
  (.execute stalled "SELECT pg_advisory_xact_lock(hashtext('stalled-1846'))")
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)]
                 (SqlTransaction DB (select-one) "stalled-1846")))
    answer
    (finally (.release connections DB stalled) (.close connections))))


(deftest test-postgres-cuts-a-stalled-transaction-so-its-lock-is-freed
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 実 PG(agora-redesign #1846 の反例): 錠を持ったまま止まった transaction は、上限(0.5 秒)で engine が切って錠を外し、同じ錠の
  ;; transaction が通る。上限の無い接続では錠が外れず、同じ錠の transaction は文の上限(3 秒)で 57014 に落ちる(2026-09-30 の着地の台帳の
  ;; 事故の形)。
  (<- freed (locked-write-after-a-stalled-transaction 500))
  (assert (= (get freed.rows 0 0) 1) freed)
  (<- blocked (locked-write-after-a-stalled-transaction None))
  (assert (isinstance blocked SqlFailed) blocked)
  (assert (= blocked.sqlstate "57014") blocked))


(deftest test-postgres-cuts-a-statement-at-the-statement-timeout
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 実 PG: 文の上限(0.5 秒)を超えた文は engine が取り消し(57014)、SqlFailed で返る。接続は返されて次の文が答える。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN "")))
                                        :timeouts (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10
                                                                    :keepalive-interval-seconds 5 :keepalive-count 3
                                                                    :unacknowledged-milliseconds 15000 :statement-milliseconds 500
                                                                    :idle-transaction-milliseconds None)))
  (try
    (<- slow (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT pg_sleep(3)" #())))
    (<- next (with-handler [(postgres-sql-handler connections)] (SqlQuery DB "SELECT 1" #())))
    (finally (.close connections)))
  (assert (= slow.sqlstate "57014") slow)
  (assert (= (get next.rows 0 0) 1) next))


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
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
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
  "許可(接続 1 本)を取り合う遅い問い合わせ 2 つの横で、別の task が刻めるかを見るため。2 つとも 1 秒眠るので、どちらが先に許可を
   取っても、後の方は先の方が許可を返すまで待ち、終わりの刻が 1 秒離れる(2 本の接続で並んで走れば、ほぼ同じ刻に終わる)。許可を取る
   順は driver の thread の起き方で決まり、決まらない — 先に spawn した方が先に取る、を断言の前提にしない(agora-redesign #3532)。"
  (<- slow (Spawn (finished-at "SELECT pg_sleep(1.0)")))
  (<- queued (Spawn (finished-at "SELECT pg_sleep(1.0)")))
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



(deftest test-pooled-postgres-answers-like-the-sqlite-handler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
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
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 接続 1 本: 遅い問い合わせが許可を持ち、2 つ目は許可を待つ。その間も別の task の刻みが遅れない。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (<- answer (with-handler [(state) (pooled-postgres-sql-handler connections pool)] (slow-beside-ticks)))
    (finally (.close connections) (.shutdown pool)))
  (val seen (get answer 0))
  (val first-done (min (get answer 1)))
  (val second-done (max (get answer 1)))
  (assert (= (len seen) 5))
  ;; 刻みは全部、先に許可を取った問い合わせ(1 秒)が終わる前に済む。
  (assert (< (max seen) first-done) #(seen first-done))
  ;; 後の方は許可(接続 1 本)が返るまで待った(並んで走れば、ほぼ同じ刻に終わる)。
  (assert (>= (- second-done first-done) 0.9) #(first-done second-done)))


(deftest test-pooled-postgres-takes-the-same-advisory-lock-as-before
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
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
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
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


(defk abandon-mid-transaction []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "transaction の途中の task を Cancel も Wait もせずに置いて、親の program を終えるため(run の終わりの後始末を撃つ — #2684)。"
  (<- (SqlQuery DB "DROP TABLE IF EXISTS pooled_abandon" #()))
  (<- (SqlQuery DB "CREATE TABLE pooled_abandon (id bigint)" #()))
  (<- (Spawn (SqlTransaction :database DB :program (insert-into-abandon-then-sleep) :lock-key "pooled-abandon")))
  (<- (pause 0.5))
  "left")


(defk insert-into-abandon-then-sleep []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で 1 行入れてから長く眠るため(眠りの間に親の run が終わる)。"
  (<- (SqlQuery DB "INSERT INTO pooled_abandon (id) VALUES (1)" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(1.5)" #()))
  None)


(defn #^ (get tuple #(str PostgresConnections)) abandoned-in-its-own-run [#^ (get Callable #([PostgresConnections] list)) answerer-of]  ; defk にできない: 親の run を別の thread で走らせ切る検の入口(この検の run の中では親が終わらない)
  "transaction の途中の task を残して終わる run を 1 つ走らせ切り、その後の接続の貸し出しを返すため。answerer-of = 接続の貸し出し → 答え手の組。"
  (import warnings)
  (import doeff [run with_handlers])
  (import doeff_core_effects.scheduler [scheduled])
  (setv connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (defn #^ str ask []  ; defk にできない: 別の thread で走らせ切る run の本体
    (with [_ (warnings.catch-warnings)]
      (warnings.simplefilter "ignore")
      (run (scheduled (with_handlers (answerer-of connections) (abandon-mid-transaction))))))
  (with [runner (ThreadPoolExecutor :max-workers 1)]
    (setv answer (.result (.submit runner ask) :timeout 20)))
  #(answer connections))


(deftest test-postgres-returns-the-connection-of-a-transaction-the-run-left-behind
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 根(agora-redesign #1859 / #2684): run の親が終わる時に途中の transaction の task を捨てると、接続を返す finally が走らず、
  ;; 接続は借りたまま・transaction の途中のまま残った(貸し出しの空き 0・許可の空き 0)。今は run の終わりに Cancel が届き、
  ;; finally が rollback して接続を返す。
  (import psycopg)
  (val pool (ThreadPoolExecutor :max-workers 2))
  (for [answerer-of [(fn [connections] [(state) (postgres-sql-handler connections)])
                     (fn [connections] [(state) (pooled-postgres-sql-handler connections pool)])]]
    (val outcome (abandoned-in-its-own-run answerer-of))
    (val answer (get outcome 0))
    (val connections (get outcome 1))
    (try
      (assert (= answer "left") answer)
      (val idle (list (. (get connections.idle DB) queue)))
      (assert (= (len idle) 1) f"接続が返っていない(貸し出しの空き {(len idle)})")
      (assert (= (. (get idle 0) info transaction-status) psycopg.pq.TransactionStatus.IDLE))
      (with [admin (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
        (assert (= (get (.fetchone (.execute admin "SELECT count(*) FROM pooled_abandon")) 0) 0) "transaction の中で入れた行が残った"))
      (finally (.close connections))))
  (.shutdown pool))


(defn #^ (| SqlRows SqlFailed SqlUnreachable) answer-while-terminated [#^ (get Callable #([PostgresConnections] list)) answerer-of]  ; defk にできない: 答え手を別の thread の run で回し、この thread から接続を切る検の入口
  "長い問い合わせの途中で管理者がその接続を切った(pg_terminate_backend — SQLSTATE 57P01)時の答えを読むため。
   answerer-of = 接続の貸し出し → 答え手の組(list)。"
  (import psycopg)
  (import uuid)
  (import doeff [run with_handlers])
  (import doeff_core_effects.scheduler [scheduled])
  (setv marker (.format "doeff_terminate_{}" (. (uuid.uuid4) hex))
        connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1)
        answers [])
  (defn #^ None ask []  ; defk にできない: thread の target
    (.append answers (run (scheduled (with_handlers (answerer-of connections)
                                                    (SqlQuery DB (.format "SELECT pg_sleep(30) /* {} */" marker) #()))))))
  (setv worker (threading.Thread :target ask))
  (.start worker)
  (with [admin (psycopg.connect (or POSTGRES-DSN "") :autocommit True)]
    (setv terminated False deadline (+ (time.monotonic) 10))
    (while (and (not terminated) (< (time.monotonic) deadline))
      (setv terminated (get (.fetchone (.execute admin "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
                                                        WHERE query LIKE %s AND pid <> pg_backend_pid()" #((.format "%{}%" marker)))) 0))
      (time.sleep 0.05)))
  (.join worker 20)
  (.close connections)
  (get answers 0))


(deftest test-postgres-reads-an-administrator-disconnect-as-unreachable
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 実 PG の反例: 問い合わせの途中で接続を管理者に切られた(57P01)答えは、同期の答え手でも塞がない答え手でも SqlUnreachable
  ;; (#880 の裁定の前は SqlFailed(57P01))。
  (val sync-answer (answer-while-terminated (fn [connections] [(postgres-sql-handler connections)])))
  (assert (isinstance sync-answer SqlUnreachable) sync-answer)
  (val pool (ThreadPoolExecutor :max-workers 1))
  (try
    (val pooled-answer (answer-while-terminated (fn [connections] [(state) (pooled-postgres-sql-handler connections pool)])))
    (finally (.shutdown pool)))
  (assert (isinstance pooled-answer SqlUnreachable) pooled-answer))


;; --- postgres-sql-handler も scheduler を塞がない(agora-redesign #1215)。実 PG が要る(環境変数が無ければ skip)-------------------------
;; 前は driver の呼びを scheduler の thread で同期に撃ったので、遅い文の間は同じ run の他の task(成果物の置き場の /healthz)も待った。

(defk timed-sleep [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "transaction の中で、錠を取った後の始まりと終わりの時刻(PG の clock_timestamp の epoch 秒)を眠りを挟んで読むため。"
  (<- began SqlRows (SqlQuery DB "SELECT extract(epoch FROM clock_timestamp())::float8" #()))
  (<- (SqlQuery DB "SELECT pg_sleep(:s)" #((SqlParam :name "s" :value seconds))))
  (<- ended SqlRows (SqlQuery DB "SELECT extract(epoch FROM clock_timestamp())::float8" #()))
  #((get began.rows 0 0) (get ended.rows 0 0)))


(defk locked-pair [first-key second-key]
  {:pre [(: first-key str) (: second-key str)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "鍵つきの transaction 2 つを並べて走らせ、その横で別の task が刻めるかを見るため(各 transaction は錠の中で 0.4 秒眠る)。"
  (val started (time.monotonic))
  (<- first (Spawn (SqlTransaction :database DB :program (timed-sleep 0.4) :lock-key first-key)))
  (<- second (Spawn (SqlTransaction :database DB :program (timed-sleep 0.4) :lock-key second-key)))
  (<- seen (ticks 4 0.05))
  (<- spans (Gather first second))
  #((tuple (gfor t seen (- t started))) (tuple spans)))


(deftest test-postgres-does-not-block-the-scheduler
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 接続 1 本: 遅い問い合わせ(pg_sleep 1 秒)が接続を持ち、2 つ目は接続を待つ。その間も別の task の刻みが遅れない。答え手は
  ;; postgres-sql-handler 1 つだけ(state も pool も被せない — 組み立ての側は変わらない)。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
  (try
    (<- answer (with-handler [(postgres-sql-handler connections)] (slow-beside-ticks)))
    (finally (.close connections)))
  (val seen (get answer 0))
  (val first-done (min (get answer 1)))
  (val second-done (max (get answer 1)))
  (assert (= (len seen) 5))
  ;; 刻みは全部、先に接続を取った問い合わせ(1 秒)が終わる前に済む(塞ぐ答え手なら刻みは遅い問い合わせの後になる)。
  (assert (< (max seen) first-done) #(seen first-done))
  ;; 後の方は接続(1 本)が返るまで待った(並んで走れば、ほぼ同じ刻に終わる)。
  (assert (>= (- second-done first-done) 0.9) #(first-done second-done)))


(deftest test-postgres-serializes-only-transactions-with-the-same-lock-key
  {:skip-if POOLED-SKIP :skip-reason POOLED-SKIP-REASON}
  ;; 接続 2 本: 同じ鍵の transaction 2 つは錠で直列(時刻の区間が重ならない)・違う鍵なら並ぶ(区間が重なる — 直列なのは錠のためで、
  ;; scheduler が塞がれたためではない)。どちらの間も別の task の刻みは遅れない。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 2))
  (try
    (<- same (with-handler [(postgres-sql-handler connections)] (locked-pair "doeff-1215-lock" "doeff-1215-lock")))
    (<- apart (with-handler [(postgres-sql-handler connections)] (locked-pair "doeff-1215-a" "doeff-1215-b")))
    (finally (.close connections)))
  (val overlap (fn [spans] (- (min (get spans 0 1) (get spans 1 1)) (max (get spans 0 0) (get spans 1 0)))))
  (assert (<= (overlap (get same 1)) 0) same)
  (assert (> (overlap (get apart 1)) 0.2) apart)
  ;; 刻み(4 回 × 0.05 秒)は始まりから 0.6 秒より前に済む。同じ鍵の 2 つは直列で合わせて 0.8 秒以上かかるので、塞ぐ答え手なら刻みは
  ;; その後になる。
  (for [result #(same apart)]
    (assert (< (max (get result 0)) 0.6) result)))


(deftest test-postgres-connections-keep-the-dsn-options-and-add-the-statement-bound
  {:skip-if PSYCOPG-SKIP :skip-reason "psycopg が無い"}
  ;; DSN が options(-c search_path …)を名乗っていれば、文の上限の -c はそれに継ぎ足す(keyword で勝たせると DSN の -c が黙って消え、
  ;; 表が名指した schema でなく public に作られた — agora-redesign #1771)。名乗らない database は文の上限だけ。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn "postgresql://u@/d?host=%2Ftmp&options=-c%20search_path%3Ds1")
                                          (PostgresDatabase :name "plain" :dsn "postgresql://u@/d"))))
  (assert (= (get (.connection-options connections DB) "options") "-c search_path=s1 -c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000")
          (.connection-options connections DB))
  (assert (= (get (.connection-options connections "plain") "options") "-c statement_timeout=60000 -c idle_in_transaction_session_timeout=30000")
          (.connection-options connections "plain")))
