;; worker が子 process の終わりを、拍ごとの問い合わせ(PollProcess)ではなく知らせで知る(#3834)。
;;
;; 本物の調整ループ(run-worker)・本物の拍の間の眠りの答え手(tick-pauses)・起こし方のまとめ(wake-host)・子 process の言い換え
;; (process-host)・入口の検めの言い換え(probe-host)・止めの印(stop-flag)を、台本の子 process(scripted-process-handler — 起こした子は
;; 止めるまで走り続け、止めた時に終わりの見張りの bell を満たす)・memory の file system・仮想の時計の上で回す。coordinator への口・版の
;; 準備・root と待ちの子の言い換えは宿の代役(exit-world)。
;;
;; - 子が終わったら、次の拍を待たずに worker が観測する(仮想の時計で 0 ms)。反例: 拍ごとに問う形(直す前)は、終わりの刻の位相しだいで
;;   最大 1 拍(500 ms)遅れる — 下の筋書きの 7.3 秒の終わりは 7.5 秒の拍まで知らない。
;; - 子が走るだけの間、worker は拍ごとに起きない(起きる回数が拍の数だけ増えない)。反例: 直す前は 7.3 秒の間に 0.5 秒ごとの 15 回。
;; - 期限は今までどおり効く: 入口の検めは時間切れの刻に止められて失敗になる(眠りが長くても期限の刻に起きる)。反例: 言い換えが期限を
;;   眠りに伝えない形は、次の heartbeat の刻(この筋書きでは 30 秒先)まで検めを止めない。
;; - 時間で変わる判断(終わった service の起こし直しの間 restart-backoff-ms)も、その拍に起きて行う(先の拍を本番の判断で試す)。
;; - 止めの合図(StopState.request)で眠りから起き、拍を待たずに止まり始める。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import pathlib [Path])
(import sys)
(import doeff [Program run with-handlers])
(import doeff_core_effects.handlers [state slog-discard-handler])
(import doeff_core_effects.scheduler [scheduled Spawn CreatePromise Promise])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess SignalProcess ProcessSignal timed-out-outcome])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView WorkerPolicy DesiredJobs ReadDesired ObserveWorld EnvReport
                                                 PublishStatus PrepareCode ReleaseLeases ProbeState ProbeView])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveProbes CodeWake EnvsWake WarmWake LinkDue HostWake])
(import doeff_cluster.worker.protocol.process_host [process-host])
(import doeff_cluster.worker.protocol.probes [probe-host])
(import doeff_cluster.worker.protocol.stop [StopState stop-flag])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.wake [wake-host])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.worker.core.probe_rules [probe-due probe-step ProbeStep])
(import doeff_cluster.worker.core.env_upkeep [PrepareLimits prepare-due prepare-overdue])
(import tests.host_rig [host-settings])
(import tests.probe_rig [probe-settings])

(val STATE "/state")
(val POLICY (WorkerPolicy))
(val TICK-MS (int (* 1000 POLICY.tick-seconds)))
(val GAP-MS (int (* 1000 POLICY.wake-gap-seconds)))
;; 宿の代役が答える次の heartbeat の刻(今から 30 秒先 — 子の終わりと期限だけで起きる事を見るため、heartbeat では起こさない)。
(val FAR-LINK-MS 30000)
(val SERVICE (JobSpec "svc" "jobs.svc" #() "rev-a"))
;; 入口の検めの対象の service(詰めた Program の置き場のキーを持つ — 持たなければ検めの前に断られる)。
(val PROBED (JobSpec "probed" "jobs.probed" #("service") "rev-a" :program "p-1"))
(val PROBE-SECONDS 3)


(defclass ExitBox []
  "筋書きと宿の代役が分ける値: never = 鳴らない宣言の変化の呼び鈴(本番の口と同じく、眠りは呼び鈴と競う)・ticks = 宣言の読み(拍)の
   仮想の刻の列・pid = 観測した job の子の pid・exit-ms = 筋書きが子を止めた刻・knew-ms = worker が子の終わりを初めて観測した刻・
   stop-ms = 筋書きが止めの合図を立てた刻・probe = 最後に観測した入口の検め・starts = job の子を観測した刻の列(起こし直しを含む)。"
  (defn #^ None __init__ [self]
    (setv self.never None self.ticks #() self.pid None self.exit-ms None self.knew-ms None self.stop-ms None self.probe None
          self.starts #())))


(defrecord ExitSeen
  "筋書き 1 回の見え方(ExitBox の写し)。"
  (#^ tuple ticks)
  (#^ (| int None) exit-ms)
  (#^ (| int None) knew-ms)
  (#^ (| int None) stop-ms)
  (#^ (| ProbeView None) probe)
  (#^ tuple starts))


(defhandler exit-world [#^ ExitBox box #^ tuple jobs]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 本番の宿のうち coordinator への口・版の準備・root と待ちの子の言い換えの代役。子 process と入口の検めの観測は本物の言い換え
  ;; (ObserveProcesses・ObserveProbes)に問う。引数に残す理由: 検ごとに別の宣言と記録の入れ物で並べる(Ask で区別できない)。
  (session var asked #())
  (ReadDesired [env-report stopping]
    (<- now int (now-epoch-ms))
    (setv box.ticks (+ box.ticks #(now)))
    (resume (DesiredJobs jobs :changed box.never.future)))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note] (resume None))
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision]
    (when (not-in revision asked)
      (:= asked (+ asked #(revision))))
    (resume None))
  (ObserveWorld []
    (<- now int (now-epoch-ms))
    (<- processes tuple (ObserveProcesses))
    (<- probes tuple (ObserveProbes))
    (for [view processes]
      (when (and (is view.exit-code None) (!= view.pid box.pid))
        (setv box.pid view.pid box.starts (+ box.starts #(now))))
      (when (and (is-not view.exit-code None) (is-not box.exit-ms None) (is box.knew-ms None))
        (setv box.knew-ms now)))
    (when probes
      (setv box.probe (get probes -1)))
    (resume (WorldView (tuple (gfor revision asked (CodeView revision CodeState.READY STATE))) processes probes)))
  (CodeWake [] (resume (HostWake :targets #())))
  (EnvsWake [] (resume (HostWake :targets #())))
  (WarmWake [] (resume (HostWake :targets #())))
  (LinkDue []
    (<- now int (now-epoch-ms))
    (resume (+ now FAR-LINK-MS))))


(defk runs-until-stopped [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の shim(job と入口の検めの子の代役): 止めるまで走り続ける。"
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk exit-story [box stop exit-at stop-at]
  {:pre [(: box ExitBox) (: stop StopState) (: exit-at (| int None)) (: stop-at int)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: exit-at(仮想の ms — None なら止めない)に job の子を止め(外から終わらせる — job が自分で終わるのと同じく、worker は終わりを
   観測で知る)、stop-at に worker の止めの合図を立てる(信号の受け手と同じ口 request)。"
  (when (is-not exit-at None)
    (<- (Delay (/ exit-at 1000.0)))
    (<- (SignalProcess :pid box.pid :signal ProcessSignal.TERM))
    (<- ended int (now-epoch-ms))
    (setv box.exit-ms ended))
  (<- now int (now-epoch-ms))
  (<- (Delay (/ (- stop-at now) 1000.0)))
  (<- asked int (now-epoch-ms))
  (setv box.stop-ms asked)
  (.request stop)
  None)


(defk exit-scene [jobs exit-at stop-at]
  {:pre [(: jobs tuple) (: exit-at (| int None)) (: stop-at int)] :post [(: % ExitSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言 jobs の worker を本物の調整ループで回し、筋書き(exit-story)の後に止まった worker の見え方を返すため(頭の註の土台)。"
  (val box (ExitBox))
  (val stop (StopState))
  (<- settings (host-settings (Path STATE) :policy POLICY))
  (<- probes (probe-settings (Path STATE) :timeout-seconds PROBE-SECONDS :policy POLICY))
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path sys.executable) name) :run runs-until-stopped))))
  (defk scene []
    {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
    "筋書きを走らせながら調整ループを止まるまで回すため。"
    (<- never Promise (CreatePromise))
    (setv box.never never)
    (<- (Spawn (exit-story box stop exit-at stop-at) :daemon True))
    (<- (run-worker POLICY))
    None)
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) slog-discard-handler (memory-file-handler (MemoryFiles :dirs #(STATE)))
                                  (scripted-process-handler script) (stop-flag stop) (probe-host probes) (process-host settings)
                                  (exit-world box jobs) wake-host tick-pauses]
                                 (scene))))
  (ExitSeen :ticks box.ticks :exit-ms box.exit-ms :knew-ms box.knew-ms :stop-ms box.stop-ms :probe box.probe :starts box.starts))


(defk ticks-between [seen since until]
  {:pre [(: seen ExitSeen) (: since int) (: until int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "拍(宣言の読み)の刻のうち since より後・until より前の物を返すため。"
  (tuple (gfor t seen.ticks :if (< since t until) t)))


(val EXIT-AT 7300)
(val EXIT-STOP-AT 12250)


(deftest test-a-child-that-ends-is-known-without-waiting-for-the-next-tick
  ;; 子が終わった刻に worker が起き、その場で終わりを観測する(仮想の時計で 0 ms)。反例: 拍ごとに問う形は 7.5 秒の拍まで知らない(200 ms 遅れ)。
  (<- seen ExitSeen (exit-scene #(SERVICE) EXIT-AT EXIT-STOP-AT))
  (assert (= seen.exit-ms EXIT-AT) seen)
  (assert (= seen.knew-ms seen.exit-ms) (.format "終わりを知るまで {} ms: {}" (- (or seen.knew-ms -1) EXIT-AT) seen)))


(deftest test-a-running-child-does-not-wake-the-worker-every-tick
  ;; 子が走るだけの間(0〜7.3 秒)、worker は拍ごとに起きない — 起きるのは起こした拍の後の揃いの追いだけ。反例: 直す前は 0.5 秒ごとに 14 回。
  (<- seen ExitSeen (exit-scene #(SERVICE) EXIT-AT EXIT-STOP-AT))
  (<- quiet tuple (ticks-between seen 0 EXIT-AT))
  (assert (<= (len quiet) 1) (.format "子が走る間に {} 回起きた: {}" (len quiet) seen.ticks)))


(deftest test-a-time-based-decision-wakes-the-worker-at-its-tick
  ;; 終わった service の起こし直しは restart-backoff-ms(2 秒)の後の拍で行う — 眠りは先の拍を本番の判断で試し、その拍で起きる。
  ;; 反例: 先の拍を試さずに heartbeat の刻まで眠る形は、30 秒先まで起こし直さない。
  (<- seen ExitSeen (exit-scene #(SERVICE) EXIT-AT EXIT-STOP-AT))
  (assert (= (len seen.starts) 2) seen)
  (val again (- (get seen.starts 1) EXIT-AT))
  (assert (<= POLICY.restart-backoff-ms again (+ POLICY.restart-backoff-ms TICK-MS)) (.format "起こし直しまで {} ms: {}" again seen)))


(deftest test-the-stop-signal-wakes-the-worker
  ;; 止めの合図(request)の刻に worker が起き、止まり始める(拍の上限まで眠り続けない)。
  (<- seen ExitSeen (exit-scene #(SERVICE) EXIT-AT EXIT-STOP-AT))
  (assert (in EXIT-STOP-AT seen.ticks) seen))


(deftest test-the-probe-timeout-still-stops-the-probe-on-time
  ;; 入口の検めの時間切れ(PROBE-SECONDS = 3 秒)は、その刻に起きて止める — 眠りが長くても遅れない。止めた後の終わりは知らせで知り、
  ;; 時間切れの失敗になる。反例: 言い換えが期限を眠りに伝えない形は、次の heartbeat の刻(30 秒先)まで検めを止めない。
  (<- seen ExitSeen (exit-scene #(PROBED) None 6000))
  (val probe seen.probe)
  (assert (is-not probe None) seen)
  (assert (= probe.state ProbeState.FAILED) probe)
  (assert (in "秒で終わらない" probe.detail) probe)
  (val deadline (+ probe.started-ms (* PROBE-SECONDS 1000) 1))
  (assert (in deadline seen.ticks) (.format "時間切れの刻 {} に起きていない: {}" deadline seen.ticks))
  (assert (<= deadline probe.failed-ms (+ deadline TICK-MS)) probe)
  ;; 検めの間は状態の表示(経過の秒)が 1 秒ごとに変わるので、その拍には起きる(heartbeat で名乗る)— 0.5 秒ごとには起きない。
  (<- waiting tuple (ticks-between seen probe.started-ms deadline))
  (assert (<= (len waiting) PROBE-SECONDS) (.format "検めが走る間に {} 回起きた: {}" (len waiting) seen.ticks)))


(deftest test-the-due-of-a-probe-is-the-first-moment-its-step-changes
  ;; 眠りが起きる刻(probe-due)は、判断(probe-step)が手を変える最初の刻と同じ — その 1 ms 前は待つ。
  (for [#(started timeout stopping deadline) #(#(1000 3000 None 5000) #(1000 2500.5 None 5000) #(1000 3000 4500 5000))]
    (<- due int (probe-due started timeout stopping deadline))
    (val since-stop (fn [now] (if (is stopping None) None (- now stopping))))
    (<- before ProbeStep (probe-step False (- (- due 1) started) timeout (since-stop (- due 1)) deadline))
    (<- at ProbeStep (probe-step False (- due started) timeout (since-stop due) deadline))
    (assert (= before ProbeStep.WAIT) #(started timeout stopping due before))
    (assert (!= at ProbeStep.WAIT) #(started timeout stopping due at))))


(deftest test-the-due-of-a-preparation-is-the-first-moment-it-is-overdue
  ;; 準備の停滞の期限の刻(prepare-due)は、prepare-overdue が真になる最初の刻と同じ — その 1 ms 前は止めない。
  (for [limits #((PrepareLimits) (PrepareLimits :stall-seconds 0.25))]
    (<- due int (prepare-due 10000 limits))
    (<- before bool (prepare-overdue 10.0 (/ (- due 1) 1000.0) limits))
    (<- at bool (prepare-overdue 10.0 (/ due 1000.0) limits))
    (assert (= #(before at) #(False True)) #(limits due before at))))
