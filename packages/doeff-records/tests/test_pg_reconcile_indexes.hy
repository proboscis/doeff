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
;;   - 旧い形の索引(全部の行に張った (ledger, 欄の式) — 2026-10-18 に手で作った records_ix_8dca46428d00 と同じ形)が在る欄は、表の欄の式の
;;     索引を待たずに答え、索引の用意は表の欄の式の索引を作る(受入の条 4 — card ki-2176012d1804 で改めた)
;;   - 旧い形の索引と前の部分索引は、宣言の索引が全部そろい表の欄の式で照らせる(見積もれる)ようになった後に、索引の用意が
;;     DROP INDEX CONCURRENTLY で外す。見積もれない間は外さず、旧い形でない宣言に無い索引は外さない。宣言の索引が全部在る置き場でも、
;;     旧い形の索引が在れば記録の service は索引の用意の task を立てて外し、外す間も答える(2026-10-19 — 本番の置き場の state_rows の
;;     索引 72 本のうち旧い形の 23 本〔約 1.9 GB〕は読みに使われず、payload を変える書きのたびに更新されていた)
;;   - 宣言から外した表ごとの索引(宣言の作る名の形 ft_・op_・wp_ と前の形 fx_・ox_・ow_ で今の宣言に無い物)も、表の用意(起動の道)では外さず、索引の用意が
;;     同じ道で外す。名の形が宣言の作る形でない手の索引は外さない(2026-10-23 — 本番の置き場の使われない表ごとの索引 11 本・874 MB)
;;   - CONCURRENTLY の作りかけ(使えない形で残った同じ名の索引)は消してから作り直す・索引の用意の錠を他の process が持つ間は作らずに待ち、
;;     錠を放した合図で観測し直す
;;   - 錠を持つ process の作りの最中に別の process の回が錠を求めても、作りは deadlock で捨てられず、両方の回が差を埋めて終わる
;;     (反例: 錠を SELECT pg_advisory_lock で待つ相手が居ると、作り手の CONCURRENTLY が 40P01 で捨てられる — 前の待ち方)
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
;; 束の読みの読み解きと綴りの Compute(ADR-DOE-RECORDS-002)を仮想の時計の上でその場で回す(本番は thread の pool)。
(import doeff_core_effects [inline-compute-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects [await-handler state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_core_effects.postgres_sql [DEFAULT-TIMEOUTS PostgresConnections PostgresDatabase PostgresTimeouts postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlFailed SqlNotify])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_time [SimClock sim-time-handler async-time-handler])
(import doeff_records [RecordsSchema Row Page NotIndexed ExpectAbsent Written Unreachable])
(import doeff_records [ReadRow ListRows PutRow])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler prepare-records-store reconcile-indexes index-round drop-records-tables notice-channel INDEX-TOPIC])
(import doeff_records.pg_sql [IndexedField table-index-name table-field-index-name former-field-index-name ordered-index-name ordered-within-index-name index-name index-lock-key
                              migrate-lock-key schema-statements])
(import doeff_records._main [RecordsSettings DDL-DATABASE DDL-TIMEOUTS PG-STORE records-serving records-connected records-process])
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
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler (postgres-sql-handler quick) (postgres-sql-handler ddl)] program))))


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
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
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

(defn create-former-indexes [watcher #^ str prefix]  ; defk にできない: 検の素の接続で旧い版の索引を作る
  "欄 note の旧い形の 2 つの索引(全部の行の (ledger, 欄の式) と前の部分索引 — 旧い版の名と形)と、宣言に無い手の索引(旧い形でない名)を
   作る。答え = #(旧い形の名 前の部分索引の名 手の索引の名)。"
  (setv legacy (run (index-name prefix "note"))
        partial (run (table-index-name prefix "parts" "note"))
        hand (+ prefix "hand_note"))
  (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (ledger, ((payload::jsonb) -> 'note'))" :i legacy :p prefix))
  (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (((payload::jsonb) -> 'note')) WHERE ledger = 'parts'" :i partial :p prefix))
  (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (version)" :i hand :p prefix))
  #(legacy partial hand))


(deftest test-a-legacy-index-over-all-rows-answers-while-the-table-field-index-is-built
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 受入の条 4(card ki-2176012d1804 で改めた): 旧い形の索引(全部の行の (ledger, 欄の式) — 2026-10-18 に手で作った records_ix_8dca46428d00 と
  ;; 同じ形・旧い名)が在る欄は、表の用意の直後から(表の欄の式の索引を待たずに)前の文の形の読みに答える。索引の用意は表の欄の式の索引を
  ;; 作り(旧い形の索引では等号 2 つの読みが索引を組めない — pg_sql の頭の註)、作り終えた後の読みは表の欄の式で照らす。前の部分索引は作らない。
  ;; 作り終えて表の標本を取り直した回は、旧い形の索引と前の部分索引を外す(読みは表の欄の式で照らすので引かない — 書きのたびの更新だけが
  ;; 残る)。旧い形でない宣言に無い索引(手の索引)は外さない。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            watcher (open-postgres))
      (try
        (setv #(legacy partial hand) (create-former-indexes watcher prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #(note-index)) (repr store.indexes.missing))
        (assert (= store.indexes.retired (tuple (sorted #(legacy partial)))) (repr store.indexes.retired))
        (assert (not-in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (setv #(_row _color by-note) (reads-of quick ddl store))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        (run-on quick ddl (reconcile-indexes store))
        (assert (is (index-validity watcher note-index) True) note-index)
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (assert (in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (setv #(_row _color note-after) (reads-of quick ddl store))
        (assert (= (listed-keys note-after) #(#("p1"))) (repr note-after))
        (assert (is (index-validity watcher legacy) None) "旧い形の索引を外さなかった")
        (assert (is (index-validity watcher partial) None) "前の部分索引を外さなかった")
        (assert (= store.indexes.retired #()) (repr store.indexes.retired))
        (assert (is (index-validity watcher hand) True) "旧い形でない手の索引を外した")
        (finally
          (.close watcher))))))


(deftest test-a-legacy-index-is-kept-until-the-table-field-index-can-be-estimated
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 表の欄の式の索引が在っても、標本を取り直す前(式の統計が無い — 見積もれない)の欄は前の文の形(ledger = :ledger と欄の式)で照らし、
  ;; その読みは旧い形の索引を引く。見積もれない間の回は旧い形の索引を外さず「埋めきれなかった」と答え(索引の用意は後で観測し直す)、
  ;; 表の標本が取れた後の回が外す。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            watcher (open-postgres))
      (try
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END), updated_at)"
                                   :i note-index :p prefix))
        (setv #(legacy partial _hand) (create-former-indexes watcher prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (assert (not-in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (assert (is (run-on quick ddl (index-round store)) False) "見積もれない間の回が埋めたと答えた")
        (assert (is (index-validity watcher legacy) True) "見積もれない間に旧い形の索引を外した")
        (assert (is (index-validity watcher partial) True) "見積もれない間に前の部分索引を外した")
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (assert (is (run-on quick ddl (index-round store)) True) "標本の後の回が埋めなかった")
        (assert (is (index-validity watcher legacy) None) "標本の後に旧い形の索引を外さなかった")
        (assert (is (index-validity watcher partial) None) "標本の後に前の部分索引を外さなかった")
        (assert (in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (setv #(_row _color note-after) (reads-of quick ddl store))
        (assert (= (listed-keys note-after) #(#("p1"))) (repr note-after))
        (finally
          (.close watcher))))))


(deftest test-the-former-table-field-index-answers-the-table-field-form-until-the-new-shape-is-sampled
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 表の欄の式の索引の項を書きの刻の順に並べる(pg_sql.hy の頭の註): 前の形の索引(式 1 つ — fx_)は先頭の式が新しい形(ft_)と同じ
  ;; なので、新しい形を作り終えて見積もれるようになるまで、表の欄の式の条件の読みに答える(scoped に数え、NotIndexed にも前の文の形にも
  ;; しない — 前の文の形を引く索引は無く、表の行を全部読む)。新しい形が見積もれない間は前の形を外さず(settled に数えない)、標本の
  ;; 後の回が外す。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            former (run (former-field-index-name prefix "parts" "note"))
            watcher (open-postgres))
      (try
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END))"
                                   :i former :p prefix))
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #(note-index)) (repr store.indexes.missing))
        (assert (= store.indexes.retired #(former)) (repr store.indexes.retired))
        (assert (in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (setv #(_row _color by-note) (reads-of quick ddl store))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        ;; 新しい形が在っても標本の前(見積もれない)は前の形を外さない。
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END), updated_at)"
                                   :i note-index :p prefix))
        (assert (is (run-on quick ddl (index-round store)) False) "新しい形が見積もれない間の回が埋めたと答えた")
        (assert (is (index-validity watcher former) True) "新しい形が見積もれない間に前の形を外した")
        (assert (in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (assert (not-in (IndexedField :table "parts" :field "note") store.indexes.settled) (repr store.indexes.settled))
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (assert (is (run-on quick ddl (index-round store)) True) "標本の後の回が埋めなかった")
        (assert (is (index-validity watcher former) None) "標本の後に前の形を外さなかった")
        (assert (in "note" (.scoped-fields store.indexes "parts")) (repr store.indexes.scoped))
        (setv #(_row _color note-after) (reads-of quick ddl store))
        (assert (= (listed-keys note-after) #(#("p1"))) (repr note-after))
        (finally
          (.close watcher))))))


(deftest test-table-shaped-indexes-dropped-from-the-declaration-are-dropped-off-the-startup-path
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 宣言から外した表ごとの索引を外す(pg_sql の頭の註 — 2026-10-23 に本番の置き場の使われない表ごとの索引 11 本・874 MB が書きのたびに
  ;; 更新されていた): 宣言 NOTE-SCHEMA の索引の用意が作った表の欄の式の索引(欄 note)と、宣言の作る名の形の上から読む欄の索引・等号の
  ;; 欄 × 上から読む欄の組の索引(欄 label — LAW-SCHEMA に無い)は、欄 note を外した宣言 LAW-SCHEMA で起こした表の用意(起動の道)では
  ;; 外さず外す物に数え、索引の用意が DROP INDEX CONCURRENTLY で外す。宣言の索引と、名の形が宣言の作る形でない手の索引は外さない。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            label-ordered (run (ordered-index-name prefix "parts" "label"))
            label-within (run (ordered-within-index-name prefix "parts" "color" "label"))
            color-index (run (table-field-index-name prefix "parts" "color"))
            note-ordered (run (ordered-index-name prefix "parts" "note"))
            hand (+ prefix "hand_note")
            watcher (open-postgres))
      (try
        (setv noted (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (run-on quick ddl (reconcile-indexes noted))
        (assert (is (index-validity watcher note-index) True) note-index)
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (key)" :i label-ordered :p prefix))
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (key)" :i label-within :p prefix))
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows (version)" :i hand :p prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE LAW-SCHEMA prefix)))
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (assert (= store.indexes.retired (tuple (sorted #(note-index label-ordered label-within)))) (repr store.indexes.retired))
        ;; 起動の道(表の用意)は外さない。
        (for [name #(note-index label-ordered label-within)]
          (assert (is (index-validity watcher name) True) (.format "表の用意が {} を外した" name)))
        (assert (= (run-on quick ddl (as-maker store (ListRows "parts" :where {"note" "n1"}))) (NotIndexed #("note"))))
        (run-on quick ddl (reconcile-indexes store))
        (for [name #(note-index label-ordered label-within)]
          (assert (is (index-validity watcher name) None) (.format "宣言に無い表ごとの索引 {} を外さなかった" name)))
        (assert (= store.indexes.retired #()) (repr store.indexes.retired))
        (assert (is (index-validity watcher color-index) True) "宣言の表の欄の式の索引を外した")
        (assert (is (index-validity watcher note-ordered) True) "宣言の上から読む欄の索引を外した")
        (assert (is (index-validity watcher hand) True) "宣言の作る形でない手の索引を外した")
        (setv by-color (run-on quick ddl (as-maker store (ListRows "parts" :where {"color" "red"}))))
        (assert (= (listed-keys by-color) #(#("p1"))) (repr by-color))
        (finally
          (.close watcher))))))


(deftest test-a-table-field-index-without-statistics-is-read-in-the-former-form-until-the-table-is-sampled
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 統計の在る表に足して標本を取り直す前の表の欄の式の索引は、値の組の見積もりが既定の選択度の積み上げになり表を頭から読む計画が
  ;; 選ばれる(pg.hy の頭の註)。観測はその欄を答える欄に数え(NotIndexed にしない)、表の欄の式で照らす欄(scoped)には標本を取り直した
  ;; 後に数える。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            watcher (open-postgres))
      (try
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END), updated_at)"
                                   :i note-index :p prefix))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (assert (= store.indexes.missing #()) (repr store.indexes.missing))
        (assert (.covers store.indexes "parts" "note") (repr store.indexes.fields))
        (assert (= (.scoped-fields store.indexes "parts") (frozenset #("color" "label"))) (repr store.indexes.scoped))
        (setv #(_row _color by-note) (reads-of quick ddl store))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (assert (is (run-on quick ddl (index-round store)) True))
        (assert (= (.scoped-fields store.indexes "parts") (frozenset #("color" "label" "note"))) (repr store.indexes.scoped))
        (setv #(_row _color note-after) (reads-of quick ddl store))
        (assert (= (listed-keys note-after) #(#("p1"))) (repr note-after))
        (finally
          (.close watcher))))))


(deftest test-a-half-built-index-is-dropped-and-built-again
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; CONCURRENTLY の作りかけ(cancel された作りが残す使えない索引)は、差の中で invalid と数え、消してから作り直す。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            writer (open-postgres)
            builder (open-postgres)
            watcher (open-postgres))
      (try
        (hold-writer writer prefix)
        (.execute builder "SET statement_timeout = 300")
        (try
          (.execute builder (.format "CREATE INDEX CONCURRENTLY {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END), updated_at)"
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


(defn released-with-notice [other store]  ; defk にできない: 検の素の接続と別の run で錠を放す代役
  "別の process の代役の接続 other が持つ索引の用意の錠を放し、本番の作り手と同じ合図(SqlNotify — 別の接続の貸し出しなので別の process の
   合図として届く)を出す。"
  (.execute other "SELECT pg_advisory_unlock(hashtext(%s))" #((run (index-lock-key store.prefix))))
  (setv notifier (quick-connections))
  (try
    (run (scheduled (with_handlers [(postgres-sql-handler notifier)]
                                   (SqlNotify DATABASE (run (notice-channel store)) #(INDEX-TOPIC)))))
    (finally (.close notifier))))


(deftest test-the-round-waits-for-the-index-lock-held-by-another-process
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 同じ置き場の別の process(本体と twin)が索引の用意の錠を持つ間、回は作らずに待ち(錠を PostgreSQL の錠の待ちで待たない)、錠を放した
  ;; 合図で観測し直して作る。待つ間に別の process が作った索引は作らない(作り直しの文が流れても IF NOT EXISTS で何もしない)。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            other (open-postgres)
            watcher (open-postgres)
            pool (ThreadPoolExecutor :max-workers 1))
      (try
        (.execute other "SELECT pg_advisory_lock(hashtext(%s))" #((run (index-lock-key prefix))))
        (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (setv rounding (.submit pool (fn [] (run-on quick ddl (index-round store)))))
        (time.sleep 1.0)
        (assert (not (.done rounding)) (repr (.result rounding)))
        (assert (is (index-validity watcher note-index) None) "錠を持たれた間に索引を作った")
        (released-with-notice other store)
        (assert (is (.result rounding 60) True) "錠が外れた後の回が埋めなかった")
        (assert (is (index-validity watcher note-index) True) note-index)
        (finally
          (.shutdown pool)
          (.close other)
          (.close watcher))))))


(defn deadlocks [watcher]  ; defk にできない: 検の素の接続で数える
  "この database で PostgreSQL が見つけた deadlock の数(pg_stat_database)。"
  (get (.fetchone (.execute watcher "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")) 0))


(defn build-beside-a-lock-seeker [prefix quick ddl seek]  ; defk にできない: 回を thread の run で回し、書きの代役を素の接続で流す入口
  "回 A が索引の用意の錠を取って作り始め、作りが書きの代役を待つ間に、錠を求める相手 seek(→ 結末を返す関数)を走らせ、相手が錠を求め
   始めてから 2.5 秒(待つ側の deadlock の検めが済む 1 秒より長い)後に代役を外す。#(A の回の答え 相手の結末 その間の deadlock の数) を返す。"
  (setv store (run-on quick ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix))
        writer (open-postgres)
        watcher (open-postgres)
        pool (ThreadPoolExecutor :max-workers 2))
  (try
    (setv before (deadlocks watcher)
          writer-pid (hold-writer writer prefix)
          building (.submit pool (fn [] (run-on quick ddl (index-round store)))))
    (wait-until-blocked watcher writer-pid)
    (setv seeking (.submit pool seek))
    (time.sleep 2.5)
    (.execute writer "COMMIT")
    (setv built (.result building 60)
          sought (.result seeking 60))
    #(built sought (- (deadlocks watcher) before))
    (finally
      (.shutdown pool)
      (.close writer)
      (.close watcher))))


(deftest test-a-build-is-not-lost-to-a-deadlock-while-another-process-seeks-the-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 本体と twin が同時に索引の用意に入る配備の代役: A が作る間に B(別の接続の貸し出し)の回が錠を求める。B は作らずに A の合図を待ち、
  ;; A の作りは捨てられない。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv other-quick (quick-connections) other-ddl (ddl-connections))
      (try
        (setv twin (run-on other-quick other-ddl (prepare-records-store DATABASE DDL-DATABASE NOTE-SCHEMA prefix)))
        (setv #(built sought found) (build-beside-a-lock-seeker prefix quick ddl
                                                                (fn [] (run-on other-quick other-ddl (index-round twin)))))
        (assert (is built True) "錠を持つ回の作りが捨てられた")
        (assert (is sought True) "錠を求めた回が差を埋めずに終わった")
        (assert (= found 0) found)
        (with [watcher (open-postgres)]
          (assert (is (index-validity watcher (run (table-field-index-name prefix "parts" "note"))) True)))
        (finally
          (.close other-quick)
          (.close other-ddl))))))


(deftest test-a-build-is-lost-to-a-deadlock-when-another-process-waits-on-the-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 反例: 前の待ち方(SELECT pg_advisory_lock で外れるまで待つ)の相手が居ると、A の CREATE INDEX CONCURRENTLY の最後の待ちが相手の
  ;; snapshot を待って輪になり、A の作りが 40P01 で捨てられる(「捨てられない」の判定が何も確かめずに緑になる形を外す)。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv seeker (open-postgres))
      (defn seek []  ; defk にできない: thread の target
        (.execute seeker "SELECT pg_advisory_lock(hashtext(%s))" #((run (index-lock-key prefix))))
        (.execute seeker "SELECT pg_advisory_unlock(hashtext(%s))" #((run (index-lock-key prefix))))
        True)
      (try
        (setv #(built sought found) (build-beside-a-lock-seeker prefix quick ddl seek))
        (assert (is built False) "前の待ち方の相手が居ても作りが通った")
        (assert (>= found 1) found)
        (finally (.close seeker))))))


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
      (.put ended (run (records-process (fn [program] (scheduled (with_handlers [await-handler (state) (async-time-handler) (stop-when stopping)]
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
  (run (scheduled (with_handlers [await-handler (http-production-handler) (sim-time-handler :clock (SimClock)) records-unwaited
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


(deftest test-the-service-drops-former-indexes-when-no-declared-index-is-missing
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 宣言の索引が全部在り見積もれる置き場(2026-10-19 の本番の形)に旧い形の索引が残っていれば、記録の service は索引の用意の task を
  ;; 立て(差が無くても)、起動の道の外で外す。外す間も外した後も記録の操作に答え、service は落ちない。
  (with-seeded-store
    (fn [prefix quick ddl]
      (setv note-index (run (table-field-index-name prefix "parts" "note"))
            watcher (open-postgres)
            opened (queue.Queue)
            ended (queue.Queue)
            stopping (threading.Event))
      (try
        (.execute watcher (.format "CREATE INDEX {i} ON {p}state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'note') END), updated_at)"
                                   :i note-index :p prefix))
        (.execute watcher (.format "ANALYZE {}state_rows" prefix))
        (setv #(legacy partial hand) (create-former-indexes watcher prefix))
        (setv thread (serve-in-thread prefix opened stopping ended))
        (setv bound (.get opened :timeout 30))
        (assert (isinstance bound HttpAddress) (repr bound))
        (setv url (.format "http://{}:{}" bound.host bound.port)
              deadline (+ (time.monotonic) DEADLINE-SECONDS))
        (while (and (or (is-not (index-validity watcher legacy) None) (is-not (index-validity watcher partial) None))
                    (< (time.monotonic) deadline))
          (time.sleep POLL-SECONDS))
        (assert (is (index-validity watcher legacy) None) "記録の service が旧い形の索引を外さなかった")
        (assert (is (index-validity watcher partial) None) "記録の service が前の部分索引を外さなかった")
        (assert (is (index-validity watcher hand) True) "旧い形でない手の索引を外した")
        (setv by-note (over-http url (ListRows "parts" :where {"note" "n1"})))
        (assert (= (listed-keys by-note) #(#("p1"))) (repr by-note))
        (assert (.is-alive thread) "記録の service が終わった")
        (.set stopping)
        (assert (= (.get ended :timeout 30) 0) "記録の service が 0 で終わらなかった")
        (finally
          (.set stopping)
          (.close watcher))))))
