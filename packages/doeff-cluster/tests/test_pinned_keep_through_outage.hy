;; coordinator と API server が同時に止まっても、node に縛られた worker は印の在る job を止めない(2026-11-10)。
;; 13:21Z に coordinator と k8s の API server の載った機体の電源が 17 分落ち、全部の worker が途絶 240 秒(長い方の柵 keep-fence-ms)で
;; 印の在る job を一斉に止め、coordinator が戻る 13:41Z まで起こせなかった(印の在る job の process は途絶の間も判断の回を回していた —
;; 止めたのは worker の自己停止だった)。長い方の柵が守っていた 2 つ:
;;   1 k8s が届かない node の Pod の新しい世代を他の node で起こす(早くても約 350 秒後)— node に縛られた worker(--pinned-to-node)の
;;     新しい世代は同じ node にしか置けず、node が API server に届かない間は起きない(届き直すと kubelet が旧い Pod を止める)。
;;   2 coordinator が担い手の沈黙を見て約束を外し、他へ移す — 縛られた担い手は heartbeat で名乗り(pinnedToNode)、coordinator は約束に
;;     残して、約束の期限を長い柵(ClusterTiming.pinned-keep-fence-ms)から求める(cluster_policy.kept-window-ms — 条 C4 と同じ形・
;;     tests/test_cluster_timing.hy)。coordinator 自身が止まっていた長さは、起き直しの読み直しが沈黙に数えない(api_policy.resume-after-downtime)。
;; worker の側から「coordinator と API server の載った機体が止まった」と「自分の node だけが網から切れた」は同じ形に見える(どちらも
;; 両方に届かない)ので、見分けずに、縛られた worker を長い柵まで止めない形にする — 後者でも 2 か所で走らない事を下の筋書きで確かめる。
;;
;; 前半 = 本体の判断を直に呼ぶ検(worker の柵の選び・約束の期限・起き直しの直後・heartbeat の名乗りと約束)。
;; 後半 = 模擬の世界(sim-cluster)の筋書き: coordinator を 20 分止める・縛られた担い手の網を約束の期限より長く切る。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import tests.room_rows [ROOM-LOADS ROOM-JSONS])
(import tests.state_rows [table-from-rows])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [JobSpec ClusterTiming http-request responded])
(import doeff_cluster [ClusterJob WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Placement KeepMark])
(import doeff_cluster._coordinator.core.cluster_policy [reconcile kept-window-ms])
(import doeff_cluster._coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster._coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import doeff_cluster._worker.protocol.declared [declared-job-spec])
(import doeff_cluster._worker.core.heartbeat_rules [desired-when-unreachable held-keep-fence-ms pinned-claim?])
(import doeff_cluster [sim-cluster SimWorker ProcessesOf CutWorker StartWorker StopCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])
(import tests.program_rows [SAMPLE-RUN])

;; 本番と同じ時間の設定(長い方の柵 240 秒・約束の期限 280 秒・縛られた担い手の長い柵 1 時間)。
(val T (ClusterTiming))
;; 2026-11-10 の機体の止まりと同じ長さ(電源の落ちから coordinator が名乗りを受けるまで 約 20 分)。
(val OUTAGE-SECONDS 1200.0)


;; --- 本体の判断 ---------------------------------------------------------------------------------------------------

(deftest test-a-pinned-worker-keeps-a-marked-job-through-a-20-minute-outage
  ;; 受入 1(worker の判断): coordinator に 20 分届かない(長い方の柵 240 秒を越える)途絶でも、縛られた worker は印の在る job を止めない。
  ;; 縛られていない worker・止まり始めた世代・長い柵を渡さない coordinator(返事の timing に欄が無い)の 3 つは今までどおり 240 秒で止める。
  ;; 印の無い job はどれも fence(20 秒)で止める。
  (<- marked JobSpec (declared-job-spec {"name" "a" "entry" "m" "revision" "r" "keepWhenCutOff" True} False))
  (<- plain JobSpec (declared-job-spec {"name" "b" "entry" "m" "revision" "r"} False))
  (val silent (int (* OUTAGE-SECONDS 1000)))
  (assert (> silent T.keep-fence-ms))
  (defk kept-names [fence]
    {:pre [(: fence int)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
    "途絶 silent の拍で、柵 fence の worker が動かし続ける job の名を読むため。"
    (val desired (desired-when-unreachable silent T.fence-ms fence #(marked plain) #() "届かない"))
    (lfor j desired.jobs j.name))
  (<- pinned int (held-keep-fence-ms T.keep-fence-ms T.pinned-keep-fence-ms True False))
  (assert (= pinned T.pinned-keep-fence-ms))
  (assert (= (! (kept-names pinned)) ["a"]))
  (<- unpinned int (held-keep-fence-ms T.keep-fence-ms T.pinned-keep-fence-ms False False))
  (assert (= (! (kept-names unpinned)) []))
  (<- stopping int (held-keep-fence-ms T.keep-fence-ms T.pinned-keep-fence-ms True True))
  (assert (= (! (kept-names stopping)) []))
  (<- old-coordinator int (held-keep-fence-ms T.keep-fence-ms None True False))
  (assert (= (! (kept-names old-coordinator)) []))
  ;; 長い柵も上限: 越えた途絶では縛られた worker も止める(約束の期限はその止め切りより後 — tests/test_cluster_timing.hy)。
  (val beyond (desired-when-unreachable (+ T.pinned-keep-fence-ms 1) T.fence-ms pinned #(marked) #() "届かない"))
  (assert (= beyond.jobs #()) beyond))


(defk job-of [name]
  {:pre [(: name str)] :post [(: % ClusterJob)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の宣言 1 つ(入れ替えでない service・能力 net を要る)を作るため。"
  (ClusterJob (JobSpec name "m" #() "rev") :needs #("net") :peak-mib 200 :settle-mib 100))


(defk worker-of [name seen]
  {:pre [(: name str) (: seen int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の worker 1 台(最後の連絡の時刻 seen・能力 net)を作るため。"
  (WorkerInfo name #("net") seen :measured (get ROOM-LOADS 10)))


(defk promised-state [w2-seen pinned]
  {:pre [(: w2-seen int) (: pinned bool)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job a を担い手 w1(時刻 0 に最後の連絡)に印の約束(縛られた担い手なら pinned)で置き、能力の合う w2 が刻 w2-seen に名乗っている状態を作るため。"
  (<- job ClusterJob (job-of "a"))
  (<- w1 WorkerInfo (worker-of "w1" 0))
  (<- w2 WorkerInfo (worker-of "w2" w2-seen))
  (ClusterState #(job) (! (table-from-rows {"w1" w1 "w2" w2})) (! (table-from-rows {"a" (Placement "a" "w1" 1 0)}))
                :keep-marks #((KeepMark :job "a" :worker "w1" :boot None :since-ms 0 :pinned pinned))))


(deftest test-a-promise-to-a-pinned-holder-outlasts-its-long-fence
  ;; 受入 2 の前半(約束の期限): 縛られた担い手 w1 の約束は、縛られていない担い手の期限(kept-reassign-after-ms・280 秒)を越えても外れず、
  ;; job は w2 へ移らない(w1 は長い柵まで動かし続けている)。長い柵から求めた期限(kept-window-ms)を越えた後に外れて w2 へ移る — w1 は
  ;; それより先に自分で止めている(条 C4)。縛られていない担い手は今までどおり 280 秒で外れる。
  (val short-lapsed (+ T.kept-reassign-after-ms 1))
  (<- pinned ClusterState (promised-state short-lapsed True))
  (<- held ClusterState (reconcile short-lapsed pinned T))
  (assert (= (len held.keep-marks) 1) held.keep-marks)
  (assert (= (. (.row held.placements "a") worker) "w1") held.placements)
  (val window (kept-window-ms T True))
  (assert (> window T.pinned-keep-fence-ms))
  (<- at-window ClusterState (reconcile window (! (promised-state window True)) T))
  (assert (= (. (.row at-window.placements "a") worker) "w1") at-window.placements)
  (<- lapsed ClusterState (reconcile (+ window 1) (! (promised-state (+ window 1) True)) T))
  (assert (= lapsed.keep-marks #()) lapsed.keep-marks)
  (assert (= (. (.row lapsed.placements "a") worker) "w2") lapsed.placements)
  (<- plain ClusterState (reconcile short-lapsed (! (promised-state short-lapsed False)) T))
  (assert (= plain.keep-marks #()) plain.keep-marks)
  (assert (= (. (.row plain.placements "a") worker) "w2") plain.placements))


(deftest test-a-coordinator-back-from-a-20-minute-stop-does-not-release-a-promise-before-the-holder-beats
  ;; 受入 2(起き直しの直後): coordinator が担い手 w1 の最後の heartbeat(時刻 0)の直後に止まり、20 分後に起き直した。能力の合う w2 が
  ;; 起き直した 5 秒後に名乗ったが、w1 はまだ heartbeat を送り直していない。読み直し(resume-after-downtime)は止まっていた長さを沈黙に
  ;; 数えないので、約束(縛られていない担い手のも)は外れず、job は w1 に残る。読み直さずに同じ刻で判じると(止まっていた長さを沈黙に
  ;; 数える — 失敗ケース)、約束を外して w2 へ移し、まだ動いている w1 の process と 2 か所で走る。
  (val boot (int (* OUTAGE-SECONDS 1000)))
  (val first-beat (+ boot 5000))
  (for [pinned #(False True)]
    (<- given ClusterState (promised-state first-beat pinned))
    ;; 生きていた最後の印は w1 の heartbeat の 1 ms 後(印の無い置き場 = 0 は止まっていた長さを知らない)。
    (val stopped (replace given :alive-ms 1 :started-ms boot))
    (val resumed (get (resume-after-downtime stopped boot) 0))
    (<- after ClusterState (reconcile first-beat resumed T))
    (assert (= (lfor m after.keep-marks #(m.job m.worker)) [#("a" "w1")]) #(pinned after.keep-marks))
    (assert (= (. (.row after.placements "a") worker) "w1") #(pinned after.placements)))
  (<- counted ClusterState (reconcile first-beat (! (promised-state first-beat False)) T))
  (assert (= counted.keep-marks #()) counted.keep-marks)
  (assert (= (. (.row counted.placements "a") worker) "w2") counted.placements))


(defk beat-body [name statuses extra]
  {:pre [(: name str) (: statuses list) (: extra dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "印を知る worker の heartbeat の本文 1 つ(今持っている印は空)に欄 extra を足して組むため。"
  (| {"name" name "boot" (+ name "-boot1") "provides" ["net"] "measured" (get ROOM-JSONS 10) "versions" {"python" "3.14.0"}
      "statuses" statuses "keptWhenCutOff" []}
     extra))


(deftest test-the-pinned-claim-is-kept-on-the-promise-and-dropped-when-the-holder-stops
  ;; heartbeat の名乗り(pinnedToNode)を約束に残し、返事の timing で長い柵を渡す。止まり始めた世代は名乗らない(pinned-claim?)ので、
  ;; その後の heartbeat で約束は縛られていない約束に書き直る(約束の期限は 280 秒へ戻る — 担い手はもう長い柵を使わない)。
  (assert (! (pinned-claim? True False)))
  (assert (not (! (pinned-claim? True True))))
  (assert (not (! (pinned-claim? False False))))
  (<- empty dict (beat-body "w1" [] {"pinnedToNode" True}))
  (val first (responded (ClusterState) (! (http-request "POST" "/heartbeat" {} empty)) 0 T))
  (val declared (responded (get first 0) (! (http-request "PUT" "/jobs" {} {"jobs" [{"name" "s0" "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "revision" "r" "needs" ["net"]}]}
                                                      :actor "test"))
                           0 T))
  (val running [{"name" "s0" "phase" "running" "revision" "r"}])
  (<- pinned-beat dict (beat-body "w1" running {"pinnedToNode" True}))
  (val marked (responded (get declared 0) (! (http-request "POST" "/heartbeat" {} pinned-beat)) 1000 T))
  (assert (= (get marked 2 "timing" "pinned_keep_fence_ms") T.pinned-keep-fence-ms) (get marked 2 "timing"))
  (assert (= (lfor m (. (get marked 0) keep-marks) #(m.job m.pinned)) [#("s0" True)]))
  ;; 止まり始めた世代(stopping)は名乗らない — 約束は縛られていない約束になり、初めて渡した刻は保つ。
  (<- stopping-beat dict (beat-body "w1" running {"stopping" True "keptWhenCutOff" ["s0"]}))
  (val dropped (responded (get marked 0) (! (http-request "POST" "/heartbeat" {} stopping-beat)) 2000 T))
  (assert (= (lfor m (. (get dropped 0) keep-marks) #(m.job m.pinned m.since-ms)) [#("s0" False 1000)])
          (. (get dropped 0) keep-marks)))


;; --- 模擬の世界の筋書き ------------------------------------------------------------------------------------------

(defrecord OutageSeen
  "筋書きの読み: before = 途絶の前の process の列・after = 途絶の後の process の列。"
  (#^ tuple before)
  (#^ tuple after))


(defk spans-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断に渡す区間の列にするため。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(defk coordinator-down []
  {:pre [] :post [(: % OutageSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(2026-11-10 13:21Z 型): 8 秒待って pulse が動いているのを読み、coordinator を 20 分止めて(同じ置き場から読み直す)、戻って
   30 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (<- (StopCoordinator OUTAGE-SECONDS))
  ;; StopCoordinator は止めを頼んで戻る(止まりの間を待たない)— 止まりの長さと戻って 30 秒を待つ。
  (<- (Delay (+ OUTAGE-SECONDS 30.0)))
  (<- after tuple (ProcessesOf "pulse"))
  (OutageSeen :before before :after after))


(deftest test-a-pinned-worker-runs-the-promised-job-through-a-20-minute-coordinator-stop
  ;; 受入 1(模擬の世界): 本物の coordinator と本物の worker で、coordinator を 20 分止めても縛られた担い手 w1 は pulse を止めず、戻った後も
  ;; 同じ process が動き続ける(起こし直しが 0 回)。縛られていない worker は今までどおり 240 秒で止め、戻った後に起こし直す(2026-11-10 の形 —
  ;; 失敗ケース)。どちらも 2 か所では走らない(条 C2)。
  (<- pinned OutageSeen (sim-cluster :notice-broker (MemoryBroker) :timing T (pulses sim-foundation) (coordinator-down)
                                     :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :pinned-to-node True))))
  (assert (= (lfor p pinned.after #(p.worker p.exit-code)) [#("w1" None)]) pinned.after)
  (assert (= (. (get pinned.after 0) instance) (. (get pinned.before 0) instance)) #(pinned.before pinned.after))
  (assert (= (! (one-place-per-job (! (spans-of pinned.after)))) #()))
  (<- plain OutageSeen (sim-cluster :notice-broker (MemoryBroker) :timing T (pulses sim-foundation) (coordinator-down)
                                    :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])))))
  (assert (= (lfor p plain.after #(p.worker p.exit-code)) [#("w1" -15) #("w1" None)]) plain.after)
  (val stopped (get plain.after 0))
  (assert (< T.keep-fence-ms (- stopped.ended-ms stopped.started-ms) (+ 8000 T.keep-fence-ms 60000)) stopped)
  (assert (= (! (one-place-per-job (! (spans-of plain.after)))) #())))


(defrecord CutSeen
  "網の途絶の筋書きの読み: mid = 約束の期限(280 秒)を越えた途絶の最中の process の列・after = 網が戻った後の process の列。"
  (#^ tuple mid)
  (#^ tuple after))


(val CUT-SECONDS (+ (/ T.kept-reassign-after-ms 1000.0) 120.0))


(defk holder-cut-off []
  {:pre [] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(縛られた担い手の node だけが網から切れた — 担い手からは 13:21Z と同じ形に見える): pulse の担い手 w1 の網を約束の期限より 120 秒
   長く切り、2 秒後に能力の合う w2 を起こす。切ってから期限の 28 秒後と、網が戻って 30 秒後に読む。"
  (<- (Delay 8.0))
  (<- (CutWorker "w1" CUT-SECONDS))
  (<- (Delay 2.0))
  (<- (StartWorker "w2"))
  (<- (Delay (+ (/ T.kept-reassign-after-ms 1000.0) 28.0)))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- (Delay 120.0))
  (<- after tuple (ProcessesOf "pulse"))
  (CutSeen :mid mid :after after))


(deftest test-a-cut-off-pinned-holder-keeps-the-job-and-a-joining-worker-does-not-run-it-twice
  ;; worker からは見分けられないもう 1 つの形(担い手の node だけが切れ、coordinator は生きて沈黙を数える): 縛られた担い手 w1 は長い柵
  ;; まで pulse を動かし続け、coordinator は約束の期限(長い柵から求めた期限)まで約束を外さないので、280 秒を越えた途絶の最中に能力の
  ;; 合う w2 が加わっても w2 で起こさない。網が戻った後も w1 の同じ process が動き続ける。どの時点でも 2 か所で走らない(条 C2)。
  (<- seen CutSeen (sim-cluster :notice-broker (MemoryBroker) :timing T (pulses sim-foundation) (holder-cut-off)
                                :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :pinned-to-node True)
                                           (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :starts-down True))))
  (assert (= (lfor p seen.mid #(p.worker p.exit-code)) [#("w1" None)]) seen.mid)
  (assert (= (lfor p seen.after #(p.worker p.exit-code)) [#("w1" None)]) seen.after)
  (assert (= (! (one-place-per-job (! (spans-of seen.after)))) #())))
