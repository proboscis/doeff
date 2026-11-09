;; jobs の層の下の job ごとの子(worker/core/job_cgroup の「job ごとの子」): io を層に掛けた後の 25 分で、機体の SSD への書きの 74% が層 jobs
;; だったが、層の下に子が無く job ごとに分けられなかった(2026-11-09)。task は job の名の子へ、名の無い子は共有の子 _shared へ置き、層そのものに
;; process を残さない(cgroup v2 は子に controller を掛けた cgroup に process を置けない)。終わった子は次の置きの前の掃きが io.stat を読んで
;; から消し、層の子へ io を掛け損ねていれば試し直す。cgroupfs の代わりは tmp の木 — rmdir は「cgroup.procs が空なら中の file ごと消える」
;; 本物の cgroupfs の振る舞いを PATH の先頭の偽の rmdir で真似る。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses)
(import os)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [os-file-handler subprocess-handler])
(import doeff_core_effects [EnvEntry EnvMode ProcessOutcome RunProcess])
(import doeff_cluster [JobSpec])
(import doeff_cluster._worker.core.job_cgroup [SHARED-CGROUP SUBGROUP-NAME-MAX io-totals job-subgroup-name layer-subgroup-procs
                                               placed-argv swept-groups sweep-argv])
(import doeff_cluster._worker.core.warm_rules [warm-child-places])
(import doeff_cluster._worker.intent.worker_model [WarmChildMark WarmChildView WarmMarkUnreadable WorldView])
(import doeff_cluster._worker.protocol.process_host [HostSettings shim-argv])
(import doeff_cluster._worker.entry.main [job-shared-placed])
(import tests.host_rig [host-settings job-ended run-on-host])

(val SHIM-ARGV #("python" "-m" "doeff_cluster._worker.entry.shim" "--" "uv" "run" "hy"))
(val IO-STAT "8:16 rbytes=1024 wbytes=4096 rios=1 wios=2 dbytes=0 dios=0\n259:0 rbytes=10 wbytes=20 rios=1 wios=1 dbytes=0 dios=0\n")
;; 本物の cgroupfs の rmdir の代わり: process の居る子(cgroup.procs が空でない)は断り(EBUSY)、空の子は中の file ごと消える。
(val FAKE-RMDIR "#!/bin/sh\n[ -s \"$1/cgroup.procs\" ] && exit 1\nrm -rf \"$1\"\n")


(defn #^ Path group-dir [#^ Path root #^ str name #^ str procs]  ; defk にできない: 検の木の下ごしらえ(Program でない tmp の file を作る)
  "模擬の cgroup の dir を 1 つ作る(cgroup.procs の中身と io.stat を置く)。"
  (setv d (/ root name))
  (.mkdir d :parents True :exist-ok True)
  (.write-text (/ d "cgroup.procs") procs)
  (.write-text (/ d "io.stat") IO-STAT)
  d)


(defn #^ Path jobs-layer [#^ Path tmp #^ str controllers]  ; defk にできない: 検の木の下ごしらえ
  "模擬の jobs の層(層の cgroup.procs は空・controllers = 親が渡す controller・subtree_control は空)と偽の rmdir の dir を作る。"
  (setv layer (/ tmp "cg" "jobs"))
  (.mkdir layer :parents True)
  (.write-text (/ layer "cgroup.procs") "")
  (.write-text (/ layer "cgroup.controllers") controllers)
  (.write-text (/ layer "cgroup.subtree_control") "")
  (setv bin (/ tmp "bin"))
  (.mkdir bin)
  (.write-text (/ bin "rmdir") FAKE-RMDIR)
  (.chmod (/ bin "rmdir") 0o755)
  layer)


(deftest test-a-job-name-becomes-one-safe-child-name
  ;; job の名の / と . は _ に(cgroup の file の名 cgroup.procs・io.stat と重ならない)。_ で始まる名は共有の子と掃きの除外に重ねない。
  (assert (= (! (job-subgroup-name "task/t40343")) "task_t40343"))
  (assert (= (! (job-subgroup-name "a.b c")) "a_b_c"))
  (assert (= (! (job-subgroup-name "_shared")) "job_shared"))
  (assert (!= (! (job-subgroup-name "_shared")) SHARED-CGROUP))
  ;; 長い名は頭を残して指紋を付ける(違う長い名は重ならない)。
  (val long-a (! (job-subgroup-name (* "x" 400))))
  (val long-b (! (job-subgroup-name (+ (* "x" 399) "y"))))
  (assert (= (len long-a) SUBGROUP-NAME-MAX) long-a)
  (assert (!= long-a long-b)))


(deftest test-a-task-is-placed-in-its-own-child-and-a-service-stays-in-services [#^ Path tmp-path]
  ;; task の shim は job の名の子へ、常駐の job は services の層へ包む。job の子が無い(置き分けない worker)task は包まない(外の placer が
  ;; 共有の子へ置く)。失敗ケース: task を層 jobs に直に置くと、層の子へ io を掛けられない(kernel が断る)。
  (<- base HostSettings (host-settings tmp-path))
  (val services "/cg/services/cgroup.procs")
  (val placing (dataclasses.replace base :service-procs services :job-procs "/cg/jobs/cgroup.procs"))
  (val job (! (layer-subgroup-procs "/cg/jobs/cgroup.procs" "task_t1")))
  (assert (= job "/cg/jobs/task_t1/cgroup.procs") job)
  (val task (JobSpec "task/t1" "doeff_cluster._worker.entry.job_entry" #() "r" :once True))
  (val service (JobSpec "records" "doeff_cluster._worker.entry.job_entry" #() "r"))
  (assert (= (! (shim-argv placing task SHIM-ARGV job)) (! (placed-argv job SHIM-ARGV))))
  (assert (= (! (shim-argv placing service SHIM-ARGV job)) (! (placed-argv services SHIM-ARGV))))
  (assert (= (! (shim-argv placing task SHIM-ARGV)) SHIM-ARGV)))


(deftest test-the-worker-makes-the-shared-child-at-boot [#^ Path tmp-path]
  ;; 名の無い子(git・uv・待ちの子)の置き場は層の下の _shared(起動の時に作る)。層の無い worker は None。
  (val layer (jobs-layer tmp-path "memory io"))
  (val shared (! (with-handlers [os-file-handler] (job-shared-placed (str (/ layer "cgroup.procs"))))))
  (assert (= shared (str (/ layer SHARED-CGROUP "cgroup.procs"))) shared)
  (assert (.is-dir (/ layer SHARED-CGROUP)))
  (assert (is (! (with-handlers [os-file-handler] (job-shared-placed None))) None)))


(deftest test-the-sweep-removes-only-finished-job-children-and-reads-their-io [#^ Path tmp-path]
  ;; 本物の sh: 終わった子(process が居ない・残す名に無い)だけを、io.stat を読んでから消す。走っている子・残す名(起こしたばかりで
  ;; まだ移っていない shim の job)・共有の子は消さない。層の子へ io を掛ける(親が io を渡す時)。
  (val layer (jobs-layer tmp-path "cpu io memory"))
  (group-dir layer "task_t0" "")
  (group-dir layer "task_t1" "4242\n")
  (group-dir layer "task_t2" "")
  (group-dir layer SHARED-CGROUP "")
  (<- argv tuple (sweep-argv (str (/ layer "cgroup.procs")) #("task_t2")))
  (<- swept ProcessOutcome (with-handlers [subprocess-handler]
                             (RunProcess :argv argv :env #((EnvEntry :name "PATH" :value (+ (str (/ tmp-path "bin")) ":" (get os.environ "PATH"))))
                                         :env-mode EnvMode.EXTEND)))
  (assert (= swept.exit-code 0) swept)
  (assert (not (.exists (/ layer "task_t0"))) "終わった子は消す")
  (assert (.is-dir (/ layer "task_t1")) "process の居る子は残る")
  (assert (.is-dir (/ layer "task_t2")) "残す名の子は消さない")
  (assert (.is-dir (/ layer SHARED-CGROUP)) "共有の子は消さない")
  (<- groups tuple (swept-groups swept.stdout))
  (assert (= groups #(#("task_t0" #(#("259:0" 10 20) #("8:16" 1024 4096))))) groups)
  (assert (= (.strip (.read-text (/ layer "cgroup.subtree_control"))) "+io")))


(deftest test-io-is-tried-only-when-the-parent-passes-it-and-a-refusal-is-retried-later [#^ Path tmp-path]
  ;; 親が io を渡さない機体では掛けない。kernel が断る時(層に process が残る — 前の世代の子)は掃きを失敗にせず、次の掃きが試し直す。
  (val layer (jobs-layer tmp-path "cpu memory"))
  (<- argv tuple (sweep-argv (str (/ layer "cgroup.procs")) #()))
  (<- plain ProcessOutcome (with-handlers [subprocess-handler] (RunProcess :argv argv)))
  (assert (= plain.exit-code 0) plain)
  (assert (= (.read-text (/ layer "cgroup.subtree_control")) "") "io を渡さない親の下では掛けない")
  ;; 断る kernel の代わり: subtree_control を書けない物(dir)にする。
  (.write-text (/ layer "cgroup.controllers") "cpu io memory")
  (.unlink (/ layer "cgroup.subtree_control"))
  (.mkdir (/ layer "cgroup.subtree_control"))
  (<- refused ProcessOutcome (with-handlers [subprocess-handler] (RunProcess :argv argv)))
  (assert (= refused.exit-code 0) refused))


(deftest test-io-stat-is-read-per-device
  ;; io.stat の行(と掃きの ; で繋いだ行)を device ごとの読み書きの byte に。欄の欠けと増えた欄で読みを落とさない。
  (assert (= (! (io-totals IO-STAT)) #(#("259:0" 10 20) #("8:16" 1024 4096))))
  (assert (= (! (io-totals "8:16 rbytes=5 wios=1;8:0 wbytes=x future=9;")) #(#("8:0" 0 0) #("8:16" 5 0))))
  (assert (= (! (io-totals "")) #()))
  (assert (= (! (swept-groups "task_t9\t\n")) #(#("task_t9" #())))))


(deftest test-a-task-runs-in-its-own-child-and-the-finished-one-is-logged [#^ Path tmp-path capfd monkeypatch]
  ;; 本物の process-host と shim: task は jobs/<job の名>/ へ移ってから走り、層そのものには何も書かない。前に終わった job の子は消され、
  ;; その読み書きの byte が記録に 1 行残る(機体の disk への書きを job の名で数える読みの元)。
  (val layer (jobs-layer tmp-path "cpu io memory"))
  (monkeypatch.setenv "PATH" (+ (str (/ tmp-path "bin")) ":" (get os.environ "PATH")))
  (group-dir layer "task_t0" "")
  (setv tree (/ tmp-path "tree"))
  (.mkdir tree)
  (.write-text (/ tree "probe_entry.py") "print('ran')\n")
  (<- base HostSettings (host-settings tmp-path))
  (val settings (dataclasses.replace base :job-procs (str (/ layer "cgroup.procs"))))
  (setv ended (! (run-on-host settings (job-ended (JobSpec "task/t1" "probe_entry" #() "rev-a" :once True) (str tree) 30))))
  (assert (= (.strip (.read-text (/ layer "task_t1" "cgroup.procs"))) (str ended.pid)) "shim は job の名の子へ移る")
  (assert (= (.read-text (/ layer "cgroup.procs")) "") "層そのものには置かない")
  (assert (not (.exists (/ layer "task_t0"))) "終わった job の子は消す")
  (assert (= (.strip (.read-text (/ layer "cgroup.subtree_control"))) "+io"))
  (setv err (. (capfd.readouterr) err))
  (assert (in "job の cgroup の読み書き" err) err)
  (assert (in "task_t0" err) err)
  (assert (in "4116" err) err))


(deftest test-a-warm-fork-is-placed-only-by-a-warm-child-that-takes-the-cgroup
  ;; 待ちの子は root の版で走り、印 serviceForks の無い版は頼みの知らない欄(cgroupProcs)を断る — その版の待ちの子から分ける task は
  ;; 移さない(待ちの子の cgroup = 共有の子に残る)。
  (val world (fn [mark] (WorldView #() #() :warm-children #((WarmChildView :key "env-a" :pid 1 :started-ms 0 :mark mark)))))
  (assert (warm-child-places (world (WarmChildMark :threads 1 :vm-live #(0 0 0) :services True)) "env-a"))
  (assert (not (warm-child-places (world (WarmChildMark :threads 1 :vm-live #(0 0 0))) "env-a")))
  (assert (not (warm-child-places (world (WarmMarkUnreadable :detail "x")) "env-a")))
  (assert (not (warm-child-places (world None) "env-a")))
  (assert (not (warm-child-places (WorldView #() #()) "env-a"))))
