;; 静かな heartbeat の早道の失敗ケース(agora-redesign #2655): coordinator の 1 歩の中(状態が同じ now で調停済み)で、変化の無い
;; heartbeat は調停を繰り返さない(api_policy.quiet-heartbeat)。早道は状態の変化を落とさない — 静かな heartbeat の答えは調停を
;; 回した答えと同じで、変化のある heartbeat(報告の行が変わる・沈黙から戻る)は早道に入らない。
(require doeff-hy.macros [deftest val])
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


(defn #^ dict beat-body [#^ str name #^ list statuses]  ; defk にできない: 判断を直に呼ぶ検(Program の外)の本文の組み立て
  {"name" name "provides" ["net"] "capacity" 10 "versions" V "statuses" statuses})


(defn #^ tuple answered [#^ ClusterState state #^ dict body #^ int now #^ bool settled]  ; defk にできない: 判断を直に呼ぶ検(Program の外)
  "heartbeat 1 つを、本文を型に解いてから判断へ渡した答え(settled = 状態が同じ now で調停済みと呼び手が保証するか)。"
  (setv request (http-request "POST" "/heartbeat" {} body))
  (respond state request now T (run (body-of request)) :settled settled))


(defn #^ ClusterState placed []  ; defk にできない: 判断を直に呼ぶ検(Program の外)の状態の組み立て
  "worker w が名乗り、Service s0 が w に置かれ、w が s0 を running と報告した状態(時刻 1000)。"
  (setv #(s _ _) (responded (ClusterState) (http-request "POST" "/heartbeat" {} (beat-body "w" [])) 0 T))
  (setv #(s _ _) (responded s (http-request "PUT" "/jobs" {} {"jobs" [{"name" "s0" "run" SAMPLE-RUN "revision" "r" "needs" ["net"]}]}
                                            :actor "test")
                            0 T))
  (setv #(s _ _) (responded s (http-request "POST" "/heartbeat" {} (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}]))
                            1000 T))
  s)


(deftest test-a-quiet-heartbeat-answers-the-same-as-the-settled-path
  ;; 同じ now で調停済みの状態に、報告の変わらない heartbeat — 早道の答え(状態・status・返事)は、調停を回した答えと同じ。
  (val now 3500)
  (val ready (tick (placed) now T))
  (val body (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}]))
  (val heard (register-heartbeat ready (run (body-of (http-request "POST" "/heartbeat" {} body))) now))
  (assert (quiet-heartbeat ready heard "w" now T) "報告の変わらない heartbeat は静か")
  (val fast (answered ready body now True))
  (val slow (answered ready body now False))
  (assert (= (get fast 0) (get slow 0)) "早道の状態は調停を回した状態と同じ")
  (assert (= (get fast 1) (get slow 1)))
  (assert (= (get fast 2) (get slow 2)) "早道の返事は調停を回した返事と同じ"))


(deftest test-a-heartbeat-that-changes-the-report-or-revives-takes-the-settled-path
  ;; 変化のある heartbeat は早道に入らない: 報告の行が変わる(running → exited)・沈黙の列から戻る worker。
  (val now 3500)
  (val ready (tick (placed) now T))
  (val changed-body (beat-body "w" [{"name" "s0" "phase" "exited" "revision" "r"}]))
  (val changed (register-heartbeat ready (run (body-of (http-request "POST" "/heartbeat" {} changed-body))) now))
  (assert (not (quiet-heartbeat ready changed "w" now T)) "報告の行が変わった heartbeat は静かでない")
  (assert (= (get (answered ready changed-body now True) 0) (get (answered ready changed-body now False) 0))
          "静かでない heartbeat は settled でも調停を回す")
  ;; 沈黙した worker の heartbeat(lease の窓を越えて連絡が無かった)は、生きているへ戻る変化 — 静かでない。
  (val late (+ 1000 T.lease-ms 5000))
  (val silent (tick (placed) late T))
  (assert (in "w" silent.silent) "lease の窓を越えた worker は沈黙の列に入る")
  (val body (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}]))
  (val revived (register-heartbeat silent (run (body-of (http-request "POST" "/heartbeat" {} body))) late))
  (assert (not (quiet-heartbeat silent revived "w" late T)) "沈黙から戻る heartbeat は静かでない")
  (assert (not-in "w" (. (get (answered silent body late True) 0) silent)) "戻った worker は調停で沈黙の列から外れる"))
