;; #3614: PostgreSQL の置き場で、期限の境・組・変更の列の刻・終端の状態で引く文が、表の行の数に比例して読まない事。
;; 使い捨ての PostgreSQL の置き場(LAW-SCHEMA — 組で数える列 pairs・出来事ごとに数える列 pulses・期限の在る表 tickets)に、文が拾わない行
;; (期限の前の出来事・終端でない行・刈らない変更)を N 行と 2N 行置き、統計を取ってから(ANALYZE)文ごとに EXPLAIN (ANALYZE, FORMAT JSON) を
;; 流して、置き場の表を読んだ行の数(走査の節ごとに (返した行 + 条件で落とした行 + 索引の再照合で落とした行) × loops の和)を比べる:
;;   - 2N の置き場でも読んだ行は N の置き場より増えない
;;   - 置き場の表(1 行の表 store_epoch は除く)の Seq Scan が無い
;; 索引の無い前の形(append_rows (ledger, at)・組の名の式・row_changes (at)・状態の欄の式の索引が無く、組の区切りと状態の欄が引数で文の式が
;; 索引の式と揃わない)では、Seq Scan か、列の索引で引いた後の条件の濾しで N に比例して読み、赤になる。
;; 文の答え(どの行を返し・消すか)は変えない — 答えの同じは法の検(test_laws・test_parity_memory_pg)が確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlRows])
(import doeff_core_effects.postgres_sql [PostgresConnections])
(import doeff_records.pg [drop-records-tables])
(import doeff_records.pg_sql [Statement expiring-events-statement expire-events-statement expire-event-groups-statement
                              expire-touched-events-statement prune-changes-statement terminal-rows-statement])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST postgres-connections fresh-prefix run-sql
                            prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 文が拾わない行の数(小さい置き場)。大きい置き場は 2 倍。
(val BASE-ROWS 2000)
;; 刻(epoch ミリ秒): 境 BEFORE = 今 − 保持の秒。YOUNG は境より後(期限の前)・OLD は境以前。
(val NOW 10000000)
(val BEFORE (- NOW 60000))
(val YOUNG (- NOW 1000))
(val OLD (- BEFORE 1000))
;; 組の古い要求と新しい結末の組(期限の境以前の出来事を持つが、組としては期限の前 — 回収も片付けも捨てない)の数と、終端の行の数。
(val MIXED-GROUPS 2)
(val DONE-ROWS 2)
;; 走査の節の種類(Bitmap Index Scan は Bitmap Heap Scan と同じ行を数えるので数えない)と、数えない 1 行の表。
(val SCANS #("Seq Scan" "Index Scan" "Index Only Scan" "Bitmap Heap Scan"))
(val ONE-ROW-TABLE "store_epoch")


(defk event-rows-statement [prefix stream head count at]
  {:pre [(: prefix str) (: stream str) (: head str) (: count int) (: at int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "列 stream に冪等キー head1 … head{count} の出来事を刻 at で count 個まとめて置く文を作るため(置き場の書きの手順を通さず、文 1 つで
   大きな置き場を作る — 読んだ行の数を測る前の用意)。"
  (Statement :text (.format "INSERT INTO {p}append_rows (ledger, at, payload, origin_host, epoch)
                       SELECT CAST(:ledger AS text), CAST(:at AS bigint),
                              json_build_object('idempotencyKey', CAST(:head AS text) || g, 'writer', 'maker',
                                                'body', json_build_object('n', g))::text,
                              CAST(:origin AS text), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("ledger" stream) #("at" at) #("head" head) #("origin" ORIGIN-HOST) #("count" count))
                                  (SqlParam :name name :value value)))))


(defk ticket-rows-statement [prefix head count state at]
  {:pre [(: prefix str) (: head str) (: count int) (: state str) (: at int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 tickets に状態 state の行を count 行(鍵 head1 …・最後に書いた刻 at)まとめて置く文を作るため。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT 'tickets', CAST(:head AS text) || g,
                              json_build_object('group', 'g', 'id', CAST(:head AS text) || g, 'state', CAST(:state AS text))::text,
                              1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("head" head) #("count" count) #("state" state) #("at" at) #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk change-rows-statement [prefix count at]
  {:pre [(: prefix str) (: count int) (: at int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "変更の列に刻 at の変更を count 個まとめて積む文を作るため。"
  (Statement :text (.format "INSERT INTO {p}row_changes (ledger, key, version, payload, at, epoch)
                       SELECT 'parts', 'k' || g, 1, json_build_object('n', g)::text, CAST(:at AS bigint), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("at" at)) (SqlParam :name name :value value)))))


(defk seed-statements [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "文が拾わない行 count 個ずつと、数の決まった拾う候補の行を置き、統計を取る文の列を作るため: pulses に期限の前の出来事 count 個・pairs に
   期限の前の組 count 組(ask: と done:)と古い要求に新しい結末の付いた組 MIXED-GROUPS 組・tickets に終端でない行 count 行と期限を過ぎた
   終端の行 DONE-ROWS 行・変更の列に刈らない変更 count 個。"
  (<- pulses (event-rows-statement prefix "pulses" "pulse-" count YOUNG))
  (<- asks (event-rows-statement prefix "pairs" "ask:" count YOUNG))
  (<- dones (event-rows-statement prefix "pairs" "done:" count YOUNG))
  (<- mixed-asks (event-rows-statement prefix "pairs" "ask:m-" MIXED-GROUPS OLD))
  (<- mixed-dones (event-rows-statement prefix "pairs" "done:m-" MIXED-GROUPS YOUNG))
  (<- open-rows (ticket-rows-statement prefix "open-" count "open" YOUNG))
  (<- done-rows (ticket-rows-statement prefix "done-" DONE-ROWS "done" OLD))
  (<- changes (change-rows-statement prefix count YOUNG))
  #(pulses asks dones mixed-asks mixed-dones open-rows done-rows changes
    (Statement :text (.format "ANALYZE {p}state_rows, {p}append_rows, {p}row_changes" :p prefix) :params #())))


(defk measured-statements [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "測る文を #(名 文) で並べるため(読みを先に・消す文を後に — 消す文も EXPLAIN ANALYZE は本当に流す)。組の片付けの鍵は古い要求に新しい
   結末の付いた組の結末(受付の列で一番多い、要求の後の結末の追記の形)。"
  (<- pulse-probe (expiring-events-statement prefix "pulses" BEFORE None))
  (<- pair-probe (expiring-events-statement prefix "pairs" BEFORE ":"))
  (<- terminal (terminal-rows-statement prefix "tickets" "state" #("done")))
  (<- touched (expire-touched-events-statement prefix "pairs" BEFORE ":" "done:m-1"))
  (<- pulse-sweep (expire-events-statement prefix "pulses" BEFORE))
  (<- pair-sweep (expire-event-groups-statement prefix "pairs" BEFORE ":"))
  (<- prune (prune-changes-statement prefix BEFORE))
  #(#("出来事ごとの列の回収の候補の読み" pulse-probe)
    #("組で数える列の回収の候補の読み" pair-probe)
    #("期限の在る表の終端の行の読み" terminal)
    #("組で数える列への追記が触る組の片付け" touched)
    #("出来事ごとの列の回収" pulse-sweep)
    #("組で数える列の回収" pair-sweep)
    #("変更の列の刈り" prune)))


(defk plan-nodes [root]
  {:pre [(: root dict)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "計画の木 root(EXPLAIN の JSON の Plan)の節を、根から幅の順に全部並べるため(InitPlan・SubPlan・CTE の節も Plans の下に在る)。"
  (var nodes #(root))
  (var index 0)
  (while (< index (len nodes))
    (:= nodes (+ nodes (tuple (.get (get nodes index) "Plans" #()))))
    (:= index (+ index 1)))
  nodes)


(defk table-scans [prefix explained]
  {:pre [(: prefix str) (: explained list)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "EXPLAIN (ANALYZE, FORMAT JSON) の答えから、置き場の表(接頭辞 prefix・1 行の表は除く)を読んだ走査の節を #(節の種類 表の名 読んだ行) で
   並べるため。読んだ行 = (返した行 + 条件で落とした行 + 索引の再照合で落とした行) × loops。"
  (<- nodes (plan-nodes (get explained 0 "Plan")))
  (tuple (gfor node nodes
               :if (and (in (get node "Node Type") SCANS)
                        (.startswith (.get node "Relation Name" "") prefix)
                        (!= (.get node "Relation Name") (+ prefix ONE-ROW-TABLE)))
               #((get node "Node Type") (get node "Relation Name")
                 (* (+ (get node "Actual Rows") (.get node "Rows Removed by Filter" 0) (.get node "Rows Removed by Index Recheck" 0))
                    (get node "Actual Loops"))))))


(defk explained-scans [prefix statement]
  {:pre [(: prefix str) (: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "文 statement を EXPLAIN (ANALYZE, FORMAT JSON) つきで本物の答え手に流し、置き場の表を読んだ走査の節(table-scans)を返すため。"
  (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
  (assert (isinstance answer SqlRows) (repr answer))
  (val plan (get answer.rows 0 0))
  (<- scans (table-scans prefix (if (isinstance plan str) (json.loads plan) plan)))
  scans)


(defk reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix に count 行ずつ置き、測る文ごとの走査の節を {名 走査の節の組} で返すため。"
  (<- seeds (seed-statements prefix count))
  (for [seed seeds]
    (<- seeded (SqlQuery DATABASE seed.text seed.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (<- measured (measured-statements prefix))
  (var reads {})
  (for [#(name statement) measured]
    (<- scans (explained-scans prefix statement))
    (:= reads (| reads {name scans})))
  reads)


(defk reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して count 行ずつ置き、文ごとの走査の節を返すため(終わりに表を消す)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var reads {})
  (try
    (:= reads (run-sql connections (reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  reads)


(defk total-read [scans]
  {:pre [(: scans tuple)] :post [(: % int)]
   :tags {:context "records" :role "program"}}
  "走査の節の読んだ行の和を作るため。"
  (sum (gfor #(_ _ read) scans read)))


(deftest test-statements-by-the-retention-indexes-read-no-more-rows-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (reads-at connections BASE-ROWS))
    (<- large (reads-at connections (* 2 BASE-ROWS)))
    (var seen {})
    (for [name (sorted small)]
      (<- small-read (total-read (get small name)))
      (<- large-read (total-read (get large name)))
      (:= seen (| seen {name #(small-read large-read (tuple (gfor #(kind table _) (get large name) #(kind table))))})))
    ;; 置き場の表を頭から読む節が無い(文の条件が索引に当たる)。
    (assert (not (lfor #(name scans) (.items large) #(kind table _) scans :if (= kind "Seq Scan") #(name table))) (repr seen))
    ;; 2 倍の置き場でも読んだ行は増えない(読むのは拾う候補の行だけ — 拾わない行の数に依らない)。
    (assert (all (gfor #(small-read large-read _) (.values seen) (<= large-read small-read))) (repr seen))
    (finally
      (.close connections))))
