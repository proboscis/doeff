;; worker の job の子 process の言い換え(process-host)が、job を止める時に計時の行を出す形の検(#3713)。
;; 止めの合図(SignalJob の TERM・KILL)ごとと、止めた子の回収(ReapJob)で 1 行ずつ — 段・job の名・pid・この刻(wall-ms)・最初の止めの
;; 合図からの ms(elapsed-ms)・KILL まで行ったか(killed = 猶予を使い切った)・止めた訳(reason)・途絶で止めた時の最後の返事からの ms
;; (silent-ms)。止めずに終わった子の回収は行を出さない。
;; 後半は本物の調整ループ(run-worker)を同じ process-host の上で回し、判断が埋める止めの訳(spec の変化・宣言から外れた・coordinator との
;; 途絶・worker の停止)と、起こしの見送りの行(同じ訳が続く間は 1 本)を見る。
;; 反例 = 行を出さない・訳を埋めない形(直す前の形)は、下の行の列の断言が赤。
;; 土台: 子 process = 台本(scripted-process-handler — 起こした子は止めるまで走り続ける)・file system = memory・時計 = 仮想の時計・
;; coordinator への口・コードの準備の観測 = 台本の宿(worker-world)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess timed-out-outcome])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [StartJob SignalJob ReapJob Outcome StopStage Undeclared CodeView CodeState WorldView
                                                 WorkerPolicy WorkerState DesiredJobs ReadDesired ObserveWorld WorkerStopRequested EnvReport
                                                 PublishStatus PrepareCode ReleaseLeases])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.protocol.process_host [HostSettings STOP-TIMING-LOG process-host])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import tests.fixtures.tick_wake [every-tick-wake])
(import doeff_cluster.worker.core.heartbeat_rules [desired-when-unreachable])
(import doeff_cluster.worker.core.program [run-worker START-HOLD-LOG])
(import tests.host_rig [host-settings])

(val STATE "/state")
(val SPEC (JobSpec "svc" "jobs.svc" #() "rev-a"))
(val SPEC-B (replace SPEC :revision "rev-b"))
(val OTHER (JobSpec "other" "jobs.other" #() "rev-a"))
;; 合図の間の仮想の秒(猶予の代わり)と、合図から終わりを観測して回収するまでの秒。
(val GRACE-SECONDS 10.0)
(val REAP-SECONDS 0.5)
;; 調整ループの方針(拍 0.1 秒)と、coordinator との途絶の柵(本番の 20 秒・240 秒の代わりの短い値)。
(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :tick-seconds 0.1))
(val FENCE-MS 2000)
(val KEEP-FENCE-MS 4000)


(defrecord StopLine
  "止めの計時の行 1 つの欄(stage・job・pid・elapsed-ms・killed・reason・silent-ms — wall-ms は仮想の時計の刻なので elapsed-ms と別に確かめる)。"
  (#^ str stage)
  (#^ str job)
  (#^ int pid)
  (#^ int wall-ms)
  (#^ int elapsed-ms)
  (#^ bool killed)
  (#^ str reason)
  (#^ (| int None) silent-ms))


(defrecord HoldLine
  "起こしの見送りの行 1 つの欄(job・reason)。"
  (#^ str job)
  (#^ str reason))


(defrecord NotedLines
  "筋書きの間に出た行(出た順): stops = 止めの計時の行・holds = 起こしの見送りの行。"
  (#^ (get tuple #(StopLine ...)) stops)
  (#^ (get tuple #(HoldLine ...)) holds))


(defrecord StopScene
  "止めの筋書き 1 回の見え方: pid = 起こした子の pid・lines = 止めの計時の行(出た順)。"
  (#^ int pid)
  (#^ (get tuple #(StopLine ...)) lines))


(defrecord Reply
  "coordinator の返事の台本の 1 行: from-ms 以降の拍の返事。jobs = 宣言の job・reached = 届くか(False = 途絶 — 宣言は本番と同じ
   desired-when-unreachable が最後に届いた宣言から決める)。"
  (#^ int from-ms)
  (#^ tuple jobs)
  (#^ bool reached))


(defrecord CodeScript
  "版の準備の台本: 頼まれた版は failed-ms まで準備中・failed-ms から失敗(None = 失敗しない)・ready-ms から READY(None = 揃わない)。"
  (setv #^ (| int None) failed-ms None)
  (setv #^ (| int None) ready-ms 0))


(defeffect NotedStopLines
  "ここまでに出た行を読む — 検だけの問い(lines-noted が答える)。"
  {:fields [] :answer NotedLines :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler lines-noted
  "外の世界の log の代役: 止めの計時の行(STOP-TIMING-LOG)と起こしの見送りの行(START-HOLD-LOG)の欄を出た順に覚え、他の行は受け流す。
   覚えた列は NotedStopLines で読む。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var stops #())
  (session var holds #())
  (SlogEffect []
    (val fields effect.kwargs)
    (cond
      (= effect.msg STOP-TIMING-LOG)
        (:= stops (+ stops #((StopLine :stage (get fields "stage") :job (get fields "job") :pid (get fields "pid")
                                       :wall-ms (get fields "wall_ms") :elapsed-ms (get fields "elapsed_ms") :killed (get fields "killed")
                                       :reason (get fields "reason") :silent-ms (get fields "silent_ms")))))
      (= effect.msg START-HOLD-LOG)
        (:= holds (+ holds #((HoldLine :job (get fields "job") :reason (get fields "reason")))))
      True None)
    (resume None))
  (NotedStopLines []
    (resume (NotedLines :stops stops :holds holds))))


(defk code-view [revision script now]
  {:pre [(: revision str) (: script CodeScript) (: now int)] :post [(: % CodeView)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "台本の版の準備の、刻 now の観測を作るため(READY の木は検の state dir)。"
  (cond
    (and (is-not script.ready-ms None) (>= now script.ready-ms)) (CodeView revision CodeState.READY STATE)
    (and (is-not script.failed-ms None) (>= now script.failed-ms))
      (CodeView revision CodeState.FAILED :detail "準備に失敗" :failed-ms script.failed-ms)
    True (CodeView revision CodeState.PREPARING)))


(defhandler worker-world [#^ tuple replies #^ CodeScript code #^ int stop-ms]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 本番の宿のうち coordinator への口(宣言の読み)・観測・準備の代役: 宣言は返事の台本(届かない拍は本番と同じ desired-when-unreachable)・
  ;; 子 process の観測は process-host(ObserveProcesses)・版の準備は台本 code・止まれの合図は stop-ms から。
  ;; 引数に残す理由: 検ごとに別の返事の台本・準備の台本・止める刻で並べる(Ask で区別できない)。
  (session var last-ok-ms 0)
  (session var last-jobs #())
  (session var asked #())
  (ReadDesired [env-report stopping]
    (<- now int (now-epoch-ms))
    (val reply (get (lfor r replies :if (<= r.from-ms now) r) -1))
    (if reply.reached
        (do (:= last-ok-ms now)
            (:= last-jobs reply.jobs)
            (resume (DesiredJobs reply.jobs)))
        (resume (desired-when-unreachable (- now last-ok-ms) FENCE-MS KEEP-FENCE-MS last-jobs #() "台本の途絶"))))
  (WorkerStopRequested []
    (<- now int (now-epoch-ms))
    (resume (>= now stop-ms)))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note] (resume None))
  ;; 終わった process の lease の返し(本番は coordinator への口)— この筋書きは lease を数えない。
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision]
    (when (not-in revision asked)
      (:= asked (+ asked #(revision))))
    (resume None))
  (ObserveWorld []
    (<- now int (now-epoch-ms))
    (<- processes tuple (ObserveProcesses))
    (var codes #())
    (for [revision asked]
      (<- view CodeView (code-view revision code now))
      (:= codes (+ codes #(view))))
    (resume (WorldView codes processes))))


(defk runs-until-stopped [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の shim(job の子の代役): 止めるまで走り続ける。"
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk stopped-scene [stages]
  {:pre [(: stages (get tuple #(StopStage ...)))] :post [(: % StopScene)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job を起こし、止めの合図 stages を GRACE-SECONDS おきに(訳 = 宣言から外れた)送り、終わりを観測して REAP-SECONDS 後に回収するため。"
  (<- (StartJob SPEC 1 STATE))
  (<- started tuple (ObserveProcesses))
  (val pid (. (get started 0) pid))
  (var first True)
  (for [stage stages]
    (when (not first)
      (<- (Delay GRACE-SECONDS)))
    (:= first False)
    (<- (SignalJob SPEC.name pid stage (Undeclared))))
  (<- ended tuple (ObserveProcesses))
  (<- (Delay REAP-SECONDS))
  (<- (ReapJob SPEC.name pid Outcome.STOPPED (. (get ended 0) exit-code)))
  (<- noted NotedLines (NotedStopLines))
  (StopScene :pid pid :lines noted.stops))


(defk ended-by-itself []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めずに終わった子(ここでは観測の外で終わった扱いの子を回収する)の回収が、止めの計時の行を出さないことを見るため。"
  (<- (StartJob SPEC 1 STATE))
  (<- started tuple (ObserveProcesses))
  (<- (ReapJob SPEC.name (. (get started 0) pid) Outcome.EXITED 0))
  (<- noted NotedLines (NotedStopLines))
  noted.stops)


(defk worker-run []
  {:pre [] :post [(: % NotedLines)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止まれの合図まで回し、出た行を返すため。"
  (<- (run-worker POLICY))
  (<- noted NotedLines (NotedStopLines))
  noted)


(defk on-scripted-host [program [inner []]]
  {:pre [(: program Program) (: inner list)] :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を process-host と台本の子 process・memory の file system・仮想の時計の下で回すため(止めの計時の行は process-host の外側の
   lines-noted が受ける)。inner = process-host の内側に置く handler(調整ループを回す筋書きは宿の代役 worker-world と拍の間の眠り)。"
  (<- settings HostSettings (host-settings (Path STATE) :policy POLICY))
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path settings.python) name) :run runs-until-stopped))))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) lines-noted (memory-file-handler (MemoryFiles :dirs #(STATE)))
                                  (scripted-process-handler script) (process-host settings) #* inner]
                                 program))))


(defk lines-of [lines job]
  {:pre [(: lines tuple) (: job str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "job の止めの計時の行を #(段 訳) の列にするため。"
  (tuple (gfor line lines :if (= line.job job) #(line.stage line.reason))))


(deftest test-a-job-stopped-with-term-only-has-a-term-line-and-a-reaped-line
  (<- scene StopScene (on-scripted-host (stopped-scene #(StopStage.TERM))))
  (assert (= (tuple (gfor line scene.lines #(line.stage line.job line.pid line.elapsed-ms line.killed line.reason line.silent-ms)))
             #(#("term" "svc" scene.pid 0 False "undeclared" None)
               #("reaped" "svc" scene.pid (int (* 1000 REAP-SECONDS)) False "undeclared" None)))
          scene.lines)
  ;; wall-ms はこの刻 — 回収の行は止めの合図の行の REAP-SECONDS 後。
  (assert (= (- (. (get scene.lines 1) wall-ms) (. (get scene.lines 0) wall-ms)) (int (* 1000 REAP-SECONDS))) scene.lines))


(deftest test-a-job-killed-after-the-grace-names-the-kill-and-the-elapsed-time
  ;; 猶予を使い切った止め: TERM の GRACE-SECONDS 後に KILL — KILL の行と回収の行は killed True で、合図からの ms と最初の合図の訳を名乗る。
  (<- scene StopScene (on-scripted-host (stopped-scene #(StopStage.TERM StopStage.KILL))))
  (val grace-ms (int (* 1000 GRACE-SECONDS)))
  (assert (= (tuple (gfor line scene.lines #(line.stage line.elapsed-ms line.killed line.reason)))
             #(#("term" 0 False "undeclared") #("kill" grace-ms True "undeclared")
               #("reaped" (+ grace-ms (int (* 1000 REAP-SECONDS))) True "undeclared")))
          scene.lines))


(deftest test-a-job-that-ended-by-itself-has-no-stop-line
  (<- lines tuple (on-scripted-host (ended-by-itself)))
  (assert (= lines #()) lines))


(deftest test-a-changed-spec-and-an-undeclared-job-are-stopped-with-their-reasons
  ;; 1 秒目の返事で svc の版が変わり、other が宣言から外れる。3 秒目に worker が止まる(残る svc は worker の停止の訳)。
  (val replies #((Reply :from-ms 0 :jobs #(SPEC OTHER) :reached True) (Reply :from-ms 1000 :jobs #(SPEC-B) :reached True)))
  (<- noted NotedLines (on-scripted-host (worker-run) [(worker-world replies (CodeScript) 3000) every-tick-wake tick-pauses]))
  (assert (= (! (lines-of noted.stops "svc"))
             #(#("term" "spec-changed") #("reaped" "spec-changed") #("term" "worker-stopping") #("reaped" "worker-stopping")))
          noted.stops)
  (assert (= (! (lines-of noted.stops "other")) #(#("term" "undeclared") #("reaped" "undeclared"))) noted.stops)
  (assert (all (gfor line noted.stops (is line.silent-ms None))) noted.stops))


(deftest test-a-worker-cut-off-past-the-fence-stops-its-job-with-cut-off-and-the-silence
  ;; 1 秒目から coordinator に届かない。最後に届いた返事から FENCE-MS を越えた拍で、宣言を絞って svc を止める(訳 = 途絶・最後の返事からの
  ;; ms つき)。5 秒目に届くようになり起こし直し、7 秒目の worker の停止で止める。
  (val replies #((Reply :from-ms 0 :jobs #(SPEC) :reached True) (Reply :from-ms 1000 :jobs #(SPEC) :reached False)
                 (Reply :from-ms 5000 :jobs #(SPEC) :reached True)))
  (<- noted NotedLines (on-scripted-host (worker-run) [(worker-world replies (CodeScript) 7000) every-tick-wake tick-pauses]))
  (assert (= (! (lines-of noted.stops "svc"))
             #(#("term" "cut-off") #("reaped" "cut-off") #("term" "worker-stopping") #("reaped" "worker-stopping")))
          noted.stops)
  (val cut (get noted.stops 0))
  ;; 最後に届いた返事は 1 秒目の直前の拍。柵を越えた最初の拍で止める(拍 1 つの幅の内)。
  (assert (< FENCE-MS cut.silent-ms (+ FENCE-MS 200)) cut)
  (assert (= (. (get noted.stops 1) silent-ms) cut.silent-ms) noted.stops))


(deftest test-a-held-start-is-noted-once-while-the-reason-lasts-and-again-when-it-changes
  ;; svc の版の準備は 1 秒目まで準備中(拍 0.1 秒 — 10 拍ほど続く)、1 秒目から失敗(撃ち直しの間 30 秒より前に 2 秒目で止まる)。
  (val replies #((Reply :from-ms 0 :jobs #(SPEC) :reached True)))
  (<- noted NotedLines (on-scripted-host (worker-run) [(worker-world replies (CodeScript :failed-ms 1000 :ready-ms None) 2000) every-tick-wake tick-pauses]))
  (assert (= noted.holds #((HoldLine :job "svc" :reason "preparing") (HoldLine :job "svc" :reason "prepare-failed"))) noted.holds)
  (assert (= noted.stops #()) noted.stops))
