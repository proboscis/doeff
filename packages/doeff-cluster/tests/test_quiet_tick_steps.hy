;; 静かな heartbeat の周 1 回の重さ(#3871 の単位 5)— 失敗ケース。
;;
;; worker は heartbeat の期限ごとに周を 1 回まわす(単位 4)。何も変わらない周でも、周の間の待ち(tick_pauses の await-wakes)が
;; 起きる物ごとに task を立てて競わせ、負けた方を取り消していた — 呼び鈴(Future)1 つにつき task 1 つ。模擬の世界の日次の検証では
;; この分が 1 周 約 112 歩・仮想の 1 日で 4 万歩を越えた。今は呼び鈴を task を立てずに Race へそのまま渡す。
;;   呼び鈴 1 つを持つ偽の宿で、静かな周 1 回の doeff-vm の歩が QUIET-TICK-STEPS 以下(直す前は 248 歩・直した後は 202 歩)。
;; 期限まで眠る task と止めの合図を待つ task も周ごとに 2 つ立てて取り消していた(仕事の無い worker 1 台が 2.5 秒ごとに払う —
;; 依頼 lt-EME2B8NTMC9Q5RRCQY5N3YWTVB)。今は期限を時計の答え手の RaceWithin が持ち、止めの合図は答え手が session で 1 つ持つ呼び鈴
;; (StopBell)を競わせる: 202 歩 → 114 歩。
;; 時計の戻りと機体の眠りへの直し(card ki-069f4a5c07f7 — 1d772f3e9 は待ちの長さを周の頭の刻の対からの経過で数え、d25e8a25e は
;; 周の頭で壁の時計と単調な時計を続けて読む)が周ごとの時計の読みを足した: 114 歩 → 129 歩(正しさのための読みで、task は増えない)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [ReadVmWorkCounts])
(import doeff_time [SimClock])
(import doeff_time [sim-time-handler])
(import doeff_core_effects [CreatePromise Promise vm-probe-handler])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.test_worker_wakes [quiet-host TickLog do-ticks])

;; 静かな周 1 回の歩の上限(今の 129 歩に 1 割足らずの余白 — 期限と止めの合図に周ごとに task を立てる形の 202 歩は越える)。
(val QUIET-TICK-STEPS 140)
(val QUIET-MS 60000)


(defk quiet-ticks-and-steps []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "鳴らない呼び鈴を 1 つ持つ偽の宿で、本物の run-worker と本番の待ちの答え手を仮想の QUIET-MS 回し、#(周の数 歩) を返すため。
  歩は前後の ReadVmWorkCounts(vm-probe-handler が答える)の差で、後の読みの分の決まった数歩を含む(周の数で割るので上限には効かない)。"
  (val log (TickLog (SimClock)))
  (<- bell Promise (CreatePromise))
  (<- before tuple (ReadVmWorkCounts))
  (<- ((sim-time-handler :clock log.clock)
       ((quiet-host log bell QUIET-MS)
        (stop-signal-never-comes
          (tick-pauses (do-ticks None None))))))
  (<- after tuple (ReadVmWorkCounts))
  #((len log.reads) (- (get after 0) (get before 0))))


(deftest test-a-quiet-heartbeat-tick-stays-light
  (<- seen tuple (vm-probe-handler (quiet-ticks-and-steps)))
  (val ticks (get seen 0))
  (val steps (get seen 1))
  (assert (> ticks 20) seen)
  (assert (<= (// steps ticks) QUIET-TICK-STEPS) #(ticks steps (// steps ticks))))
