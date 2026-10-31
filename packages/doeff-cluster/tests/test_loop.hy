;; 調整ループ全体を仮想時計・偽の子 process・台本の宣言で決定的に動かす。
(require doeff-hy.macros [deftest defk defhandler <- var val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [Program run])
(import dataclasses [replace])
(import doeff_time [SimClock sim-time-handler])
(import doeff_core_effects [slog-discard-handler])
(import doeff_core_effects [StopRequested])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster [CodeState CodeView ProcessView WorldView StopStage WorkerPolicy ReadDesired ObserveWorld PublishStatus EnvReport PrepareCode StartJob SignalJob ReapJob]
        doeff_cluster._worker.intent.worker_model [WorkerState DesiredJobs DesiredUnreadable JobStarted JobNotStarted] doeff_cluster [JobSpec JobPhase])
(import doeff_cluster._worker.core.program [run-worker])
(import doeff_cluster [tick-pauses])
(import tests.wake_fixtures [wakes-every])

(setv POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000)
      A1 (JobSpec "a" "jobs.a" #() "rev1")
      A2 (replace A1 :revision "rev2")
      B1 (JobSpec "b" "jobs.b" #() "rev1"))

(defclass FakeWorld []
  "子 process とコードの展開を模す。TERM を無視する job と、KILL でも死なない job を指定できる。crashes = job の名 → 自分で落ちる刻
   (その刻の後の最初の観測で exit code 1 — 1 度だけ)・leaving-at = この宿の Pod の止まり(preStop)が始まる刻(その後の観測は
   WorldView.leaving — AL-D2p)・refusals = job の名 → 起こすのを断る回数(StartJob の答えを JobNotStarted にする)。"
  (defn #^ None __init__ [self #^ tuple [ignore-term #()] #^ tuple [unkillable #()] #^ dict [crashes None] #^ (| int None) [leaving-at None]
                          #^ dict [refusals None]]
    ;; 時刻は doeff-time の仮想の時計(epoch 0 から)。now = その epoch ミリ秒(記録の刻)。
    (setv self.clock (SimClock) self.codes {} self.procs {} self.next-pid 100 self.events []
          self.ignore-term ignore-term self.unkillable unkillable self.statuses []
          self.crashes (dict (or crashes {})) self.leaving-at leaving-at self.refusals (dict (or refusals {}))))
  (defn [property] #^ int now [self] (run (clock-ms self.clock)))
  (defn #^ WorldView observe [self]
    ;; 展開は依頼の次の拍で終わる。
    (setv views (tuple (gfor #(rev state) (.items self.codes)
      (CodeView rev state (when (= state CodeState.READY) f"/c/{rev}")))))
    (for [rev (list self.codes)] (setv (get self.codes rev) CodeState.READY))
    (for [#(name at) (list (.items self.crashes))]
      (when (and (>= self.now at) (in name self.procs))
        (setv (get self.procs name) (replace (get self.procs name) :exit-code 1))
        (del (get self.crashes name))))
    (WorldView views (tuple (.values self.procs))
               :leaving (and (is-not self.leaving-at None) (>= self.now self.leaving-at))))
  (defn #^ (| JobStarted JobNotStarted) start [self #^ StartJob action]
    (setv name action.spec.name)
    (when (> (.get self.refusals name 0) 0)
      (setv (get self.refusals name) (- (get self.refusals name) 1))
      (.append self.events #("refused" name action.spec.revision self.now))
      (return (JobNotStarted :name name :warm-key None :detail "子を起こせない: 模擬の断り" :at-ms self.now)))
    (setv self.next-pid (+ self.next-pid 1))
    (.append self.events #("start" name action.spec.revision self.now))
    (setv (get self.procs name)
      (ProcessView name action.spec action.attempt self.next-pid self.now))
    (JobStarted :name name :pid self.next-pid))
  (defn #^ None signal [self #^ SignalJob action]
    (.append self.events #("signal" action.name action.stage.value self.now))
    (setv proc (get self.procs action.name))
    (setv dies (if (= action.stage StopStage.TERM)
                 (not-in action.name self.ignore-term)
                 (not-in action.name self.unkillable)))
    (when dies
      (setv (get self.procs action.name) (replace proc :exit-code (if (= action.stage StopStage.TERM) 0 -9)))))
  (defn #^ None reap [self #^ ReapJob action]
    (.append self.events #("reap" action.name action.outcome.value self.now))
    (del (get self.procs action.name))))

(defhandler fake-host-script [#^ FakeWorld world #^ tuple script #^ int stop-at]
  (ReadDesired []
    ;; 台本 = #((開始時刻 宣言) ...)。その時刻以前で最後の宣言を返す。
    (var current (DesiredJobs #()))
    (for [#(at desired) script] (when (>= world.now at) (:= current desired)))
    (resume current))
  (StopRequested [] (resume (if (>= world.now stop-at) "signal 15" None)))
  (EnvReport [] (resume None))
  (ObserveWorld [] (resume (.observe world)))
  (PublishStatus [statuses note] (.append world.statuses #(world.now statuses note)) (resume None))
  (PrepareCode [revision]
    (setv (get world.codes revision) CodeState.PREPARING) (resume None))
  (StartJob [spec attempt code-path] (resume (.start world (StartJob spec attempt code-path))))
  (SignalJob [name pid stage reason] (.signal world (SignalJob name pid stage reason)) (resume None))
  (ReapJob [name pid outcome exit-code] (.reap world (ReapJob name pid outcome exit-code)) (resume None)))

(defk fake-host [world script stop-at program]
  {:pre [(: world FakeWorld) (: script tuple) (: stop-at int) (: program Program)] :post [(: % WorkerState)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を台本の世界(fake-host-script)の下で走らせ、その答えを返すため。台本の外側に仮想の時計(world の SimClock)を、内側に拍の間の
   眠りの本番の答え手 tick-pauses を被せる(tick-pauses の止めの問いは台本が答え、止めの合図は来ない — stop-signal-never-comes)。
   拍の間の眠りは仮想の時刻を進めるだけで、実時間は使わない。"
  (<- answer WorkerState ((sim-time-handler :clock world.clock) ((fake-host-script world script stop-at) (stop-signal-never-comes ((wakes-every 100) (tick-pauses (slog-discard-handler program)))))))
  answer)

(defk events-of [world name]
  {:pre [(: world FakeWorld) (: name str)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "世界の出来事のうち job name の物を、#(種類 名 版か止め方の語) の list で読むため(時刻は落とす)。"
  (lfor e world.events :if (= (get e 1) name) (cut e 0 3)))

(deftest test-update-one-job-keeps-the-other-running
  (setv world (FakeWorld)
        script #(#(0 (DesiredJobs #(A1 B1))) #(1000 (DesiredJobs #(A2 B1)))))
  (<- (fake-host world script 3000 (run-worker POLICY)))
  ;; a は旧版を止めてから新版を起動する(同時稼働しない)。
  (assert (= (! (events-of world "a"))
             [#("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")
              #("start" "a" "rev2") #("signal" "a" "term") #("reap" "a" "stopped")]))
  ;; b は worker の停止まで一度も触られない。
  (assert (= (! (events-of world "b"))
             [#("start" "b" "rev1") #("signal" "b" "term") #("reap" "b" "stopped")]))
  (setv b-signal (lfor e world.events :if (= (cut e 0 2) #("signal" "b")) (get e 3)))
  (assert (>= (get b-signal 0) 3000))
  (assert (= world.procs {})))

(deftest test-a-leaving-pod-does-not-restart-a-crashed-handoff-job
  ;; 調整ループ全体で(AL-D2p): 宿の観測が止まり(preStop の始まり)になった後は、同じ
  ;; 判断の Program(run-worker)が、落ちた handoff の job を起こし直さない。止まりでなければ backoff の後に起こし直す。変えるのは宿の
  ;; 観測(FakeWorld の leaving-at)だけ。
  (val handoff (replace A1 :handoff True))
  (val script #(#(0 (DesiredJobs #(handoff)))))
  (val staying (FakeWorld :crashes {"a" 1000}))
  (<- (fake-host staying script 6000 (run-worker POLICY)))
  (assert (= (! (events-of staying "a"))
             [#("start" "a" "rev1") #("reap" "a" "exited") #("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")]))
  (val leaving (FakeWorld :crashes {"a" 1000} :leaving-at 500))
  (<- (fake-host leaving script 6000 (run-worker POLICY)))
  (assert (= (! (events-of leaving "a")) [#("start" "a" "rev1") #("reap" "a" "exited")])
          "止まりの後に落ちた handoff の job は起こし直さない"))

(deftest test-unreadable-declaration-does-not-stop-jobs
  (setv world (FakeWorld)
        script #(#(0 (DesiredJobs #(A1))) #(500 (DesiredUnreadable "JSON が壊れています"))))
  (<- (fake-host world script 2000 (run-worker POLICY)))
  (assert (= (! (events-of world "a")) [#("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")]))
  (setv signal-at (get (lfor e world.events :if (= (get e 0) "signal") (get e 3)) 0))
  (assert (>= signal-at 2000))
  ;; 読めない宣言の理由を状態表示に載せる。
  (assert (in "JSON が壊れています" (lfor s world.statuses (get s 2)))))

(deftest test-stubborn-job-escalates-and-unkillable-is-reported
  (setv world (FakeWorld :ignore-term #("a" "b") :unkillable #("b"))
        script #(#(0 (DesiredJobs #(A1 B1)))))
  (<- final WorkerState (fake-host world script 500 (run-worker POLICY)))
  (setv a-events (! (events-of world "a")))
  (assert (= a-events [#("start" "a" "rev1") #("signal" "a" "term") #("signal" "a" "kill") #("reap" "a" "stopped")]))
  ;; b は KILL でも終わらない。worker は猶予の後に諦めて終わり、停止未確認として報告する。
  (assert (in "b" world.procs))
  (setv #(_ last-statuses _) (get world.statuses -1))
  (assert (= (lfor s last-statuses :if (= s.name "b") s.phase) [JobPhase.STOP-UNCONFIRMED])))

(deftest test-a-job-that-cannot-be-started-backs-off-alone-and-the-worker-keeps-running
  ;; 宿が「起こせなかった」(StartJob の答え JobNotStarted)と答えても worker の拍は落ちず、その job だけを落ちた時と
  ;; 同じ backoff の後に起こし直す。同じ拍の b は起こし、worker の停止まで触らない(前は宿の OSError が拍を抜けて worker ごと落ちた)。
  (setv world (FakeWorld :refusals {"a" 2})
        script #(#(0 (DesiredJobs #(A1 B1)))))
  (<- (fake-host world script 20000 (run-worker POLICY)))
  (assert (= (! (events-of world "a"))
             [#("refused" "a" "rev1") #("refused" "a" "rev1") #("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")]))
  (assert (= (! (events-of world "b")) [#("start" "b" "rev1") #("signal" "b" "term") #("reap" "b" "stopped")]))
  ;; 2 度目の起こしは backoff(2 秒)の後、3 度目は倍の 4 秒の後。
  (setv a-times (lfor e world.events :if (= (get e 1) "a") (get e 3)))
  (assert (>= (- (get a-times 1) (get a-times 0)) 2000) a-times)
  (assert (>= (- (get a-times 2) (get a-times 1)) 4000) a-times)
  ;; 断りの間の状態の行は backoff と訳を名乗る。
  (setv refused-rows (lfor #(_ rows _) world.statuses row rows :if (and (= row.name "a") (= row.phase JobPhase.BACKOFF)) row))
  (assert refused-rows)
  (assert (.startswith (. (get refused-rows 0) detail) "起こせなかった(shim): 子を起こせない: 模擬の断り") (. (get refused-rows 0) detail)))
