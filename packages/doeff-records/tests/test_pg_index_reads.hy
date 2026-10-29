;; #3614: PostgreSQL の置き場で、期限の境・組・変更の列の刻・終端の状態・冪等キー(#3750)で引く文が、表の行の数に比例して読まない事。
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
(import doeff_records._pg [drop-records-tables EXPIRE-BATCH PRUNE-BATCH])
(import doeff_records._admission [canonical-json])
(import doeff_records [ListCursor])
(import doeff_records._pg_sql [Statement EventExpiry TailRead expiring-candidates-statement expire-chosen-events-statement
                              touched-events-statement prune-bound-statement prune-changes-statement UNBOUNDED-SEQ terminal-rows-statement watch-head-statement
                              event-by-key-statement list-rows-statement top-rows-statement table-field-index-name
                              ordered-index-name ordered-expression ordered-within-index-name ordered-within-index table-field-expression
                              declared-indexes retired-index?])
(import doeff_records._laws [LAW-SCHEMA])
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
;; 回収の候補の読みの上限(置き場の回収と同じ数)。
(val CANDIDATE-LIMIT EXPIRE-BATCH)


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


(defk sweep-sequences [prefix stream]
  {:pre [(: prefix str) (: stream str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "回収の錠の中の文(expire-chosen-events-statement)に渡す番号を作るため: 列 stream の境以前の出来事(pairs では古い要求に新しい結末の
   付いた組の古い要求 — 組の照らしを索引で引く事も測る)と、列の最初の出来事(期限の前 — 条件の読み直しが落とす)。"
  (<- answer (SqlQuery DATABASE (.format "SELECT seq FROM {p}append_rows WHERE ledger = :ledger AND at <= :before_at
                                          UNION SELECT min(seq) FROM {p}append_rows WHERE ledger = :ledger ORDER BY 1" :p prefix)
                       #((SqlParam :name "ledger" :value stream) (SqlParam :name "before_at" :value BEFORE))))
  (assert (isinstance answer SqlRows) (repr answer))
  (tuple (gfor record answer.rows (int (get record 0)))))


(defk measured-statements [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "測る文を #(名 文) で並べるため(読みを先に・消す文を後に — 消す文も EXPLAIN ANALYZE は本当に流す)。組の片付けの鍵は古い要求に新しい
   結末の付いた組の結末(受付の列で一番多い、要求の後の結末の追記の形)。"
  (<- pulse-probe (expiring-candidates-statement prefix "pulses" BEFORE None CANDIDATE-LIMIT))
  (<- pair-probe (expiring-candidates-statement prefix "pairs" BEFORE ":" CANDIDATE-LIMIT))
  (<- terminal (terminal-rows-statement prefix "tickets" "state" #("done") True BEFORE))
  (<- touched (touched-events-statement prefix "pairs" BEFORE ":" "done:m-1"))
  ;; WatchChanges の頭の文に列の末尾を並べた形(#3718 — 列ごとの副問い合わせ stream-tail-statement): (ledger, seq) の索引を後ろから 1 行。
  ;; pulses の出来事は pairs より前に積んである — 主鍵 seq を後ろから読む道なら pairs の出来事を全部読む(等号の照らしの形がそうだった)。
  ;; 組で数える列(pairs)の末尾も並べる: 期限の条件(living-events-filter — ReadEvents と同じ)の組の照らしは値の副問い合わせで、
  ;; 出来事ごとに組の名の式の索引を引く(NOT EXISTS の形は hashed SubPlan の Seq Scan に計画された — pg_sql.expired-event-condition)。
  (val pulse-expiry (EventExpiry :before-at BEFORE :separator None))
  (<- watch-head (watch-head-statement prefix #((TailRead :stream "pulses" :expiry pulse-expiry)
                                                (TailRead :stream "pairs" :expiry (EventExpiry :before-at BEFORE :separator ":"))
                                                (TailRead :stream "journal" :expiry None))))
  ;; 冪等キー 1 つの読み(#3750 — ReadEventByKey): 一意の索引 (ledger, 冪等キー) で 1 行を引き、期限の判定はその 1 行についてだけ見る
  ;; (組で数える列は組の名の式の索引)。鍵は拾わない行の中の 1 つ(置き場が大きくても読む行は変わらない)。
  (<- pulse-key (event-by-key-statement prefix "pulses" "pulse-7" pulse-expiry))
  (<- pair-key (event-by-key-statement prefix "pairs" "done:m-1" (EventExpiry :before-at BEFORE :separator ":")))
  ;; 回収の錠の中の文は候補の番号で捨てる(この置き場に期限を過ぎた出来事は無い — 錠の中の条件の読み直しが落とす番号を渡す)。
  (<- pulse-chosen (sweep-sequences prefix "pulses"))
  (<- pair-chosen (sweep-sequences prefix "pairs"))
  (<- pulse-sweep (expire-chosen-events-statement prefix "pulses" BEFORE None pulse-chosen))
  (<- pair-sweep (expire-chosen-events-statement prefix "pairs" BEFORE ":" pair-chosen))
  ;; 変更の列の刈り(pg_sql の頭の註の「変更の列の刈り」): 錠の外の境の読み(刻の索引を頭から PRUNE-BATCH 行)と、錠の中の番号の
  ;; DELETE(主鍵を頭から PRUNE-BATCH 行 — 境は境の読みの答え)。
  (<- prune-bound (prune-bound-statement prefix BEFORE UNBOUNDED-SEQ PRUNE-BATCH))
  (<- bound (SqlQuery DATABASE prune-bound.text prune-bound.params))
  (assert (isinstance bound SqlRows) (repr bound))
  (<- prune (prune-changes-statement prefix (int (get bound.rows 0 0)) PRUNE-BATCH))
  #(#("出来事ごとの列の回収の候補の読み" pulse-probe)
    #("組で数える列の回収の候補の読み" pair-probe)
    #("期限の在る表の終端の行の読み" terminal)
    #("組で数える列への追記が触る組の読み" touched)
    #("待ちの頭と列の末尾の読み" watch-head)
    #("出来事ごとの列の冪等キーの読み" pulse-key)
    #("組で数える列の冪等キーの読み" pair-key)
    #("出来事ごとの列の回収" pulse-sweep)
    #("組で数える列の回収" pair-sweep)
    #("変更の列の刈りの境の読み" prune-bound)
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


;; --- 値の組の読み(ListRows の where-any)--------------------------------------------------------------------------
;; 表 parts の索引の欄 color を値の組 ANY-OF-VALUES 個で絞る 1 頁の読み(list-rows-statement)が、表の欄の式の索引(parts の color)に当たり、
;; 組に当たらない行を読まない事。置き場は行ごとに color の値が違う行を ANY-OF-ROWS 行と 2 倍の行で作り、組は在る値 ANY-OF-VALUES 個。
;; 行の大きさは本番の表 message の行(13,941 行・約 25 MB — 1 行 約 1.8 KB・2026-10-17 の測り)に近づける(欄 note に ANY-OF-NOTE-BLOCKS 個の
;; md5 の綴り)。計画は見積もりで選ばれ、表を頭から読む見積もりは行ごとの payload の jsonb への読み直しを安く数えるので、数十 byte の行
;; だけの置き場では値 500 個の組に Seq Scan が選ばれる(1 万行で 1 頁 約 22 ms — 答えは同じ・2026-10-17 の探り)。
(val ANY-OF-ROWS 10000)
(val ANY-OF-VALUES 500)
(val ANY-OF-NOTE-BLOCKS 50)


(defk part-rows-statement [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 parts に、欄 color の値が行ごとに違う行を count 行(鍵 p1 … ・color c1 …・欄 note は本番の行の大きさに近い md5 の綴りの連なり)
   まとめて置く文を作るため(値の組の読みを測る前の用意)。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT 'parts', '[\"p' || g || '\"]',
                              json_build_object('id', 'p' || g, 'color', 'c' || g, 'state', 'open',
                                                'note', (SELECT string_agg(md5(g || '-' || b), '')
                                                           FROM generate_series(1, CAST(:blocks AS integer)) AS b))::text,
                              1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("blocks" ANY-OF-NOTE-BLOCKS) #("at" YOUNG) #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk any-of-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "値の組の 1 頁の読みの文を作るため: 欄 color が c1 … c{ANY-OF-VALUES} のどれかの行を、鍵の順に ANY-OF-VALUES 行まで(保持の期限の無い表の形)。"
  (<- statement (list-rows-statement prefix "parts" #("id") (frozenset #("color")) None {}
                                     {"color" (tuple (gfor n (range 1 (+ ANY-OF-VALUES 1)) (canonical-json (.format "c{}" n))))}
                                     ANY-OF-VALUES None))
  statement)


(defk index-names [explained]
  {:pre [(: explained list)] :post [(: % frozenset)]
   :tags {:context "records" :role "program"}}
  "EXPLAIN (FORMAT JSON) の答えの計画の木から、引いた索引の名を集めるため(Bitmap Index Scan の節も含む)。"
  (<- nodes (plan-nodes (get explained 0 "Plan")))
  (frozenset (gfor node nodes :if (in "Index Name" node) (get node "Index Name"))))


(defk any-of-reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix の表 parts に count 行を置いて統計を取り、値の組の 1 頁の読みの #(走査の節 引いた索引の名 返した行の数) を返すため。"
  (<- seed (part-rows-statement prefix count))
  (for [statement #(seed (Statement :text (.format "ANALYZE {p}state_rows" :p prefix) :params #()))]
    (<- seeded (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (<- statement (any-of-statement prefix))
  (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
  (assert (isinstance answer SqlRows) (repr answer))
  (val plan (get answer.rows 0 0))
  (val explained (if (isinstance plan str) (json.loads plan) plan))
  (<- scans (table-scans prefix explained))
  (<- names (index-names explained))
  (<- listed (SqlQuery DATABASE statement.text statement.params))
  (assert (isinstance listed SqlRows) (repr listed))
  #(scans names (len listed.rows)))


(defk any-of-reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して表 parts に count 行を置き、値の組の読みを測るため(終わりに表を消す)。
   答え = #(走査の節 引いた索引の名 返した行の数 欄 color の索引の名)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var measured #())
  (try
    (:= measured (run-sql connections (any-of-reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  (<- color-index (table-field-index-name store.prefix "parts" "color"))
  (+ measured #(color-index)))


(deftest test-a-list-by-a-value-set-reads-through-the-field-index-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (any-of-reads-at connections ANY-OF-ROWS))
    (<- large (any-of-reads-at connections (* 2 ANY-OF-ROWS)))
    (for [#(scans names returned color-index) #(small large)]
      ;; 1 頁 = 組に当たる行 ANY-OF-VALUES 行(続きの有無を知るための 1 行は当たる行が無いので出ない)。
      (assert (= returned ANY-OF-VALUES) (repr #(returned scans)))
      ;; 欄の式の索引を引く・置き場の表を頭から読む節が無い。
      (assert (in color-index names) (repr #(color-index names scans)))
      (assert (not (lfor #(kind table _) scans :if (= kind "Seq Scan") table)) (repr scans)))
    ;; 2 倍の置き場でも読んだ行は増えない(読むのは組に当たる行だけ — 当たらない行の数に依らない)。
    (<- small-read (total-read (get small 0)))
    (<- large-read (total-read (get large 0)))
    (assert (<= large-read small-read) (repr #(small-read large-read small large)))
    (finally
      (.close connections))))


;; --- 鍵の欄の絞り(where / where-any)は主キーの列で引く -----------------------------------------------------------
;; 鍵の欄が 1 つの表の鍵の欄を文字列で名指す絞りは、欄の式でなく主キー (ledger, key) の列 key の綴り(key-text)で照らす(表を頭から
;; 読み直す走査にしない)。鍵の欄が 2 つの表・鍵でない欄・文字列でない値の組は今の欄の式のまま。
(deftest test-a-key-field-filter-is-read-through-the-key-column
  (<- by-set (list-rows-statement "records_" "message" #("id") (frozenset) None {} {"id" #((canonical-json "m1") (canonical-json "é"))} 10 None))
  (assert (in "AND key IN (:a0_0, :a0_1)" by-set.text) by-set.text)
  (assert (not (in "payload" (get (.split by-set.text "WHERE") 1))) by-set.text)
  (assert (= (lfor p by-set.params :if (.startswith p.name "a0_") p.value) ["[\"m1\"]" "[\"\\u00e9\"]"]) by-set.params)
  (<- by-one (list-rows-statement "records_" "message" #("id") (frozenset) None {"id" (canonical-json "m1")} {} 10 None))
  (assert (in "key = :w0" by-one.text) by-one.text)
  (assert (= (lfor p by-one.params :if (= p.name "w0") p.value) ["[\"m1\"]"]) by-one.params)
  (<- top (top-rows-statement "records_" "message" #("id") (frozenset) "n" False None {"id" (canonical-json "m1")} {} 10 None))
  (assert (in "key = :w0" top.text) top.text)
  ;; 文字列でない値が混ざる組・鍵でない欄・鍵の欄が 2 つの表は欄の式のまま。
  (<- mixed (list-rows-statement "records_" "message" #("id") (frozenset) None {} {"id" #((canonical-json "m1") (canonical-json 2))} 10 None))
  (assert (in "((payload::jsonb) -> 'id') IN" mixed.text) mixed.text)
  (<- other (list-rows-statement "records_" "message" #("id") (frozenset) None {} {"kind" #((canonical-json "a"))} 10 None))
  (assert (in "((payload::jsonb) -> 'kind') IN" other.text) other.text)
  (<- pair (list-rows-statement "records_" "tickets" #("group" "id") (frozenset) None {} {"id" #((canonical-json "a"))} 10 None))
  (assert (in "((payload::jsonb) -> 'id') IN" pair.text) pair.text))


;; --- 当たる行の少ない絞り(等号 2 つ・値の組と値の組)は当たる行だけを読む(card ki-2176012d1804)-----------------------------------
;; 本番の表 message(2026-10-18 — 44,296 行)で、宛先 1 人の行 5,283 行のうち kind = ask の 34 行を等号 2 つで読む 1 頁が、宛先の索引で
;; 5,283 行を引いて 1 行ずつ payload を jsonb に読み直し、kind で 5,249 行を捨てた(0.43 秒)。2 つの欄の索引が同じ ledger の条件を使うと
;; planner は索引を組まない(pg_sql の頭の註)。表 parts の索引の欄 label(宛先の代役)と color(kind の代役)で同じ形を作る: label = "hot" の
;; 行と color = "ask" の行がそれぞれ表の 1/5 で、両方に合う行は PAIR-HITS 行(表の大きさに依らない)。値の組と値の組の読み(in_reply_to の組
;; 995 個と kind の組 5 個の代役)は、color の値の組 SET-VALUES 個(行ごとに違う値)と label の組 SET-LABELS。置き場を PAIR-ROWS 行と 2 倍の
;; 行で作り、読んだ行の数が 2 倍の置き場でも増えない(当たる行の数で決まる)事を確かめる。前の形では、等号 2 つの読みは片方の欄の行
;; (表の 1/5)を全部読み、2 倍の置き場で倍に読んで赤になる。
(val PAIR-ROWS 10000)
(val PAIR-HITS 32)
(val PAIR-PAGE 500)
(val SET-VALUES 995)
(val SET-LABELS #("l0" "l1" "l2" "l3" "l4"))
;; 表 parts の表の欄の式の索引が在る欄(新しい置き場は表の用意で作る)。
(val PARTS-SCOPED (frozenset #("color" "label")))


(defk pair-rows-statement [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 parts に count 行(鍵 q1 …)をまとめて置く文を作るため: 行 g の label は g が 5 の倍数なら hot・他は l{g mod 7}、color は g mod 5 = 1
   の行と hot の行の頭の PAIR-HITS 行が ask・他は行ごとに違う c{g}。欄 note は本番の行の大きさに近い md5 の綴りの連なり。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT 'parts', '[\"q' || g || '\"]',
                              json_build_object('id', 'q' || g,
                                                'label', CASE WHEN g % 5 = 0 THEN 'hot' ELSE 'l' || (g % 7) END,
                                                'color', CASE WHEN g % 5 = 1 OR (g % 5 = 0 AND g <= 5 * CAST(:hits AS integer)) THEN 'ask'
                                                              ELSE 'c' || g END,
                                                'state', 'open',
                                                'note', (SELECT string_agg(md5(g || '-' || b), '')
                                                           FROM generate_series(1, CAST(:blocks AS integer)) AS b))::text,
                              1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("hits" PAIR-HITS) #("blocks" ANY-OF-NOTE-BLOCKS) #("at" YOUNG)
                                                  #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk pair-statements [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "測る 2 つの読みを #(名 文) で並べるため: 等号 2 つ(label = hot かつ color = ask)の 1 頁と、color の値の組 SET-VALUES 個(hot でも ask でも
   ない行の値)かつ label の組 SET-LABELS の 1 頁(どちらも PAIR-PAGE 行まで・表の欄の式で照らす形)。"
  (val colors (tuple (gfor g (cut (lfor g (range 1 (* 4 SET-VALUES)) :if (in (% g 5) #(2 3 4)) g) SET-VALUES)
                           (canonical-json (.format "c{}" g)))))
  (<- pair (list-rows-statement prefix "parts" #("id") PARTS-SCOPED None {"label" (canonical-json "hot") "color" (canonical-json "ask")} {}
                                PAIR-PAGE None))
  (<- sets (list-rows-statement prefix "parts" #("id") PARTS-SCOPED None {}
                                {"color" colors "label" (tuple (gfor label SET-LABELS (canonical-json label)))} PAIR-PAGE None))
  #(#("等号 2 つ" pair) #("値の組と値の組" sets)))


(defk pair-reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix の表 parts に count 行を置いて統計を取り、測る読みごとの #(走査の節 引いた索引の名 返した行の数) を
   {名 …} で返すため。"
  (<- seed (pair-rows-statement prefix count))
  (for [statement #(seed (Statement :text (.format "ANALYZE {p}state_rows" :p prefix) :params #()))]
    (<- seeded (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (<- measured (pair-statements prefix))
  (var reads {})
  (for [#(name statement) measured]
    (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
    (assert (isinstance answer SqlRows) (repr answer))
    (val plan (get answer.rows 0 0))
    (val explained (if (isinstance plan str) (json.loads plan) plan))
    (<- scans (table-scans prefix explained))
    (<- names (index-names explained))
    (<- listed (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance listed SqlRows) (repr listed))
    (:= reads (| reads {name #(scans names (len listed.rows))})))
  reads)


(defk pair-reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して表 parts に count 行を置き、測る読みを測るため(終わりに表を消す)。
   答え = #(読みごとの測り 表の欄の式の索引が在ると覚えた parts の欄 label と color の表の欄の式の索引の名)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var reads {})
  (try
    (:= reads (run-sql connections (pair-reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  (<- label-index (table-field-index-name store.prefix "parts" "label"))
  (<- color-index (table-field-index-name store.prefix "parts" "color"))
  #(reads (.scoped-fields store.indexes "parts") (frozenset #(label-index color-index))))


(deftest test-a-narrow-filter-on-two-fields-reads-only-the-matching-rows-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (pair-reads-at connections PAIR-ROWS))
    (<- large (pair-reads-at connections (* 2 PAIR-ROWS)))
    (for [#(reads scoped field-indexes) #(small large)]
      ;; 新しい置き場は表の用意で表の欄の式の索引を作り、覚えに書く(読みの文が表の欄の式で照らす)。
      (assert (= scoped PARTS-SCOPED) (repr scoped))
      (val pair (get reads "等号 2 つ"))
      (val sets (get reads "値の組と値の組"))
      ;; 等号 2 つ: 当たる PAIR-HITS 行だけを返し、2 つの欄の表の欄の式の索引を組んで引く(片方の欄の行を全部読まない)。
      (assert (= (get pair 2) PAIR-HITS) (repr pair))
      (assert (<= field-indexes (get pair 1)) (repr #(field-indexes pair)))
      (<- pair-read (total-read (get pair 0)))
      (assert (<= pair-read PAIR-HITS) (repr pair))
      ;; 値の組と値の組: 1 頁 + 続きの有無を知る 1 行を返し、置き場の表を頭から読む節が無い。
      (assert (= (get sets 2) (+ PAIR-PAGE 1)) (repr sets))
      (assert (not (lfor #(kind table _) (get sets 0) :if (= kind "Seq Scan") table)) (repr sets)))
    ;; 2 倍の置き場でも読んだ行は増えない(読むのは当たる行と値の組の行だけ — 片方の欄だけに合う行の数に依らない)。
    (for [name #("等号 2 つ" "値の組と値の組")]
      (<- small-read (total-read (get (get small 0) name 0)))
      (<- large-read (total-read (get (get large 0) name 0)))
      (assert (<= large-read small-read) (repr #(name small-read large-read (get (get small 0) name 0) (get (get large 0) name 0)))))
    (finally
      (.close connections))))


(deftest test-a-filter-of-scoped-fields-reads-through-the-table-field-expressions-without-the-ledger-condition
  ;; 絞りの欄が全部表の欄の式の索引の在る欄なら、条件は表の欄の式だけで ledger = :ledger を置かない(pg_sql の頭の註)。表の欄の式の索引が
  ;; まだ無い欄(作り終える前 — 旧い形の索引が答える)・鍵の欄の絞り(主キーの列)が混ざれば ledger = :ledger を置き、その欄は前の形で照らす。
  (val kind (canonical-json "ask"))
  (val recipient (canonical-json "m1"))
  (<- scoped (list-rows-statement "records_" "message" #("id") (frozenset #("kind" "recipient")) "k" {"kind" kind "recipient" recipient} {}
                                  10 None))
  (assert (not (in "ledger = :ledger" scoped.text)) scoped.text)
  (assert (in "(CASE WHEN ledger = 'message' THEN ((payload::jsonb) -> 'kind') END) = :w0::jsonb" scoped.text) scoped.text)
  (assert (in "(CASE WHEN ledger = 'message' THEN ((payload::jsonb) -> 'recipient') END) = :w1::jsonb" scoped.text) scoped.text)
  (assert (in "key COLLATE \"C\" > :after" scoped.text) scoped.text)
  (assert (not-in "ledger" (lfor p scoped.params p.name)) scoped.params)
  (<- any-of (list-rows-statement "records_" "message" #("id") (frozenset #("kind" "in_reply_to")) None {}
                                  {"in_reply_to" #((canonical-json "a") (canonical-json "b")) "kind" #(kind)} 10 None))
  (assert (not (in "ledger = :ledger" any-of.text)) any-of.text)
  (assert (in "(CASE WHEN ledger = 'message' THEN ((payload::jsonb) -> 'in_reply_to') END) IN (:a0_0::jsonb, :a0_1::jsonb)" any-of.text)
          any-of.text)
  (<- top (top-rows-statement "records_" "message" #("id") (frozenset #("kind")) "at" False None {"kind" kind} {} 10 None))
  (assert (not (in "ledger = :ledger" top.text)) top.text)
  (assert (not (in ":after" top.text)) top.text)
  ;; 上から読む読みの続き: (値, 鍵) が位置より小さい行(位置の値は文字列で渡して numeric に読む — int8 を越える整数も)・1 頁 + 1 行。
  (<- below (top-rows-statement "records_" "message" #("id") (frozenset #("kind")) "at" False (ListCursor 1 "[\"m9\"]" (** 10 20)) {"kind" kind} {}
                                10 None))
  (assert (in "< CAST(:after_value AS numeric) OR" below.text) below.text)
  (assert (in "= CAST(:after_value AS numeric) AND key COLLATE \"C\" < :after)" below.text) below.text)
  (assert (= (lfor p below.params :if (in p.name #("after_value" "after" "limit")) #(p.name p.value))
             [#("after_value" "100000000000000000000") #("after" "[\"m9\"]") #("limit" 11)])
          below.params)
  ;; 表の欄の式の索引がまだ無い欄が混ざる。
  (<- partly (list-rows-statement "records_" "message" #("id") (frozenset #("kind")) None {"kind" kind "recipient" recipient} {} 10 None))
  (assert (in "WHERE ledger = :ledger AND (CASE WHEN ledger = 'message' THEN ((payload::jsonb) -> 'kind') END) = :w0::jsonb" partly.text)
          partly.text)
  (assert (in "((payload::jsonb) -> 'recipient') = :w1::jsonb" partly.text) partly.text)
  ;; 鍵の欄の絞り(主キーの列で照らす)が混ざる。
  (<- keyed (list-rows-statement "records_" "message" #("id") (frozenset #("kind")) None {"kind" kind} {"id" #((canonical-json "m1"))} 10 None))
  (assert (in "WHERE ledger = :ledger AND " keyed.text) keyed.text)
  (assert (in "key IN (:a0_0)" keyed.text) keyed.text)
  ;; 絞りの無い読みは表の行に限る条件だけ。
  (<- bare (list-rows-statement "records_" "message" #("id") (frozenset #("kind")) None {} {} 10 None))
  (assert (in "WHERE ledger = :ledger ORDER BY" bare.text) bare.text))


;; --- 上から読む読み(ListRows の descending-by)は、上から読む欄の並べる式の索引を 1 頁だけ辿る -------------------------------------
;; 2026-10-19: 本番の表 message(4.7 万行・1 行 約 1.5 KB)を欄 at の新しい順に読む 1 頁が、表の全部の行の payload を jsonb に読み直して
;; 並べ替え(同じ規模の置き場で 1 頁 0.6〜0.8 秒)、続きを辿る読みが負荷の下で読みの期限を越えた。表 parts の上から読む欄 note
;; (LAW-SCHEMA の descending)の並べる式の索引で、上からの 1 頁と、続きの位置(真ん中)からの 1 頁が、置き場を TOP-ROWS 行と 2 倍の行で作っても
;; 読んだ行を増やさない事を確かめる。置き場には並べない行(note が文字列の行 — 10 行に 1 行・他の表 tickets の note が整数の行)を混ぜ、
;; 答えの行が並べる行だけな事も見る。前の形(全部の行の payload の式で並べる・続きの位置を OR で照らす)では Seq Scan か、表の行を全部
;; 読んで並べ替え、2 倍の置き場で倍に読んで赤になる。
(val TOP-ROWS 4000)
(val TOP-PAGE 50)
(val TOP-NOTE-BLOCKS 10)


(defk top-rows-seed-statement [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 parts に count 行(鍵 t1 …・note は g・g が 10 の倍数の行は文字列 n{g}・label は本番の行の大きさに近い md5 の綴りの連なり)と、
   表 tickets に note が整数の行 count 行をまとめて置く文を作るため。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT t.ledger, t.key, t.payload, 1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM (SELECT 'parts' AS ledger, '[\"t' || g || '\"]' AS key,
                                      json_build_object('id', 't' || g, 'color', 'c', 'state', 'open',
                                                        'note', CASE WHEN g % 10 = 0 THEN to_jsonb('n' || g) ELSE to_jsonb(g) END,
                                                        'label', (SELECT string_agg(md5(g || '-' || b), '')
                                                                    FROM generate_series(1, CAST(:blocks AS integer)) AS b))::text AS payload
                                 FROM generate_series(1, CAST(:count AS integer)) AS g
                               UNION ALL
                               SELECT 'tickets', '[\"g\", \"k' || g || '\"]',
                                      json_build_object('group', 'g', 'id', 'k' || g, 'state', 'open', 'note', CAST(:count AS integer) + g)::text
                                 FROM generate_series(1, CAST(:count AS integer)) AS g) AS t" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("blocks" TOP-NOTE-BLOCKS) #("at" YOUNG) #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk top-keys-expected [high]
  {:pre [(: high int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "note の値 high 以下で並べる行(10 の倍数でない g)の鍵を、大きい順に TOP-PAGE + 1 行並べるため(1 頁 + 続きの有無を知る 1 行)。"
  (tuple (cut (lfor g (range high 0 -1) :if (% g 10) (json.dumps [(.format "t{}" g)])) (+ TOP-PAGE 1))))


(defk top-reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix に count 行ずつ置いて統計を取り、上からの 1 頁と真ん中からの 1 頁の #(走査の節 引いた索引の名 返した鍵 期待の鍵)
   を {名 …} で返すため。"
  (<- seed (top-rows-seed-statement prefix count))
  (for [statement #(seed (Statement :text (.format "ANALYZE {p}state_rows" :p prefix) :params #()))]
    (<- seeded (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (val middle (+ (// count 2) 1))
  (<- head (top-rows-statement prefix "parts" #("id") (frozenset) "note" True None {} {} TOP-PAGE None))
  (<- below (top-rows-statement prefix "parts" #("id") (frozenset) "note" True (ListCursor 1 (json.dumps [(.format "t{}" middle)]) middle)
                                {} {} TOP-PAGE None))
  (<- head-expected (top-keys-expected count))
  (<- below-expected (top-keys-expected (- middle 1)))
  (var reads {})
  (for [#(name statement expected) #(#("上から" head head-expected) #("真ん中から" below below-expected))]
    (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
    (assert (isinstance answer SqlRows) (repr answer))
    (val plan (get answer.rows 0 0))
    (val explained (if (isinstance plan str) (json.loads plan) plan))
    (<- scans (table-scans prefix explained))
    (<- names (index-names explained))
    (<- listed (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance listed SqlRows) (repr listed))
    (:= reads (| reads {name #(scans names (tuple (gfor record listed.rows (get record 0))) expected)})))
  reads)


(defk top-reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して count 行ずつ置き、上から読む読みを測るため(終わりに表を消す)。
   答え = #(読みごとの測り parts の note の並べる式の索引の名)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var reads {})
  (try
    (:= reads (run-sql connections (top-reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  (<- note-index (ordered-index-name store.prefix "parts" "note"))
  #(reads note-index))


(deftest test-a-descending-page-reads-through-the-ordered-index-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (top-reads-at connections TOP-ROWS))
    (<- large (top-reads-at connections (* 2 TOP-ROWS)))
    (for [#(reads note-index) #(small large)
          name #("上から" "真ん中から")]
      (setv [scans names keys expected] (get reads name))
      ;; 並べる行(note が整数の parts の行)だけを、(値, 鍵) の大きい順に 1 頁 + 1 行。
      (assert (= keys expected) (repr #(name keys expected)))
      ;; 並べる式の索引を引き、置き場の表を頭から読む節が無い。
      (assert (in note-index names) (repr #(name note-index names scans)))
      (assert (not (lfor #(kind table _) scans :if (= kind "Seq Scan") table)) (repr #(name scans))))
    ;; 2 倍の置き場でも読んだ行は増えない(読むのは 1 頁 + 1 行と、その間の並べない行だけ — 置き場の行の数に依らない)。
    (for [name #("上から" "真ん中から")]
      (<- small-read (total-read (get (get (get small 0) name) 0)))
      (<- large-read (total-read (get (get (get large 0) name) 0)))
      (assert (<= large-read small-read) (repr #(name small-read large-read))))
    (finally
      (.close connections))))


(deftest test-a-descending-read-of-a-declared-field-sorts-by-the-ordered-expression-and-compares-rows-for-the-cursor
  ;; 表の上から読む欄(ordered)の文: 並べる式で並べ(NULL は後ろ)・式が NULL でない行だけ・続きの位置は行の比べ(索引の範囲になる形)・
  ;; 条件 ledger = :ledger を置かない(式が NULL でない行は表の行だけ)。宣言の無い欄は前の形のまま。
  (val kind (canonical-json "ask"))
  (<- head (top-rows-statement "records_" "message" #("id") (frozenset #("kind")) "at" True None {"kind" kind} {} 10 None))
  (<- ordered (ordered-expression "message" "at"))
  (assert (in (+ ordered " IS NOT NULL") head.text) head.text)
  (assert (in (.format "ORDER BY {} DESC NULLS LAST, key COLLATE \"C\" DESC LIMIT :limit" ordered) head.text) head.text)
  (assert (not (in "ledger = :ledger" head.text)) head.text)
  (assert (not (in ":after" head.text)) head.text)
  (<- below (top-rows-statement "records_" "message" #("id") (frozenset) "at" True (ListCursor 1 "[\"m9\"]" (** 10 20)) {} {} 10 None))
  (assert (in (.format "({}, key COLLATE \"C\") < (CAST(:after_value AS numeric), :after)" ordered) below.text) below.text)
  (assert (not (in " OR " below.text)) below.text)
  (assert (not (in "ledger = :ledger" below.text)) below.text)
  (assert (= (lfor p below.params #(p.name p.value)) [#("after_value" "100000000000000000000") #("after" "[\"m9\"]") #("limit" 11)])
          below.params)
  ;; 宣言の無い欄は前の形。
  (<- plain (top-rows-statement "records_" "message" #("id") (frozenset) "at" False None {} {} 10 None))
  (assert (in "WHERE ledger = :ledger AND jsonb_typeof(((payload::jsonb) -> 'at')) = 'number'" plain.text) plain.text)
  (assert (not (in "NULLS LAST" plain.text)) plain.text))


;; --- 鍵の綴りの順の頁読み(絞りの無い ListRows)は照合順 "C" の索引を辿る -------------------------------------------------
;; 列 key が照合順 "C" でない置き場(本番の database の既定は en_US.utf8 — 検の使い捨ての PostgreSQL は --locale=C で立ち ICU も無いので、
;; 列を libc の照合順 C.utf8 に替えて本番の形を作る。C.utf8 の順は "C" と同じでも、planner は別の照合順として主キーを頁の順に使わない)で、
;; 続きの位置からの 1 頁(list-rows-statement)が、鍵の綴りの順の索引 (ledger, key COLLATE "C") の範囲を 1 頁 + 1 行だけ辿る事。主キー
;; (ledger, key) は列の照合順で並ぶので頁の順 key COLLATE "C" では辿れず、索引の無い前の形は表の行を全部読んで並べ替えた(Sort — 1 頁ごとに
;; 表の全部の行・2026-10-19 の本番)。
(val KEY-ORDER-ROWS 5000)
(val KEY-ORDER-PAGE 200)
(val NON-C-COLLATION "C.utf8")


(defk key-order-seed-statement [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 parts に id = p-1・P-2・p-3 …(奇数は小文字・偶数は大文字の頭)の行を count 行、表 tickets に count 行(別の表の行 — 頁に出ない)まとめて置く文を作るため。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT t.ledger, t.key, t.payload, 1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM (SELECT 'parts' AS ledger,
                                      '[\"' || CASE WHEN g % 2 = 1 THEN 'p' ELSE 'P' END || '-' || g || '\"]' AS key,
                                      json_build_object('id', CASE WHEN g % 2 = 1 THEN 'p' ELSE 'P' END || '-' || g,
                                                        'color', 'c', 'state', 'open')::text AS payload
                                 FROM generate_series(1, CAST(:count AS integer)) AS g
                               UNION ALL
                               SELECT 'tickets', '[\"g\", \"k' || g || '\"]',
                                      json_build_object('group', 'g', 'id', 'k' || g, 'state', 'open')::text
                                 FROM generate_series(1, CAST(:count AS integer)) AS g) AS t" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("at" YOUNG) #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk key-order-reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix の列 key を照合順 C.utf8 に替え、表 parts と tickets に count 行ずつ置いて統計を取り、真ん中の鍵の後ろからの 1 頁の
   #(走査の節 引いた索引の名 計画の節の種類 返した鍵 期待の鍵) を返すため。期待 = 鍵の綴りの符号点の順の、真ん中の鍵の後ろの 1 頁 + 1 行。"
  (<- seed (key-order-seed-statement prefix count))
  (for [statement #((Statement :text (.format "ALTER TABLE {p}state_rows ALTER COLUMN key TYPE text COLLATE \"{c}\"" :p prefix :c NON-C-COLLATION)
                               :params #())
                    seed
                    (Statement :text (.format "ANALYZE {p}state_rows" :p prefix) :params #()))]
    (<- seeded (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  ;; 表 parts の g 番目の行の id(奇数は小文字・偶数は大文字の頭 — "C" の順では大文字の頭の行が全部先・置く文と同じ形)。
  (val ordered (sorted (gfor g (range 1 (+ count 1)) (json.dumps [(.format "{}-{}" (if (% g 2) "p" "P") g)]))))
  (val middle (get ordered (// count 2)))
  (val expected (tuple (cut ordered (+ (// count 2) 1) (+ (// count 2) 2 KEY-ORDER-PAGE))))
  (<- statement (list-rows-statement prefix "parts" #("id") (frozenset) middle {} {} KEY-ORDER-PAGE None))
  (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
  (assert (isinstance answer SqlRows) (repr answer))
  (val plan (get answer.rows 0 0))
  (val explained (if (isinstance plan str) (json.loads plan) plan))
  (<- scans (table-scans prefix explained))
  (<- names (index-names explained))
  (<- nodes (plan-nodes (get explained 0 "Plan")))
  (<- listed (SqlQuery DATABASE statement.text statement.params))
  (assert (isinstance listed SqlRows) (repr listed))
  #(scans names (frozenset (gfor node nodes (get node "Node Type"))) (tuple (gfor record listed.rows (get record 0))) expected))


(defk key-order-reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して count 行ずつ置き、鍵の綴りの順の頁読みを測るため(終わりに表を消す)。
   答え = #(走査の節 引いた索引の名 計画の節の種類 返した鍵 期待の鍵 鍵の綴りの順の索引の名)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var measured #())
  (try
    (:= measured (run-sql connections (key-order-reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  (+ measured #((+ store.prefix "state_rows_key_c"))))


(deftest test-a-page-in-key-order-reads-through-the-c-collated-key-index-when-the-key-column-is-not-c
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (key-order-reads-at connections KEY-ORDER-ROWS))
    (<- large (key-order-reads-at connections (* 2 KEY-ORDER-ROWS)))
    (for [#(scans names kinds keys expected key-index) #(small large)]
      ;; 鍵の綴りの符号点の順(memory の置き場と同じ順)に、続きの位置の後ろの 1 頁 + 1 行。
      (assert (= keys expected) (repr #(keys expected)))
      ;; 鍵の綴りの順の索引を辿り、並べ替えの節も置き場の表を頭から読む節も無い。
      (assert (in key-index names) (repr #(key-index names scans)))
      (assert (not-in "Sort" kinds) (repr #(kinds scans)))
      (assert (not (lfor #(kind table _) scans :if (= kind "Seq Scan") table)) (repr scans))
      ;; 読んだ行は 1 頁 + 1 行だけ(表 parts の行の数に依らない)。
      (<- read (total-read scans))
      (assert (<= read (+ KEY-ORDER-PAGE 1)) (repr #(read scans))))
    (finally
      (.close connections))))


;; --- 等号で絞った上から読む読みは、等号の欄 × 上から読む欄の組の索引を 1 頁だけ辿る -------------------------------------------------
;; 2026-10-23: 本番の表で、欄 1 つの値 1 つ(表の 15%)の行を刻の欄の新しい順に読む続きの頁が、等号の欄の索引と上から読む欄の索引を組んで
;; (BitmapAnd)位置より下の当たる行を全部引き、その全部の行の payload を jsonb に読み直して並べ替え(Sort)、冷えた buffer で読みの期限を
;; 越えた。表 parts の組(LAW-SCHEMA の descending-within — color × note)の索引で、color = "hot" の上からの 1 頁と、続きの位置(真ん中)からの
;; 1 頁が、組の索引の Index Scan だけで答え(BitmapAnd も Sort も無い)、置き場を TOP-ROWS 行と 2 倍の行で作っても読んだ行を増やさない事を
;; 確かめる。置き場には絞りに合わない行(color が他の値)と並べない行(note が文字列の行 — 10 行に 1 行)を混ぜ、答えの行が絞りに合う並べる行
;; だけな事も見る。組の索引が無い形では、引いた索引の名に組の索引が無く、続きの頁は BitmapAnd と Sort で赤になる。
;; 絞りに合う行(color = hot)は HOT-EVERY 行に 1 行。note の値は行の置き順と関わらない並び(g × NOTE-STRIDE を NOTE-MODULUS で割った余り —
;; どちらも素数で、g < NOTE-MODULUS の内は値が重ならない)— 置き順と並べる欄の順が揃う置き場では、planner は上から読む欄の索引を辿って
;; 絞りの欄で濾す計画を安く見積もり、組の索引を測れない。
(val HOT-EVERY 7)
(val NOTE-STRIDE 7919)
(val NOTE-MODULUS 100003)


(defk within-seed-statement [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "表 parts に count 行(鍵 w1 …・color は g が HOT-EVERY の倍数なら hot・他は c{g mod 7}・note は g × NOTE-STRIDE mod NOTE-MODULUS・
   g が 10 の倍数の行は文字列 n{g}・
   label は本番の行の大きさに近い md5 の綴りの連なり)をまとめて置く文を作るため。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       SELECT 'parts', '[\"w' || g || '\"]',
                              json_build_object('id', 'w' || g, 'state', 'open',
                                                'color', CASE WHEN g % CAST(:every AS integer) = 0 THEN 'hot' ELSE 'c' || (g % 7) END,
                                                'note', CASE WHEN g % 10 = 0 THEN to_jsonb('n' || g)
                                                             ELSE to_jsonb((g * CAST(:stride AS bigint)) % CAST(:modulus AS bigint)) END,
                                                'label', (SELECT string_agg(md5(g || '-' || b), '')
                                                            FROM generate_series(1, CAST(:blocks AS integer)) AS b))::text,
                              1, CAST(:at AS bigint), 'maker', CAST(:origin AS text), 1
                         FROM generate_series(1, CAST(:count AS integer)) AS g" :p prefix)
             :params (tuple (gfor #(name value) #(#("count" count) #("every" HOT-EVERY) #("stride" NOTE-STRIDE) #("modulus" NOTE-MODULUS)
                                                  #("blocks" TOP-NOTE-BLOCKS) #("at" YOUNG) #("origin" ORIGIN-HOST))
                                  (SqlParam :name name :value value)))))


(defk within-ranked [count]
  {:pre [(: count int) (< count NOTE-MODULUS)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "置いた count 行のうち color = hot の並べる行(HOT-EVERY の倍数で 10 の倍数でない g)の #(note の値 鍵) を、大きい順に並べるため
   (note の値は行ごとに違う — 鍵で並べ分ける行は無い)。"
  (tuple (sorted (gfor g (range 1 (+ count 1)) :if (and (= (% g HOT-EVERY) 0) (% g 10))
                       #((% (* g NOTE-STRIDE) NOTE-MODULUS) (json.dumps [(.format "w{}" g)])))
                 :reverse True)))


(defk plan-node-types [explained]
  {:pre [(: explained list)] :post [(: % frozenset)]
   :tags {:context "records" :role "program"}}
  "EXPLAIN (FORMAT JSON) の答えの計画の木の節の種類を集めるため。"
  (<- nodes (plan-nodes (get explained 0 "Plan")))
  (frozenset (gfor node nodes (get node "Node Type"))))


(defk within-reads-on-seeded-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix に count 行を置いて統計を取り、color = hot の上からの 1 頁と真ん中からの 1 頁の
   #(走査の節 引いた索引の名 節の種類 返した鍵 期待の鍵) を {名 …} で返すため。"
  (<- seed (within-seed-statement prefix count))
  (for [statement #(seed (Statement :text (.format "ANALYZE {p}state_rows" :p prefix) :params #()))]
    (<- seeded (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (val hot {"color" (canonical-json "hot")})
  (<- ranked (within-ranked count))
  (val middle (// (len ranked) 2))
  (setv [middle-value middle-key] (get ranked middle))
  (<- head (top-rows-statement prefix "parts" #("id") PARTS-SCOPED "note" True None hot {} TOP-PAGE None))
  (<- below (top-rows-statement prefix "parts" #("id") PARTS-SCOPED "note" True (ListCursor 1 middle-key middle-value) hot {} TOP-PAGE None))
  (val head-expected (tuple (gfor #(_ key) (cut ranked (+ TOP-PAGE 1)) key)))
  (val below-expected (tuple (gfor #(_ key) (cut ranked (+ middle 1) (+ middle TOP-PAGE 2)) key)))
  (var reads {})
  (for [#(name statement expected) #(#("上から" head head-expected) #("真ん中から" below below-expected))]
    (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
    (assert (isinstance answer SqlRows) (repr answer))
    (val plan (get answer.rows 0 0))
    (val explained (if (isinstance plan str) (json.loads plan) plan))
    (<- scans (table-scans prefix explained))
    (<- names (index-names explained))
    (<- kinds (plan-node-types explained))
    (<- listed (SqlQuery DATABASE statement.text statement.params))
    (assert (isinstance listed SqlRows) (repr listed))
    (:= reads (| reads {name #(scans names kinds (tuple (gfor record listed.rows (get record 0))) expected)})))
  reads)


(defk within-reads-at [connections count]
  {:pre [(: connections PostgresConnections) (: count int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)を用意して count 行を置き、等号で絞った上から読む読みを測るため(終わりに表を消す)。
   答え = #(読みごとの測り parts の color × note の組の索引の名)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var reads {})
  (try
    (:= reads (run-sql connections (within-reads-on-seeded-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  (<- within-index (ordered-within-index-name store.prefix "parts" "color" "note"))
  #(reads within-index))


(deftest test-a-descending-page-within-an-equal-field-reads-through-the-pair-index-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (within-reads-at connections TOP-ROWS))
    (<- large (within-reads-at connections (* 2 TOP-ROWS)))
    (for [#(reads within-index) #(small large)
          name #("上から" "真ん中から")]
      (setv [scans names kinds keys expected] (get reads name))
      ;; 絞りに合う並べる行だけを、(値, 鍵) の大きい順に 1 頁 + 1 行。
      (assert (= keys expected) (repr #(name keys expected)))
      ;; 組の索引の Index Scan で答え、索引を組む節(BitmapAnd)も並べ替えの節(Sort)も置き場の表を頭から読む節も無い。
      (assert (= names (frozenset #(within-index))) (repr #(name within-index names scans)))
      (assert (in "Index Scan" kinds) (repr #(name kinds)))
      (assert (not (& kinds (frozenset #("BitmapAnd" "Bitmap Heap Scan" "Sort" "Incremental Sort" "Seq Scan")))) (repr #(name kinds scans))))
    ;; 2 倍の置き場でも読んだ行は増えない(読むのは 1 頁 + 1 行と、その間の並べない行だけ — 絞りに合う行の数に依らない)。
    (for [name #("上から" "真ん中から")]
      (<- small-read (total-read (get (get (get small 0) name) 0)))
      (<- large-read (total-read (get (get (get large 0) name) 0)))
      (assert (<= large-read small-read) (repr #(name small-read large-read))))
    (finally
      (.close connections))))


(deftest test-the-pair-index-uses-the-same-expressions-as-the-descending-read-within-an-equal-field
  ;; 組の索引の式は、読みの文の等号の条件(表の欄の式)と並べる式と同じ字面(文を変えずに planner が選ぶ — pg_sql の頭の註)。
  (<- index (ordered-within-index "records_" "parts" "color" "note"))
  (<- equal (table-field-expression "" "parts" "color"))
  (<- ordered (ordered-expression "parts" "note"))
  (assert (in (.format "state_rows ({}, {} DESC NULLS LAST, key COLLATE \"C\" DESC)" equal ordered) index.text) index.text)
  (<- read (top-rows-statement "records_" "parts" #("id") (frozenset #("color")) "note" True None {"color" (canonical-json "hot")} {} 10 None))
  (assert (in (+ equal " = :w0::jsonb") read.text) read.text)
  (assert (in (.format "ORDER BY {} DESC NULLS LAST, key COLLATE \"C\" DESC" ordered) read.text) read.text)
  ;; 置き場に在るべき索引に、宣言の組ごとに 1 つ入る(旧い形の索引の名の形には当たらない — 索引の用意が外さない)。
  (<- declared (declared-indexes "records_" LAW-SCHEMA))
  (assert (in index declared) (repr (lfor d declared d.name)))
  (<- retired (retired-index? "records_" index.name))
  (assert (not retired) index.name))
