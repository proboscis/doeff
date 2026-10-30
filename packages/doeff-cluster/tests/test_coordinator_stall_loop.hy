;; 調停ループ(core/program.hy の run-coordinator)の水準で、止まりの判断が api_policy.stall-of の 1 つだけである事の失敗ケース
;; (card ki-d033e5f969cf の設計検証 — 盲検 B: 調停ループの側で別の閾・別の意味の Stall を作って時計をずらしても、判断の test
;; test_coordinator_stall.hy は stall-of しか通らないので見逃す)。
;;   1 予定の起きる刻を lease 以下(7 秒)寝過ごした歩は止まりでない — 止まりの行を出さず、時計をずらさない。
;;   2 保存が lease 以下(7 秒)かかった歩の次の歩も止まりでない。
;;   3 寝過ごしが lease を越えた(30 秒)歩は止まりで、行はちょうど 1 つ・内訳は stall-of の定義どおり(経過に数えない長さ = 前の歩の判断の
;;     刻から今まで・前の歩の長さ 0・寝過ごし 30 秒)。
;;   4 保存が lease を越えた(15 秒)歩の次の歩は止まりで、行はちょうど 1 つ・内訳の前の歩の長さは 15 秒。
;; 土台: 調停ループを台本の受付と仮想の時計で回す(test_coordinator_heartbeat_lag.hy と同じ組)。w1 が名乗り、次の起きる予定は w1 の
;; 期限の次の ms(名乗りの刻 + lease + 1 ms)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timezone])
(import doeff [run with-handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [scheduled])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_events [MemoryBroker])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply Request CoordinatorStopRequested])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming SaveState])
(import doeff_cluster.coordinator.core.program [run-coordinator STALL-LOG])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [kube-unavailable])

(val T (ClusterTiming))
(val VERSIONS {"python" "3.14.0" "doeff" "1"})
(val NO-KUBE "検の coordinator は k8s を持たない")
(val START (datetime 2026 10 13 2 22 0 :tzinfo timezone.utc))


(defrecord StallLine
  "止まりの行 1 つの欄(program.STALL-LOG)。"
  (#^ int gap-ms)
  (#^ int busy-ms)
  (#^ int overslept-ms))


(defclass Script []
  "台本の受付: 最初の取りで w1 の heartbeat を渡し、2 度目の取りで予定の起きる刻より oversleep 秒遅れて空で返し、3 度目で止めの合図を
   立てる。slow-save = heartbeat を受けた歩の保存(SaveState)を仮想の時計で待たせる秒(0 = 待たない)。stalls = 出た止まりの行。"
  (defn #^ None __init__ [self #^ Request request #^ float oversleep #^ float slow-save]
    (setv self.request request self.oversleep oversleep self.slow-save slow-save self.takes 0 self.saved False self.done False
          self.stalls [])
    None))


(defhandler scripted [#^ Script script]
  "受付・保存・log の代役: 受付は台本の 3 つの取り、保存は最初の書きだけ台本の秒を待つ、log は止まりの行だけ覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに寝過ごしと保存の遅さの台本を変え、出た行を検へ返す(Ask で区別できない)。
  (NextRequests [timeout-seconds limit]
    (setv script.takes (+ script.takes 1))
    (cond
      (= script.takes 1) (resume [script.request])
      (= script.takes 2) (do (<- (Delay (+ (or timeout-seconds 0.0) script.oversleep)))
                             (resume []))
      True (do (setv script.done True)
               (resume []))))
  (Reply [request status body] (resume None))
  (SaveState [before after]
    (when (and (> script.slow-save 0) (not script.saved) (is-not before after))
      (setv script.saved True)
      (<- (Delay script.slow-save)))
    (resume None))
  (CoordinatorStopRequested [] (resume script.done))
  (SlogEffect []
    (val fields effect.kwargs)
    (when (= effect.msg STALL-LOG)
      (setv script.stalls (+ script.stalls [(StallLine :gap-ms (get fields "gap_ms") :busy-ms (get fields "busy_ms")
                                                       :overslept-ms (get fields "overslept_ms"))])))
    (resume None)))


(defk stalls-of [oversleep slow-save]
  {:pre [(: oversleep float) (: slow-save float)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "新しい状態の調停ループを、寝過ごし oversleep 秒・最初の保存 slow-save 秒の台本の上で止めの合図まで回し、出た止まりの行を返すため。"
  (val request (! (http-request "POST" "/heartbeat" {} {"name" "w1" "provides" ["net"] "backgroundRoom" 10 "taskReserve" 0 "versions" VERSIONS}
                                :actor "w1" :peer "w1" :queued-ms 0)))
  (val script (Script request oversleep slow-save))
  (run (scheduled ((sim-time-handler :clock (SimClock :current-time START))
                    ((scripted script)
                      (request-bodies (reply-bodies (with-handlers (memory-notices (MemoryBroker))
                                                      (with-handlers [(state) (kube-unavailable NO-KUBE)]
                                                        (run-coordinator (ClusterState) T (ClusterNaming))))))))))
  script.stalls)


(deftest test-a-short-oversleep-is-no-stall-in-the-loop
  (<- stalls list (stalls-of 7.0 0.0))
  (assert (= stalls []) stalls))


(deftest test-a-slow-step-within-the-lease-is-no-stall-in-the-loop
  (<- stalls list (stalls-of 0.0 7.0))
  (assert (= stalls []) stalls))


(deftest test-a-long-oversleep-is-one-stall-as-stall-of-defines-it
  ;; w1 の名乗りの歩(判断の刻 t0)の後、予定の起きる刻 t0 + lease + 1 ms を 30 秒寝過ごした: 経過に数えない長さは t0 から今まで。
  ;; 待ちの秒は浮動小数なので、寝過ごしの ms は ±1 を許し、経過に数えない長さとの差(予定の起きる刻 − 判断の刻)は厳密に比べる。
  (<- stalls list (stalls-of 30.0 0.0))
  (assert (= (len stalls) 1) stalls)
  (val line (get stalls 0))
  (assert (= line.busy-ms 0) line)
  (assert (<= 29999 line.overslept-ms 30001) line)
  (assert (= (- line.gap-ms line.overslept-ms) (+ T.lease-ms 1)) line))


(deftest test-a-step-held-past-the-lease-is-one-stall-as-stall-of-defines-it
  ;; w1 の名乗りの歩の保存が 15 秒かかった: 次の歩は前の歩の長さ 15 秒で止まり(予定の起きる刻は既に過ぎていて寝過ごしは 0)。
  (<- stalls list (stalls-of 0.0 15.0))
  (assert (= (len stalls) 1) stalls)
  (val line (get stalls 0))
  (assert (= #(line.busy-ms line.overslept-ms) #(15000 0)) line)
  (assert (= line.gap-ms 15000) line))
