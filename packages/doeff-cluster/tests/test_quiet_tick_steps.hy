;; 静かな heartbeat の周 1 回の重さ(#3871 の単位 5)— 失敗ケース。
;;
;; worker は heartbeat の期限ごとに周を 1 回まわす(単位 4)。何も変わらない周でも、周の間の待ち(tick_pauses の await-wakes)が
;; 起きる物ごとに task を立てて競わせ、負けた方を取り消していた — 呼び鈴(Future)1 つにつき task 1 つ。模擬の世界の日次の検証では
;; この分が 1 周 約 112 歩・仮想の 1 日で 4 万歩を越えた。今は呼び鈴を task を立てずに Race へそのまま渡す。
;;   呼び鈴 1 つを持つ偽の宿で、静かな周 1 回の doeff-vm の歩が QUIET-TICK-STEPS 以下(直す前は 248 歩・直した後は 202 歩)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_vm.doeff_vm [vm-work-counts])
(import doeff_time [SimClock])
(import doeff_time [sim-time-handler])
(import doeff_core_effects.scheduler [CreatePromise Promise])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.test_worker_wakes [quiet-host TickLog do-ticks])

;; 静かな周 1 回の歩の上限(直した後の 202 歩に 1 割足らずの余白 — 呼び鈴に task を立てる形の 248 歩は越える)。
(val QUIET-TICK-STEPS 220)
(val QUIET-MS 60000)


(defk quiet-ticks-and-steps []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "鳴らない呼び鈴を 1 つ持つ偽の宿で、本物の run-worker と本番の待ちの答え手を仮想の QUIET-MS 回し、#(周の数 歩) を返すため。"
  (val log (TickLog (SimClock)))
  (<- bell Promise (CreatePromise))
  (val before (get (vm-work-counts) 0))
  (<- ((sim-time-handler :clock log.clock)
       ((quiet-host log bell QUIET-MS)
        (stop-signal-never-comes
          (tick-pauses (do-ticks None None))))))
  #((len log.reads) (- (get (vm-work-counts) 0) before)))


(deftest test-a-quiet-heartbeat-tick-stays-light
  (<- seen tuple (quiet-ticks-and-steps))
  (val ticks (get seen 0))
  (val steps (get seen 1))
  (assert (> ticks 20) seen)
  (assert (<= (// steps ticks) QUIET-TICK-STEPS) #(ticks steps (// steps ticks))))
