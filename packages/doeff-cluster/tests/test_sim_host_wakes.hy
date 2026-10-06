;; 模擬の世界の worker の代役が、本番と同じ待ち(tick_pauses の await-wakes)で周の間を待つ(#3871 の単位 5)— 失敗ケース。
;;
;; 前の形は代役が周の間の待ち(AwaitNextTick)に自分で答え、模擬の刻み(0.5 秒)の格子で周を回すか、静かな周を先に試して一度に
;; 眠った(heartbeat は仮の拍として coordinator の受付の列に預けた)。今は代役が起きる物の組(WorkerWakes)に自分の期限と呼び鈴を
;; 足し、本番の答え手がその早い 1 つまで待つ。
;;   1 何も変わらない仮想の 10 分に、代役の周は heartbeat の分だけ回る(預けた heartbeat を周なしで届けない)。
;;   2 準備の揃う刻ちょうどに job が起きる(0.5 秒の格子に丸まらない)。状態を変える周が続いて落ちる事も無い。
;;   3 代役の組の期限は本番の関数(beat-due・fence-due・RESEND-AFTER-MS)と止まりの明け・準備の揃う刻から組み、期限が全部過ぎた
;;     代役の組にも代役の呼び鈴が在る(起きる物の無い待ち — await-wakes の RuntimeError — を作れない)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_events [MemoryBroker])
(import dataclasses [replace])
(import doeff_time [Delay])
(import doeff_core_effects.scheduler [CreatePromise Promise])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.due_model [DueAt DueNever])
(import doeff_cluster.worker.intent.worker_model [WakeSet])
(import doeff_cluster.worker.protocol.coordinator_link [RESEND-AFTER-MS])
(import doeff_cluster.sim.local [sim-cluster SimWorker HostTruth HostTruthOf ProcessesOf PreparationsOf SimPreparation
                                 fresh-truth host-wakes])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons NET])

(val TIMING (ClusterTiming))
(val QUIET-SECONDS 600.0)
;; 周の数と heartbeat の数の差の上限: 起動の周・宣言の待ちが最初に「変わった」と答えた周など(heartbeat を送らない周)。
(val STARTUP-TICKS 4)
;; 準備の秒(0.5 秒の格子に乗らない長さ)。
(val PREPARE-SECONDS 1.3)


;; --- 1: 静かな 10 分の周は heartbeat の分だけ --------------------------------------------------------------------

(defk quiet-minutes []
  {:pre [] :post [(: % HostTruth)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 仮想の 10 分を何もせずに待ち、w1 の代役の真実を返す。"
  (<- (Delay QUIET-SECONDS))
  (<- truth HostTruth (HostTruthOf "w1"))
  truth)


(deftest test-quiet-minutes-step-only-for-the-heartbeats
  (<- truth HostTruth (sim-cluster :notice-broker (MemoryBroker) :timing TIMING (beacons sim-foundation) (quiet-minutes)
                                   :workers #((SimWorker :name "w1" :provides NET :task-reserve 0))))
  ;; heartbeat は間隔(生存の窓の 1/4 = 2.5 秒)ごとに届く — 10 分で 200 を越える。周はその数と起動の分だけ。
  (assert (> truth.beats 200) #(truth.ticks truth.beats))
  (assert (<= truth.beats truth.ticks (+ truth.beats STARTUP-TICKS)) #(truth.ticks truth.beats)))


;; --- 2: 準備の揃う刻ちょうどに job が起きる ------------------------------------------------------------------------

(defk ready-then-started []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon が起きるまで待ち、#(w1 の準備の揃う刻の最も遅い物 beacon の process の起きた刻 w1 の世代の名) を返す。"
  (<- (Delay 20.0))
  (<- preparations tuple (PreparationsOf "w1"))
  (<- processes tuple (ProcessesOf "beacon"))
  (<- truth HostTruth (HostTruthOf "w1"))
  (val started (. (get processes 0) started-ms))
  #((max (gfor p preparations :if (<= p.ready-ms started) p.ready-ms)) started truth.boot))


(deftest test-a-job-starts-at-the-instant-its-code-is-ready
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) :timing TIMING (beacons sim-foundation) (ready-then-started)
                              :workers #((SimWorker :name "w1" :provides NET :task-reserve 0 :prepare-seconds PREPARE-SECONDS))))
  (assert (= (get seen 0) (get seen 1)) seen)
  ;; 状態を変える周が続いて落ちた(WorkerUnsettled)なら、世代が替わっている。
  (assert (= (get seen 2) "w1-boot1") seen))


;; --- 3: 代役の組の期限と呼び鈴 ---------------------------------------------------------------------------------

(val WORKER (SimWorker :name "w1" :provides NET :task-reserve 0))
(val BEAT-MS 2500)


(defk wakes-at [truth now]
  {:pre [(: truth HostTruth) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "代役の真実 truth の、刻 now の起きる物の組と、渡した呼び鈴を返すため。"
  (<- bell Promise (CreatePromise))
  (<- wakes WakeSet (host-wakes WORKER truth now bell.future))
  #(wakes bell.future))


(deftest test-the-host-wakes-at-the-production-deadlines-and-always-has-its-bell
  (<- base HostTruth (fresh-truth "w1" 1 0 TIMING))
  (val heard (replace base :fresh True :beat-interval-ms BEAT-MS))
  ;; heartbeat の期限(最後に届いた返事 0 + 間隔)。呼び鈴は代役の 1 つ。
  (<- quiet tuple (wakes-at heard 1000))
  (assert (= (. (get quiet 0) due) (DueAt :at BEAT-MS)) quiet)
  (assert (= (len (. (get quiet 0) bells)) 1) quiet)
  ;; 前の heartbeat が届いていなければ、送り直しの刻(今 + RESEND-AFTER-MS)。
  (<- unheard tuple (wakes-at base 1000))
  (assert (= (. (get unheard 0) due) (DueAt :at (+ 1000 RESEND-AFTER-MS))) unheard)
  ;; 処理の止まりの明けと、準備の揃う刻。
  (<- stalled tuple (wakes-at (replace heard :stalled-until-ms 2000) 1000))
  (assert (= (. (get stalled 0) due) (DueAt :at 2000)) stalled)
  (val preparing (SimPreparation :worker "w1" :key "k" :env False :warm False :started-ms 0 :ready-ms 1800 :failure None))
  (<- prepared tuple (wakes-at (replace heard :codes {"k" preparing}) 1000))
  (assert (= (. (get prepared 0) due) (DueAt :at 1800)) prepared)
  ;; 期限が全部過ぎた代役(柵も越えた)にも呼び鈴が在る — 起きる物の無い待ちを作らない。
  (<- late tuple (wakes-at heard (* 10 TIMING.keep-fence-ms)))
  (assert (= (. (get late 0) due) (DueNever)) late)
  (assert (= (len (. (get late 0) bells)) 1) late))
