;; 入口の検め(probe・2026-09-25): service の job は、木が揃った後に worker の実行環境で入口の module を読み込めるかを試し、
;; 通るまで起こさない・入れ替えの旧を外さない。純粋な判断(worker_policy)・実の子 process(検めの言い換え worker/protocol/probes)。
;; Program の job(2026-09-27・ADR-DOE-CLUSTER-001)は関数の参照を持たないので、検める対象は入口の module(spec.entry)だけ
;; (詰めた Program の版と復元は起こした子が検める — job_entry の service / probe の検は test_job_entry_program)。
;; 旧い形の service の spec(置き場のキー無し・--factory / --env / --config)は検めの段で理由つきに断る(計画 2.8 の入口 15)。
(require doeff-hy.macros [deftest defk val var <-])
(import dataclasses [replace])
(import os)
(import sys)
(import time)
(import pathlib [Path])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView ProcessView WorldView ProbeState ProbeView ProbeStatus JobRecord
 WorkerPolicy PrepareCode StartJob RetireJob ProbeEntry ForgetProbes] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [probed-job probe-args probe-refusal])
(import doeff_cluster.worker.protocol.observations [ObserveProbes])
(import doeff_cluster.worker.core.policy [plan statuses])
(import doeff_cluster.worker.protocol.heartbeat [status-row] doeff_cluster.worker.core.probe_rules [probe-reason])
(import tests.probe_rig [probe-settings observed run-probes])
(import doeff_cluster.worker.core.shim_timing [shim-deadline-ms])
(import tests.program_rows [SAMPLE-RUN SAMPLE-PROGRAM])
(import doeff_cluster.coordinator.core.cluster_policy [JOB-ENTRY LIVE-PHASES spec-of-declaration])

(setv POLICY (WorkerPolicy :code-retry-ms 30000)
      S1 (spec-of-declaration {"name" "w" "revision" "rev1" "run" SAMPLE-RUN "update" "handoff"})
      S2 (replace S1 :revision "rev2")
      READY1 (CodeView "rev1" CodeState.READY "/c/rev1")
      READY2 (CodeView "rev2" CodeState.READY "/c/rev2"))


(defk world [[processes #()] [codes #(READY1)] [probes #()]]
  {:pre [(: processes (get tuple #(ProcessView ...))) (: codes (get tuple #(CodeView ...))) (: probes (get tuple #(ProbeView ...)))]
   :post [(: % WorldView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の観測(動いている process・揃った木・検めの答え)を、判断 plan・statuses に渡す形で組むため。"
  (WorldView codes processes probes))


(defk running [spec [pid 10]]
  {:pre [(: spec JobSpec) (: pid int)] :post [(: % ProcessView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "spec の job が pid で動いている観測。"
  (ProcessView spec.name spec 1 pid 0 :instance (.format "1-{}" pid)))


(defk passed [spec]
  {:pre [(: spec JobSpec)] :post [(: % ProbeView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "spec の入口の検めが通った答え。"
  (ProbeView (spec-hash spec) ProbeState.PASSED))


(defk failed [spec [at 1000]]
  {:pre [(: spec JobSpec) (: at int)] :post [(: % ProbeView)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "spec の入口の検めが at に失敗した答え(読み込めない理由つき)。"
  (ProbeView (spec-hash spec) ProbeState.FAILED :detail "ImportError: cannot import name 'GetMonotonic'" :failed-ms at))


(deftest test-only-service-jobs-are-probed
  (assert (probed-job S1))
  (assert (= (! (probe-args S1)) #("probe")))
  (assert (not (probed-job (JobSpec "a" "jobs.a" #() "rev1"))))
  (assert (not (probed-job (JobSpec "task/1" JOB-ENTRY #("task" "--result" "r") "rev1" :once True)))))


(deftest test-a-service-starts-only-after-the-probe-passes
  ;; 木が揃う → 検めを撃つ → 走っている間は待つ → PASSED で起こす。
  (assert (= (! (plan 0 #(S1) (! (world :codes #())) {} POLICY)) #((PrepareCode "rev1"))))
  (assert (= (! (plan 0 #(S1) (! (world)) {} POLICY)) #((ProbeEntry S1 "/c/rev1"))))
  (assert (= (! (plan 0 #(S1) (! (world :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING)))) {} POLICY)) #()))
  (assert (= (. (get (! (statuses 0 #(S1) (! (world)) {} POLICY)) 0) phase) JobPhase.STARTING))
  (assert (= (! (plan 0 #(S1) (! (world :probes #((! (passed S1))))) {} POLICY)) #((StartJob S1 1 "/c/rev1")))))


(deftest test-a-failed-probe-is-shown-and-fired-again-after-the-retry-wait
  (setv w (! (world :probes #((! (failed S1 1000))))))
  (assert (= (! (plan 30999 #(S1) w {} POLICY)) #()))
  (setv #(status) (! (statuses 30999 #(S1) w {} POLICY)))
  (assert (= status.phase JobPhase.PROBE-FAILED))
  (assert (= status.detail "新の入口を読み込めない: ImportError: cannot import name 'GetMonotonic'") status.detail)
  (assert (= (! (plan 31000 #(S1) w {} POLICY)) #((ProbeEntry S1 "/c/rev1")))))


(deftest test-handoff-keeps-the-old-writer-until-the-new-entry-probes
  (setv codes #(READY1 READY2))
  ;; 新の木が揃っても、検めの前・走っている間・FAILED の間は旧を名から外さない(止めもしない)。
  (assert (= (! (plan 0 #(S2) (! (world :processes #((! (running S1))) :codes codes)) {} POLICY)) #((ProbeEntry S2 "/c/rev2"))))
  (assert (= (! (plan 0 #(S2) (! (world :processes #((! (running S1))) :codes codes
                                     :probes #((ProbeView (spec-hash S2) ProbeState.RUNNING))))
                   {} POLICY))
             #()))
  (setv broken (! (world :processes #((! (running S1))) :codes codes :probes #((! (failed S2 1000))))))
  (assert (= (! (plan 5000 #(S2) broken {} POLICY)) #()))
  (setv #(status) (! (statuses 5000 #(S2) broken {} POLICY)))
  (assert (= status.phase JobPhase.RUNNING))
  (assert (= status.running-revision "rev1"))
  (assert (.startswith status.detail "入れ替えを待つ(旧は動かしたまま)— 新の入口を読み込めない: ImportError") status.detail)
  ;; 撃ち直しの間を過ぎたら撃ち直す(旧はそのまま)。
  (assert (= (! (plan 31000 #(S2) broken {} POLICY)) #((ProbeEntry S2 "/c/rev2"))))
  ;; PASSED になったら旧を名から外す(次の拍で新を起こす)。
  (setv ok (! (world :processes #((! (running S1))) :codes codes :probes #((! (passed S2))))))
  (assert (= (! (plan 31000 #(S2) ok {} POLICY)) #((RetireJob "w" 10 "w#retired-1-10")))))


;; --- 実の子 process(検めの言い換え probe-host — #2465)と入口(job_entry probe)-------------------------------------------
;; 検めの記録は probe-host の session の値なので、筋書きは 1 本の Program(defk)にして run-probes で回す(tests/probe_rig.hy)。

(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk probe-tree [tmp]
  {:pre [(: tmp Path)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検めの木: 読める module・ImportError を起こす module・読むのに時間の掛かる module を置く(入口の job_entry は実行環境の物)。"
  (.write-text (/ tmp "probe_ok.hy") "(defn program [] None)\n(defn handlers [config ctx] [])\n" :encoding "utf-8")
  (.write-text (/ tmp "probe_broken.hy") "(import doeff_time [NoSuchClockName])\n(defn program [] None)\n" :encoding "utf-8")
  (.write-text (/ tmp "probe_slow.hy") "(import time)\n(time.sleep 30)\n(defn program [] None)\n" :encoding "utf-8")
  (str tmp))


;; Program の job の service の spec の雛形。検めの対象は入口(spec.entry)だけなので、検めたい import path を (replace SERVICE :entry …)
;; で入口に置く(名を変える時は :name も)。
(val SERVICE (JobSpec "w" "probe_ok:program" #("service" "--identity" (* "0" 16)) "rev1" :program SAMPLE-PROGRAM))


(defk entry-probe-scene [tree good broken missing]
  {:pre [(: tree str) (: good JobSpec) (: broken JobSpec) (: missing JobSpec)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "3 つの入口を積み、走っている間の観測と 3 つの答えを返すため。"
  (for [spec [good broken missing]] (<- (ProbeEntry spec tree)))
  (<- first tuple (ObserveProbes))
  (<- g ProbeView (observed good))
  (<- b ProbeView (observed broken))
  (<- m ProbeView (observed missing))
  #(first g b m))


(deftest test-probe-store-runs-the-entry-probe-in-the-tree [tmp-path]
  (val tree (! (probe-tree tmp-path)))
  (val good (replace SERVICE :entry "probe_ok:program"))
  (val broken (replace SERVICE :entry "probe_broken:program"))
  (val missing (replace SERVICE :entry "probe_ok:no_such_attr"))
  (<- got tuple (run-probes (! (probe-settings tmp-path)) (entry-probe-scene tree good broken missing)))
  (val first (get got 0))
  (val g (get got 1))
  (val b (get got 2))
  (val m (get got 3))
  ;; 走っている間は RUNNING として観測に載る。
  (assert (any (gfor v first (= v.state ProbeState.RUNNING))))
  (assert (= g.state ProbeState.PASSED))
  (assert (= b.state ProbeState.FAILED))
  (assert (in "NoSuchClockName" b.detail) b.detail)
  (assert (is-not b.failed-ms None))
  (assert (= m.state ProbeState.FAILED))
  (assert (in "no_such_attr" m.detail) m.detail))


(defk probed-once [spec tree]
  {:pre [(: spec JobSpec) (: tree str)] :post [(: % ProbeView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口を 1 つ積み、答えを返すため。"
  (<- (ProbeEntry spec tree))
  (<- view ProbeView (observed spec))
  view)


(deftest test-probe-store-stops-a-probe-that-runs-too-long [tmp-path]
  (val tree (! (probe-tree tmp-path)))
  (val slow (replace SERVICE :entry "probe_slow:program"))
  (<- v ProbeView (run-probes (! (probe-settings tmp-path :timeout-seconds 1)) (probed-once slow tree)))
  (assert (= v.state ProbeState.FAILED))
  (assert (in "終わらない" v.detail) v.detail))


;; --- 反例(2026-09-27 の本番: 同じ worker に 7 つの service を新しい root で宣言し直し、17 分 starting のまま起きなかった)------

(defk process-alive [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "pid の process が生きているか(終わって回収を待つだけの zombie は生きていない)— 検めの子や孫が残っていないかを見るため。"
  (val text (try (.read-text (Path (.format "/proc/{}/stat" pid))) (except [OSError] None)))
  ;; 「pid (名前) 状態 …」— 名前に空白と括弧が入りうるので、最後の「)」の後を読む。
  (and (is-not text None)
       (!= (get (.split (.strip (cut text (+ (.rindex text ")") 1) None))) 0) "Z")))


(deftest test-a-timed-out-probe-leaves-no-child-process [tmp-path]
  ;; 反例 (a): 時間切れで止めた検めの子(import の途中で起こした孫 process)が残らない。以前は Popen の直の子だけを kill し、
  ;; 孫(実行環境の job では uv の下の hy)が孤児として CPU を使い続け、撃ち直しのたびに積み上がった(最大 47 本)。
  (! (probe-tree tmp-path))
  (.write-text (/ tmp-path "probe_forks.hy")
               (.join "\n" ["(import subprocess time pathlib [Path])"
                            "(setv child (subprocess.Popen [\"sleep\" \"60\"]))"
                            "(.write-text (Path \"child.pid\") (str child.pid))"
                            "(time.sleep 60)"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (val forks (replace SERVICE :entry "probe_forks:program"))
  (val pid-file (/ tmp-path "child.pid"))
  (<- v ProbeView (run-probes (! (probe-settings tmp-path :timeout-seconds 3)) (probed-once forks (str tmp-path))))
  (assert (= v.state ProbeState.FAILED))
  (assert (in "終わらない" v.detail) v.detail)
  (assert (.exists pid-file) "検めが孫を起こす前に時間切れになった(検の前提が崩れた)")
  (val child (int (.read-text pid-file)))
  (val deadline (+ (time.monotonic) 5))
  (try
    (while (and (! (process-alive child)) (< (time.monotonic) deadline)) (time.sleep 0.05))
    (assert (not (! (process-alive child))) (.format "時間切れの検めの孫 {} が生き残った" child))
    (finally
      (when (! (process-alive child))
        (os.kill child 9)))))


(defk probed-all [specs tree]
  {:pre [(: specs list) (: tree str)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口を全部積み、積んだ順の答えを返すため。"
  (for [spec specs] (<- (ProbeEntry spec tree)))
  (var views [])
  (for [spec specs]
    (<- view ProbeView (observed spec))
    (:= views (+ views [view])))
  views)


(deftest test-probes-on-the-same-tree-run-as-one-process [tmp-path]
  ;; 反例 (c): 同じ木に 7 つの service が同時に来ても、検めの process を 7 本並べない(同じ木の検めは 1 本にまとめる)。
  ;; 以前は spec ごとに 1 本ずつ起こし、同じ import の閉包を 7 本が同時に compile して CPU の上限 4 の Pod を締め付けた。
  ;; まとめても、読み込めない入口の理由はその入口の spec にだけ付く。
  (! (probe-tree tmp-path))
  (for [i (range 6)]
    (.write-text (/ tmp-path (.format "probe_m{}.hy" i))
                 (.join "\n" ["(import os time pathlib [Path])"
                              "(with [f (open (Path \"pids.txt\") \"a\")] (.write f (.format \"{}\\n\" (os.getpid))))"
                              "(time.sleep 0.3)"
                              "(defn program [] None)"])
                 :encoding "utf-8"))
  (val specs (+ (lfor i (range 6) (replace SERVICE :entry (.format "probe_m{}:program" i) :name (.format "w{}" i)))
                [(replace SERVICE :entry "probe_broken:program" :name "w6")]))
  (<- views list (run-probes (! (probe-settings tmp-path)) (probed-all specs (str tmp-path))))
  (assert (= (lfor v (cut views 0 6) v.state) (* [ProbeState.PASSED] 6)) views)
  (assert (= (. (get views 6) state) ProbeState.FAILED) views)
  (assert (in "NoSuchClockName" (. (get views 6) detail)) (. (get views 6) detail))
  (val pids (set (.split (.read-text (/ tmp-path "pids.txt")))))
  (assert (= (len pids) 1) (.format "同じ木の検めが {} 本の process で走った" (len pids))))


(defk wait-pid [path]
  {:pre [(: path Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検めの子が書いた pid の file を待って読む(上限 30 秒)— 子の process を名指して確かめるため。"
  (val deadline (+ (time.monotonic) 30))
  (while (and (not (and (.exists path) (.strip (.read-text path)))) (< (time.monotonic) deadline))
    (time.sleep 0.05))
  (when (not (and (.exists path) (.strip (.read-text path))))
    (raise (AssertionError (.format "{} が書かれない" path))))
  (int (.read-text path)))


(defk assert-gone [pid what]
  {:pre [(: pid int) (: what str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "pid の process が 5 秒の内に消えることを確かめる(残っていれば止めてから赤にする)。"
  (val deadline (+ (time.monotonic) 5))
  (while (and (! (process-alive pid)) (< (time.monotonic) deadline)) (time.sleep 0.05))
  (when (! (process-alive pid))
    (os.kill pid 9)
    (raise (AssertionError (.format "{} {} が生き残った" what pid))))
  None)


(deftest test-a-passed-probe-leaves-no-child-process [tmp-path]
  ;; 反例(構成レビュー 2026-09-27 の必須 2 (a)): import の途中で孫を起こして返った検め(通る)の後にも、孫が残らない。
  ;; 以前は process group を止めるのが時間切れの時だけで、通った検め・失敗した検めの孫は孤児として残った。
  (! (probe-tree tmp-path))
  (.write-text (/ tmp-path "probe_spawns.hy")
               (.join "\n" ["(import subprocess pathlib [Path])"
                            "(setv child (subprocess.Popen [\"sleep\" \"60\"]))"
                            "(.write-text (Path \"spawned.pid\") (str child.pid))"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (val spawns (replace SERVICE :entry "probe_spawns:program"))
  (<- v ProbeView (run-probes (! (probe-settings tmp-path :timeout-seconds 30)) (probed-once spawns (str tmp-path))))
  (assert (= v.state ProbeState.PASSED))
  (! (assert-gone (! (wait-pid (/ tmp-path "spawned.pid"))) "通った検めの孫")))


(defk shim-killed-scene [spec tree pid-file]
  {:pre [(: spec JobSpec) (: tree str) (: pid-file Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検めを起こし、本体が pid を書いたら group の先頭(shim — 本体の process group の番号)を kill -9 し、答えと本体の pid を返すため。"
  (<- (ProbeEntry spec tree))
  (<- (ObserveProbes))
  (val sleeper (! (wait-pid pid-file)))
  (os.kill (os.getpgid sleeper) 9)
  (<- view ProbeView (observed spec))
  #(view sleeper))


(deftest test-a-probe-whose-shim-dies-first-leaves-no-child-process [tmp-path]
  ;; 反例(必須 2 (b)): 検めの group の先頭(shim)だけが先に kill -9 で死んでも、検めの本体(hy)が残らない。
  (! (probe-tree tmp-path))
  (.write-text (/ tmp-path "probe_sleeps.hy")
               (.join "\n" ["(import os time pathlib [Path])"
                            "(.write-text (Path \"sleeper.pid\") (str (os.getpid)))"
                            "(time.sleep 20)"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (val sleeps (replace SERVICE :entry "probe_sleeps:program"))
  (<- got tuple (run-probes (! (probe-settings tmp-path :timeout-seconds 30))
                       (shim-killed-scene sleeps (str tmp-path) (/ tmp-path "sleeper.pid"))))
  (assert (= (. (get got 0) state) ProbeState.FAILED))
  (! (assert-gone (get got 1) "shim の死んだ検めの本体")))


(defk stopped-probe-scene [spec tree pid-file]
  {:pre [(: spec JobSpec) (: tree str) (: pid-file Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めの合図を捨てる検めを起こし、捨てる構えができた(本体が pid を書いた)後、終わるまで観測し、#(答え 観測 1 回の最長の秒 本体の pid)を
   返すため(観測が待ち込むかを測る)。"
  (<- (ProbeEntry spec tree))
  (<- (ObserveProbes))
  (val body (! (wait-pid pid-file)))
  (val key (spec-hash spec))
  (val limit (+ (time.monotonic) 60))
  (var longest 0.0)
  (var found None)
  (while (and (is found None) (< (time.monotonic) limit))
    (val began (time.monotonic))
    (<- views tuple (ObserveProbes))
    (:= longest (max longest (- (time.monotonic) began)))
    (for [view views]
      (when (and (= view.spec-hash key) (= view.state ProbeState.FAILED)) (:= found view)))
    (when (is found None) (time.sleep 0.05)))
  (when (is found None) (raise (AssertionError "止めの合図を捨てる検めが 60 秒で片づかない")))
  #(found longest body))


(deftest test-a-timed-out-probe-is-killed-only-after-the-shim-deadline-without-blocking [tmp-path]
  ;; 失敗ケース(#2940): 時間切れの検めは group へ止めの合図を送り、合図から shim の期限(shim の猶予 + 掃除の余裕)まで KILL を送らない。
  ;; 以前は合図と同時に KILL を送った(起こしてから片づけまでが時間の上限とほぼ同じ — shim が子孫を片づける前に shim を殺す)。期限まで
  ;; 観測の中で待ち込む形も退ける — 観測は拍の時計を読んだ後に走るので、待ち込むと同じ拍の job の止めの合図が遅れる(観測 1 回が期限の
  ;; 長さになる)。期限を過ぎれば強いて止め、本体は残らない。方針は短くして(shim の期限 3 秒)検の時間を抑える。2 段目から shim は
  ;; 合図を捨てる本体を自分の猶予(期限より前)で KILL して片づけ、自分で終わるので、片づけの下限は合図 + shim の猶予(合図と同時の
  ;; KILL は赤のまま)。
  (! (probe-tree tmp-path))
  (.write-text (/ tmp-path "probe_ignores.hy")
               (.join "\n" ["(import os signal time pathlib [Path])"
                            "(signal.signal signal.SIGTERM signal.SIG-IGN)"
                            "(.write-text (Path \"ignorer.pid\") (str (os.getpid)))"
                            "(time.sleep 60)"
                            "(defn program [] None)"])
               :encoding "utf-8")
  (val policy (WorkerPolicy :stop-grace-ms 3000 :shim-sweep-margin-ms 1000))
  (val timeout-seconds 5)
  (val settings (! (probe-settings tmp-path :timeout-seconds timeout-seconds :policy policy)))
  (val deadline-ms (! (shim-deadline-ms settings.shim)))
  (val ignores (replace SERVICE :entry "probe_ignores:program"))
  (<- got tuple (run-probes settings (stopped-probe-scene ignores (str tmp-path) (/ tmp-path "ignorer.pid"))))
  (val view (get got 0))
  (assert (in "終わらない" view.detail) view.detail)
  (val grace-ms settings.shim.shim-grace-ms)
  (assert (>= (- view.failed-ms view.started-ms) (+ (* timeout-seconds 1000) grace-ms))
          (.format "合図から shim の猶予 {} ms を待たずに KILL を送った(起こしてから片づけまで {} ms)" grace-ms (- view.failed-ms view.started-ms)))
  (assert (< (get got 1) (/ deadline-ms 1000 2)) (.format "観測 1 回が {:.3f} 秒待ち込んだ" (get got 1)))
  (! (assert-gone (get got 2) "時間切れの検めの本体")))


(defk hanging-scene [specs hang ok-specs tree]
  {:pre [(: specs list) (: hang JobSpec) (: ok-specs list) (: tree str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固まる入口を中ほどに置いた束を検め、固まった入口の答え・ほかの答え・撃ち直しの直後の観測を返すため。撃ち直しは時間切れまで待って片づける。"
  (for [spec specs] (<- (ProbeEntry spec tree)))
  (<- hung ProbeView (observed hang))
  (var oks [])
  (for [spec ok-specs]
    (<- v ProbeView (observed spec))
    (:= oks (+ oks [v])))
  ;; 撃ち直し: 時間切れだった spec は単独の束で起こす(同じ木の ok の spec は待ちに残る)。
  (<- (ProbeEntry hang tree))
  (<- (ProbeEntry (get ok-specs 0) tree))
  (<- refired tuple (ObserveProbes))
  ;; 検の後始末: 撃ち直しが時間切れになるまで待つ(group ごと止まる)。
  (<- (observed hang))
  #(hung oks refired))


(deftest test-a-hanging-entry-does-not-take-down-its-batch [tmp-path]
  ;; 反例(必須 1): 同じ木の束に固まる入口が 1 つあっても、ほかの入口は結果どおり(PASSED)になり、時間切れで FAILED になるのは固まった
  ;; 入口を持つ spec だけ。撃ち直しでは、直前に時間切れになった spec を束に混ぜず単独で起こす(同じ束で全部が道連れを繰り返さない)。
  (! (probe-tree tmp-path))
  (val ok-specs (lfor i (range 6) (replace SERVICE :entry (.format "probe_ok:program{}" i) :name (.format "w{}" i))))
  (val hang (replace SERVICE :entry "probe_slow:program" :name "hang"))
  ;; 固まる入口を束の中ほどに置く(前の対象は結果が出ている・後の対象は結果が出ていない)。
  (val specs (+ (cut ok-specs 0 3) [hang] (cut ok-specs 3 None)))
  (for [i (range 6)]
    (.write-text (/ tmp-path "probe_ok.hy")
                 (+ (.read-text (/ tmp-path "probe_ok.hy")) (.format "(defn program{} [] None)\n" i))))
  (<- got tuple (run-probes (! (probe-settings tmp-path :timeout-seconds 3)) (hanging-scene specs hang ok-specs (str tmp-path))))
  (val hung (get got 0))
  (val oks (get got 1))
  (val refired (get got 2))
  (assert (= hung.state ProbeState.FAILED) hung)
  (assert (in "終わらない" hung.detail) hung.detail)
  (for [#(spec v) (zip ok-specs oks)]
    (assert (= v.state ProbeState.PASSED) (.format "{} が固まった入口の道連れになった: {}" spec.name v))
    (assert (= v.attempts 1) v))
  (val states (dfor v refired v.spec-hash v.state))
  (assert (= (get states (spec-hash hang)) ProbeState.RUNNING) states)
  (assert (= (get states (spec-hash (get ok-specs 0))) ProbeState.QUEUED) states))


(deftest test-a-running-probe-is-shown-as-probing-with-its-reason
  ;; 反例 (d): 検めの間の状態が starting ではなく検めの段(probing)で、経過の秒・回数・直前の失敗の理由を持つ。
  ;; 以前は検めの間を starting と出し、理由(probe-failed)は FAILED から撃ち直すまでの 30 秒しか見えなかった。
  (val plain (! (world :codes #(READY1) :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING)))))
  (val first (get (! (statuses 0 #(S1) plain {} POLICY)) 0))
  (assert (= first.phase JobPhase.PROBING) first.phase)
  ;; 撃ち直しの間: 直前の失敗の理由・回数・経過の秒が状態に残る。
  (val again (! (world :codes #(READY1)
                       :probes #((ProbeView (spec-hash S1) ProbeState.RUNNING :started-ms 1000 :attempts 2
                                            :last-failure "ImportError: cannot import name 'GetMonotonic'")))))
  (val status (get (! (statuses 46000 #(S1) again {} POLICY)) 0))
  (assert (= status.phase JobPhase.PROBING))
  (assert (= status.probe (ProbeStatus :state "running" :elapsed-seconds 45 :attempts 2
                                       :last-failure "ImportError: cannot import name 'GetMonotonic'"))
          status.probe)
  (assert (in "GetMonotonic" status.detail) status.detail)
  ;; 状態の JSON(heartbeat と status の file)にも載る。
  (<- row (status-row status))
  (assert (= (get row "phase") "probing"))
  (assert (= (get row "probe") {"state" "running" "elapsedSeconds" 45 "attempts" 2
                                "lastFailure" "ImportError: cannot import name 'GetMonotonic'"})
          row)
  ;; 検めの行は coordinator から見て「その worker で起こしかけている」(他へ置かない)。
  (assert (in "probing" LIVE-PHASES)))


(defk refired-scene [broken tree]
  {:pre [(: broken JobSpec) (: tree str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "失敗した検めを撃ち直し、最初の答え・撃ち直しの直後の観測・2 回目の答えを返すため。"
  (<- (ProbeEntry broken tree))
  (<- first ProbeView (observed broken))
  (<- (ProbeEntry broken tree))
  (<- views tuple (ObserveProbes))
  (<- second ProbeView (observed broken))
  #(first views second))


(deftest test-a-refired-probe-keeps-the-last-failure [tmp-path]
  ;; 反例 (d) の観測の側: FAILED の後に撃ち直した検めの観測は、走っている間も直前の失敗の理由と回数を持つ。
  (val tree (! (probe-tree tmp-path)))
  (val broken (replace SERVICE :entry "probe_broken:program"))
  (<- got tuple (run-probes (! (probe-settings tmp-path)) (refired-scene broken tree)))
  (val first (get got 0))
  (val second (get got 2))
  (assert (= first.state ProbeState.FAILED))
  (val matching (lfor v (get got 1) :if (= v.spec-hash (spec-hash broken)) v))
  (assert (= (len matching) 1) matching)
  (val refiring (get matching 0))
  (assert (in refiring.state #(ProbeState.QUEUED ProbeState.RUNNING)) refiring)
  (assert (= refiring.attempts 2) refiring)
  (assert (= refiring.last-failure first.detail) refiring)
  (assert (= second.attempts 2) second))


(deftest test-probe-reason-is-the-last-line
  (assert (= (! (probe-reason 1 "Traceback\n  File x\nImportError: nope\n\n")) "ImportError: nope"))
  (assert (in "終了 3" (! (probe-reason 3 "")))))


;; --- 宣言から消えた spec の検めの記録(2026-09-27 — #757)--------------------------------------------------
;; 検めの持ち主(以前の ProbeStore・今は検めの言い換え probe-host)は答え・回数・前の回の失敗の理由・時間切れの印を spec-hash ごとに持ち、宣言から消えた spec の分を
;; 落とさなかった(版を上げるたびに増え続ける)。plan が今の宣言の spec の指紋を渡し(ForgetProbes)、持ち主が集合に無い分を落とす。

(deftest test-the-plan-hands-the-declared-spec-hashes-when-a-stale-probe-record-is-observed
  (val stale (! (plan 0 #(S2) (! (world :codes #(READY2) :probes #((! (passed S2)) (! (failed S1))))) {} POLICY)))
  (assert (in (ForgetProbes (frozenset [(spec-hash S2)])) stale) stale)
  ;; 宣言の spec の記録だけなら撃たない。
  (val clean (! (plan 0 #(S2) (! (world :codes #(READY2) :probes #((! (passed S2))))) {} POLICY)))
  (assert (not (any (gfor a clean (isinstance a ForgetProbes)))) clean))


(defk forget-scene [kept gone nap tree]
  {:pre [(: kept JobSpec) (: gone JobSpec) (: nap JobSpec) (: tree str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宣言から消えた spec の記録を忘れる筋書き: 最初の答え・忘れた直後の観測・走っていた検めの答え・2 度目に忘れた後の観測・忘れた spec を
   積み直した観測を返すため。"
  (for [spec [kept gone]] (<- (ProbeEntry spec tree)))
  (<- first ProbeView (observed kept))
  (<- (observed gone))
  ;; 宣言に残る spec は撃ち直して 2 回目(前の回の失敗の理由を持つ)。
  (<- (ProbeEntry kept tree))
  (<- (observed kept))
  ;; 宣言から消えた spec の検めが走っている間に宣言が変わる。
  (<- (ProbeEntry nap tree))
  (<- (ObserveProbes))
  (val keep (frozenset [(spec-hash kept)]))
  (<- (ForgetProbes keep))
  (<- after tuple (ObserveProbes))
  (<- napped ProbeView (observed nap))
  (<- (ForgetProbes keep))
  (<- last tuple (ObserveProbes))
  ;; 忘れた spec を積み直すと回数は 1 から数え直す(回数の記録も落ちた)。
  (<- (ProbeEntry gone tree))
  (<- again tuple (ObserveProbes))
  (<- (observed gone))
  #(first after napped last again))


(deftest test-probe-store-forgets-the-records-of-specs-no-longer-declared [tmp-path]
  (val tree (! (probe-tree tmp-path)))
  (.write-text (/ tmp-path "probe_nap.hy") "(import time)\n(time.sleep 1)\n(defn program [] None)\n" :encoding "utf-8")
  (val kept (replace SERVICE :entry "probe_broken:program" :name "kept"))
  (val gone (replace SERVICE :entry "probe_broken:program" :name "gone"))
  (val nap (replace SERVICE :entry "probe_nap:program" :name "nap"))
  (<- got tuple (run-probes (! (probe-settings tmp-path :timeout-seconds 30)) (forget-scene kept gone nap tree)))
  (val first (get got 0))
  (val napped (get got 2))
  (val last (get got 3))
  (val again (get got 4))
  (val views (dfor v (get got 1) v.spec-hash v))
  (assert (not-in (spec-hash gone) views) views)
  ;; 宣言に残る spec の失敗の理由と回数は残る。
  (val k (get views (spec-hash kept)))
  (assert (= #(k.state k.attempts k.last-failure) #(ProbeState.FAILED 2 first.detail)) k)
  ;; 走っている検めの process は落とさない(終わった後の答えを次の片づけで落とす)。
  (assert (= (. (get views (spec-hash nap)) state) ProbeState.RUNNING) views)
  (assert (= napped.state ProbeState.PASSED))
  (assert (= (sfor v last v.spec-hash) #{(spec-hash kept)}) last)
  (val regone (lfor v again :if (= v.spec-hash (spec-hash gone)) v))
  (assert (= (len regone) 1) regone)
  (assert (= #((. (get regone 0) attempts) (. (get regone 0) last-failure)) #(1 "")) regone))


;; --- 旧い形の service の spec(計画 2.8 の入口 15 — 2026-09-27)---------------------------------------------------
;; 旧い coordinator の返事の spec(job_entry service --factory … --env … --config …・置き場のキー無し)は、入口の module の import だけを
;; 検めると通ってしまい、子の job_entry が argparse で落ちて起こし直しを繰り返す。検めの段で process を起こさずに理由つきで断る。

(val OLD-SPEC (JobSpec "w" JOB-ENTRY #("service" "--factory" "m.f:program" "--env" "m.e:handlers" "--config" "{}") "rev1"))


(deftest test-an-old-service-spec-is-refused-by-the-probe-with-its-reason
  (val reason (! (probe-refusal OLD-SPEC)))
  (assert (is-not reason None) "古い形の service は断る")
  (assert (in "--factory・--env・--config" reason) reason)
  ;; 新しい形でも置き場のキーが無ければ断る。
  (val keyless (! (probe-refusal (replace S1 :program None))))
  (assert (is-not keyless None) "置き場のキーの無い service は断る")
  (assert (in "置き場のキー" keyless) keyless)
  ;; 新しい形の service・task・素の entry は断らない。
  (assert (is (! (probe-refusal S1)) None))
  (assert (is (! (probe-refusal (JobSpec "task/1" JOB-ENTRY #("task" "--result" "r") "rev1" :once True))) None))
  (assert (is (! (probe-refusal (JobSpec "a" "jobs.a" #() "rev1"))) None)))


(defk old-spec-scene []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "旧い形の service の spec を積み、直後の観測と次の拍の観測を返すため。"
  (<- (ProbeEntry OLD-SPEC "/nonexistent-tree"))
  (<- views tuple (ObserveProbes))
  (<- again tuple (ObserveProbes))
  #(views again))


(deftest test-the-probe-store-fails-an-old-service-spec-without-a-process [tmp-path]
  (<- got tuple (run-probes (! (probe-settings tmp-path)) (old-spec-scene)))
  (val views (get got 0))
  (val again (get got 1))
  ;; process を起こさない: 待ちにも走りにも載らず、すぐ FAILED の答えになる(無い木で起こせば OSError で落ちる)。
  (assert (= (len views) 1) views)
  (val view (get views 0))
  (assert (= view.state ProbeState.FAILED) view)
  (assert (in "旧い service の spec の引数" view.detail) view.detail)
  (assert (= again views) again)
  ;; worker の状態の報告では probe-failed と理由(起こさない)。
  (val w (! (world :probes #(view))))
  (assert (= (! (plan (+ view.failed-ms 1) #(OLD-SPEC) w {} POLICY)) #()))
  (val status (get (! (statuses (+ view.failed-ms 1) #(OLD-SPEC) w {} POLICY)) 0))
  (assert (= status.phase JobPhase.PROBE-FAILED) status)
  (assert (in "旧い service の spec の引数" status.detail) status.detail))
