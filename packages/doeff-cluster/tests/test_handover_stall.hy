;; 世代をまたぐ受け渡しの止まりを名指す(card ki-d8e594fe934d — 10-22 15:45〜16:08 JST の zeus): 前の世代がまだ走っている間に名乗った
;; 新しい世代(overlapsElder)で、受け渡しを止めている job(handoff の job が今の世代で Ready でない・recreate の job が受け取れる
;; phase に無い)の名・最後の結末と、受け渡しの始まりの刻を Worker の status.handover に載せ、始まりから HANDOVER-STALL-MS を越えたら
;; stalled と GET /metrics の doeff_worker_handover_stalled{worker} で知らせる。直す前は status.handover が elder・ready・leaving
;; だけを持ち、止めている job の名も始まりの刻も出ず、計器も無かった(新しい世代の handoff の job が落ち続けた 23 分、見えたのは
;; job の落ちの行だけ)。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run])
(import doeff_cluster.coordinator.core.cluster_policy [HANDOVER-STALL-MS])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import tests.test_drain [Coord service K3S T])


(defn #^ dict new-beat [#^ Coord c #^ str worker #^ list statuses]  ; defk にできない: 模擬の世界の method を呼ぶ(状態を進める)
  "今の世代(boot new)の heartbeat(前の世代がまだ走っている間に起きたと名乗る)。"
  (c.call "POST" "/heartbeat" {"name" worker "provides" K3S "backgroundRoom" 10 "taskReserve" 0 "versions" {} "boot" "new"
                               "statuses" statuses "overlapsElder" True "stopping" False}
          :actor None))


(defn #^ (| dict None) handover [#^ Coord c #^ str worker]  ; defk にできない: 模擬の世界の method を呼ぶ
  (.get (get (c.call "GET" (+ "/resources/Worker/" worker)) "status") "handover"))


(defn #^ dict metric-lines [#^ Coord c #^ str prefix]  ; defk にできない: 模擬の世界の method を呼ぶ
  "GET /metrics の本文(metrics-text)の行のうち prefix で始まる物(sample の綴り → 値)。"
  (dfor line (.splitlines (metrics-text c.state c.now T))
        :if (.startswith line prefix)
        :setv #(sample value) (.rsplit line " " 1)
        sample (float value)))


(defn #^ None hold-both [#^ Coord c #^ int seconds #^ list new-statuses]  ; defk にできない: 模擬の世界の時計と method を進める
  "前の世代と今の世代の両方が lease の内に報告し続ける間に、時計を seconds 秒進める(前の世代が黙れば受け渡しの待ちは外れる)。"
  (for [_ (range (// seconds 5))]
    (c.advance 5)
    (c.beat "atlas" ["w"] :boot "old")
    (new-beat c "atlas" new-statuses)))


(defk writer-on-old []
  {:pre [] :post [(: % Coord)] :tags {:context "doeff-cluster-test" :role "program"}}
  "書き手 w(handoff)が atlas の前の世代(boot old)で動き Ready(active)。"
  (val c (Coord #("atlas")))
  (c.beat "atlas" :boot "old")
  (c.call "POST" "/resources/Service" {"name" "w" "spec" (! (service))} :expect 201)
  (c.beat "atlas" ["w"] :boot "old")
  (c.ready "atlas" "w" :role "active")
  (c.advance 1)
  c)


(deftest test-a-crashing-handoff-job-of-the-new-generation-is-named-as-what-holds-the-handover
  ;; 失敗ケース(10-22 の zeus): 新しい世代の handoff の job w の process が落ち続ける(backoff・続けて落ちた回数・最後の終わりの
  ;; code・detail)。受け渡しは w が今の世代で Ready になるまで外れない。status.handover に w の名と最後の結末と始まりの刻が出る。
  (<- c (writer-on-old))
  (val crashing (| (c.row "atlas" "w") {"instance" "atlas-w-new" "phase" "backoff" "failures" 3 "lastExitCode" 1
                                        "detail" "HomeStampDiffers: 設定 dir の版が違う"}))
  (setv (get c.boots "atlas") "new")
  (val started c.now)
  (val first (new-beat c "atlas" [crashing]))
  (assert (get first "handoverPending") first)
  (var h (handover c "atlas"))
  (assert (is-not h None) "前の世代が w を動かしている間は status.handover が在る")
  (assert (= (get h "blocking") [{"job" "w" "update" "handoff" "phase" "backoff" "failures" 3 "lastExitCode" 1
                                  "detail" "HomeStampDiffers: 設定 dir の版が違う"}])
          h)
  (assert (= (get h "sinceMs") started) h)
  (assert (not (get h "stalled")) h)
  ;; 上限の手前では知らせない(計器の値は 0)。
  (hold-both c (- (// HANDOVER-STALL-MS 1000) 30) [crashing])
  (assert (not (get (handover c "atlas") "stalled")))
  (assert (= (get (metric-lines c "doeff_worker_handover_stalled") "doeff_worker_handover_stalled{worker=\"atlas\"}") 0.0))
  ;; 上限を越えた → stalled と計器 1。始まりの刻は動かない。
  (hold-both c 60 [crashing])
  (:= h (handover c "atlas"))
  (assert (get h "stalled") h)
  (assert (= (get h "sinceMs") started) h)
  (assert (= (get (metric-lines c "doeff_worker_handover_stalled") "doeff_worker_handover_stalled{worker=\"atlas\"}") 1.0))
  (assert (>= (get (metric-lines c "doeff_worker_handover_pending_seconds") "doeff_worker_handover_pending_seconds{worker=\"atlas\"}")
              (/ HANDOVER-STALL-MS 1000)))
  ;; 新しい世代の process が Ready になった → 止めている job が無くなり、知らせも消える(受け渡しそのものは leaving まで続く)。
  (val ready-row (| crashing {"phase" "running" "failures" 0 "lastExitCode" None "detail" ""}))
  (c.call "POST" "/resources/Service/w/readiness"
          {"worker" "atlas" "pid" 2 "revision" (get ready-row "runningRevision") "instance" "atlas-w-new" "attempt" "1"
           "specHash" (get ready-row "specHash") "placement" (get ready-row "placement") "ready" True "role" "standby"}
          :actor None)
  (val settled (new-beat c "atlas" [ready-row]))
  (assert (not (.get settled "handoverPending" False)) settled)
  (:= h (handover c "atlas"))
  (assert (not-in "blocking" h) h)
  (assert (not (get h "stalled")) h)
  (assert (not-in "doeff_worker_handover_stalled{worker=\"atlas\"}" (metric-lines c "doeff_worker_handover_stalled"))))


(deftest test-a-recreate-job-not-yet-settled-is-named-and-the-start-survives-a-reload
  ;; recreate の job r は、今の世代が受け取れる phase(elder-runs など)を報告するまで受け渡しを止める — 準備中(preparing)の r の名と
  ;; phase が出る。始まりの刻は保存の行(superseded/<名>/<世代>)に残り、coordinator を作り直しても同じ刻を出す。
  (val c (Coord #("atlas")))
  (c.beat "atlas" :boot "old")
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (! (service {"update" "recreate"}))} :expect 201)
  (c.beat "atlas" ["r"] :boot "old")
  (c.advance 1)
  (val old-row (c.row "atlas" "r"))
  (setv (get c.boots "atlas") "new")
  (val started c.now)
  (val preparing {"name" "r" "phase" "preparing" "desiredRevision" (get old-row "desiredRevision")})
  (assert (get (new-beat c "atlas" [preparing]) "handoverPending"))
  (val h (handover c "atlas"))
  (assert (= (lfor b (get h "blocking") #((get b "job") (get b "update") (get b "phase"))) [#("r" "recreate" "preparing")]) h)
  (assert (= (get h "sinceMs") started) h)
  (c.advance 3)
  (c.beat "atlas" ["r"] :boot "old")
  (val kv (! (full-kv c.state)))
  (setv c.state (! (state-from-kv kv c.now)))
  (assert (= (get (handover c "atlas") "sinceMs") started) "読み直しても始まりの刻は同じ")
  ;; 始まりの欄の前に保存した行(since_ms が無い)は始まりを知らない — 止めている job は名指し、始まりは前の世代の次の報告の刻から数える。
  (setv c.state (! (state-from-kv (dfor #(k v) (.items kv)
                                        k (if (.startswith k "superseded/") (dfor #(f x) (.items v) :if (!= f "since_ms") f x) v))
                                  c.now)))
  (val unknown (handover c "atlas"))
  (assert (and (not-in "sinceMs" unknown) (= (len (get unknown "blocking")) 1)) unknown)
  (c.advance 1)
  (c.beat "atlas" ["r"] :boot "old")
  (assert (= (get (handover c "atlas") "sinceMs") c.now)))
