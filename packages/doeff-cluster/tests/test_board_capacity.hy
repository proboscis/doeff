;;; 盤の掃除と容量(2026-09-25): 期限つきの行(ttlSeconds)・上限を越える書きの断り・task の上限・沈黙した worker を忘れる。
(require doeff-hy.macros [defk deftest <- val var])
(import doeff_hy.table [Table TableWrite table-of])
(import doeff_cluster.shared.core.lease_rules [semaphore-key])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [BoardRow ClusterState WorkerInfo TaskRecord])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [tick clocks-shifted])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.durable_kv [durable-kv full-kv durable-delta state-from-kv])
(import doeff_cluster.coordinator.core.cluster_policy [BOARD-MAX-VALUE-BYTES BOARD-MAX-ROWS BOARD-MAX-BYTES TASK-MAX-OPEN
                          board-usage board-tally-of value-size sweep-board] doeff_cluster.coordinator.protocol.state_json [board-rows-of])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import tests.program_rows [SAMPLE-RUN SAMPLE-TASK-PROGRAM program-placed])
(import tests.state_rows [table-from-rows])

(setv T (ClusterTiming))


(defk call [state method path [body None] [now 1000]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict list str int float bool None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の純粋な判断 responded に要求を 1 つ渡し、(状態 状態の番号 本文) の組を返すため。"
  (responded state (! (http-request method path {} body :actor "c-test")) now T))


(defk expiry-of [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "盤の行のうち期限つきの物の鍵 → 期限。"
  (dfor #(k row) (.items state.board) :if (is-not row.expires-ms None) k row.expires-ms))


(defk put [state key value [now 1000] [ttlSeconds None]]
  {:pre [(: state ClusterState) (: key str) (: value (| dict list str int float bool None)) (: now int) (: ttlSeconds (| int float None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "盤の鍵 key へ値を書く PUT /board/<key> を 1 つ渡すため(ttlSeconds を渡した時だけ本文に期限を載せる)。"
  (! (call state "PUT" (+ "/board/" key) (if (is ttlSeconds None) {"value" value} {"value" value "ttlSeconds" ttlSeconds}) now)))


(deftest test-a-row-with-a-ttl-is-swept-after-it-expires-and-the-sweep-is-persisted
  (<- reply-1 (put (ClusterState) "w/process/atlas/7" {"n" 1} 1000 :ttlSeconds 60))
  (var s (get reply-1 0))
  (val status (get reply-1 1))
  (assert (= status 200))
  (<- reply-2 (put s "w/cycle" {"n" 2} 1000))
  (:= s (get reply-2 0))
  (assert (= (! (expiry-of s)) {"w/process/atlas/7" 61000}))
  ;; 期限は行と一緒に保存し、読み直しで戻る
  (setv back (! (state-from-kv (! (full-kv s)) 2000)))
  (assert (= (! (expiry-of back)) {"w/process/atlas/7" 61000}))
  ;; 期限の前は残り、過ぎた後の最初の調停(要求の無い拍でも)で消える
  (setv #(s1 _ _) (! (call s "GET" "/state" None 60000)))
  (setv #(s2 _ _) (! (call s "POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 1 "taskReserve" 0 "statuses" []} 61000)))
  (assert (is-not (.row s1.board "w/process/atlas/7") None))
  (assert (is (.row s2.board "w/process/atlas/7") None))
  (assert (is-not (.row s2.board "w/cycle") None))
  (setv delta (! (durable-delta s s2)))
  (assert (= (get delta "board/w/process/atlas/7") None)))


(deftest test-a-put-without-ttl-makes-the-row-permanent-again
  (<- reply-3 (put (ClusterState) "k" 1 1000 :ttlSeconds 5))
  (var s (get reply-3 0))
  (<- reply-4 (put s "k" 2 2000))
  (:= s (get reply-4 0))
  (assert (= (! (expiry-of s)) {})))


;; 書けない期限(数でない・0 以下・30 日を越える)の断りは tests/test_shared_contract.hy が本物の client と fake の両方で見る。


(deftest test-writes-over-the-limits-are-refused-but-shrinking-and-deleting-pass
  (setv big (* "x" (+ BOARD-MAX-VALUE-BYTES 1)))
  (<- reply-5 (put (ClusterState) "k" big))
  (val s (get reply-5 0))
  (var status (get reply-5 1))
  (var body (get reply-5 2))
  (assert (= status 507))
  (assert (in "1 行の上限" (get body "error")))
  ;; 合計の上限: 上限の直前まで埋まった盤に、大きくする書きは断り、小さくする書きと消す書きは通す
  (setv half (* "y" (- (// BOARD-MAX-VALUE-BYTES 2) 10)))
  (setv full (replace (ClusterState) :board (table-of #((TableWrite "a" (BoardRow :value half :version 1 :expires-ms None :size (- BOARD-MAX-BYTES 100)))))))
  (<- reply-6 (put full "b" "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"))
  (:= status (get reply-6 1))
  (:= body (get reply-6 2))
  (assert (= status 507))
  (assert (in "合計" (get body "error")))
  (<- reply-7 (put full "a" "small"))
  (val shrunk (get reply-7 0))
  (:= status (get reply-7 1))
  (assert (= status 200))
  (assert (= (. (.row shrunk.board "a") size) (value-size "small")))
  (<- reply-8 (call full "PUT" "/board/a" {"value" None "delete" True}))
  (:= status (get reply-8 1))
  (assert (= status 200))
  ;; 行の数の上限
  (setv many (replace (ClusterState) :board (! (board-rows-of (dfor i (range BOARD-MAX-ROWS) (str i) 1) {}))))
  (<- reply-9 (put many "new" 1))
  (:= status (get reply-9 1))
  (assert (= status 507))
  (<- reply-10 (put many "0" 2))
  (:= status (get reply-10 1))                    ; 在る行の書き直しは通す
  (assert (= status 200)))


(deftest test-board-usage-is-measured-again-on-load-and-exposed-as-metrics
  (setv #(s _ _) (! (put (ClusterState) "a" {"k" "日本語"})))
  (setv back (! (state-from-kv (! (full-kv s)) 2000)))
  (assert (= (board-usage back) (board-usage s)))
  (assert (= (. (board-usage back) bytes) (value-size {"k" "日本語"})))
  (setv text (metrics-text back 2000 T))
  (assert (in (.format "doeff_worker_board_bytes {}" (float (value-size {"k" "日本語"}))) text))
  (assert (in "doeff_worker_board_max_bytes" text)))


(deftest test-tasks-have-a-lease-cap-and-an-open-count-cap
  ;; task の本文は置き場に置いた詰めた Program のキーを運ぶ(置き場に無い sha の task は受けない — 上限の検の前に置いておく)。
  (<- empty tuple (program-placed (ClusterState) {} :now 1000))
  (val body {"program" (get empty 1) "revision" "r" "needs" ["net"] "leaseSeconds" 7200})
  (<- capped (call (get empty 0) "POST" "/tasks" body))
  (assert (= (get capped 1) 400) capped)
  (assert (in "leaseSeconds" (get capped 2 "error")) capped)
  ;; 終わっていない task が上限に達した盤(置ける worker はあるが空きが無い = 待っている)
  (val queued (dfor i (range TASK-MAX-OPEN) (.format "t{}" i)
                    (TaskRecord (.format "t{}" i) "n" SAMPLE-TASK-PROGRAM "r" #() #() 60000 999999999 0)))
  (<- full tuple (program-placed (ClusterState :tasks (! (table-from-rows queued)) :next-task (+ TASK-MAX-OPEN 1)
                                               :workers {"a" (WorkerInfo "a" #("net") 0 1000 :task-reserve 0)})
                                 {} :now 1000))
  (<- over (call (get full 0) "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= (get over 1) 429) over)
  ;; 終わった task は数えない
  (val done (replace (get full 0) :tasks (! (table-from-rows (dfor #(k t) (.items queued) k (replace t :phase "finished"))))))
  (<- fits (call done "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= (get fits 1) 200) fits))


(deftest test-a-worker-silent-for-a-week-without-work-is-forgotten
  (setv old (WorkerInfo "newmac" #("net") 10 0 :task-reserve 0) busy (WorkerInfo "atlas" #("net") 10 0 :task-reserve 0))
  (var s (ClusterState :workers {"newmac" old "atlas" busy}))
  (<- reply-11 (call s "PUT" "/jobs" {"jobs" [{"name" "j" "run" SAMPLE-RUN "revision" "r" "needs" ["net"] "pin" "atlas"}]} 1000))
  (:= s (get reply-11 0))
  ;; atlas は置き先を持つので忘れない(置き先は移し替えの規則が扱う)
  (assert (in "j" s.placements))
  (setv later (! (tick s (+ (. (ClusterTiming) worker-forget-ms) 1) T)))
  (assert (not-in "newmac" later.workers))
  (assert (in "atlas" later.workers)))


;; ---- 耐久の差分は丸ごとの直列化と同じ答え(#1843)----------------------------------------------------------
;; durable-delta は元の値が同じ物の鍵を直列化しない。差分の中身(Persist に渡す物)が、前と後を丸ごと durable-kv にして比べた答えと
;; 1 字も違わない事を、worker・Service・task・盤・監査の行が動く拍の並びで確かめる。

(defk delta-by-full-serialization [before after]
  {:pre [(: before ClusterState) (: after ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "以前の形の差分(盤を除く): 前と後を丸ごと直列化して比べる — durable-delta の答えと突き合わせるため。"
  (<- old dict (durable-kv before))
  (<- new dict (durable-kv after))
  (| (dfor #(k v) (.items new) :if (!= (.get old k) v) k v)
     (dfor k old :if (not-in k new) k None)))


(defk without-board [delta]
  {:pre [(: delta dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "耐久の差分から盤の鍵(board/…)を除くため(以前の形の差分は盤を含まない)。"
  (dfor #(k v) (.items delta) :if (not (.startswith k "board/")) k v))


(deftest test-the-durable-delta-equals-the-delta-of-the-full-serialization
  (val steps [#("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []} 1000)
              #("POST" "/resources/Service" {"name" "a" "spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN}} 1500)
              #("PUT" "/board/k" {"value" 1} 2000)
              #("GET" "/state" None 2500)
              #("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []} 3000)
              #("POST" "/heartbeat" {"name" "zeus" "provides" ["net" "gpu"] "capacity" 1 "taskReserve" 0 "statuses" []} 3500)
              #("PUT" "/board/k" {"value" 2} 4000)
              #("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "taskReserve" 0 "statuses" []} 70000)])
  (var s (ClusterState))
  (var compared 0)
  (for [#(method path body now) steps]
    (<- reply (call s method path body now))
    (val after (get reply 0))
    (assert (= (! (without-board (! (durable-delta s after)))) (! (delta-by-full-serialization s after))) #(method path))
    (:= compared (+ compared 1))
    (:= s after))
  (assert (= compared (len steps)))
  ;; 作り直したが中身の同じ部品(別の物で等しい)は、直列化して比べる側へ回り、差分に入らない。
  (val rebuilt (replace s :tasks (! (table-from-rows (dfor #(k t) (.items s.tasks) k (replace t))))
                          :workers (dfor #(k w) (.items s.workers) k (replace w))))
  (assert (= (! (without-board (! (durable-delta s rebuilt)))) {}))
  ;; 中身の違う部品は差分に入る(同一性だけで黙って落とさない)。
  (val changed (replace s :workers (dfor #(k w) (.items s.workers) k (replace w :capacity (+ w.capacity 1)))))
  (<- got (without-board (! (durable-delta s changed))))
  (assert (= got (! (delta-by-full-serialization s changed))))
  (assert (and got (all (gfor k got (.startswith k "worker/"))))))


;;; 盤は Table[BoardRow](ki-900fccc19cd6 / T2): 1 行の書きは 1 行だけ作り直し、残りの行は同じ object のまま引き継ぐ。

(defn rebuilt-rows [before after]
  "after の盤で、before の盤と同じ object でない行の数(作り直した行・足した行)。"
  (sum (gfor k (.keys after) (if (is (.row before k) (.row after k)) 0 1))))

(deftest test-a-one-row-write-rebuilds-one-row-of-a-big-board
  (val big (replace (ClusterState) :board (! (board-rows-of (dfor i (range 2057) (str i) {"n" i}) {}))))
  (assert (= (.size big.board) 2057))
  (<- written (put big "5" {"n" "new"}))
  (val after (get written 0))
  (assert (= (.size after.board) 2057))
  (assert (= (rebuilt-rows big.board after.board) 1))
  (assert (= (. (.row after.board "5") version) 2))
  (assert (= (. (.row big.board "5") version) 1) "元の盤は書きで変わらない")
  ;; 1 行を足しても作り直すのは足した 1 行だけ
  (<- added (put big "new-key" 1))
  (assert (= (.size (. (get added 0) board)) 2058))
  (assert (= (rebuilt-rows big.board (. (get added 0) board)) 1))
  ;; 1 行を消しても他の行は同じ object
  (<- deleted (call big "PUT" "/board/7" {"value" None "delete" True}))
  (assert (= (.size (. (get deleted 0) board)) 2056))
  (assert (is (.row (. (get deleted 0) board) "7") None))
  (assert (= (rebuilt-rows big.board (. (get deleted 0) board)) 0))
  ;; 保存の差分は書いた 1 行だけ(鍵と値は従来の board/<鍵> と {value resourceVersion})
  (assert (= (! (durable-delta big after)) {"board/5" {"value" {"n" "new"} "resourceVersion" 2}})))

(deftest test-sweep-board-removes-only-the-expired-rows-and-keeps-the-others-as-the-same-object
  (<- r1 (put (ClusterState) "ttl-old" 1 1000 :ttlSeconds 10))
  (<- r2 (put (get r1 0) "ttl-new" 2 1000 :ttlSeconds 100))
  (<- r3 (put (get r2 0) "forever" 3 1000))
  (val s (get r3 0))
  (val swept (! (sweep-board s 11000)))
  (assert (is (.row swept.board "ttl-old") None))
  (assert (is (.row swept.board "ttl-new") (.row s.board "ttl-new")))
  (assert (is (.row swept.board "forever") (.row s.board "forever")))
  (assert (= (.size swept.board) 2))
  ;; 期限切れが無ければ状態そのもの
  (assert (is (! (sweep-board s 5000)) s))
  (val only-forever (! (sweep-board (replace s :board (.with-writes s.board #((TableWrite "ttl-old" None) (TableWrite "ttl-new" None)))) 99999)))
  (assert (= (.size only-forever.board) 1)))

(deftest test-a-stall-shifts-the-lease-holders-with-a-version-bump-and-the-ttl-row-without
  (val lease-key (semaphore-key "lock"))
  (val lease-row {"permits" 2 "holders" {"alive/1" 20000}})
  (val untouched (BoardRow :value 7 :version 4 :expires-ms None :size 1))
  (val state (replace (ClusterState) :alive-ms 10000
                      :board (table-of #((TableWrite lease-key (BoardRow :value lease-row :version 3 :expires-ms None :size 0))
                                         (TableWrite "ttl" (BoardRow :value 1 :version 2 :expires-ms 30000 :size 1))
                                         (TableWrite "plain" untouched)))))
  (val shifted (! (clocks-shifted state 70000 60000)))
  (assert (= (. (.row shifted.board lease-key) version) 4) "lease の行は版 +1")
  (assert (= (get (. (.row shifted.board lease-key) value) "holders") {"alive/1" 80000}))
  (assert (= (. (.row shifted.board "ttl") expires-ms) 90000))
  (assert (= (. (.row shifted.board "ttl") version) 2) "TTL の行は版を進めない")
  (assert (is (.row shifted.board "plain") untouched) "期限なしの行は同じ object"))


;;; 盤の集計 board-tally は書きごとの足し引き(ki-ea1179b7565a / T2b): 全行から求めた値と一致し、書きは全行を読まない。

(defk tally-matches-all-rows [state]
  {:pre [(: state ClusterState)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "持ち回った集計が、盤の全行から求め直した集計と同じか。"
  (and (is-not state.board-tally None) (= state.board-tally (board-tally-of state.board))))

(defclass CountingTable [Table]
  "全行を読んだ回数を数える Table(行の引き .row と書き with-writes は数えない)。"
  (setv full-reads [])
  (defn _flat-rows [self]
    (.append CountingTable.full-reads 1)
    (Table._flat-rows self)))

(deftest test-board-tally-follows-write-delete-sweep-and-restart
  (<- r1 (put (ClusterState) "a" {"n" 1} 1000 :ttlSeconds 10))
  (<- r2 (put (get r1 0) "b" "xyz" 1000))
  (<- r3 (put (get r2 0) "b" "longer value" 1000 :ttlSeconds 100))
  (val s (get r3 0))
  (assert (! (tally-matches-all-rows s)))
  (assert (= s.board-tally (board-tally-of s.board)))
  (assert (= (. (board-usage s) rows) 2))
  (assert (= (. (board-usage s) expiring) 2))
  (<- deleted (call s "PUT" "/board/a" {"value" None "delete" True}))
  (assert (! (tally-matches-all-rows (get deleted 0))))
  (val swept (! (sweep-board s 11000)))
  (assert (! (tally-matches-all-rows swept)))
  (assert (= (. (board-usage swept) rows) 1))
  (val shifted (! (clocks-shifted s 70000 60000)))
  (assert (! (tally-matches-all-rows shifted)))
  (val back (! (state-from-kv (! (full-kv s)) 2000)))
  (assert (! (tally-matches-all-rows back)))
  (assert (= (board-usage back) (board-usage s))))

(deftest test-a-board-write-does-not-read-all-rows
  (val rows (dfor i (range 2057) (str i) (BoardRow :value i :version 1 :expires-ms None :size 1)))
  (val counted (CountingTable rows {} (frozenset)))
  (val state (replace (ClusterState) :board counted :board-tally (board-tally-of (table-of (tuple (gfor #(k v) (.items rows) (TableWrite k v)))))))
  (.clear CountingTable.full-reads)
  (assert (= (. (board-usage state) rows) 2057))
  (<- written (put state "5" {"n" "new"}))
  (assert (= (. (board-usage (get written 0)) rows) 2057))
  (assert (= (len CountingTable.full-reads) 0) "board-usage と 1 行の書きは盤の全行を読まない")
  (assert (! (tally-matches-all-rows (get written 0)))))
