;;; 接続の上の prepared statement の名の持ち主は doeff 1 つ(agora card ki-2c5646a8a2c1・調べは ki-746fc833ea6a): 束ねた transaction の
;;; 文は postgres-flush が doeff_pq_N の名で用意して接続ごとの PreparedBook に覚え、psycopg の自動の prepare は止めてある。psycopg に名を
;;; 持たせると、cursor の道で同じ文を 6 回目から _pg3_N として用意し、その後の ROLLBACK で自分の覚えを捨てて DEALLOCATE ALL を送り、
;;; doeff_pq_N も消す — PreparedBook は知らないので、その接続の次の束ねた往復は 26000 で落ち続けた(merge-queue-ledger の put-row の 500)。
;;;   - 使い捨ての PostgreSQL(postgres_support/disposable_postgres.py)へ、貸し出しの上限 1 本で繋ぐ(どの借りも同じ接続)。
;;;   - 2 つの PostgreSQL の答え手(postgres-sql-handler・pooled-postgres-sql-handler)の両方で、本番の経路を撃つ: 束ねた transaction で名を
;;;     用意する → cursor の道で同じ文を psycopg の既定の閾値(5 回)を越えて流す → 束ねない transaction が落ちて ROLLBACK → 束ねた
;;;     transaction をもう一度。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects [state])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlEnsureTables SqlParam SqlRows SqlFailed SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects.postgres_sql [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))
;; psycopg の既定の prepare_threshold(5)を越える回数。
(val CURSOR-REPEATS 8)

(val NAMED (SqlTable :name "named_rows"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "label" :type SqlColumnType.TEXT))
                     :primary-key #("id")))


(defk counted []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "表の行の数を読むため(束ねた transaction でも cursor の道でも同じ文)。"
  (<- answer (SqlQuery DB "SELECT count(*) FROM named_rows WHERE id >= :floor" #((SqlParam :name "floor" :value 0))))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0 0))


(defk duplicate-insert []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "2 文目が一意の違反で落ちる program(transaction は ROLLBACK される)。"
  (<- (SqlQuery DB "INSERT INTO named_rows (id, label) VALUES (1, 'x')" #()))
  (<- (SqlQuery DB "INSERT INTO named_rows (id, label) VALUES (1, 'y')" #()))
  "来ない")


(defk prepared-names []
  {:pre [] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "今の接続(上限 1 本 — 同じ接続)の server の上の prepared statement の名を、名の順に読むため。"
  (<- answer (SqlQuery DB "SELECT name FROM pg_prepared_statements ORDER BY name" #()))
  (tuple (gfor row answer.rows (get row 0))))


(defk rolled-back-path []
  {:pre [] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "本番の経路(頭の註)を撃ち、束ねた transaction の答えと server の上の名を集めるため。"
  (<- (SqlEnsureTables DB #(NAMED)))
  (<- (SqlQuery DB "DELETE FROM named_rows" #()))
  (<- first (SqlTransaction DB (counted) None True))
  (for [_ (range CURSOR-REPEATS)]
    (<- (counted)))
  (<- before (prepared-names))
  (<- failed (SqlTransaction DB (duplicate-insert) None False))
  (<- after (prepared-names))
  (<- again (SqlTransaction DB (counted) None True))
  {"first" first "before" before "failed" failed "after" after "again" again})


(defk asserted-rolled-back-path [seen]
  {:pre [(: seen dict)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "rolled-back-path の答えを、名の持ち主の決まりに照らすため。"
  (assert (= (get seen "first") 0) seen)
  (assert (isinstance (get seen "failed") SqlFailed) seen)
  ;; server の上の名は doeff の名だけ(psycopg は cursor の道で何度流しても名を付けない)。ROLLBACK の後も消えない。
  (assert (all (gfor name (get seen "before") (.startswith name "doeff_pq_"))) seen)
  (assert (get seen "before") seen)
  (assert (= (get seen "after") (get seen "before")) seen)
  ;; ROLLBACK の後の束ねた往復も、覚えた名で通る(26000 で落ちない)。
  (assert (= (get seen "again") 0) seen)
  None)


(deftest test-postgres-rollback-keeps-doeff-prepared-names
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; 2 つの PostgreSQL の答え手とも、cursor の道の ROLLBACK が doeff の名を消さず、次の束ねた transaction が通る。
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (rolled-back-path)))
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn (or POSTGRES-DSN ""))) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (rolled-back-path)))
      (finally (.close shared)))
    (finally (.shutdown pool)))
  (<- (asserted-rolled-back-path direct))
  (<- (asserted-rolled-back-path pooled)))
