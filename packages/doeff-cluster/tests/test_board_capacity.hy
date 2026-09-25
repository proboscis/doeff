;;; 盤の掃除と容量(2026-09-25): 期限つきの行(ttlSeconds)・上限を越える書きの断り・task の上限・沈黙した worker を忘れる。
(require doeff-hy.macros [deftest])
(import dataclasses [replace])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request WorkerInfo TaskRecord])
(import doeff_cluster.api_policy [respond tick])
(import doeff_cluster.durable_kv [durable-kv full-kv kv-delta state-from-kv])
(import doeff_cluster.cluster_policy [BOARD-MAX-VALUE-BYTES BOARD-MAX-ROWS BOARD-MAX-BYTES TASK-MAX-OPEN WORKER-FORGET-MS
                          board-usage value-size])
(import doeff_cluster.metrics_policy [metrics-text])

(setv T (ClusterTiming))


(defn #^ tuple call [#^ ClusterState state #^ str method #^ str path #^ object [body None] #^ int [now 1000]]
  (respond state (Request method path {} body :actor "c-test") now T))


(defn #^ tuple put [#^ ClusterState state #^ str key #^ object value #^ int [now 1000] #^ object [ttlSeconds None]]
  (call state "PUT" (+ "/board/" key) (if (is ttlSeconds None) {"value" value} {"value" value "ttlSeconds" ttlSeconds}) now))


(deftest test-a-row-with-a-ttl-is-swept-after-it-expires-and-the-sweep-is-persisted
  (setv #(s status _) (put (ClusterState) "w/process/atlas/7" {"n" 1} 1000 :ttlSeconds 60))
  (assert (= status 200))
  (setv #(s _ _) (put s "w/cycle" {"n" 2} 1000))
  (assert (= s.board-expiry {"w/process/atlas/7" 61000}))
  ;; 期限は行と一緒に保存し、読み直しで戻る
  (setv back (state-from-kv (full-kv s) 2000))
  (assert (= back.board-expiry {"w/process/atlas/7" 61000}))
  ;; 期限の前は残り、過ぎた後の最初の調停(要求の無い拍でも)で消える
  (setv #(s1 _ _) (call s "GET" "/state" None 60000))
  (setv #(s2 _ _) (call s "POST" "/heartbeat" {"name" "atlas" "labels" {} "capacity" 1 "statuses" []} 61000))
  (assert (in "w/process/atlas/7" s1.board))
  (assert (not-in "w/process/atlas/7" s2.board))
  (assert (in "w/cycle" s2.board))
  (setv delta (kv-delta (durable-kv s) (durable-kv s2) s s2))
  (assert (= (get delta "board/w/process/atlas/7") None)))


(deftest test-a-put-without-ttl-makes-the-row-permanent-again
  (setv #(s _ _) (put (ClusterState) "k" 1 1000 :ttlSeconds 5))
  (setv #(s _ _) (put s "k" 2 2000))
  (assert (= s.board-expiry {})))


(deftest test-a-bad-ttl-is-refused
  (for [bad [0 -1 "60" (* 365 24 3600)]]
    (setv #(_ status _) (put (ClusterState) "k" 1 1000 :ttlSeconds bad))
    (assert (= status 400) bad)))


(deftest test-writes-over-the-limits-are-refused-but-shrinking-and-deleting-pass
  (setv big (* "x" (+ BOARD-MAX-VALUE-BYTES 1)))
  (setv #(s status body) (put (ClusterState) "k" big))
  (assert (= status 507))
  (assert (in "1 行の上限" (get body "error")))
  ;; 合計の上限: 上限の直前まで埋まった盤に、大きくする書きは断り、小さくする書きと消す書きは通す
  (setv half (* "y" (- (// BOARD-MAX-VALUE-BYTES 2) 10)))
  (setv full (replace (ClusterState) :board {"a" half} :board-versions {"a" 1}
                      :board-sizes {"a" (- BOARD-MAX-BYTES 100)}))
  (setv #(_ status body) (put full "b" "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"))
  (assert (= status 507))
  (assert (in "合計" (get body "error")))
  (setv #(shrunk status _) (put full "a" "small"))
  (assert (= status 200))
  (assert (= (get shrunk.board-sizes "a") (value-size "small")))
  (setv #(_ status _) (call full "PUT" "/board/a" {"value" None "delete" True}))
  (assert (= status 200))
  ;; 行の数の上限
  (setv many (replace (ClusterState) :board (dfor i (range BOARD-MAX-ROWS) (str i) 1)
                      :board-sizes (dfor i (range BOARD-MAX-ROWS) (str i) 1)))
  (setv #(_ status _) (put many "new" 1))
  (assert (= status 507))
  (setv #(_ status _) (put many "0" 2))                    ; 在る行の書き直しは通す
  (assert (= status 200)))


(deftest test-board-usage-is-measured-again-on-load-and-exposed-as-metrics
  (setv #(s _ _) (put (ClusterState) "a" {"k" "日本語"}))
  (setv back (state-from-kv (full-kv s) 2000))
  (assert (= (board-usage back) (board-usage s)))
  (assert (= (get (board-usage back) "bytes") (value-size {"k" "日本語"})))
  (setv text (metrics-text back 2000 T))
  (assert (in (.format "doeff_worker_board_bytes {}" (float (value-size {"k" "日本語"}))) text))
  (assert (in "doeff_worker_board_max_bytes" text)))


(deftest test-tasks-have-a-lease-cap-and-an-open-count-cap
  (setv body {"env" "e" "blob" "b" "revision" "r" "leaseSeconds" 7200})
  (setv #(_ status _) (call (ClusterState) "POST" "/tasks" body))
  (assert (= status 400))
  ;; 終わっていない task が上限に達した盤(置ける worker はあるが空きが無い = 待っている)
  (setv queued (dfor i (range TASK-MAX-OPEN) (.format "t{}" i)
                     (TaskRecord (.format "t{}" i) "n" "e" "b" "r" #() #() 60000 999999999 0)))
  (setv s (ClusterState :tasks queued :next-task (+ TASK-MAX-OPEN 1) :workers {"a" (WorkerInfo "a" #() 0 1000)}))
  (setv #(_ status reply) (call s "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= status 429) reply)
  ;; 終わった task は数えない
  (setv done (replace s :tasks (dfor #(k t) (.items queued) k (replace t :phase "finished"))))
  (setv #(_ status _) (call done "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= status 200)))


(deftest test-a-worker-silent-for-a-week-without-work-is-forgotten
  (setv old (WorkerInfo "newmac" #() 10 0) busy (WorkerInfo "atlas" #() 10 0))
  (setv s (ClusterState :workers {"newmac" old "atlas" busy}))
  (setv #(s _ _) (call s "PUT" "/jobs" {"jobs" [{"name" "j" "entry" "m" "args" [] "revision" "r" "pin" "atlas"}]} 1000))
  ;; atlas は置き先を持つので忘れない(置き先は移し替えの規則が扱う)
  (assert (in "j" s.placements))
  (setv later (tick s (+ WORKER-FORGET-MS 1) T))
  (assert (not-in "newmac" later.workers))
  (assert (in "atlas" later.workers)))
