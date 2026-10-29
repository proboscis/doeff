;; 2026-10-18: PostgreSQL の置き場の回収(SweepExpired)が書きの錠を持つ間に触る行は、候補の番号の数で縛られ、列の行の数の見積もりに依らない事。
;; 本番では、受付の列の刈りの DELETE 1 文(列の全部を期限の条件で消す)が `ledger = 列 AND at <= 境` を実際の約 180 行に対し約 47,000 行と
;; 見積もり(planner は ledger と at の相関を知らない — 境より前の出来事は他の列に在る)、組の照らし(NOT EXISTS)を Hash Anti Join にして
;; 列の生きた出来事を全部読み、書きの錠を 13〜21 秒持った(錠を待つ要求が client の側で切られた)。
;; 置き場は同じ相関の罠を持つ: 組で数える列 pairs に期限の前の組 N 組(ask: と done:)・期限を過ぎた組 EXPIRED-GROUPS 組・古い要求に新しい
;; 結末の付いた組 MIXED-GROUPS 組、出来事ごとに数える列 pulses に期限の前の出来事 N 個と期限を過ぎた出来事 EXPIRED-PULSES 個、期限の無い
;; 列 journal に境より前の出来事 TRAP-FACTOR × N 個。
;;   - 罠の確かめ: `ledger = 'pairs' AND at <= 境` の見積もりが実際の行の TRAP-RATIO 倍以上(見積もりの外れる置き場で測っている事)
;;   - 錠の外の候補の読み(expiring-candidates-statement)と錠の中の文(候補の番号の DELETE — expire-chosen-events-statement)を、
;;     nested loop を選ばせない planner(enable_nestloop = off — 見積もりが「照らす出来事ごとに索引を引く」を高く見る側へ外れた時の代わり)で
;;     EXPLAIN (ANALYZE, FORMAT JSON) つきで流し、置き場の表の Seq Scan が無く、2N の置き場でも読んだ行が N の置き場より増えない
;; 反例(2026-10-18 に流した): 前の形(列の全部を条件で消す DELETE 1 文・組の照らしは NOT EXISTS)は、この罠の置き場で既定の planner の
;; ままでも Hash Anti Join を選び、列 pairs の生きた出来事を全部読んだ(N = 20000 で 40,000 行)。番号の DELETE でも組の照らしが NOT EXISTS
;; のままだと、nested loop を高く見る planner では同じ hash を選び、読んだ行が N = 4000 で 8,002・2N で 16,002 と倍になって赤(照らしは
;; 値の副問い合わせ — pg_sql.expired-event-condition)。
;; 回収の効果を通す検: 期限を過ぎた出来事が EXPIRE-BATCH 個を越える列を SweepExpired 1 回で全部捨て、書きの錠の transaction ごとに捨てる文の
;; 番号は EXPIRE-BATCH 個まで・候補の読みは錠の外。前の形は 1 つの transaction の 1 文で全部を捨てた(番号で縛らない)— 赤。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import re)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlRows SqlTransaction])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_records.maintenance [SweepExpired Swept])
(import doeff_records._laws [MAKER PULSE-KEEP-SECONDS])
(import doeff_records.pg [pg-records-handler drop-records-tables EXPIRE-BATCH])
(import doeff_records.pg_sql [Statement expiring-candidates-statement expire-chosen-events-statement])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE postgres-connections fresh-prefix run-sql prepared-store])
(import tests.sql_probes [QueryProbe StatementCounts probe-sql-handler])
(import tests.test_pg_index_reads [event-rows-statement explained-scans plan-nodes total-read BEFORE YOUNG OLD])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 期限の前の組・出来事の数 N(小さい置き場)。大きい置き場は 2 倍。
(val BASE-GROUPS 4000)
;; 期限の無い列 journal に置く境より前の出来事の数(N の倍数 — ledger と at の相関の罠)。
(val TRAP-FACTOR 4)
;; 回収が捨てる物: 期限を過ぎた組の数(組ごとに ask: と done: の 2 つ)と出来事の数。期限の前に戻る組(古い要求・新しい結末)の数。
(val EXPIRED-GROUPS 5)
(val EXPIRED-PULSES 7)
(val MIXED-GROUPS 2)
;; 罠の確かめ: 境より前の pairs の出来事の見積もりが、実際の行の何倍以上か。
(val TRAP-RATIO 100)


(defk trap-seed-statements [prefix count expired]
  {:pre [(: prefix str) (: count int) (: expired int)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "相関の罠を持つ置き場の行(頭の註 — 期限を過ぎた組は expired 組)を置き、統計を取る文の列を作るため。"
  (<- asks (event-rows-statement prefix "pairs" "ask:" count YOUNG))
  (<- dones (event-rows-statement prefix "pairs" "done:" count YOUNG))
  (<- expired-asks (event-rows-statement prefix "pairs" "ask:x-" expired OLD))
  (<- expired-dones (event-rows-statement prefix "pairs" "done:x-" expired OLD))
  (<- mixed-asks (event-rows-statement prefix "pairs" "ask:m-" MIXED-GROUPS OLD))
  (<- mixed-dones (event-rows-statement prefix "pairs" "done:m-" MIXED-GROUPS YOUNG))
  (<- pulses (event-rows-statement prefix "pulses" "pulse-" count YOUNG))
  (<- expired-pulses (event-rows-statement prefix "pulses" "pulse-x-" EXPIRED-PULSES OLD))
  (<- journal (event-rows-statement prefix "journal" "j-" (* TRAP-FACTOR count) OLD))
  #(asks dones expired-asks expired-dones mixed-asks mixed-dones pulses expired-pulses journal
    (Statement :text (.format "ANALYZE {p}append_rows" :p prefix) :params #())))


(defk estimate-and-actual [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "境より前の pairs の出来事(`ledger = 'pairs' AND at <= 境` — 組の照らしの前の候補)の #(見積もりの行 実際の行) を読むため(罠の確かめ)。"
  (<- answer (SqlQuery DATABASE (.format "EXPLAIN (ANALYZE, FORMAT JSON) SELECT seq FROM {p}append_rows AS old
                                            WHERE old.ledger = :ledger AND old.at <= :before_at" :p prefix)
                       #((SqlParam :name "ledger" :value "pairs") (SqlParam :name "before_at" :value BEFORE))))
  (assert (isinstance answer SqlRows) (repr answer))
  (val plan (get answer.rows 0 0))
  (val root (get (if (isinstance plan str) (json.loads plan) plan) 0 "Plan"))
  #((get root "Plan Rows") (get root "Actual Rows")))


(defk scans-without-nested-loops [prefix statement]
  {:pre [(: prefix str) (: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "SqlTransaction の中で、planner に nested loop を選ばせない設定(SET LOCAL — transaction 1 つの間だけ効く)の後に文 statement の走査の節を
   測るため(scans-priced-against-nested-loops の中身)。"
  (<- set-off (SqlQuery DATABASE "SET LOCAL enable_nestloop = off" #()))
  (<- scans (explained-scans prefix statement))
  scans)


(defk scans-priced-against-nested-loops [prefix statement]
  {:pre [(: prefix str) (: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "文 statement を、nested loop を選ばせない planner(enable_nestloop = off — 見積もりが「照らす出来事ごとに索引を引く」を高く見る側へ
   外れた時の代わり。本番の外れ = 期限を過ぎた出来事を数百倍に見積もる)で EXPLAIN (ANALYZE, FORMAT JSON) つきで流し、置き場の表を
   読んだ走査の節を返すため。組の照らしが join なら planner は列の生きた出来事を全部読む hash へ移る — 値の副問い合わせは join でないので
   計画が変わらない。"
  (<- scans (SqlTransaction DATABASE (scans-without-nested-loops prefix statement)))
  (assert (isinstance scans tuple) (repr scans))
  scans)


(defk measured-sweep [prefix stream separator]
  {:pre [(: prefix str) (: stream str) (: separator (| str None))] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "列 stream の回収の 1 回分(錠の外の候補の読み → 錠の中の番号の DELETE)を、nested loop を選ばせない planner で EXPLAIN (ANALYZE,
   FORMAT JSON) つきで流し、#(候補の読みの走査の節 消す文の走査の節 捨てた出来事の数) を返すため(DELETE は本当に流れる)。"
  (<- candidates (expiring-candidates-statement prefix stream BEFORE separator EXPIRE-BATCH))
  (<- read-scans (scans-priced-against-nested-loops prefix candidates))
  (<- chosen (SqlQuery DATABASE candidates.text candidates.params))
  (assert (isinstance chosen SqlRows) (repr chosen))
  (val sequences (tuple (gfor record chosen.rows (int (get record 0)))))
  (<- delete (expire-chosen-events-statement prefix stream BEFORE separator sequences))
  (<- delete-scans (scans-priced-against-nested-loops prefix delete))
  (<- left (SqlQuery DATABASE delete.text delete.params))
  (assert (isinstance left SqlRows) (repr left))
  ;; EXPLAIN ANALYZE が消した後に流すので、残りは 0(候補は全部、錠の中の読み直しでも期限を過ぎていた)。
  (assert (= (len left.rows) 0) (repr left.rows))
  #(read-scans delete-scans (len sequences)))


(defk sweeps-on-trap-store [prefix count]
  {:pre [(: prefix str) (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix に罠の行を count 組ずつ置き、{名 値} で #(見積もりの行 実際の行) と列ごとの回収の走査の節を返すため。"
  (<- seeds (trap-seed-statements prefix count EXPIRED-GROUPS))
  (for [seed seeds]
    (<- seeded (SqlQuery DATABASE seed.text seed.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (<- trap (estimate-and-actual prefix))
  (<- pairs (measured-sweep prefix "pairs" ":"))
  (<- pulses (measured-sweep prefix "pulses" None))
  {"trap" trap "pairs" pairs "pulses" pulses})


(defk sweeps-at [connections count]
  {:pre [(: connections "PostgresConnections") (: count int)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "新しい置き場(乱数の接頭辞・LAW-SCHEMA の表と索引)に罠の行を count 組ずつ置いて回収の 1 回分を測るため(終わりに表を消す)。"
  (val store (prepared-store connections (fresh-prefix)))
  (var measured {})
  (try
    (:= measured (run-sql connections (sweeps-on-trap-store store.prefix count)))
    (finally
      (run-sql connections (drop-records-tables store))))
  measured)


(deftest test-the-sweep-under-the-writer-lock-reads-no-more-rows-when-the-estimate-is-off-on-a-store-twice-as-large
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (try
    (<- small (sweeps-at connections BASE-GROUPS))
    (<- large (sweeps-at connections (* 2 BASE-GROUPS)))
    (for [measured #(small large)]
      ;; 罠の確かめ: 境より前の pairs の出来事は 期限を過ぎた組 + 古い要求 だけなのに、見積もりは TRAP-RATIO 倍以上。
      (val trap (get measured "trap"))
      (assert (= (get trap 1) (+ (* 2 EXPIRED-GROUPS) MIXED-GROUPS)) (repr measured))
      (assert (>= (get trap 0) (* TRAP-RATIO (get trap 1))) (repr measured))
      ;; 候補は期限を過ぎた物だけ(古い要求に新しい結末の付いた組は候補にならない)で、錠の中で全部捨てる。
      (assert (= (get measured "pairs" 2) (* 2 EXPIRED-GROUPS)) (repr measured))
      (assert (= (get measured "pulses" 2) EXPIRED-PULSES) (repr measured))
      ;; 置き場の表を頭から読む節が無い。
      (assert (not (lfor stream #("pairs" "pulses") scans (cut (get measured stream) 2) #(kind table _) scans
                         :if (= kind "Seq Scan") #(stream table)))
              (repr measured)))
    ;; 2 倍の置き場でも、候補の読みと錠の中の文が読んだ行は増えない(読むのは候補と組の照らしだけ — 見積もりにも、生きた出来事の数にも依らない)。
    (var reads #())
    (for [stream #("pairs" "pulses") index #(0 1)]
      (<- small-read (total-read (get small stream index)))
      (<- large-read (total-read (get large stream index)))
      (:= reads (+ reads #(#(stream index small-read large-read)))))
    (assert (all (gfor #(_ _ small-read large-read) reads (<= large-read small-read))) (repr #(reads small large)))
    (finally
      (.close connections))))


;; --- 本番の大きさの見積もりの外れで、錠の中の文の時間 ----------------------------------------------------------------
;; 本番の外れ(候補 約 180 行・見積もり 約 47,000 行)と同じ大きさの罠の置き場で、既定の planner のまま、錠の中で流れる文(候補の番号の
;; DELETE)の計画と実行の時間の和が LOCK-LIMIT-MS 未満である事。置き場は期限の前の組 PROD-GROUPS 組(生きた出来事 2 × PROD-GROUPS 個)・
;; 期限を過ぎた組 PROD-EXPIRED-GROUPS 組・journal に境より前の出来事 TRAP-FACTOR × PROD-GROUPS 個。
;; 反例(2026-10-18 に流した): 前の形(列の全部を条件で消す DELETE 1 文)は同じ置き場(見積もり 69,060 行・実際 182 行)で 2,165 ms
;; かかり赤 — 新しい形は 6.65 ms。前の形の時間は列の生きた出来事の数に比例する(48,000 組で 1,022 ms・75,000 組で 3,304 ms)。
(val PROD-GROUPS 60000)
(val PROD-EXPIRED-GROUPS 90)
;; 見積もりの下限(本番の見積もり 約 47,000 行)と、錠を持つ時間の上限。
(val PROD-ESTIMATE 47000)
(val LOCK-LIMIT-MS 1000.0)


(defk explained-milliseconds [statement]
  {:pre [(: statement Statement)] :post [(: % float)]
   :tags {:context "records" :role "program"}}
  "文 statement を EXPLAIN (ANALYZE, FORMAT JSON) つきで流し(本当に流れる)、計画と実行の時間の和(ms — 錠の中で流れれば錠を持つ間)を返すため。"
  (<- answer (SqlQuery DATABASE (+ "EXPLAIN (ANALYZE, FORMAT JSON) " statement.text) statement.params))
  (assert (isinstance answer SqlRows) (repr answer))
  (val plan (get answer.rows 0 0))
  (val explained (get (if (isinstance plan str) (json.loads plan) plan) 0))
  (float (+ (get explained "Planning Time") (get explained "Execution Time"))))


(defk locked-sweep-milliseconds [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "列 pairs の回収の 1 回分を流し、#(錠の中の文の ms 捨てた出来事の数) を返すため(候補の番号は錠の外の読みの答え)。"
  (<- candidates (expiring-candidates-statement prefix "pairs" BEFORE ":" EXPIRE-BATCH))
  (<- chosen (SqlQuery DATABASE candidates.text candidates.params))
  (assert (isinstance chosen SqlRows) (repr chosen))
  (val sequences (tuple (gfor record chosen.rows (int (get record 0)))))
  (<- delete (expire-chosen-events-statement prefix "pairs" BEFORE ":" sequences))
  (<- milliseconds (explained-milliseconds delete))
  #(milliseconds (len sequences)))


(defk locked-sweep-on-prod-sized-trap [prefix]
  {:pre [(: prefix str)] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "用意した置き場 prefix に本番の大きさの罠の行を置き、{名 値} で #(見積もりの行 実際の行) と #(錠の中の文の ms 捨てた出来事の数) を返すため。"
  (<- seeds (trap-seed-statements prefix PROD-GROUPS PROD-EXPIRED-GROUPS))
  (for [seed seeds]
    (<- seeded (SqlQuery DATABASE seed.text seed.params))
    (assert (isinstance seeded SqlRows) (repr seeded)))
  (<- trap (estimate-and-actual prefix))
  (<- locked (locked-sweep-milliseconds prefix))
  {"trap" trap "locked" locked})


(deftest test-the-sweep-holds-the-writer-lock-under-a-second-when-the-estimate-is-off-like-in-production
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (val measured (run-sql connections (locked-sweep-on-prod-sized-trap store.prefix)))
    (val trap (get measured "trap"))
    (val locked (get measured "locked"))
    ;; 罠の確かめ: 境より前の pairs の出来事は 期限を過ぎた組 + 古い要求 だけ(約 180 行)なのに、見積もりは本番と同じ 47,000 行以上。
    (assert (= (get trap 1) (+ (* 2 PROD-EXPIRED-GROUPS) MIXED-GROUPS)) (repr measured))
    (assert (>= (get trap 0) PROD-ESTIMATE) (repr measured))
    ;; 錠の中の文は期限を過ぎた組を全部捨て、計画と実行の時間の和が 1 秒未満。
    (assert (= (get locked 1) (* 2 PROD-EXPIRED-GROUPS)) (repr measured))
    (assert (< (get locked 0) LOCK-LIMIT-MS) (repr measured))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- 回収の効果を通す ---------------------------------------------------------------------------------------------
;; 期限を過ぎた出来事の数(EXPIRE-BATCH を 2 回越える — 錠の transaction 3 つに分かれる)。
(val MANY-EXPIRED (+ (* 2 EXPIRE-BATCH) 7))
;; 捨てる文の番号の引数(`:s0, :s1, …`)。
(val SEQUENCE-PARAM (re.compile r":s\d+\b"))


(defk sweep-many-expired [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "期限つきの列 pulses に刻 0 の出来事を MANY-EXPIRED 個置き、保持の秒の後に SweepExpired を 1 回撃って、#(答え 残った出来事の数 鍵の覚えの数)
   を返すため。"
  (<- seed (event-rows-statement prefix "pulses" "pulse-" MANY-EXPIRED 0))
  (<- seeded (SqlQuery DATABASE seed.text seed.params))
  (assert (isinstance seeded SqlRows) (repr seeded))
  (<- (Delay (+ PULSE-KEEP-SECONDS 1)))
  (<- swept (SweepExpired))
  (<- left (SqlQuery DATABASE (.format "SELECT count(*) FROM {p}append_rows WHERE ledger = 'pulses'" :p prefix) #()))
  (<- retired (SqlQuery DATABASE (.format "SELECT count(*) FROM {p}retired_keys WHERE ledger = 'pulses'" :p prefix) #()))
  #(swept (int (get left.rows 0 0)) (int (get retired.rows 0 0))))


(defk transactions-of [flushes]
  {:pre [(: flushes tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "往復の列(StatementCounts.flushes)を、BEGIN から COMMIT までの transaction ごとの文の tuple に切り分けるため(transaction の外の文は除く)。"
  (val texts (tuple (gfor trip flushes text trip (.strip text))))
  (val begins (tuple (gfor #(index text) (enumerate texts) :if (= text "BEGIN") index)))
  (tuple (gfor begin begins
               (cut texts begin (+ (next (gfor #(index text) (enumerate texts) :if (and (> index begin) (= text "COMMIT")) index)) 1)))))


(deftest test-a-sweep-of-many-expired-events-holds-the-writer-lock-per-bounded-batch
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val counts (StatementCounts))
  (try
    (val outcome (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                 (probe-sql-handler connections DATABASE (QueryProbe counts))
                                                 (pg-records-handler store MAKER "test-host")]
                                                (sweep-many-expired store.prefix)))))
    ;; 1 回の SweepExpired で全部捨て(行は無いので答えは 0)、捨てた出来事は全部鍵の覚えに移る。
    (assert (= outcome #((Swept 0) 0 MANY-EXPIRED)) (repr outcome))
    (<- transactions (transactions-of counts.flushes))
    (val deletes (tuple (gfor statements transactions text statements
                              :if (.startswith text (.format "DELETE FROM {}append_rows" store.prefix))
                              text)))
    ;; 書きの錠の transaction は ⌈MANY-EXPIRED / EXPIRE-BATCH⌉ 個で、どれも番号で引く DELETE 1 つ(番号は EXPIRE-BATCH 個まで)。
    (assert (= (len deletes) (- (// (- MANY-EXPIRED) EXPIRE-BATCH))) deletes)
    (assert (all (gfor text deletes (and (in "old.seq IN (" text) (<= (len (.findall SEQUENCE-PARAM text)) EXPIRE-BATCH)))) deletes)
    ;; 候補の読みは錠の外(transaction の中に候補の読みの文が無い)。
    (assert (not (lfor statements transactions text statements :if (.startswith text "SELECT old.seq FROM") text)) transactions)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
