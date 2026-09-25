;; 調整ループ全体を仮想時計・偽の子 process・台本の宣言で決定的に動かす。
(require doeff-hy.macros [deftest defhandler <-])
(import collections.abc [Callable])
(import dataclasses [replace])
(import doeff_time [SimClock sim-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage JobPhase
  WorkerPolicy WorkerState DesiredJobs DesiredUnreadable ReadDesired ObserveWorld
  WorkerStopRequested PublishStatus PrepareCode StartJob SignalJob ReapJob])
(import doeff_cluster.worker [run-worker])

(setv POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000
                           :tick-seconds 0.1)
      A1 (JobSpec "a" "jobs.a" #() "rev1")
      A2 (replace A1 :revision "rev2")
      B1 (JobSpec "b" "jobs.b" #() "rev1"))

(defclass FakeWorld []
  "子 process とコードの展開を模す。TERM を無視する job と、KILL でも死なない job を指定できる。"
  (defn __init__ [self [ignore-term #()] [unkillable #()]]
    ;; 時刻は doeff-time の仮想の時計(epoch 0 から)。now = その epoch ミリ秒(記録の刻)。
    (setv self.clock (SimClock) self.codes {} self.procs {} self.next-pid 100 self.events []
          self.ignore-term ignore-term self.unkillable unkillable self.statuses []))
  (defn [property] #^ int now [self] (clock-ms self.clock))
  (defn observe [self]
    ;; 展開は依頼の次の拍で終わる。
    (setv views (tuple (gfor #(rev state) (.items self.codes)
      (CodeView rev state (when (= state CodeState.READY) f"/c/{rev}")))))
    (for [rev (list self.codes)] (setv (get self.codes rev) CodeState.READY))
    (WorldView views (tuple (.values self.procs))))
  (defn start [self action]
    (setv self.next-pid (+ self.next-pid 1))
    (.append self.events #("start" action.spec.name action.spec.revision self.now))
    (setv (get self.procs action.spec.name)
      (ProcessView action.spec.name action.spec action.attempt self.next-pid self.now)))
  (defn signal [self action]
    (.append self.events #("signal" action.name action.stage.value self.now))
    (setv proc (get self.procs action.name))
    (setv dies (if (= action.stage StopStage.TERM)
                 (not-in action.name self.ignore-term)
                 (not-in action.name self.unkillable)))
    (when dies
      (setv (get self.procs action.name) (replace proc :exit-code (if (= action.stage StopStage.TERM) 0 -9)))))
  (defn reap [self action]
    (.append self.events #("reap" action.name action.outcome.value self.now))
    (del (get self.procs action.name))))

(defhandler fake-host-script [#^ FakeWorld world #^ tuple script #^ int stop-at]
  (ReadDesired []
    ;; 台本 = #((開始時刻 宣言) ...)。その時刻以前で最後の宣言を返す。
    (setv current (DesiredJobs #()))
    (for [#(at desired) script] (when (>= world.now at) (setv current desired)))
    (resume current))
  (WorkerStopRequested [] (resume (>= world.now stop-at)))
  (ObserveWorld [] (resume (.observe world)))
  (PublishStatus [statuses note] (.append world.statuses #(world.now statuses note)) (resume None))
  (PrepareCode [revision]
    (setv (get world.codes revision) CodeState.PREPARING) (resume None))
  (StartJob [spec attempt code-path] (.start world (StartJob spec attempt code-path)) (resume None))
  (SignalJob [name pid stage] (.signal world (SignalJob name pid stage)) (resume None))
  (ReapJob [name pid outcome exit-code] (.reap world (ReapJob name pid outcome exit-code)) (resume None)))

(defn #^ Callable fake-host [#^ FakeWorld world #^ tuple script #^ int stop-at]
  "台本の外側に仮想の時計(world の SimClock)を被せる。拍の間の眠りは仮想の時刻を進めるだけで、実時間は使わない。"
  (fn [program] ((sim-time-handler :clock world.clock) ((fake-host-script world script stop-at) program))))

(defn events-of [world name]
  (lfor e world.events :if (= (get e 1) name) (cut e 0 3)))

(deftest test-update-one-job-keeps-the-other-running
  (setv world (FakeWorld)
        script #(#(0 (DesiredJobs #(A1 B1))) #(1000 (DesiredJobs #(A2 B1)))))
  (<- ((fake-host world script 3000) (run-worker POLICY)))
  ;; a は旧版を止めてから新版を起動する(同時稼働しない)。
  (assert (= (events-of world "a")
             [#("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")
              #("start" "a" "rev2") #("signal" "a" "term") #("reap" "a" "stopped")]))
  ;; b は worker の停止まで一度も触られない。
  (assert (= (events-of world "b")
             [#("start" "b" "rev1") #("signal" "b" "term") #("reap" "b" "stopped")]))
  (setv b-signal (lfor e world.events :if (= (cut e 0 2) #("signal" "b")) (get e 3)))
  (assert (>= (get b-signal 0) 3000))
  (assert (= world.procs {})))

(deftest test-unreadable-declaration-does-not-stop-jobs
  (setv world (FakeWorld)
        script #(#(0 (DesiredJobs #(A1))) #(500 (DesiredUnreadable "JSON が壊れています"))))
  (<- ((fake-host world script 2000) (run-worker POLICY)))
  (assert (= (events-of world "a") [#("start" "a" "rev1") #("signal" "a" "term") #("reap" "a" "stopped")]))
  (setv signal-at (get (lfor e world.events :if (= (get e 0) "signal") (get e 3)) 0))
  (assert (>= signal-at 2000))
  ;; 読めない宣言の理由を状態表示に載せる。
  (assert (in "JSON が壊れています" (lfor s world.statuses (get s 2)))))

(deftest test-stubborn-job-escalates-and-unkillable-is-reported
  (setv world (FakeWorld :ignore-term #("a" "b") :unkillable #("b"))
        script #(#(0 (DesiredJobs #(A1 B1)))))
  (<- final WorkerState ((fake-host world script 500) (run-worker POLICY)))
  (setv a-events (events-of world "a"))
  (assert (= a-events [#("start" "a" "rev1") #("signal" "a" "term") #("signal" "a" "kill") #("reap" "a" "stopped")]))
  ;; b は KILL でも終わらない。worker は猶予の後に諦めて終わり、停止未確認として報告する。
  (assert (in "b" world.procs))
  (setv #(_ last-statuses _) (get world.statuses -1))
  (assert (= (lfor s last-statuses :if (= s.name "b") s.phase) [JobPhase.STOP-UNCONFIRMED])))
