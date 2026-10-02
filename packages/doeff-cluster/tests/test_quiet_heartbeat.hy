;; 静かな heartbeat の早道の失敗ケース(#2655): coordinator の 1 歩の中(状態が同じ now で調停済み)で、変化の無い
;; heartbeat は調停を繰り返さない(api_policy.quiet-heartbeat)。早道は状態の変化を落とさない — 静かな heartbeat の答えは調停を
;; 回した答えと同じで、変化のある heartbeat(報告の行が変わる・沈黙から戻る)は早道に入らない。
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.core.api_policy [respond tick quiet-heartbeat])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat])
(import doeff_cluster.coordinator.protocol.request_bodies [body-of responded])
(import tests.program_rows [SAMPLE-RUN])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0" "doeff" "1"})


(defk beat-body [name statuses]
  {:pre [(: name str) (: statuses list)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker name の heartbeat の本文(報告の行 statuses)を組むため。"
  {"name" name "provides" ["net"] "capacity" 10 "versions" V "statuses" statuses})


(defk answered [state body now settled]
  {:pre [(: state ClusterState) (: body dict) (: now int) (: settled bool)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "heartbeat 1 つを、本文を型に解いてから判断へ渡した答えを得るため(settled = 状態が同じ now で調停済みと呼び手が保証するか)。"
  (val request (http-request "POST" "/heartbeat" {} body))
  (<- typed (body-of request))
  (respond state request now T typed :settled settled))


(defk placed []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w が名乗り、Service s0 が w に置かれ、w が s0 を running と報告した状態(時刻 1000)を、検の出発点として組むため。"
  (<- named dict (beat-body "w" []))
  (val first (responded (ClusterState) (http-request "POST" "/heartbeat" {} named) 0 T))
  (val second (responded (get first 0) (http-request "PUT" "/jobs" {} {"jobs" [{"name" "s0" "run" SAMPLE-RUN "revision" "r" "needs" ["net"]}]}
                                                     :actor "test")
                         0 T))
  (<- reporting dict (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}]))
  (val third (responded (get second 0) (http-request "POST" "/heartbeat" {} reporting) 1000 T))
  (get third 0))


(deftest test-a-quiet-heartbeat-answers-the-same-as-the-settled-path
  ;; 同じ now で調停済みの状態に、報告の変わらない heartbeat — 早道の答え(状態・status・返事)は、調停を回した答えと同じ。
  (val now 3500)
  (val ready (tick (! (placed)) now T))
  (val body (! (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}])))
  (val heard (register-heartbeat ready (run (body-of (http-request "POST" "/heartbeat" {} body))) now))
  (assert (quiet-heartbeat ready heard "w" now T) "報告の変わらない heartbeat は静か")
  (val fast (! (answered ready body now True)))
  (val slow (! (answered ready body now False)))
  (assert (= (get fast 0) (get slow 0)) "早道の状態は調停を回した状態と同じ")
  (assert (= (get fast 1) (get slow 1)))
  (assert (= (get fast 2) (get slow 2)) "早道の返事は調停を回した返事と同じ"))


(deftest test-a-heartbeat-that-changes-the-report-or-revives-takes-the-settled-path
  ;; 変化のある heartbeat は早道に入らない: 報告の行が変わる(running → exited)・沈黙の列から戻る worker。
  (val now 3500)
  (val ready (tick (! (placed)) now T))
  (val changed-body (! (beat-body "w" [{"name" "s0" "phase" "exited" "revision" "r"}])))
  (val changed (register-heartbeat ready (run (body-of (http-request "POST" "/heartbeat" {} changed-body))) now))
  (assert (not (quiet-heartbeat ready changed "w" now T)) "報告の行が変わった heartbeat は静かでない")
  (assert (= (get (! (answered ready changed-body now True)) 0) (get (! (answered ready changed-body now False)) 0))
          "静かでない heartbeat は settled でも調停を回す")
  ;; 沈黙した worker の heartbeat(lease の窓を越えて連絡が無かった)は、生きているへ戻る変化 — 静かでない。
  (val late (+ 1000 T.lease-ms 5000))
  (val silent (tick (! (placed)) late T))
  (assert (in "w" silent.silent) "lease の窓を越えた worker は沈黙の列に入る")
  (val body (! (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}])))
  (val revived (register-heartbeat silent (run (body-of (http-request "POST" "/heartbeat" {} body))) late))
  (assert (not (quiet-heartbeat silent revived "w" late T)) "沈黙から戻る heartbeat は静かでない")
  (assert (not-in "w" (. (get (! (answered silent body late True)) 0) silent)) "戻った worker は調停で沈黙の列から外れる"))
