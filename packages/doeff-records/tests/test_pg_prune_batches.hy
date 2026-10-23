;; 2026-10-24: PostgreSQL の置き場の変更の列の刈り(PruneChanges)が、番号の錠を持つ間に触る page を PRUNE-BATCH で縛り、変更の列の長さに
;; 依らない事。本番では、刈りの 1 文(`DELETE … WHERE seq <= least(:upto, (SELECT max(seq) … WHERE at <= 境))`)の境の読みを planner が
;; 主鍵の逆順の走査で計画し、境より古い変更に当たるまで保持の窓の変更を全部読んだ(約 150 万行・heap 973 MB)。DB が起き直した直後の
;; 冷えた cache で、この文が文の上限 60 秒を越え(57014)、その間に番号の錠を待つ書きが同じ上限で切られた(pg_sql の頭の註の
;; 「変更の列の刈り」)。
;;   - 1 batch の文: 変更 MILLION 個(境より新しい)と境より古い変更 OLD-CHANGES 個(本番と同じ約 3 KB の本文 — TOAST に出る)の置き場で、
;;     錠の外の境の読み(prune-bound-statement)と錠の中の DELETE(prune-changes-statement)を EXPLAIN (ANALYZE, BUFFERS) で流し、どちらも
;;     触った page(共有の buffer の hit + read — 同じ page を 2 度触れば 2 と数える上からの見積もり)を全部 冷えた cache の読み
;;     COLD-PAGE-MS で読んだとしても、置き場の接続の文の上限の 1/10(PRUNE-STATEMENT-MILLISECONDS)に収まる
;;   - 錠の持ち方: 刈りの効果を通すと、DELETE は番号の錠の transaction ごとに 1 文(PRUNE-BATCH 個まで — 錠と錠の間に書きが入れる)で、
;;     境の読みは錠を取らない transaction の中。どの transaction も手入れの文の上限(SET LOCAL statement_timeout)を持つ
;;   - 文の上限で切れた刈りは失敗にせず、消し終えた所までを答える(手入れの係を止めない)。次の回が残りを消す
;; 反例(2026-10-24 に流した): 前の形の 1 文の境の読み(`max(seq) WHERE at <= 境` — 主鍵の逆順の走査)は、同じ 100 万行の置き場で
;; 触った page が 16,832(温まった cache で 370 ms)— 冷えた cache の読み COLD-PAGE-MS で約 168 秒、60 秒の上限の 3 倍に近い。新しい形は
;; 境の読みが 4・DELETE 1 batch(PRUNE-BATCH = 50)が 558(冷えた cache の読みで 5.6 秒)。1 batch を 200 にした時は 2,210 で赤。
(require doeff-hy.macros [deftest defk <- val var])
(import datetime [datetime timezone])
(import json)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlRows])
(import doeff_core_effects.postgres_sql [postgres-sql-handler DEFAULT-TIMEOUTS])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.maintenance [PruneChanges Pruned])
(import doeff_records.laws [MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables PRUNE-BATCH PRUNE-STATEMENT-MILLISECONDS])
(import doeff_records.pg_sql [Statement prune-bound-statement prune-changes-statement UNBOUNDED-SEQ])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE postgres-connections fresh-prefix run-sql prepared-store
                            open-postgres])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])
(import tests.test_pg_expire_lock [transactions-of])
(import tests.test_pg_index_reads [plan-nodes])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 仮想の時計の今(epoch ミリ秒)と保持の秒: 境 = 今 − 保持。OLD は境より前・YOUNG は境より後。
(val NOW-MS 100000)
(val KEEP-SECONDS 50.0)
(val OLD-AT 1000)
(val YOUNG-AT 99000)
(val BEFORE (- NOW-MS (int (* 1000 KEEP-SECONDS))))
;; 境より新しい変更の数(保持の窓 — 本番は約 150 万)と、境より古い変更の数・本文の 32 字の塊の数(本番の変更は約 3 KB)。
(val MILLION 1000000)
(val OLD-CHANGES 2000)
(val OLD-PAYLOAD-BLOCKS 94)
;; 冷えた cache で page 1 つを読む ms(network の disk の random read の悲観の値)。
(val COLD-PAGE-MS 10)


(defk old-seed-statement [prefix count at]
  {:pre [(: prefix str) (: count int) (: at int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "刻 at の変更を count 個、本番と同じ大きさの縮まない本文(md5 の塊 OLD-PAYLOAD-BLOCKS 個 — TOAST に出る)で積む文を作るため。"
  (Statement :text (.format "INSERT INTO {p}row_changes (ledger, key, version, payload, at, epoch)
                       SELECT 'journal', 'old-' || n, 1,
                              (SELECT string_agg(md5(n::text || ':' || k::text), '') FROM generate_series(1, :blocks) AS k), :at, 1
                       FROM generate_series(1, :count) AS n" :p prefix)
             :params #((SqlParam :name "blocks" :value OLD-PAYLOAD-BLOCKS) (SqlParam :name "at" :value at)
                       (SqlParam :name "count" :value count))))


(defk young-seed-statement [prefix count at]
  {:pre [(: prefix str) (: count int) (: at int)] :post [(: % Statement)]
   :tags {:context "records" :role "program"}}
  "刻 at の小さな変更を count 個積む文を作るため(保持の窓の変更 — 刈りの文が触ってはいけない行)。"
  (Statement :text (.format "INSERT INTO {p}row_changes (ledger, key, version, payload, at, epoch)
                       SELECT 'journal', 'young-' || n, 1, '{{}}', :at, 1 FROM generate_series(1, :count) AS n" :p prefix)
             :params #((SqlParam :name "at" :value at) (SqlParam :name "count" :value count))))


(defk run-statement [statement]
  {:pre [(: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "文 statement を流して行を返すため(落ちれば検を落とす)。"
  (<- answer (SqlQuery DATABASE statement.text statement.params))
  (assert (isinstance answer SqlRows) (repr answer))
  answer.rows)


(defk seeded-store [prefix old young]
  {:pre [(: prefix str) (: old int) (: young int)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "変更の列に境より古い変更 old 個(番号の頭)と新しい変更 young 個を積み、表の統計を取るため。"
  (<- olds (old-seed-statement prefix old OLD-AT))
  (<- (run-statement olds))
  (<- youngs (young-seed-statement prefix young YOUNG-AT))
  (<- (run-statement youngs))
  (<- (run-statement (Statement :text (.format "ANALYZE {p}row_changes" :p prefix) :params #())))
  None)


(defk explained-pages [statement]
  {:pre [(: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "文 statement を EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) つきで流し(本当に流れる)、#(触った page の数 計画と実行の ms) を返すため。
   page の数は計画の節のうち最大の共有の buffer の hit + read(根の節は子の節を含む — data を変える CTE も根の副問い合わせが流す)。"
  (<- rows (run-statement (Statement :text (+ "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " statement.text) :params statement.params)))
  (val plan (get rows 0 0))
  (val explained (get (if (isinstance plan str) (json.loads plan) plan) 0))
  (<- nodes (plan-nodes (get explained "Plan")))
  (val pages (max (gfor node nodes (+ (.get node "Shared Hit Blocks" 0) (.get node "Shared Read Blocks" 0)))))
  #(pages (float (+ (get explained "Planning Time") (get explained "Execution Time")))))


(defk batch-on-million-store [prefix]
  {:pre [(: prefix str)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "100 万行の置き場に刈りの 1 周(境の読み → 錠の中の DELETE 1 batch)を流し、{名 値} で文ごとの #(page ms) と境と消した数を返すため。"
  (<- (seeded-store prefix OLD-CHANGES MILLION))
  (<- bound-statement (prune-bound-statement prefix BEFORE UNBOUNDED-SEQ PRUNE-BATCH))
  (<- bound-cost (explained-pages bound-statement))
  (<- bound-rows (run-statement bound-statement))
  (val bound (int (get bound-rows 0 0)))
  (<- delete (prune-changes-statement prefix bound PRUNE-BATCH))
  (<- delete-cost (explained-pages delete))
  (<- left (run-statement (Statement :text (.format "SELECT count(*), min(seq) FROM {p}row_changes WHERE at <= :before_at" :p prefix)
                                     :params #((SqlParam :name "before_at" :value BEFORE)))))
  {"bound" bound-cost "delete" delete-cost "bound-seq" bound "left" (tuple (get left 0))})


(deftest test-a-prune-batch-touches-few-enough-pages-for-a-cold-cache-on-a-million-change-store
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (val measured (run-sql connections (batch-on-million-store store.prefix)))
    ;; 手入れの文の上限は置き場の接続の文の上限の 1/10。
    (assert (= (* 10 PRUNE-STATEMENT-MILLISECONDS) DEFAULT-TIMEOUTS.statement-milliseconds) (repr DEFAULT-TIMEOUTS))
    ;; 境は境より古い変更の刻の順の頭 PRUNE-BATCH 個の最大の番号で、DELETE はちょうどその PRUNE-BATCH 個を消した。
    (assert (= (get measured "left") #((- OLD-CHANGES PRUNE-BATCH) (+ (get measured "bound-seq") 1))) (repr measured))
    ;; どちらの文も、触った page を全部 冷えた cache で読んでも手入れの文の上限に収まる(実際の ms も)。
    (for [name #("bound" "delete")]
      (val cost (get measured name))
      (assert (<= (* (get cost 0) COLD-PAGE-MS) PRUNE-STATEMENT-MILLISECONDS) (repr measured))
      (assert (< (get cost 1) PRUNE-STATEMENT-MILLISECONDS) (repr measured)))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- 錠の持ち方 ------------------------------------------------------------------------------------------------------
;; 境より古い変更の数(PRUNE-BATCH を 2 回越える — 錠の transaction 3 つに分かれる)と、残る新しい変更の数。
(val MANY-OLD (+ (* 2 PRUNE-BATCH) 7))
(val FEW-YOUNG 30)
;; 代役が数える排他の錠の文(鍵は引数 — 綴りは答え手の lock-text)。
(val EXCLUSIVE-LOCK "SELECT pg_advisory_xact_lock(hashtext(:key))")


(defk prune-once [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "置き場に境より古い変更 MANY-OLD 個と新しい変更 FEW-YOUNG 個を積み、PruneChanges を 1 回撃って #(答え 残った変更の数 古い変更の最大の番号)
   を返すため。"
  (<- (seeded-store prefix MANY-OLD FEW-YOUNG))
  (<- last-old (run-statement (Statement :text (.format "SELECT max(seq) FROM {p}row_changes WHERE at <= :before_at" :p prefix)
                                         :params #((SqlParam :name "before_at" :value BEFORE)))))
  (<- pruned (PruneChanges KEEP-SECONDS))
  (<- left (run-statement (Statement :text (.format "SELECT count(*) FROM {p}row_changes" :p prefix) :params #())))
  #(pruned (int (get left 0 0)) (int (get last-old 0 0))))


(defn sim-clock []
  "仮想の時計(今 = NOW-MS)。"
  (SimClock (datetime.fromtimestamp (/ NOW-MS 1000) timezone.utc)))


(deftest test-a-prune-holds-the-numbering-lock-per-bounded-batch-and-reads-its-bound-outside-the-lock
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val outcome (run (scheduled (with_handlers [(sim-time-handler :clock (sim-clock)) (postgres-sql-handler connections)
                                                 (probe-sql-handler connections DATABASE (QueryProbe counts))
                                                 (pg-records-handler store MAKER "test-host")]
                                                (prune-once store.prefix)))))
    ;; 1 回の PruneChanges で境より古い変更を全部消し、floor は古い変更の最大の番号(答えの 3 つ目)。新しい変更は残る。
    (assert (= (get outcome 0) (Pruned (get outcome 2) MANY-OLD)) (repr outcome))
    (assert (= (get outcome 1) FEW-YOUNG) (repr outcome))
    (<- transactions (transactions-of counts.flushes))
    (val limit-text (.format "SET LOCAL statement_timeout = {}" PRUNE-STATEMENT-MILLISECONDS))
    (val deleting (tuple (gfor statements transactions
                               :if (any (gfor text statements (.startswith text (.format "WITH removed AS (DELETE FROM {}row_changes" store.prefix))))
                               statements)))
    (val bounding (tuple (gfor statements transactions
                               :if (any (gfor text statements (.startswith text "SELECT least(:upto_seq")))
                               statements)))
    ;; 番号の錠の transaction は ⌈MANY-OLD / PRUNE-BATCH⌉ 個で、どれも手入れの文の上限の下に DELETE 1 文だけを流す(錠は batch ごとに
    ;; COMMIT で離れ、次の batch の前に他の書きが錠を取れる)。
    (assert (= (len deleting) (- (// (- MANY-OLD) PRUNE-BATCH))) transactions)
    (for [statements deleting]
      ;; 錠は置き場全体の共有と、部分の錠の排他 1 つ(番号の単位 — pg.prune-batch の #(ROWS-UNIT))だけ。
      (assert (= (len (lfor text statements :if (= text EXCLUSIVE-LOCK) text)) 1) statements)
      (assert (in limit-text statements) statements)
      (assert (= (len (lfor text statements :if (.startswith text "WITH removed AS") text)) 1) statements))
    ;; 境の読みは錠を取らない transaction の中(手入れの文の上限つき)。
    (assert bounding transactions)
    (for [statements bounding]
      (assert (not (lfor text statements :if (in "pg_advisory_xact_lock" text) text)) statements)
      (assert (in limit-text statements) statements))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- 文の上限で切れた刈り -----------------------------------------------------------------------------------------------

(deftest test-a-prune-cut-by-the-maintenance-statement-limit-answers-what-it-removed-and-the-next-prune-finishes
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  ;; 別の接続が store_epoch の行を錠つきで持つ間、刈りの DELETE の床の繰り上げ(同じ行の UPDATE)は待ち、手入れの文の上限で切れる。
  (val blocker (open-postgres))
  (defn pruned-now [program]
    (run (scheduled (with_handlers [(sim-time-handler :clock (sim-clock)) (postgres-sql-handler connections)
                                    (pg-records-handler store MAKER "test-host")]
                                   program))))
  (try
    (run-sql connections (seeded-store store.prefix MANY-OLD FEW-YOUNG))
    (val before (run-sql connections (run-statement (Statement :text (.format "SELECT floor FROM {p}store_epoch WHERE id = 1" :p store.prefix)
                                                               :params #()))))
    (.execute blocker "BEGIN")
    (.execute blocker (.format "SELECT floor FROM {p}store_epoch WHERE id = 1 FOR UPDATE" :p store.prefix))
    ;; 切れた刈りは失敗にせず、消し終えた所(0 個)と今の floor を答える。変更は残る。
    (val cut (pruned-now (PruneChanges KEEP-SECONDS)))
    (assert (= cut (Pruned (int (get before 0 0)) 0)) (repr cut))
    (.execute blocker "ROLLBACK")
    ;; 次の刈りが全部を消す。
    (val again (pruned-now (PruneChanges KEEP-SECONDS)))
    (assert (= again.removed MANY-OLD) (repr again))
    (finally
      (.close blocker)
      (run-sql connections (drop-records-tables store))
      (.close connections))))
