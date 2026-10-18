;; 索引の用意(card ki-954c58540d0f — 2026-10-18 13:18〜14:26 JST に、記録の店の表に索引を 1 つ宣言した配備で、起動の表の用意の
;; CREATE INDEX が query 用の文の上限 60 秒で cancel され、起動が 65 秒ごとに落ち続けた): 表の用意は宣言と置き場の差を観測して埋める形で、
;; 置き場の大きさに比例する仕事を起動の道と query 用の文の上限に置かない(pg.hy の頭の註の「索引の用意」)。
;; 既に在る置き場(表 parts に行)に、宣言 NOTE-SCHEMA が表 parts の索引の欄 note を足す。索引の作りの長さは、書きの代役(書きの錠の外で
;; state_rows に RowExclusiveLock を持つ transaction — CREATE INDEX CONCURRENTLY はその終わりを待つ)を持つ秒で作る。
;; 性質:
;;   - query 用の文の上限を 1 秒にした接続でも、用意は通り、1 秒を越えて待つ索引の作りも索引の用意の task(文の上限の無い DDL の接続)が
;;     通す。作り終えるまで、索引の欄 note を名指す ListRows だけが NotIndexed で、他の読みと書きは答える(受入の条 1)
;;   - 記録の service(本番と同じ records-connected・PG-STORE)は、索引を作り終える前から記録の操作に 200 で答え、作り終えれば note の
;;     読みにも答える。service は落ちない(受入の条 2)
;;   - 旧い形の索引(全部の行に張った (ledger, 欄の式) — 2026-10-18 に手で作った records_ix_8dca46428d00 と同じ形)が在る欄は、作り直さずに
;;     答える(受入の条 4)
;;   - CONCURRENTLY の作りかけ(使えない形で残った同じ名の索引)は消してから作り直す・索引の用意の錠を他の process が持つ間は錠を待つ
;; 反例: 前の形(既に在る表へ移行の transaction の中で CREATE INDEX)は、同じ筋書きで 1 秒の文の上限に cancel される(57014 — 本番の失敗と
;; 同じ形。「通る」の判定が何も確かめずに緑になる形を外す)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defhandler defk <- val])
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses)
(import queue)
(import threading)
(import time)
(import pytest)
(import doeff [run with_handlers])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_core_effects.postgres_sql [DEFAULT-TIMEOUTS PostgresConnections PostgresDatabase PostgresTimeouts postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlFailed])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_time [SimClock sim-time-handler async-time-handler])
(import doeff_records.values [RecordsSchema Row Page NotIndexed ExpectAbsent Written Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler prepare-records-store reconcile-indexes index-round drop-records-tables])
(import doeff_records.pg_sql [table-index-name index-name index-lock-key migrate-lock-key schema-statements])
(import doeff_records.main [RecordsSettings DDL-DATABASE DDL-TIMEOUTS PG-STORE records-serving records-connected records-process])
(import doeff_records.http_server [MaintenancePlan RecordsListening])
(import doeff_records.http_client [RecordsEndpoint http-records-handler records-unwaited])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST open-postgres fresh-prefix])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))
;; 本物の PostgreSQL の lock の待ちと、索引の作りが書きの代役を待つ実時間が対象。
(val pytestmark pytest.mark.realtime)
;; 宣言に足す索引の欄(表 parts の欄 note — LAW-SCHEMA の索引の欄は color と label)。
(val NOTE-SCHEMA
  (RecordsSchema :tables (FrozenMap (| (dict LAW-SCHEMA.tables)
                                       {"parts" (dataclasses.replace (get LAW-SCHEMA.tables "parts") :indexes #("color" "label" "note"))}))
                 :streams LAW-SCHEMA.streams :operators LAW-SCHEMA.operators))
;; query 用の文の上限を 1 秒にした接続の上限(本番の 60 秒の代わり — 他は本番と同じ)。
(val QUICK-TIMEOUTS (dataclasses.replace DEFAULT-TIMEOUTS :statement-milliseconds 1000))
;; 書きの代役が索引の作りを待たせる秒(query 用の文の上限 1 秒より長い)。
(val HOLD-SECONDS 1.5)
;; 待ちを確かめる締め切り(秒)と読み直しの間(秒)。
(val DEADLINE-SECONDS 20.0)
(val POLL-SECONDS 0.05)


;; --- 組み立て ----------------------------------------------------------------------------------------------------------------------

(defn quick-connections []  ; defk にできない: 検の資源の組み立て(Program の外)
  "query 用の接続の貸し出し(文の上限 1 秒)。"
  (PostgresConnections #((PostgresDatabase :name DATABASE :dsn PG-DSN)) :size 2 :timeouts QUICK-TIMEOUTS))


(defn ddl-connections []  ; defk にできない: 検の資源の組み立て(Program の外)
  "索引の用意の接続の貸し出し(本番と同じ DDL-DATABASE の接続 1 本・文の上限なし)。"
  (PostgresConnections #((PostgresDatabase :name DDL-DATABASE :dsn PG-DSN)) :size 1 :timeouts DDL-TIMEOUTS))


(defn run-on [quick ddl program]  ; defk にできない: 検の入口で run を撃つ
  "query 用と索引の用意の SQL の答え手と仮想の時計の下で program を 1 回走らせる。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler quick) (postgres-sql-handler ddl)] program))))


(defn as-maker [store program]  ; defk にできない: 検の入口の組み立て
  "program を書き手 MAKER の PostgreSQL の置き場の handler の下に置く。"
  (with_handlers [(pg-records-handler store MAKER ORIGIN-HOST)] program))


(defn blocked-by [watcher #^ int pid]  ; defk にできない: 検の素の接続で数える
  "接続 pid の lock を待っている接続の数。"
  (get (.fetchone (.execute watcher "SELECT count(*) FROM pg_stat_activity WHERE %s = ANY(pg_blocking_pids(pid))" #(pid))) 0))


(defn index-validity [watcher #^ str name]  ; defk にできない: 検の素の接続で読む
  "索引 name の使える形か(True / False — 無ければ None)。"
  (setv found (.fetchone (.execute watcher "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid
                                            WHERE c.relname = %s" #(name))))
  (if (is found None) None (get found 0)))


(defn hold-writer [writer #^ str prefix]  ; defk にできない: 検の素の接続で書きの代役を始める
  "書きの錠の外の書きの代役を始める(state_rows に RowExclusiveLock — CREATE INDEX CONCURRENTLY はこの transaction の終わりを待ち、
   移行の transaction の中の CREATE INDEX は ShareLock を待つ)。答え = 代役の接続の pid。"
  (setv pid (get (.fetchone (.execute writer "SELECT pg_backend_pid()")) 0))
  (.execute writer "BEGIN")
  (.execute writer (.format "LOCK TABLE {}state_rows IN ROW EXCLUSIVE MODE" prefix))
  pid)


(defn wait-until-blocked [watcher #^ int pid]  ; defk にできない: 検の素の接続で待つ
  "接続 pid を待つ接続が現れるまで待つ(締め切りを越えたら赤)。"
  (setv deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (while (and (= (blocked-by watcher pid) 0) (< (time.monotonic) deadline))
    (time.sleep POLL-SECONDS))
  (assert (>= (blocked-by watcher pid) 1) "索引の作りが書きの代役を待ち始めなかった"))


(defn with-seeded-store [check]  ; defk にできない: 検の置き場の組み立てと後片付け
  "宣言 LAW-SCHEMA の置き場を用意して表 parts に行 p1(note = n1)を置き、check に #(接頭辞 quick ddl) を渡して、後で表を消す。"
  (setv prefix (fresh-prefix)
        quick (quick-connections)
        ddl (ddl-connections)
        base (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE LAW-SCHEMA prefix)))
  (try
    (setv put (run-on quick ddl (as-maker base (PutRow "parts" #("p1") {"label" "a" "color" "red" "note" "n1"} (ExpectAbsent)))))
    (assert (isinstance put Written) (repr put))
    (check prefix quick ddl)
    (finally
      (run-on quick ddl (drop-records-tables base))
      (.close quick)
      (.close ddl))))


(defn reads-of [quick ddl store]  ; defk にできない: 検の入口で run を撃つ
  "#(行 p1 の ReadRow・欄 color の ListRows・欄 note の ListRows) の答え。"
  #((run-on quick ddl (as-maker store (ReadRow "parts" #("p1"))))
    (run-on quick ddl (as-maker store (ListRows "parts" :where {"color" "red"})))
    (run-on quick ddl (as-maker store (ListRows "parts" :where {"note" "n1"})))))


(defn listed-keys [answer]  ; defk にできない: 検の答えの読み
  "頁の行の鍵の並び(頁でなければ答えのまま — 赤の文に載せる)。"
  (if (isinstance answer Page) (tuple (gfor row answer.rows row.key)) answer))


;; --- query 用の文の上限に縛られない ---------------------------------------------------------------------------------------------------

(deftest test-an-index-build-longer-than-the-query-statement-timeout-is-made-while-reads-answer
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 受入の条 1: 文の上限 1 秒の接続で、索引を足す宣言の用意は通り、1 秒を越えて待つ索引の作りも通る。作る間も読みと書きは答え、
  ;; note の読みだけが NotIndexed。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-index-name prefix "parts" "note"))
            writer (open-postgres)
            watcher (open-postgres)
            pool (ThreadPoolExecutor :max-workers 1))
      (try
        (setv writer-pid (hold-writer writer prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #(note-index)) (repr store.indexes.missing))
        (setv #(row by-color by-note) (reads-of quick ddl store))
        (assert (isinstance row Row) (repr row))
        (assert (= (listed-keys by-color) #(#("p1"))) (repr by-color))
        (assert (= by-note (NotIndexed #("note"))) (repr by-note))
        (setv started (time.monotonic)
              reconciling (.submit pool (fn [] (run-on quick ddl (reconcile-indexes store)))))
        (wait-until-blocked watcher writer-pid)
        ;; 作りが書きの代役を待つ間も、読みと書きは答える。
        (setv during (run-on quick ddl (as-maker store (PutRow "parts" #("p2") {"label" "b" "color" "red" "note" "n2"} (ExpectAbsent)))))
        (assert (isinstance during Written) (repr during))
        (setv #(row-during color-during note-during) (reads-of quick ddl store))
        (assert (isinstance row-during Row) (repr row-during))
        (assert (= (listed-keys color-during) #(#("p1") #("p2"))) (repr color-during))
        (assert (= note-during (NotIndexed #("note"))) (repr note-during))
        (time.sleep HOLD-SECONDS)
        (.execute writer "COMMIT")
        (.result reconciling 60)
        (setv took (- (time.monotonic) started))
        (assert (> took 1.0) took)
        (assert (index-validity watcher note-index) note-index)
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (setv #(_row _color note-after) (reads-of quick ddl store))
        (assert (= (listed-keys note-after) #(#("p1"))) (repr note-after))
        (finally
          (.shutdown pool)
          (.close writer)
          (.close watcher))))))


(defk statements-in-order [statements]
  {:pre [(: statements tuple)] :post [(: % None)] :tags {:context "records" :role "program"}}
  "前の形の用意の文を順に流すため(反例 — 移行の transaction の中身)。"
  (for [statement statements]
    (<- (SqlQuery DATABASE statement.text statement.params)))
  None)


(defk prepared-before-the-fix [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % "SqlTransaction の答え")] :tags {:context "records" :role "program"}}
  "前の形の用意(反例): 空の置き場の文の全部を、既に在る表にも移行の錠の transaction の中で流すため(答え = SqlTransaction の答え)。"
  (<- statements (schema-statements prefix schema))
  (<- key (migrate-lock-key prefix))
  (<- answer (SqlTransaction DATABASE (statements-in-order statements) :lock-key key))
  answer)


(deftest test-the-form-before-the-fix-is-cancelled-by-the-query-statement-timeout
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 同じ筋書きで、既に在る表へ transaction の中で CREATE INDEX を流す前の形は、文の上限 1 秒で cancel される(57014)。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv writer (open-postgres))
      (try
        (hold-writer writer prefix)
        (setv answer (run-on quick ddl (prepared-before-the-fix prefix NOTE-SCHEMA)))
        (assert (and (isinstance answer SqlFailed) (= answer.sqlstate "57014")) (repr answer))
        (finally
          (.execute writer "ROLLBACK")
          (.close writer))))))


;; --- 旧い形の索引・作りかけ・錠 ------------------------------------------------------------------------------------------------------

(deftest test-a-legacy-index-over-all-rows-answers-without-building-again
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 受入の条 4: 旧い形の索引(全部の行の (ledger, 欄の式) — 2026-10-18 に手で作った records_ix_8dca46428d00 と同じ形・旧い名)が在る欄は、
  ;; 用意が差に数えず、作り直さずに読みに答える。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv legacy (run (index-name prefix "note"))
            watcher (open-postgres))
      (try
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (ledger, ((payload::jsonb) -> 'note'))" :i legacy :p prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (setv #(_row _color by-note) (reads-of quick ddl store))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        (assert (is (index-validity watcher (run (table-index-name prefix "parts" "note"))) None) "部分索引を作り直した")
        (finally
          (.close watcher))))))


(deftest test-a-half-built-index-is-dropped-and-built-again
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; CONCURRENTLY の作りかけ(cancel された作りが残す使えない索引)は、差の中で invalid と数え、消してから作り直す。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-index-name prefix "parts" "note"))
            writer (open-postgres)
            builder (open-postgres)
            watcher (open-postgres))
      (try
        (hold-writer writer prefix)
        (.execute builder "SET statement_timeout = 300")
        (try
          (.execute builder (.format "CREATE INDEX CONCURRENTLY {i} ON {p}state_rows (((payload::jsonb) -> 'note')) WHERE ledger = 'parts'"
                                     :i note-index :p prefix))
          (except [error Exception]
            (assert (= (getattr error "sqlstate" None) "57014") (repr error))))
        (.execute writer "COMMIT")
        (assert (is (index-validity watcher note-index) False) note-index)
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #(note-index)) (repr store.indexes.missing))
        (run-on quick ddl (reconcile-indexes store))
        (assert (is (index-validity watcher note-index) True) note-index)
        (setv #(_row _color by-note) (reads-of quick ddl store))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        (finally
          (.close writer)
          (.close builder)
          (.close watcher))))))


(deftest test-the-round-waits-for-the-index-lock-held-by-another-process
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 同じ置き場の別の process(本体と twin)が索引の用意の錠を持つ間、回は錠を待って作らず(時間で見直さない — 錠の待ち)、錠が外れたら
  ;; 錠の中で観測し直して作る。待つ間に別の process が作った索引は作らない(作り直しの文が流れても IF NOT EXISTS で何もしない)。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-index-name prefix "parts" "note"))
            other (open-postgres)
            watcher (open-postgres)
            pool (ThreadPoolExecutor :max-workers 1))
      (try
        (setv other-pid (get (.fetchone (.execute other "SELECT pg_backend_pid()")) 0))
        (.execute other "SELECT pg_advisory_lock(hashtext(%s))" #((run (index-lock-key prefix))))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (setv rounding (.submit pool (fn [] (run-on quick ddl (index-round store)))))
        (wait-until-blocked watcher other-pid)
        (assert (not (.done rounding)) "錠を持たれた回が待たずに終わった")
        (assert (is (index-validity watcher note-index) None) "錠を持たれた間に索引を作った")
        (.execute other "SELECT pg_advisory_unlock(hashtext(%s))" #((run (index-lock-key prefix))))
        (assert (is (.result rounding 60) True) "錠が外れた後の回が埋めなかった")
        (assert (is (index-validity watcher note-index) True) note-index)
        (finally
          (.shutdown pool)
          (.close other)
          (.close watcher))))))


;; --- 記録の service(本番の土台の口)-------------------------------------------------------------------------------------------------

(defhandler stop-when [#^ object stopping]
  "止めの合図を、検の殻の合図(threading.Event)が立ったら答えるため。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: stopping は thread をまたぐ検の殻の合図。
  (StopRequested []
    (resume (if (.is-set stopping) "検の止め" None))))


(defhandler listening-into [#^ object opened]
  "待ち受けが結んだ宛先を検の殻の queue へ渡すため(名乗りは外側の印字へも回さない)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: opened は thread をまたぐ検の殻の queue。
  (RecordsListening [address]
    (.put opened address)
    (resume None)))


(defn serve-in-thread [#^ str prefix opened stopping ended]  ; defk にできない: 記録の service を別の thread の run で起こす検の殻
  "本番の土台の口(records-connected・PG-STORE・宣言 NOTE-SCHEMA)の下で記録の service を別の thread で起こし、終わりの code を ended へ渡す。"
  (setv settings (RecordsSettings :dsn PG-DSN :prefix prefix :origin-host ORIGIN-HOST :pool-size 2
                                  :address (HttpAddress :host "127.0.0.1" :port 0)
                                  :maintenance (MaintenancePlan :interval-seconds 60.0 :keep-seconds 604800.0))
        serving (dataclasses.replace (run (records-serving NOTE-SCHEMA settings PG-STORE)) :maintenance None))
  (defn body []  ; defk にできない: thread の target
    (try
      (.put ended (run (records-process (fn [program] (scheduled (with_handlers [(await-handler) (state) (async-time-handler) (stop-when stopping)]
                                                                                (records-connected settings (with_handlers [(listening-into opened)]
                                                                                                                           program)))))
                                        serving)))
      (except [error BaseException]
        (.put opened error)
        (.put ended error))))
  (setv thread (threading.Thread :target body :daemon True :name "records-reconcile-test"))
  (.start thread)
  thread)


(defn over-http [#^ str url ask]  ; defk にできない: 検の入口で client の run を撃つ
  "記録の service の口へ client の handler で ask を 1 つ撃つ。"
  (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock)) records-unwaited
                                  (http-records-handler (RecordsEndpoint url :writer MAKER))]
                                 ask))))


(deftest test-the-service-answers-while-the-reconciler-builds-and-does-not-end
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 受入の条 2: 索引を足した宣言で起こした記録の service は、索引の用意の task が作り終える前から記録の操作に答え(note の読みだけ
  ;; NotIndexed)、作り終えれば note の読みにも答える。service は落ちない(止めの合図で 0 で終わる)。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv writer (open-postgres)
            watcher (open-postgres)
            opened (queue.Queue)
            ended (queue.Queue)
            stopping (threading.Event))
      (try
        (setv writer-pid (hold-writer writer prefix))
        (setv thread (serve-in-thread prefix opened stopping ended))
        (setv bound (.get opened :timeout 30))
        (assert (isinstance bound HttpAddress) (repr bound))
        (setv url (.format "http://{}:{}" bound.host bound.port))
        ;; 用意(一瞬)の後は記録の操作に答える。
        (setv deadline (+ (time.monotonic) DEADLINE-SECONDS)
              row (over-http url (ReadRow "parts" #("p1"))))
        (while (and (isinstance row Unreachable) (< (time.monotonic) deadline))
          (time.sleep POLL-SECONDS)
          (setv row (over-http url (ReadRow "parts" #("p1")))))
        (assert (isinstance row Row) (repr row))
        (wait-until-blocked watcher writer-pid)
        (setv by-color (over-http url (ListRows "parts" :where {"color" "red"}))
              by-note (over-http url (ListRows "parts" :where {"note" "n1"})))
        (assert (= (listed-keys by-color) #(#("p1"))) (repr by-color))
        (assert (= by-note (NotIndexed #("note"))) (repr by-note))
        (assert (.is-alive thread) "記録の service が終わった")
        (.execute writer "COMMIT")
        (setv deadline (+ (time.monotonic) DEADLINE-SECONDS)
              after (over-http url (ListRows "parts" :where {"note" "n1"})))
        (while (and (isinstance after NotIndexed) (< (time.monotonic) deadline))
          (time.sleep POLL-SECONDS)
          (setv after (over-http url (ListRows "parts" :where {"note" "n1"}))))
        (assert (= (listed-keys after) #(#("p1"))) (repr after))
        (assert (.is-alive thread) "記録の service が終わった")
        (.set stopping)
        (assert (= (.get ended :timeout 30) 0) "記録の service が 0 で終わらなかった")
        (finally
          (.set stopping)
          (.close writer)
          (.close watcher))))))
