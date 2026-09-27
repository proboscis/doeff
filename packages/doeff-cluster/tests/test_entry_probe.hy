;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に worker の実行環境で入口の module を読み込めるかを試し、
;; 通るまで起こさない・入れ替えの旧を外さない。純粋な判断(worker_policy)・実の子 process(handlers.ProbeStore)。
;; Program の job(2026-09-27・ADR-DOE-CLUSTER-001)は関数の参照を持たないので、検める対象は入口の module(spec.entry)だけ
;; (詰めた Program の版と復元は起こした子が検める — job_entry の service / probe の検は test_job_entry_program)。
;; 旧い形の service の spec(置き場のキー無し・--factory / --env / --config)は検めの段で理由つきに断る(計画 2.8 の入口 15)。
(require doeff-hy.macros [deftest val <-])
(import dataclasses [replace])
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_cluster.worker_model [JobSpec CodeState CodeView ProcessView WorldView ProbeState ProbeView ProbeStatus JobPhase JobRecord
                        WorkerPolicy PrepareCode StartJob RetireJob ProbeEntry ForgetProbes spec-hash probed-job probe-args probe-refusal])
(import doeff_cluster.worker_policy [plan statuses])
(import doeff_cluster.handlers [ProbeStore probe-reason status-row])
(import tests.program_rows [SAMPLE-RUN SAMPLE-PROGRAM])
(import doeff_cluster.cluster_policy [JOB-ENTRY LIVE-PHASES spec-of-declaration])

(setv POLICY (WorkerPolicy :code-retry-ms 30000)
      S1 (spec-of-declaration {"name" "w" "revision" "rev1" "run" SAMPLE-RUN "update" "handoff"})
      S2 (replace S1 :revision "rev2")
      READY1 (CodeView "rev1" CodeState.READY "/c/rev1")
      READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))

(defn #^ WorldView world [#^ ProcessView #* processes #^ tuple [codes #(READY1)] #^ tuple [probes #()]] (WorldView codes processes probes))
(defn #^ ProcessView running [#^ JobSpec spec #^ int [pid 10]] (ProcessView spec.name spec 1 pid 0 :instance (.format "1-{}" pid)))
(defn #^ ProbeView passed [#^ JobSpec spec] (ProbeView (spec-hash spec) ProbeState.PASSED))
(defn #^ ProbeView failed [#^ JobSpec spec #^ int [at 1000]] (ProbeView (spec-hash spec) ProbeState.FAILED :detail "ImportError: cannot import name 'GetMonotonic'" :failed-ms at))


(defn #^ None test-only-service-jobs-are-probed []
  (assert (probed-job S1))
  (assert (= (probe-args S1) #("probe")))
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


(defn #^ JobSpec service-spec [#^ str target]
  "Program の job の service の spec。検めの対象は入口(spec.entry)だけなので、検めたい import path を入口に置く。"
  (JobSpec "w" target #("service" "--identity" (* "0" 16)) "rev1" :program SAMPLE-PROGRAM))


(defn #^ ProbeView observed [#^ ProbeStore store #^ JobSpec spec]
  "検めが終わるまで観測する(上限 60 秒)。"
  (setv key (spec-hash spec) deadline (+ (time.monotonic) 60))
  (while (< (time.monotonic) deadline)
    (for [view (.observe store)]
      (when (and (= view.spec-hash key) (not-in view.state #(ProbeState.RUNNING ProbeState.QUEUED))) (return view)))
    (time.sleep 0.05))
  (raise (AssertionError "検めが 60 秒で終わらない")))


(defn #^ None test-probe-store-runs-the-entry-probe-in-the-tree [#^ Path tmp-path]
  (setv tree (probe-tree tmp-path) store (ProbeStore HY)
        good (service-spec "probe_ok:program")
        broken (service-spec "probe_broken:program")
        missing (service-spec "probe_ok:no_such_attr"))
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
  (assert (in "no_such_attr" m.detail) m.detail))


(defn #^ None test-probe-store-stops-a-probe-that-runs-too-long [#^ Path tmp-path]
  (setv tree (probe-tree tmp-path) store (ProbeStore HY :timeout-seconds 1)
        slow (service-spec "probe_slow:program"))
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
        forks (service-spec "probe_forks:program")
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
        specs (+ (lfor i (range 6) (replace (service-spec (.format "probe_m{}:program" i)) :name (.format "w{}" i)))
                 [(replace (service-spec "probe_broken:program") :name "w6")]))
  (for [spec specs] (.start store (ProbeEntry spec (str tmp-path))))
  (setv views (lfor spec specs (observed store spec)))
  (assert (= (lfor v (cut views 0 6) v.state) (* [ProbeState.PASSED] 6)) views)
  (assert (= (. (get views 6) state) ProbeState.FAILED) views)
  (assert (in "NoSuchClockName" (. (get views 6) detail)) (. (get views 6) detail))
  (setv pids (set (.split (.read-text (/ tmp-path "pids.txt")))))
  (assert (= (len pids) 1) (.format "同じ木の検めが {} 本の process で走った" (len pids))))


(defn #^ int wait-pid [#^ Path path]
  "検めの子が書いた pid の file を待って読む(上限 30 秒)。"
  (setv deadline (+ (time.monotonic) 30))
  (while (< (time.monotonic) deadline)
    (when (and (.exists path) (.strip (.read-text path)))
      (return (int (.read-text path))))
    (time.sleep 0.05))
  (raise (AssertionError (.format "{} が書かれない" path))))


(defn #^ None assert-gone [#^ int pid #^ str what]
  "pid の process が 5 秒の内に消えることを確かめる(残っていれば止めてから赤にする)。"
  (setv deadline (+ (time.monotonic) 5))
  (while (and (process-alive pid) (< (time.monotonic) deadline)) (time.sleep 0.05))
  (when (process-alive pid)
    (os.kill pid 9)
    (raise (AssertionError (.format "{} {} が生き残った" what pid)))))


(defn #^ None test-a-passed-probe-leaves-no-child-process [#^ Path tmp-path]
  ;; 反例(構成レビュー 2026-09-27 の必須 2 (a)): import の途中で孫を起こして返った検め(通る)の後にも、孫が残らない。
  ;; 以前は process group を止めるのが時間切れの時だけで、通った検め・失敗した検めの孫は孤児として残った。
  (probe-tree tmp-path)
  (.write-text (/ tmp-path "probe_spawns.hy")
               (.join "\n" ["(import subprocess pathlib [Path])"
                            "(setv child (subprocess.Popen [\"sleep\" \"60\"]))"
                            "(.write-text (Path \"spawned.pid\") (str child.pid))"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (setv store (ProbeStore HY :timeout-seconds 30)
        spawns (service-spec "probe_spawns:program"))
  (.start store (ProbeEntry spawns (str tmp-path)))
  (assert (= (. (observed store spawns) state) ProbeState.PASSED))
  (assert-gone (wait-pid (/ tmp-path "spawned.pid")) "通った検めの孫"))


(defn #^ None test-a-probe-whose-shim-dies-first-leaves-no-child-process [#^ Path tmp-path]
  ;; 反例(必須 2 (b)): 検めの group の先頭(shim)だけが先に kill -9 で死んでも、検めの本体(hy)が残らない。
  (probe-tree tmp-path)
  (.write-text (/ tmp-path "probe_sleeps.hy")
               (.join "\n" ["(import os time pathlib [Path])"
                            "(.write-text (Path \"sleeper.pid\") (str (os.getpid)))"
                            "(time.sleep 20)"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (setv store (ProbeStore HY :timeout-seconds 30)
        sleeps (service-spec "probe_sleeps:program"))
  (.start store (ProbeEntry sleeps (str tmp-path)))
  (.observe store)
  (setv sleeper (wait-pid (/ tmp-path "sleeper.pid"))
        shim (. (next (iter (.values store.runs))) process pid))
  (os.kill shim 9)
  (assert (= (. (observed store sleeps) state) ProbeState.FAILED))
  (assert-gone sleeper "shim の死んだ検めの本体"))


(defn #^ None test-a-hanging-entry-does-not-take-down-its-batch [#^ Path tmp-path]
  ;; 反例(必須 1): 同じ木の束に固まる入口が 1 つあっても、ほかの入口は結果どおり(PASSED)になり、時間切れで FAILED になるのは固まった
  ;; 入口を持つ spec だけ。撃ち直しでは、直前に時間切れになった spec を束に混ぜず単独で起こす(同じ束で全部が道連れを繰り返さない)。
  (probe-tree tmp-path)
  (setv store (ProbeStore HY :timeout-seconds 3)
        ok-specs (lfor i (range 6) (replace (service-spec (.format "probe_ok:program{}" i))
                                            :name (.format "w{}" i)))
        hang (replace (service-spec "probe_slow:program") :name "hang")
        ;; 固まる入口を束の中ほどに置く(前の対象は結果が出ている・後の対象は結果が出ていない)。
        specs (+ (cut ok-specs 0 3) [hang] (cut ok-specs 3 None)))
  (for [i (range 6)]
    (.write-text (/ tmp-path "probe_ok.hy")
                 (+ (.read-text (/ tmp-path "probe_ok.hy")) (.format "(defn program{} [] None)\n" i))))
  (for [spec specs] (.start store (ProbeEntry spec (str tmp-path))))
  (setv hung (observed store hang))
  (assert (= hung.state ProbeState.FAILED) hung)
  (assert (in "終わらない" hung.detail) hung.detail)
  (for [spec ok-specs]
    (setv v (observed store spec))
    (assert (= v.state ProbeState.PASSED) (.format "{} が固まった入口の道連れになった: {}" spec.name v))
    (assert (= v.attempts 1) v))
  ;; 撃ち直し: 時間切れだった spec は単独の束で起こす。
  (.start store (ProbeEntry hang (str tmp-path)))
  (.start store (ProbeEntry (get ok-specs 0) (str tmp-path)))
  (.observe store)
  (setv runs (list (.values store.runs)))
  (try
    (assert (= (len runs) 1) runs)
    (assert (= (. (get runs 0) specs) #(hang)) (. (get runs 0) specs))
    (finally
      ;; 検の後始末: 走っている撃ち直しの group を止める。
      (for [run runs]
        (try (os.killpg run.process.pid 9) (except [ProcessLookupError] None))
        (.wait run.process)))))


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
        broken (service-spec "probe_broken:program"))
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


(defn #^ None test-probe-reason-is-the-last-line []
  (assert (= (probe-reason 1 "Traceback\n  File x\nImportError: nope\n\n") "ImportError: nope"))
  (assert (in "終了 3" (probe-reason 3 ""))))


;; --- 宣言から消えた spec の検めの記録(2026-09-27 — #757)--------------------------------------------------
;; 検めの持ち主(ProbeStore)は答え・回数・前の回の失敗の理由・時間切れの印を spec-hash ごとに持ち、宣言から消えた spec の分を
;; 落とさなかった(版を上げるたびに増え続ける)。plan が今の宣言の spec の指紋を渡し(ForgetProbes)、持ち主が集合に無い分を落とす。

(deftest test-the-plan-hands-the-declared-spec-hashes-when-a-stale-probe-record-is-observed
  (val stale (plan 0 #(S2) (world :codes #(READY2) :probes #((passed S2) (failed S1))) {} POLICY))
  (assert (in (ForgetProbes (frozenset [(spec-hash S2)])) stale) stale)
  ;; 宣言の spec の記録だけなら撃たない。
  (val clean (plan 0 #(S2) (world :codes #(READY2) :probes #((passed S2))) {} POLICY))
  (assert (not (any (gfor a clean (isinstance a ForgetProbes)))) clean))


(defn #^ None test-probe-store-forgets-the-records-of-specs-no-longer-declared [#^ Path tmp-path]
  (setv tree (probe-tree tmp-path))
  (.write-text (/ tmp-path "probe_nap.hy") "(import time)\n(time.sleep 1)\n(defn program [] None)\n" :encoding "utf-8")
  (setv store (ProbeStore HY :timeout-seconds 30)
        kept (replace (service-spec "probe_broken:program") :name "kept")
        gone (replace (service-spec "probe_broken:program") :name "gone")
        nap (replace (service-spec "probe_nap:program") :name "nap"))
  (for [spec [kept gone]] (.start store (ProbeEntry spec tree)))
  (setv first (observed store kept))
  (observed store gone)
  ;; 宣言に残る spec は撃ち直して 2 回目(前の回の失敗の理由を持つ)。
  (.start store (ProbeEntry kept tree))
  (observed store kept)
  ;; 宣言から消えた spec の検めが走っている間に宣言が変わる。
  (.start store (ProbeEntry nap tree))
  (.observe store)
  (setv keep (frozenset [(spec-hash kept)]))
  (.forget store keep)
  (setv views (dfor v (.observe store) v.spec-hash v))
  (assert (not-in (spec-hash gone) views) views)
  (for [table [store.done store.attempts store.last-failure]]
    (assert (not-in (spec-hash gone) table) table))
  ;; 宣言に残る spec の失敗の理由と回数は残る。
  (setv k (get views (spec-hash kept)))
  (assert (= #(k.state k.attempts k.last-failure) #(ProbeState.FAILED 2 first.detail)) k)
  ;; 走っている検めの process は落とさない(終わった後の答えを次の片づけで落とす)。
  (assert (= (. (get views (spec-hash nap)) state) ProbeState.RUNNING) views)
  (assert (= (. (observed store nap) state) ProbeState.PASSED))
  (.forget store keep)
  (assert (= (sfor v (.observe store) v.spec-hash) #{(spec-hash kept)}))
  (assert (= (set store.attempts) #{(spec-hash kept)}) store.attempts))


;; --- 旧い形の service の spec(計画 2.8 の入口 15 — 2026-09-27)---------------------------------------------------
;; 旧い coordinator の返事の spec(job_entry service --factory … --env … --config …・置き場のキー無し)は、入口の module の import だけを
;; 検めると通ってしまい、子の job_entry が argparse で落ちて起こし直しを繰り返す。検めの段で process を起こさずに理由つきで断る。

(val OLD-SPEC (JobSpec "w" JOB-ENTRY #("service" "--factory" "m.f:program" "--env" "m.e:handlers" "--config" "{}") "rev1"))


(deftest test-an-old-service-spec-is-refused-by-the-probe-with-its-reason
  (val reason (probe-refusal OLD-SPEC))
  (assert (in "--factory・--env・--config" reason) reason)
  ;; 新しい形でも置き場のキーが無ければ断る。
  (val keyless (probe-refusal (replace S1 :program None)))
  (assert (in "置き場のキー" keyless) keyless)
  ;; 新しい形の service・task・素の entry は断らない。
  (assert (is (probe-refusal S1) None))
  (assert (is (probe-refusal (JobSpec "task/1" JOB-ENTRY #("task" "--blob" "b") "rev1" :once True)) None))
  (assert (is (probe-refusal (JobSpec "a" "jobs.a" #() "rev1")) None)))


(deftest test-the-probe-store-fails-an-old-service-spec-without-a-process
  (val store (ProbeStore HY))
  (.start store (ProbeEntry OLD-SPEC "/nonexistent-tree"))
  (assert (= store.runs {}) "旧い spec の検めの process を起こした")
  (val views (.observe store))
  (assert (= (len views) 1) views)
  (val view (get views 0))
  (assert (= view.state ProbeState.FAILED) view)
  (assert (in "旧い service の spec の引数" view.detail) view.detail)
  (assert (= store.runs {}))
  ;; worker の状態の報告では probe-failed と理由(起こさない)。
  (val w (world :probes #(view)))
  (assert (= (plan (+ view.failed-ms 1) #(OLD-SPEC) w {} POLICY) #()))
  (val status (get (statuses (+ view.failed-ms 1) #(OLD-SPEC) w {} POLICY) 0))
  (assert (= status.phase JobPhase.PROBE-FAILED) status)
  (assert (in "旧い service の spec の引数" status.detail) status.detail))
