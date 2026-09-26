;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に worker の実行環境で入口(factory と env)を読み込めるかを試し、
;; 通るまで起こさない・入れ替えの旧を外さない。純粋な判断(worker_policy)・実の子 process(handlers.ProbeStore)・入口(job_entry probe)。
(require doeff-hy.macros [deftest val])
(import dataclasses [replace])
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_cluster.worker_model [JobSpec CodeState CodeView ProcessView WorldView ProbeState ProbeView ProbeStatus JobPhase JobRecord
                        WorkerPolicy PrepareCode StartJob RetireJob ProbeEntry spec-hash probed-job probe-args])
(import doeff_cluster.worker_policy [plan statuses])
(import doeff_cluster.handlers [ProbeStore probe-reason status-row])
(import doeff_cluster.job_entry [probe-problem])
(import doeff_cluster.cluster_policy [JOB-ENTRY LIVE-PHASES spec-of-declaration])

(setv POLICY (WorkerPolicy :code-retry-ms 30000)
      RUN {"kind" "service" "factory" "m.f:program" "env" "m.e:handlers" "config" {}}
      S1 (spec-of-declaration {"name" "w" "revision" "rev1" "run" RUN "update" "handoff"})
      S2 (replace S1 :revision "rev2")
      READY1 (CodeView "rev1" CodeState.READY "/c/rev1")
      READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))

(defn #^ WorldView world [#^ ProcessView #* processes #^ tuple [codes #(READY1)] #^ tuple [probes #()]] (WorldView codes processes probes))
(defn #^ ProcessView running [#^ JobSpec spec #^ int [pid 10]] (ProcessView spec.name spec 1 pid 0 :instance (.format "1-{}" pid)))
(defn #^ ProbeView passed [#^ JobSpec spec] (ProbeView (spec-hash spec) ProbeState.PASSED))
(defn #^ ProbeView failed [#^ JobSpec spec #^ int [at 1000]] (ProbeView (spec-hash spec) ProbeState.FAILED :detail "ImportError: cannot import name 'GetMonotonic'" :failed-ms at))


(defn #^ None test-only-service-jobs-are-probed []
  (assert (probed-job S1))
  (assert (= (probe-args S1) #("probe" "--factory" "m.f:program" "--env" "m.e:handlers")))
  (assert (not (probed-job (JobSpec "a" "jobs.a" #() "rev1"))))
  (assert (not (probed-job (JobSpec "task/1" JOB-ENTRY #("task" "--blob" "b") "rev1" :once True)))))


(deftest test-a-service-starts-only-after-the-probe-passes
  ;; 木が揃う → 検めを撃つ → 走っている間は待つ → PASSED で起こす。
  (assert (= (plan 0 #(S1) (world :codes #()) {} POLICY) #((PrepareCode "rev1"))))
  (assert (= (plan 0 #(S1) (world) {} POLICY) #((ProbeEntry S1 "/c/rev1"))))
  (assert (= (plan 0 #(S1) (world :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING))) {} POLICY) #()))
  (assert (= (. (get (statuses 0 #(S1) (world) {} POLICY) 0) phase) JobPhase.STARTING))
  (assert (= (plan 0 #(S1) (world :probes #((passed S1))) {} POLICY) #((StartJob S1 1 "/c/rev1")))))


(deftest test-a-failed-probe-is-shown-and-fired-again-after-the-retry-wait
  (setv w (world :probes #((failed S1 1000))))
  (assert (= (plan 30999 #(S1) w {} POLICY) #()))
  (setv #(status) (statuses 30999 #(S1) w {} POLICY))
  (assert (= status.phase JobPhase.PROBE-FAILED))
  (assert (= status.detail "新の入口を読み込めない: ImportError: cannot import name 'GetMonotonic'") status.detail)
  (assert (= (plan 31000 #(S1) w {} POLICY) #((ProbeEntry S1 "/c/rev1")))))


(deftest test-handoff-keeps-the-old-writer-until-the-new-entry-probes
  (setv codes #(READY1 READY2))
  ;; 新の木が揃っても、検めの前・走っている間・FAILED の間は旧を名から外さない(止めもしない)。
  (assert (= (plan 0 #(S2) (world (running S1) :codes codes) {} POLICY) #((ProbeEntry S2 "/c/rev2"))))
  (assert (= (plan 0 #(S2) (world (running S1) :codes codes :probes #((ProbeView (spec-hash S2) ProbeState.RUNNING))) {} POLICY) #()))
  (setv broken (world (running S1) :codes codes :probes #((failed S2 1000))))
  (assert (= (plan 5000 #(S2) broken {} POLICY) #()))
  (setv #(status) (statuses 5000 #(S2) broken {} POLICY))
  (assert (= status.phase JobPhase.RUNNING))
  (assert (= status.running-revision "rev1"))
  (assert (.startswith status.detail "入れ替えを待つ(旧は動かしたまま)— 新の入口を読み込めない: ImportError") status.detail)
  ;; 撃ち直しの間を過ぎたら撃ち直す(旧はそのまま)。
  (assert (= (plan 31000 #(S2) broken {} POLICY) #((ProbeEntry S2 "/c/rev2"))))
  ;; PASSED になったら旧を名から外す(次の拍で新を起こす)。
  (setv ok (world (running S1) :codes codes :probes #((passed S2))))
  (assert (= (plan 31000 #(S2) ok {} POLICY) #((RetireJob "w" 10 "w#retired-1-10")))))


;; --- 実の子 process(ProbeStore)と入口(job_entry probe)-------------------------------------------

(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defn #^ str probe-tree [#^ Path tmp]
  "検めの木: 読める module・ImportError を起こす module・読むのに時間の掛かる module を置く(入口の job_entry は実行環境の物)。"
  (.write-text (/ tmp "probe_ok.hy") "(defn program [] None)\n(defn handlers [config ctx] [])\n" :encoding "utf-8")
  (.write-text (/ tmp "probe_broken.hy") "(import doeff_time [NoSuchClockName])\n(defn program [] None)\n" :encoding "utf-8")
  (.write-text (/ tmp "probe_slow.hy") "(import time)\n(time.sleep 30)\n(defn program [] None)\n" :encoding "utf-8")
  (str tmp))


(defn #^ JobSpec service-spec [#^ str factory #^ str env]
  (spec-of-declaration {"name" "w" "revision" "rev1" "run" {"kind" "service" "factory" factory "env" env}}))


(defn #^ ProbeView observed [#^ ProbeStore store #^ JobSpec spec]
  "検めが終わるまで観測する(上限 60 秒)。"
  (setv key (spec-hash spec) deadline (+ (time.monotonic) 60))
  (while (< (time.monotonic) deadline)
    (for [view (.observe store)]
      (when (and (= view.spec-hash key) (!= view.state ProbeState.RUNNING)) (return view)))
    (time.sleep 0.05))
  (raise (AssertionError "検めが 60 秒で終わらない")))


(defn #^ None test-probe-store-runs-the-entry-probe-in-the-tree [#^ Path tmp-path]
  (setv tree (probe-tree tmp-path) store (ProbeStore HY)
        good (service-spec "probe_ok:program" "probe_ok:handlers")
        broken (service-spec "probe_broken:program" "probe_ok:handlers")
        missing (service-spec "probe_ok:program" "probe_ok:no_such_env"))
  (for [spec [good broken missing]] (.start store (ProbeEntry spec tree)))
  ;; 走っている間は RUNNING として観測に載る。
  (assert (any (gfor v (.observe store) (= v.state ProbeState.RUNNING))))
  (assert (= (. (observed store good) state) ProbeState.PASSED))
  (setv b (observed store broken))
  (assert (= b.state ProbeState.FAILED))
  (assert (in "NoSuchClockName" b.detail) b.detail)
  (assert (is-not b.failed-ms None))
  (setv m (observed store missing))
  (assert (= m.state ProbeState.FAILED))
  (assert (in "no_such_env" m.detail) m.detail))


(defn #^ None test-probe-store-stops-a-probe-that-runs-too-long [#^ Path tmp-path]
  (setv tree (probe-tree tmp-path) store (ProbeStore HY :timeout-seconds 1)
        slow (service-spec "probe_slow:program" "probe_ok:handlers"))
  (.start store (ProbeEntry slow tree))
  (setv v (observed store slow))
  (assert (= v.state ProbeState.FAILED))
  (assert (in "終わらない" v.detail) v.detail))


;; --- 反例(2026-09-27 の本番: 同じ worker に 7 つの service を新しい root で宣言し直し、17 分 starting のまま起きなかった)------

(defn #^ bool process-alive [#^ int pid]
  "pid の process が生きているか(終わって回収を待つだけの zombie は生きていない)。"
  (setv stat (Path (.format "/proc/{}/stat" pid)))
  (try
    (setv text (.read-text stat))
    (except [OSError] (return False)))
  ;; 「pid (名前) 状態 …」— 名前に空白と括弧が入りうるので、最後の「)」の後を読む。
  (!= (get (.split (.strip (cut text (+ (.rindex text ")") 1) None))) 0) "Z"))


(defn #^ None test-a-timed-out-probe-leaves-no-child-process [#^ Path tmp-path]
  ;; 反例 (a): 時間切れで止めた検めの子(import の途中で起こした孫 process)が残らない。以前は Popen の直の子だけを kill し、
  ;; 孫(実行環境の job では uv の下の hy)が孤児として CPU を使い続け、撃ち直しのたびに積み上がった(最大 47 本)。
  (probe-tree tmp-path)
  (.write-text (/ tmp-path "probe_forks.hy")
               (.join "\n" ["(import subprocess time pathlib [Path])"
                            "(setv child (subprocess.Popen [\"sleep\" \"60\"]))"
                            "(.write-text (Path \"child.pid\") (str child.pid))"
                            "(time.sleep 60)"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (setv store (ProbeStore HY :timeout-seconds 3)
        forks (service-spec "probe_forks:program" "probe_ok:handlers")
        pid-file (/ tmp-path "child.pid"))
  (.start store (ProbeEntry forks (str tmp-path)))
  (setv v (observed store forks))
  (assert (= v.state ProbeState.FAILED))
  (assert (in "終わらない" v.detail) v.detail)
  (assert (.exists pid-file) "検めが孫を起こす前に時間切れになった(検の前提が崩れた)")
  (setv child (int (.read-text pid-file)) deadline (+ (time.monotonic) 5))
  (try
    (while (and (process-alive child) (< (time.monotonic) deadline)) (time.sleep 0.05))
    (assert (not (process-alive child)) (.format "時間切れの検めの孫 {} が生き残った" child))
    (finally
      (when (process-alive child)
        (os.kill child 9)))))


(defn #^ None test-probes-on-the-same-tree-run-as-one-process [#^ Path tmp-path]
  ;; 反例 (c): 同じ木に 7 つの service が同時に来ても、検めの process を 7 本並べない(同じ木の検めは 1 本にまとめる)。
  ;; 以前は spec ごとに 1 本ずつ起こし、同じ import の閉包を 7 本が同時に compile して CPU の上限 4 の Pod を締め付けた。
  ;; まとめても、読み込めない入口の理由はその入口の spec にだけ付く。
  (probe-tree tmp-path)
  (for [i (range 6)]
    (.write-text (/ tmp-path (.format "probe_m{}.hy" i))
                 (.join "\n" ["(import os time pathlib [Path])"
                              "(with [f (open (Path \"pids.txt\") \"a\")] (.write f (.format \"{}\\n\" (os.getpid))))"
                              "(time.sleep 0.3)"
                              "(defn program [] None)"])
                 :encoding "utf-8"))
  (setv store (ProbeStore HY)
        specs (+ (lfor i (range 6) (replace (service-spec (.format "probe_m{}:program" i) "probe_ok:handlers") :name (.format "w{}" i)))
                 [(replace (service-spec "probe_broken:program" "probe_ok:handlers") :name "w6")]))
  (for [spec specs] (.start store (ProbeEntry spec (str tmp-path))))
  (setv views (lfor spec specs (observed store spec)))
  (assert (= (lfor v (cut views 0 6) v.state) (* [ProbeState.PASSED] 6)) views)
  (assert (= (. (get views 6) state) ProbeState.FAILED) views)
  (assert (in "NoSuchClockName" (. (get views 6) detail)) (. (get views 6) detail))
  (setv pids (set (.split (.read-text (/ tmp-path "pids.txt")))))
  (assert (= (len pids) 1) (.format "同じ木の検めが {} 本の process で走った" (len pids))))


(deftest test-a-running-probe-is-shown-as-probing-with-its-reason
  ;; 反例 (d): 検めの間の状態が starting ではなく検めの段(probing)で、経過の秒・回数・直前の失敗の理由を持つ。
  ;; 以前は検めの間を starting と出し、理由(probe-failed)は FAILED から撃ち直すまでの 30 秒しか見えなかった。
  (val plain (world :codes #(READY1) :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING))))
  (val first (get (statuses 0 #(S1) plain {} POLICY) 0))
  (assert (= first.phase JobPhase.PROBING) first.phase)
  ;; 撃ち直しの間: 直前の失敗の理由・回数・経過の秒が状態に残る。
  (val again (world :codes #(READY1)
                     :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING :started-ms 1000 :attempts 2
                                          :last-failure "ImportError: cannot import name 'GetMonotonic'"))))
  (val status (get (statuses 46000 #(S1) again {} POLICY) 0))
  (assert (= status.phase JobPhase.PROBING))
  (assert (= status.probe (ProbeStatus :state "running" :elapsed-seconds 45 :attempts 2
                                       :last-failure "ImportError: cannot import name 'GetMonotonic'"))
          status.probe)
  (assert (in "GetMonotonic" status.detail) status.detail)
  ;; 状態の JSON(heartbeat と status の file)にも載る。
  (val row (status-row status))
  (assert (= (get row "phase") "probing"))
  (assert (= (get row "probe") {"state" "running" "elapsedSeconds" 45 "attempts" 2
                                "lastFailure" "ImportError: cannot import name 'GetMonotonic'"})
          row)
  ;; 検めの行は coordinator から見て「その worker で起こしかけている」(他へ置かない)。
  (assert (in "probing" LIVE-PHASES)))


(defn #^ None test-a-refired-probe-keeps-the-last-failure [#^ Path tmp-path]
  ;; 反例 (d) の観測の側: FAILED の後に撃ち直した検めの観測は、走っている間も直前の失敗の理由と回数を持つ。
  (setv tree (probe-tree tmp-path) store (ProbeStore HY)
        broken (service-spec "probe_broken:program" "probe_ok:handlers"))
  (.start store (ProbeEntry broken tree))
  (setv first (observed store broken))
  (assert (= first.state ProbeState.FAILED))
  (.start store (ProbeEntry broken tree))
  (setv #(running) (lfor v (.observe store) :if (= v.spec-hash (spec-hash broken)) v))
  (assert (in running.state #(ProbeState.QUEUED ProbeState.RUNNING)) running)
  (assert (= running.attempts 2) running)
  (assert (= running.last-failure first.detail) running)
  (setv second (observed store broken))
  (assert (= second.attempts 2) second))


(defn #^ None test-job-entry-probe-resolves-without-calling []
  (assert (is (probe-problem "doeff_cluster.service_model:resolve" "doeff_cluster.job_entry:env_handlers") None))
  (setv absent (probe-problem "doeff_cluster.no_such_module:f" "doeff_cluster.job_entry:env_handlers"))
  (assert (.startswith absent "factory doeff_cluster.no_such_module:f を読み込めない: ModuleNotFoundError") absent)
  (setv attr (probe-problem "doeff_cluster.service_model:resolve" "doeff_cluster.service_model:nothing_here"))
  (assert (.startswith attr "env doeff_cluster.service_model:nothing_here を読み込めない: AttributeError") attr)
  (assert (not-in "\n" absent)))


(defn #^ None test-probe-reason-is-the-last-line []
  (assert (= (probe-reason 1 "Traceback\n  File x\nImportError: nope\n\n") "ImportError: nope"))
  (assert (in "終了 3" (probe-reason 3 ""))))
