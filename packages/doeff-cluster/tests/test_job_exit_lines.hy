;; 止めを求めていない job の終わりを回収した拍の 1 行の検(card ki-cb9f34c49f83)。以前は止めた終わりの計時の行(process_host の
;; STOP-TIMING-LOG)しか無く、job が SIGSEGV で落ちても worker の log に落ち方が 1 行も残らなかった(終了 code 139 は状態の file の
;; lastExitCode を読むまで分からない)。
;;   前半: 終了 code から signal の名を読む表(exit-signal)と、回収の action から行の欄を作る判断(exit-notes)— 純粋な関数。
;;   後半: 本物の shim の下で SIGSEGV で落ちる本物の子 process を、本物の調整ループ(run-worker)で回収させ、その拍の 1 行に
;;         終了 code 139・signal SIGSEGV・起こし直すまでの間が出る事を見る。
;; 反例 = 行を出さない形(直す前の形)は、後半の行の列の断言が赤。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import sys)
(import pathlib [Path])
(import pytest)
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_exit [process-exit-handler])
(import doeff_time [async-time-handler])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.wake_fixtures [wakes-every])
(import tests.host_rig [host-settings])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [ReapJob SignalJob Outcome StopStage Undeclared CodeView CodeState WorldView ProcessView
                                                 JobRecord WorkerPolicy DesiredJobs ReadDesired ObserveWorld EnvReport PublishStatus
                                                 PrepareCode ReleaseLeases])
(import doeff_cluster.worker.core.policy [ExitNote exit-signal exit-notes])
(import doeff_cluster.worker.core.worker_rules [retired-name])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.protocol.process_host [HostSettings process-host])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_cluster.worker.core.program [run-worker JOB-EXIT-LOG])

;; この file の後半の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の
;; 眠りを、検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)

(val LINUX (.startswith sys.platform "linux"))
(val NOT-LINUX "入れ物 shim の子孫の引き取り(PR_SET_CHILD_SUBREAPER)は Linux だけ — 本物の shim の下の子の検の範囲は Linux の worker")
(val HY (str (/ (. (Path sys.executable) parent) "hy")))
;; job の木 = この package の dir(sleeper を tests.fixtures.sleeper として読める)。
(val PACKAGE (str (. (Path __file__) parent parent)))
(val POLICY (WorkerPolicy))
(val SERVICE (JobSpec "svc" "jobs.svc" #() "rev-a"))
(val TASK (replace SERVICE :name "task/1" :once True))
;; 本物の筋書きが落ちの行を待つ上限(ms)— 越えたら行の無いまま止めて、断言が名指す。
(val CRASH-DEADLINE-MS 30000)


;; --- 前半: 純粋な判断 ---------------------------------------------------------------------------

(deftest test-exit-signal-reads-the-shim-code-and-the-negative-code
  ;; 入れ物 shim は job の signal の終わりを 128 + 番号で終わる(139 = SIGSEGV・137 = SIGKILL)。子が直に signal で終わった code は負
  ;; (-11 = SIGSEGV)。0・1 などの普通の終わりと、待ちの子の終わりの印が無い -1 は signal と読まない。
  (assert (= (exit-signal 139) "SIGSEGV"))
  (assert (= (exit-signal 137) "SIGKILL"))
  (assert (= (exit-signal 143) "SIGTERM"))
  (assert (= (exit-signal -11) "SIGSEGV"))
  (assert (= (exit-signal -9) "SIGKILL"))
  (assert (is (exit-signal 0) None))
  (assert (is (exit-signal 1) None))
  (assert (is (exit-signal 128) None))
  (assert (is (exit-signal -1) None)))


(defk notes-of [views actions records]
  {:pre [(: views tuple) (: actions tuple) (: records dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 10000 の観測 views と、数えた後の記憶 records で、actions の回収の行の欄を求めるため。"
  (<- notes tuple (exit-notes 10000 (WorldView #() views) records actions POLICY))
  notes)


(deftest test-an-unexpected-end-of-a-service-names-the-code-the-signal-the-run-and-the-backoff
  ;; 起こして 7 秒で 139 で終わった service: 起こし直すまでの間は数えた後の続けて終わった回数(2 回目 → restart-backoff-ms の倍)。
  (val view (ProcessView "svc" SERVICE 3 4242 3000 :exit-code 139))
  (<- notes tuple (notes-of #(view) #((ReapJob "svc" 4242 Outcome.EXITED 139))
                            {"svc" (JobRecord "svc" :unexpected-exits 2 :failures 2)}))
  (assert (= notes #((ExitNote :name "svc" :pid 4242 :exit-code 139 :signal "SIGSEGV" :ran-ms 7000
                               :backoff-ms (* 2 POLICY.restart-backoff-ms) :memory-killed False)))
          notes))


(deftest test-a-stopped-end-has-no-note-and-a-task-or-a-retired-process-has-no-backoff
  ;; 止めた終わり(Outcome.STOPPED)は止めの計時の行が名乗るので、この行を出さない。task と退いた process は起こし直さない(間は None)。
  (val stopped (ProcessView "svc" SERVICE 1 10 0 :exit-code 143))
  (val task (ProcessView "task/1" TASK 1 11 9000 :exit-code 0))
  (val retired-as (retired-name "svc" "1-abc"))
  (val retired (ProcessView retired-as SERVICE 1 12 1000 :exit-code -11 :retired-from "svc"))
  (<- notes tuple (notes-of #(stopped task retired)
                            #((ReapJob "svc" 10 Outcome.STOPPED 143) (ReapJob "task/1" 11 Outcome.EXITED 0)
                              (ReapJob retired-as 12 Outcome.EXITED -11))
                            {}))
  (assert (= notes #((ExitNote :name "task/1" :pid 11 :exit-code 0 :signal None :ran-ms 1000 :backoff-ms None :memory-killed False)
                     (ExitNote :name retired-as :pid 12 :exit-code -11 :signal "SIGSEGV" :ran-ms 9000 :backoff-ms None
                               :memory-killed False)))
          notes))


;; --- 後半: 本物の shim の下の SIGSEGV の子を本物の調整ループで回収する ---------------------------------------

(defrecord ExitLine
  "回収の拍の行 1 つの欄(level・job・pid・exit-code・signal・ran-ms・backoff-ms・memory-killed)。"
  (#^ str level)
  (#^ str job)
  (#^ int pid)
  (#^ int exit-code)
  (#^ (| str None) signal)
  (#^ int ran-ms)
  (#^ (| int None) backoff-ms)
  (#^ bool memory-killed))


(defeffect NotedExitLines
  "ここまでに出た回収の拍の行を読む — 検だけの問い(exit-lines-noted が答える)。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler exit-lines-noted
  "外の世界の log の代役: 回収の拍の行(JOB-EXIT-LOG)の欄を出た順に覚え、他の行は受け流す。覚えた列は NotedExitLines で読む。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (SlogEffect []
    (val fields effect.kwargs)
    (when (= effect.msg JOB-EXIT-LOG)
      (:= lines (+ lines #((ExitLine :level (get fields "level") :job (get fields "job") :pid (get fields "pid")
                                     :exit-code (get fields "exit_code") :signal (get fields "signal") :ran-ms (get fields "ran_ms")
                                     :backoff-ms (get fields "backoff_ms") :memory-killed (get fields "memory_killed"))))))
    (resume None))
  (NotedExitLines []
    (resume lines)))


(defhandler crashing-world [#^ JobSpec spec #^ str tree #^ int limit-ms]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 本番の宿のうち coordinator への口(宣言の読み)・観測・準備の代役: 宣言は spec 1 つ・版の木は頼まれたらすぐ READY(tree)・子 process の
  ;; 観測は process-host(ObserveProcesses)・止まれの合図は回収の拍の行が 1 本出た後か、最初の問いから limit-ms を過ぎた後。
  ;; 引数に残す理由: 検ごとに別の job・木・期限で並べる(Ask で区別できない)。
  (session var asked #())
  (session var began None)
  (ReadDesired [env-report stopping]
    (resume (DesiredJobs #(spec))))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (when (is began None) (:= began now))
    (<- lines tuple (NotedExitLines))
    (resume (if (or lines (>= (- now began) limit-ms)) "signal 15" None)))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note] (resume None))
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision]
    (when (not-in revision asked)
      (:= asked (+ asked #(revision))))
    (resume None))
  (ObserveWorld []
    (<- processes tuple (ObserveProcesses))
    (resume (WorldView (tuple (gfor revision asked (CodeView revision CodeState.READY tree))) processes))))


(defk crash-run []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止まれの合図まで回し、出た回収の拍の行を返すため。"
  (<- (run-worker POLICY))
  (<- lines tuple (NotedExitLines))
  lines)


(deftest test-a-job-killed-by-sigsegv-under-the-real-shim-leaves-a-line-with-the-code-and-the-signal [tmp-path]
  {:skip-if (not LINUX) :skip-reason NOT-LINUX}
  ;; 起こされてすぐ SIGSEGV で落ちる sleeper(--segfault)を、本物の shim・本物の子 process の答え手・本物の run-worker で回す。落ちを
  ;; 観測した拍の回収で 1 行 — 終了 code は shim が写した 139・signal は SIGSEGV・起こし直すまでの間は 1 回目の restart-backoff-ms。
  (<- settings HostSettings (host-settings tmp-path :hy-command HY :policy POLICY))
  (val spec (JobSpec "svc" "tests.fixtures.sleeper" #((str (/ tmp-path "beat")) "--segfault") "rev-a"))
  (val lines (run (scheduled (with-handlers [(await-handler) (async-time-handler) subprocess-handler process-exit-handler (state)
                                             os-file-handler exit-lines-noted (process-host settings)
                                             (crashing-world spec PACKAGE CRASH-DEADLINE-MS)
                                             stop-signal-never-comes tick-pauses (wakes-every 100)]
                                            (crash-run)))))
  (assert (= (len lines) 1) lines)
  (val line (get lines 0))
  (assert (= #(line.level line.job line.exit-code line.signal line.backoff-ms line.memory-killed)
             #("warning" "svc" 139 "SIGSEGV" POLICY.restart-backoff-ms False))
          line)
  (assert (> line.pid 0) line)
  (assert (<= 0 line.ran-ms CRASH-DEADLINE-MS) line)
  ;; 落ちる前に拍を 1 度書いている(起こした job が実際に走って落ちた — 起こせずに終わったのではない)。
  (assert (.exists (/ tmp-path "beat"))))
