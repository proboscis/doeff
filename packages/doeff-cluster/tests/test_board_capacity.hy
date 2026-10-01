;;; 盤の掃除と容量(2026-09-25): 期限つきの行(ttlSeconds)・上限を越える書きの断り・task の上限・沈黙した worker を忘れる。
(require doeff-hy.macros [deftest <- val var])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState WorkerInfo TaskRecord])
(import doeff_cluster.foundation.coordinator_inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.durable_kv [durable-kv full-kv durable-delta state-from-kv])
(import doeff_cluster.coordinator.core.cluster_policy [BOARD-MAX-VALUE-BYTES BOARD-MAX-ROWS BOARD-MAX-BYTES TASK-MAX-OPEN WORKER-FORGET-MS
                          board-usage value-size])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import tests.program_rows [SAMPLE-RUN SAMPLE-TASK-PROGRAM program-placed])

(setv T (ClusterTiming))


(defn #^ tuple call [#^ ClusterState state #^ str method #^ str path #^ (| dict list str int float bool None) [body None] #^ int [now 1000]]
  (responded state (http-request method path {} body :actor "c-test") now T))


(defn #^ tuple put [#^ ClusterState state #^ str key #^ object value #^ int [now 1000] #^ object [ttlSeconds None]]
  (call state "PUT" (+ "/board/" key) (if (is ttlSeconds None) {"value" value} {"value" value "ttlSeconds" ttlSeconds}) now))


(deftest test-a-row-with-a-ttl-is-swept-after-it-expires-and-the-sweep-is-persisted
  (val reply-1 (put (ClusterState) "w/process/atlas/7" {"n" 1} 1000 :ttlSeconds 60))
  (var s (get reply-1 0))
  (val status (get reply-1 1))
  (assert (= status 200))
  (val reply-2 (put s "w/cycle" {"n" 2} 1000))
  (:= s (get reply-2 0))
  (assert (= s.board-expiry {"w/process/atlas/7" 61000}))
  ;; 期限は行と一緒に保存し、読み直しで戻る
  (setv back (state-from-kv (full-kv s) 2000))
  (assert (= back.board-expiry {"w/process/atlas/7" 61000}))
  ;; 期限の前は残り、過ぎた後の最初の調停(要求の無い拍でも)で消える
  (setv #(s1 _ _) (call s "GET" "/state" None 60000))
  (setv #(s2 _ _) (call s "POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 1 "statuses" []} 61000))
  (assert (in "w/process/atlas/7" s1.board))
  (assert (not-in "w/process/atlas/7" s2.board))
  (assert (in "w/cycle" s2.board))
  (setv delta (durable-delta s s2))
  (assert (= (get delta "board/w/process/atlas/7") None)))


(deftest test-a-put-without-ttl-makes-the-row-permanent-again
  (val reply-3 (put (ClusterState) "k" 1 1000 :ttlSeconds 5))
  (var s (get reply-3 0))
  (val reply-4 (put s "k" 2 2000))
  (:= s (get reply-4 0))
  (assert (= s.board-expiry {})))


;; 書けない期限(数でない・0 以下・30 日を越える)の断りは tests/test_shared_contract.hy が本物の client と fake の両方で見る。


(deftest test-writes-over-the-limits-are-refused-but-shrinking-and-deleting-pass
  (setv big (* "x" (+ BOARD-MAX-VALUE-BYTES 1)))
  (val reply-5 (put (ClusterState) "k" big))
  (val s (get reply-5 0))
  (var status (get reply-5 1))
  (var body (get reply-5 2))
  (assert (= status 507))
  (assert (in "1 行の上限" (get body "error")))
  ;; 合計の上限: 上限の直前まで埋まった盤に、大きくする書きは断り、小さくする書きと消す書きは通す
  (setv half (* "y" (- (// BOARD-MAX-VALUE-BYTES 2) 10)))
  (setv full (replace (ClusterState) :board {"a" half} :board-versions {"a" 1}
                      :board-sizes {"a" (- BOARD-MAX-BYTES 100)}))
  (val reply-6 (put full "b" "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"))
  (:= status (get reply-6 1))
  (:= body (get reply-6 2))
  (assert (= status 507))
  (assert (in "合計" (get body "error")))
  (val reply-7 (put full "a" "small"))
  (val shrunk (get reply-7 0))
  (:= status (get reply-7 1))
  (assert (= status 200))
  (assert (= (get shrunk.board-sizes "a") (value-size "small")))
  (val reply-8 (call full "PUT" "/board/a" {"value" None "delete" True}))
  (:= status (get reply-8 1))
  (assert (= status 200))
  ;; 行の数の上限
  (setv many (replace (ClusterState) :board (dfor i (range BOARD-MAX-ROWS) (str i) 1)
                      :board-sizes (dfor i (range BOARD-MAX-ROWS) (str i) 1)))
  (val reply-9 (put many "new" 1))
  (:= status (get reply-9 1))
  (assert (= status 507))
  (val reply-10 (put many "0" 2))
  (:= status (get reply-10 1))                    ; 在る行の書き直しは通す
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
  ;; task の本文は置き場に置いた詰めた Program のキーを運ぶ(置き場に無い sha の task は受けない — 上限の検の前に置いておく)。
  (<- empty tuple (program-placed (ClusterState) {} :now 1000))
  (val body {"program" (get empty 1) "revision" "r" "needs" ["net"] "leaseSeconds" 7200})
  (val capped (call (get empty 0) "POST" "/tasks" body))
  (assert (= (get capped 1) 400) capped)
  (assert (in "leaseSeconds" (get capped 2 "error")) capped)
  ;; 終わっていない task が上限に達した盤(置ける worker はあるが空きが無い = 待っている)
  (val queued (dfor i (range TASK-MAX-OPEN) (.format "t{}" i)
                    (TaskRecord (.format "t{}" i) "n" SAMPLE-TASK-PROGRAM "r" #() #() 60000 999999999 0)))
  (<- full tuple (program-placed (ClusterState :tasks queued :next-task (+ TASK-MAX-OPEN 1)
                                               :workers {"a" (WorkerInfo "a" #("net") 0 1000)})
                                 {} :now 1000))
  (val over (call (get full 0) "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= (get over 1) 429) over)
  ;; 終わった task は数えない
  (val done (replace (get full 0) :tasks (dfor #(k t) (.items queued) k (replace t :phase "finished"))))
  (val fits (call done "POST" "/tasks" (| body {"leaseSeconds" 60})))
  (assert (= (get fits 1) 200) fits))


(deftest test-a-worker-silent-for-a-week-without-work-is-forgotten
  (setv old (WorkerInfo "newmac" #("net") 10 0) busy (WorkerInfo "atlas" #("net") 10 0))
  (var s (ClusterState :workers {"newmac" old "atlas" busy}))
  (val reply-11 (call s "PUT" "/jobs" {"jobs" [{"name" "j" "run" SAMPLE-RUN "revision" "r" "needs" ["net"] "pin" "atlas"}]} 1000))
  (:= s (get reply-11 0))
  ;; atlas は置き先を持つので忘れない(置き先は移し替えの規則が扱う)
  (assert (in "j" s.placements))
  (setv later (tick s (+ WORKER-FORGET-MS 1) T))
  (assert (not-in "newmac" later.workers))
  (assert (in "atlas" later.workers)))


;; ---- 耐久の差分は丸ごとの直列化と同じ答え(#1843)----------------------------------------------------------
;; durable-delta は元の値が同じ物の鍵を直列化しない。差分の中身(Persist に渡す物)が、前と後を丸ごと durable-kv にして比べた答えと
;; 1 字も違わない事を、worker・Service・task・盤・監査の行が動く拍の並びで確かめる。

(defn #^ dict delta-by-full-serialization [#^ ClusterState before #^ ClusterState after]
  "以前の形の差分(盤を除く): 前と後を丸ごと直列化して比べる。"
  (setv old (durable-kv before) new (durable-kv after))
  (| (dfor #(k v) (.items new) :if (!= (.get old k) v) k v)
     (dfor k old :if (not-in k new) k None)))


(defn #^ dict without-board [#^ dict delta]
  (dfor #(k v) (.items delta) :if (not (.startswith k "board/")) k v))


(deftest test-the-durable-delta-equals-the-delta-of-the-full-serialization
  (val steps [#("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []} 1000)
              #("POST" "/resources/Service" {"name" "a" "spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN}} 1500)
              #("PUT" "/board/k" {"value" 1} 2000)
              #("GET" "/state" None 2500)
              #("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []} 3000)
              #("POST" "/heartbeat" {"name" "zeus" "provides" ["net" "gpu"] "capacity" 1 "statuses" []} 3500)
              #("PUT" "/board/k" {"value" 2} 4000)
              #("POST" "/heartbeat" {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []} 70000)])
  (var s (ClusterState))
  (var compared 0)
  (for [#(method path body now) steps]
    (val reply (call s method path body now))
    (val after (get reply 0))
    (assert (= (without-board (durable-delta s after)) (delta-by-full-serialization s after)) #(method path))
    (:= compared (+ compared 1))
    (:= s after))
  (assert (= compared (len steps)))
  ;; 作り直したが中身の同じ部品(別の物で等しい)は、直列化して比べる側へ回り、差分に入らない。
  (val rebuilt (replace s :tasks (dfor #(k t) (.items s.tasks) k (replace t))
                          :workers (dfor #(k w) (.items s.workers) k (replace w))))
  (assert (= (without-board (durable-delta s rebuilt)) {}))
  ;; 中身の違う部品は差分に入る(同一性だけで黙って落とさない)。
  (val changed (replace s :workers (dfor #(k w) (.items s.workers) k (replace w :capacity (+ w.capacity 1)))))
  (val got (without-board (durable-delta s changed)))
  (assert (= got (delta-by-full-serialization s changed)))
  (assert (and got (all (gfor k got (.startswith k "worker/"))))))
