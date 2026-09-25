;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に worker の実行環境で入口(factory と env)を読み込めるかを試し、
;; 通るまで起こさない・入れ替えの旧を外さない。純粋な判断(worker_policy)・実の子 process(handlers.ProbeStore)・入口(job_entry probe)。
(require doeff-hy.macros [deftest])
(import dataclasses [replace])
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_cluster.worker_model [JobSpec CodeState CodeView ProcessView WorldView ProbeState ProbeView JobPhase JobRecord WorkerPolicy
                        PrepareCode StartJob RetireJob ProbeEntry spec-hash probed-job probe-args])
(import doeff_cluster.worker_policy [plan statuses])
(import doeff_cluster.handlers [ProbeStore probe-reason])
(import doeff_cluster.job_entry [probe-problem])
(import doeff_cluster.cluster_policy [JOB-ENTRY spec-of-declaration])

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
