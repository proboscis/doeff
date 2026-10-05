;;; 既に在る SqlTransaction の意味は変えない(agora-redesign #3605): batched を選ばない transaction について、答え手が DB へ流す往復の列
;;; (往復ごとの文の並び)が基点(eaeeb75c4 — 往復をまとめる形を足す前)と同じである事を、基点の並びを書いた期待と比べる。
;;;   - PostgreSQL(postgres-sql-handler と pooled-postgres-sql-handler): client と使い捨ての PostgreSQL の間に置いた中継
;;;     (postgres_support/statement_wire_tap.py)が、wire の上で client が送った文を往復ごとに記録する。
;;;   - sqlite(sqlite-file-sql-handler — memory の DB の答え手と同じ sqlite-answer-transaction を通る): 接続の trace の callback が、流した文を
;;;     流した順に記録する(sqlite に往復は無いので文の並びだけ)。
;;; 代表の program: 文 2 本と SqlInsertRows(2 行)と SqlNotify(錠の鍵あり)・SQL を出さない program(錠の鍵あり / なし)・途中で落ちて
;;; ROLLBACK・預かり所の本体の commit-rows の形(controllers/custody_body/protocol/store.hy — 錠の鍵つきで version の SELECT × 2 → UPSERT × 2)・
;;; sqlite が断る SqlNotify(ROLLBACK)。
;;; この file は基点に在る名だけを使う — 基点の答え手の源に差し替えても同じ期待で緑になる事を、変更の時に確かめた。合図だけは
;;; agora-redesign #3688 で形が変わった: SqlNotify が関わる名(topics)を持ち、pg_notify の本文が空でなく引数($2)になった(往復の数と
;;; 順は変わらない)。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import queue)
(import sqlite3)
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [Program])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlNotify SqlEnsureTables SqlParam SqlRows SqlFailed
                                        SqlTransactionMisuse SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects.postgres_sql [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_core_effects.sqlite_file_sql [SqliteFile open-sqlite-files close-sqlite-files sqlite-file-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])
(import statement_wire_tap [StatementWireTap])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val LOCK "invariant-lock")

(val ITEMS (SqlTable :name "items"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT))
                     :primary-key #("id")))
(val ACCOUNTS (SqlTable :name "accounts"
                        :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "version" :type SqlColumnType.INTEGER))
                        :primary-key #("id")))

;; 基点の往復の列(PostgreSQL)— 文 1 つが往復 1 回。SqlInsertRows は executemany(行ごとの文を 1 往復で)。引数は wire の綴り($1 …)。
;; ROLLBACK の後の DEALLOCATE ALL は driver(psycopg)が流す — psycopg が自分で prepare した文を覚えている接続で ROLLBACK を流すと、
;; transaction の中で prepare した文が消えたかもしれないので、覚えを捨てて DEALLOCATE ALL を流す(答え手の手順ではない)。
(val INSERT-PARAMS "INSERT INTO items (id, label) VALUES ($1, $2)")
(val LOCK-WIRE "SELECT pg_advisory_xact_lock(hashtext($1))")
(val BASE-POSTGRES-ROUND-TRIPS
  {"文と SqlInsertRows と合図(錠あり)" #(#("BEGIN") #(LOCK-WIRE) #(INSERT-PARAMS) #(INSERT-PARAMS INSERT-PARAMS)
                                        #("SELECT count(*) FROM items") #("SELECT pg_notify($1, $2)") #("COMMIT"))
   "SQL を出さない(錠あり)" #(#("BEGIN") #(LOCK-WIRE) #("COMMIT"))
   "SQL を出さない(錠なし)" #(#("BEGIN") #("COMMIT"))
   "途中で落ちて ROLLBACK" #(#("BEGIN") #("INSERT INTO items (id, label) VALUES (110, 'x')")
                             #("INSERT INTO items (id, label) VALUES (110, 'y')") #("ROLLBACK") #("DEALLOCATE ALL"))
   "預かり所の commit-rows の形(錠あり)" #(#("BEGIN") #(LOCK-WIRE) #("SELECT version FROM accounts WHERE id = $1")
                                            #("SELECT version FROM accounts WHERE id = $1")
                                            #("INSERT INTO accounts (id, version) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET version = excluded.version")
                                            #("INSERT INTO accounts (id, version) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET version = excluded.version")
                                            #("COMMIT"))})

;; 基点の文の並び(sqlite — trace の callback は値を埋めた文を渡す)。
(val BASE-SQLITE-STATEMENTS
  {"文と SqlInsertRows(錠あり)" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (100, 'a')" "INSERT INTO items (id, label) VALUES (101, 'b')"
                                  "INSERT INTO items (id, label) VALUES (102, 'c')" "SELECT count(*) FROM items" "COMMIT")
   "SQL を出さない(錠あり)" #("BEGIN IMMEDIATE" "COMMIT")
   "途中で落ちて ROLLBACK" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (110, 'x')" "INSERT INTO items (id, label) VALUES (110, 'y')"
                             "ROLLBACK")
   "預かり所の commit-rows の形(錠あり)" #("BEGIN IMMEDIATE" "SELECT version FROM accounts WHERE id = 1" "SELECT version FROM accounts WHERE id = 2"
                                            "INSERT INTO accounts (id, version) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                                            "INSERT INTO accounts (id, version) VALUES (2, 1) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                                            "COMMIT")
   "sqlite が断る SqlNotify" #("BEGIN IMMEDIATE" "INSERT INTO items (id, label) VALUES (120, 'n')" "ROLLBACK")})


;; --- 代表の program -----------------------------------------------------------------------------------------------------

(defk statements-and-rows [notice]
  {:pre [(: notice bool)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "文 2 本と SqlInsertRows(2 行)を流し、notice なら最後に合図を出す program。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (:id, :label)" #((SqlParam :name "id" :value 100) (SqlParam :name "label" :value "a"))))
  (<- (SqlInsertRows DB "items" #("id" "label") #(#(101 "b") #(102 "c"))))
  (<- (SqlQuery DB "SELECT count(*) FROM items" #()))
  (when notice
    (<- (SqlNotify DB "invariant" #("table:items"))))
  "流した")


(defk no-sql []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "SQL を 1 つも出さない program(純粋な計算だけ)。"
  (+ 1 2))


(defk duplicate-insert []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "2 文目が一意の違反で落ちる program。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (110, 'x')" #()))
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (110, 'y')" #()))
  "来ない")


(defk checked-and-upserted []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "預かり所の本体の commit-rows の形: 行ごとに version を SELECT して照らし、行ごとに UPSERT する program(SqlNotify は出さない)。"
  (for [id #(1 2)]
    (<- (SqlQuery DB "SELECT version FROM accounts WHERE id = :id" #((SqlParam :name "id" :value id)))))
  (for [#(id version) #(#(1 2) #(2 1))]
    (<- (SqlQuery DB "INSERT INTO accounts (id, version) VALUES (:id, :version) ON CONFLICT (id) DO UPDATE SET version = excluded.version"
                  #((SqlParam :name "id" :value id) (SqlParam :name "version" :value version)))))
  "照らして書いた")


(defk notice-under-sqlite []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "1 行入れてから合図を出す program(sqlite の答え手は合図を受けないので断る)。"
  (<- (SqlQuery DB "INSERT INTO items (id, label) VALUES (120, 'n')" #()))
  (<- (SqlNotify DB "invariant" #("table:items")))
  "来ない")


(defk prepared-tables []
  {:pre [] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "検の表を作り直すため(前の走行の行を残さない)。"
  (<- (SqlQuery DB "DROP TABLE IF EXISTS items" #()))
  (<- (SqlQuery DB "DROP TABLE IF EXISTS accounts" #()))
  (<- (SqlEnsureTables DB #(ITEMS ACCOUNTS)))
  None)


(defk answer-or-refusal [transaction]
  {:pre [(: transaction SqlTransaction)] :post [(: % "transaction の答え | 断りの印")]
   :tags {:context "sql" :role "program"}}
  "transaction を撃ち、SqlTransactionMisuse で断られたら断りの印を返すため。"
  (try
    (<- answer transaction)
    answer
    (except [SqlTransactionMisuse]
      "断られた")))


;; --- PostgreSQL ---------------------------------------------------------------------------------------------------------

(defk postgres-round-trips [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "代表の program を batched を選ばない transaction で 1 つずつ撃ち、名 → #(答え 往復の列) を返すため(往復は中継が記録した物)。"
  (<- (prepared-tables))
  (var seen {})
  (for [#(name transaction) #(#("文と SqlInsertRows と合図(錠あり)" (SqlTransaction DB (statements-and-rows True) LOCK))
                              #("SQL を出さない(錠あり)" (SqlTransaction DB (no-sql) LOCK))
                              #("SQL を出さない(錠なし)" (SqlTransaction DB (no-sql) None))
                              #("途中で落ちて ROLLBACK" (SqlTransaction DB (duplicate-insert) None))
                              #("預かり所の commit-rows の形(錠あり)" (SqlTransaction DB (checked-and-upserted) LOCK)))]
    (.reset tap)
    (<- answer (answer-or-refusal transaction))
    (:= seen (| seen {name #(answer (.round-trips tap))})))
  seen)


(defk tapped-dsn [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "検の DSN を、中継の口へ繋ぐ DSN に書き換えるため(SSL・GSS の交渉をしない — 中継が読むのは平文の message)。"
  (import psycopg.conninfo [make-conninfo])
  (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1" :port (str tap.port) :sslmode "disable" :gssencmode "disable"))


(defk tap-target []
  {:pre [] :post [(: % (| str tuple))]
   :tags {:context "sql" :role "program"}}
  "検の DSN が指す PostgreSQL の口(unix socket の path か #(host port))を読むため。"
  (import psycopg.conninfo [conninfo-to-dict])
  (val given (conninfo-to-dict (or POSTGRES-DSN "")))
  (val host (.get given "host" "127.0.0.1"))
  (val port (.get given "port" "5432"))
  (if (.startswith host "/")
      (.format "{}/.s.PGSQL.{}" host port)
      #(host (int port))))


(defk asserted-postgres-round-trips [seen]
  {:pre [(: seen dict)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "postgres-round-trips の答えを、基点の往復の列と答えに照らすため。"
  (assert (= (dfor #(name #(_ trips)) (.items seen) name trips) BASE-POSTGRES-ROUND-TRIPS)
          (dfor #(name #(_ trips)) (.items seen) name trips))
  (val answers (dfor #(name #(answer _)) (.items seen) name answer))
  (assert (= (get answers "文と SqlInsertRows と合図(錠あり)") "流した") answers)
  (assert (= (get answers "SQL を出さない(錠あり)") 3) answers)
  (assert (= (get answers "SQL を出さない(錠なし)") 3) answers)
  (val failed (get answers "途中で落ちて ROLLBACK"))
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23505")) failed)
  (assert (= (get answers "預かり所の commit-rows の形(錠あり)") "照らして書いた") answers)
  None)


(deftest test-postgres-unbatched-transactions-send-the-base-round-trips
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; 既に在る SqlTransaction の意味は変えない(#3605): batched を選ばない transaction は、BEGIN・錠・文・合図・COMMIT / ROLLBACK を
  ;; それぞれ往復 1 回で、出た順に流す — 2 つの PostgreSQL の答え手とも、往復の列が基点と同じ。
  (<- target (tap-target))
  (val tap (StatementWireTap target))
  (<- dsn (tapped-dsn tap))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (postgres-round-trips tap)))
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (postgres-round-trips tap)))
      (finally (.close shared)))
    (finally (.shutdown pool) (.close tap)))
  (<- (asserted-postgres-round-trips direct))
  (<- (asserted-postgres-round-trips pooled)))


;; --- sqlite ---------------------------------------------------------------------------------------------------------------

(defk drained [traced]
  {:pre [(: traced queue.SimpleQueue)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "trace の callback が受けた文を、受けた順に全部取り出すため。"
  (tuple (gfor _ (range (.qsize traced)) (.get traced))))


(defk sqlite-statements [traced]
  {:pre [(: traced queue.SimpleQueue)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "代表の program を batched を選ばない transaction で 1 つずつ撃ち、名 → #(答え 流した文の並び) を返すため。"
  (<- (prepared-tables))
  (var seen {})
  (for [#(name transaction) #(#("文と SqlInsertRows(錠あり)" (SqlTransaction DB (statements-and-rows False) LOCK))
                              #("SQL を出さない(錠あり)" (SqlTransaction DB (no-sql) LOCK))
                              #("途中で落ちて ROLLBACK" (SqlTransaction DB (duplicate-insert) None))
                              #("預かり所の commit-rows の形(錠あり)" (SqlTransaction DB (checked-and-upserted) LOCK))
                              #("sqlite が断る SqlNotify" (SqlTransaction DB (notice-under-sqlite) None)))]
    (<- (drained traced))
    (<- answer (answer-or-refusal transaction))
    (<- statements (drained traced))
    (:= seen (| seen {name #(answer statements)})))
  seen)


(deftest test-sqlite-unbatched-transactions-run-the-base-statements [tmp-path]
  ;; 既に在る SqlTransaction の意味は変えない(#3605): batched を選ばない transaction は、BEGIN IMMEDIATE・文・COMMIT / ROLLBACK を出た順に
  ;; 流す — 文の並びが基点と同じ(預かり所の本体の答え手 foundation の sqlite_store と同じ sqlite-answer-transaction を通る)。
  (val path (str (/ tmp-path "invariant.sqlite3")))
  (.close (sqlite3.connect path))
  (<- files (open-sqlite-files #((SqliteFile :name DB :path path))))
  (val traced (queue.SimpleQueue))
  (.set-trace-callback (. (get files.connections 0) connection) (fn [text] (.put traced text)))
  (try
    (<- seen (with-handler [(state) (sqlite-file-sql-handler files)] (sqlite-statements traced)))
    (finally (<- (close-sqlite-files files))))
  (assert (= (dfor #(name #(_ statements)) (.items seen) name statements) BASE-SQLITE-STATEMENTS)
          (dfor #(name #(_ statements)) (.items seen) name statements))
  (val answers (dfor #(name #(answer _)) (.items seen) name answer))
  (assert (= (get answers "文と SqlInsertRows(錠あり)") "流した") answers)
  (assert (= (get answers "SQL を出さない(錠あり)") 3) answers)
  (val failed (get answers "途中で落ちて ROLLBACK"))
  (assert (and (isinstance failed SqlFailed) (= failed.sqlstate "23000")) failed)
  (assert (= (get answers "預かり所の commit-rows の形(錠あり)") "照らして書いた") answers)
  (assert (= (get answers "sqlite が断る SqlNotify") "断られた") answers))
