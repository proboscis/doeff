;; coordinator が止まって起き直した時、止まっていた間に延ばせなかった lease を切らない(agora-controllers の card ki-3b7498747e4c — 親
;; ki-be59e760eb6c。10-11 00:25:51 UTC の coordinator の作り直しで約 1〜2 分 延ばしが届かず、盤は戻った時の自分の時計で期限を判じて
;; 持ち主の会話の lease を切り、待っていた別の process に渡した — 同じ会話のターンが 2 本並んだ根)。
;;
;; coordinator は起きる時に、止まっていた長さ(gap)を task の lease・worker の最後の連絡・Rollout の段・入れ替えの期限の時間に数えない
;; (api_policy.resume-after-downtime)。盤の lease の行(semaphore/<名>)の担い手の期限も同じく gap だけずらす。
;;
;;   (a) sim-cluster: lease を持つ job(writer)と、後から同じ lease を取りに来て待つ job(taker)を置き、coordinator を lease の TTL(15 秒)より
;;       長く止めて戻す → 持ち主の延ばしが通って lease を持ち続け、待つ取りは通らない。直す前は、起き直した coordinator が持ち主の担い手を
;;       期限切れと読み、taker の取りが通った(盤の担い手の行が taker の世代に替わる)。
;;   (b) 純粋: 持ち主が本当に居ない lease は、起き直した刻 + 止まる前に残っていた時間(≤ TTL)を過ぎると取りが通る(切れない lease を
;;       作らない)。止まる前に既に切れていた担い手は、ずらしても切れたまま。
(require doeff-hy.macros [deftest defk defsystem <- val])
(import collections.abc [Callable])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.cluster_control [StopCoordinator])
(import doeff_cluster.sim.local [sim-cluster ProcessesOf SharedRows])
(import doeff_cluster.coordinator.intent.cluster_model [BoardRow ClusterState])
(import doeff_cluster.coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-key])
(import tests.fixtures.lease_programs [lease-sim-foundation lease-writer HOLDER-ROW LOCK])

;; 担い手(cluster-semaphore の SemaphoreSession)の既定の TTL(秒)と、coordinator を止める秒(TTL より長く)。
(val TTL-SECONDS 15.0)
(val DOWN-SECONDS 20.0)
;; 取りを待つ job taker は起き上がりに 3 秒かける(持ち主が先に取る — defsystem の job の引数は literal)。


(defsystem holder-and-taker [#^ Callable foundation]
  "lease を持つ job と、後から同じ lease を取りに来て待つ job(どちらも取ったら盤の HOLDER-ROW に世代と刻を書き、返さずに持ち続ける)"
  (writer (lease-writer foundation 1.0 0.0) :replicas 1 :needs #{"cluster-net"} :readiness {"windowSeconds" 5})
  (taker (lease-writer foundation 1.0 3.0) :replicas 1 :needs #{"cluster-net"} :readiness {"windowSeconds" 5}))


(defk stop-the-coordinator-and-read []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 持ち主が lease を取って taker が待ちに入るまで待って盤を読み、coordinator を DOWN-SECONDS 止めて戻し、TTL だけ待ってから盤と
   2 つの job の process を読むため。答え = #(止める前の盤の行 後の盤の行 writer の process の列 taker の process の列)。"
  (<- (Delay 8.0))
  (<- before dict (SharedRows HOLDER-ROW))
  (<- (StopCoordinator DOWN-SECONDS))
  (<- (Delay (+ DOWN-SECONDS TTL-SECONDS)))
  (<- after dict (SharedRows HOLDER-ROW))
  (<- writers tuple (ProcessesOf "writer"))
  (<- takers tuple (ProcessesOf "taker"))
  #(before after writers takers))


(deftest test-a-the-holder-keeps-its-lease-through-a-coordinator-stop-longer-than-the-ttl
  (<- answer tuple (sim-cluster :notice-broker (MemoryBroker) (holder-and-taker lease-sim-foundation) (stop-the-coordinator-and-read)))
  (setv #(before after writers takers) answer)
  ;; 止める前: 持ち主(writer の世代)が lease を持つ。
  (val holder (next (gfor p writers :if (= p.instance (get before HOLDER-ROW "instance")) p) None))
  (assert (is-not holder None) #(before writers takers))
  ;; 起き直した後も持ち主が lease を持ち続け(盤の担い手の行が替わらない)、持ち主の process は動き続ける。
  (assert (= (get after HOLDER-ROW "instance") holder.instance) #(before after takers))
  (assert (is holder.exit-code None) writers))


(deftest test-b-a-lease-of-a-holder-that-is-really-gone-lapses-after-the-time-it-had-left
  ;; 止まる前の最後の生きていた印 = 10 秒。担い手 alive/1 の期限 = 20 秒(残り 10 秒)・stale/1 の期限 = 5 秒(止まる前に切れていた)。
  ;; 70 秒に起き直す(止まっていた 60 秒)。
  (val key (semaphore-key LOCK))
  (val row {"permits" 2 "holders" {"alive/1" 20000 "stale/1" 5000}})
  (val state (ClusterState :alive-ms 10000 :board {key (BoardRow :value row :version 3 :expires-ms None :size 0)}))
  (setv #(resumed gap) (resume-after-downtime state 70000))
  (assert (= gap 60000) gap)
  (val shifted (. (get resumed.board key) value))
  (assert (= (get shifted "holders") {"alive/1" 80000 "stale/1" 65000}) shifted)
  ;; 持ち主の延ばしの無いまま、残っていた時間(10 秒)の内は満ちている — 2 つ目の空きは stale/1 が切れたままなので取れる。
  (setv #(_ one) (lease-op shifted "claim" "taker/1" 2 15000 70000))
  (assert one.ok one)
  ;; permits 1 の行なら、残っていた時間の内(起き直してから 10 秒の内)は取れず、それを過ぎると取れる(起き直した刻 + TTL 15 秒の内)。
  (val single {"permits" 1 "holders" {"alive/1" 80000}})
  (setv #(_ early) (lease-op single "claim" "taker/1" 1 15000 79999))
  (assert (not early.ok) early)
  (setv #(_ late) (lease-op single "claim" "taker/1" 1 15000 80000))
  (assert late.ok late)
  (assert (<= 80000 (+ 70000 (int (* 1000 TTL-SECONDS))))))
