;; 起動の script の cgroup の層(deploy/boot.sh の WORKER_CGROUP_LAYOUT)の検。
;;
;; k8s の Pod の worker では、worker の本体・常駐の job・task が 1 つの cgroup(container の cgroup)を分け合い、1 本の task が memory を
;; 増やすと、container の上限の手前で回収が詰まり、本体も常駐の job も一緒に止まった(10-24 06:03 JST に 16.7 分)。層は container の cgroup
;; の中に子を作り、守る子(memory.min)と限る子(memory.max)を分ける。worker はその子へ job を置き分ける(test_job_cgroup.hy)。
;;
;; 模擬: cgroupfs の代わりに tmp の dir と file で、ROLE=cgroup の boot.sh が作る物と断る形を確かめる。
;; 本物: 書ける委ねられた cgroup(DOEFF_WORKER_CGROUP_DIR — 層を作った worker が job の子へ渡す)が在る機体だけで、限る子の中で memory を
;; 際限なく増やす子を kernel が止め、守る子が拍を打ち続ける事を確かめる(無い機体では skip)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import shutil)
(import subprocess)
(import tempfile)
(import time)
(import pathlib [Path])
(import pytest)
(import doeff_cluster._worker.core.job_cgroup [placed-argv])
(import doeff_cluster._worker.entry.main [CGROUP-DIR-VAR])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
(val LAYOUT "worker:memory.min=2G services:memory.min=24G jobs:memory.max=36G commands:memory.max=28G")


(defk fake-container [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "program"}}
  "host の cgroupfs の mount の代わりの tmp の木に、container の cgroup の dir(process 2 つ・子の無い cgroup.subtree_control)を作るため。"
  (val scope (/ tmp-path "host" "kubepods.slice" "pod-x.slice" "cri-containerd-y.scope"))
  (.mkdir scope :parents True)
  (.write-text (/ scope "cgroup.procs") "4242\n4243\n")
  (.write-text (/ scope "cgroup.subtree_control") "")
  scope)


(defk run-layout [tmp-path ns-root layout [wait "2"]]
  {:pre [(: tmp-path Path) (: ns-root Path) (: layout str) (: wait str)] :post [(: % subprocess.CompletedProcess)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "ROLE=cgroup で boot.sh を起こす(host の見えと名前空間の根を検の tmp に向ける)— 層が作る物と断りを外から読むため。"
  (subprocess.run ["sh" BOOT-SH]
                  :env {"PATH" (os.environ.get "PATH" "") "HOME" (str tmp-path) "ROLE" "cgroup"
                        "WORKER_CGROUP_HOST" (str (/ tmp-path "host")) "WORKER_CGROUP_NS_ROOT" (str ns-root)
                        "WORKER_CGROUP_WAIT" wait "WORKER_CGROUP_LAYOUT" layout}
                  :capture-output True :text True :timeout 30))


(deftest test-the-layout-moves-every-process-to-the-first-layer-and-writes-each-value [#^ Path tmp-path]
  ;; 名前空間の根と同じ inode の dir(= 自分の container の cgroup)を host の見えの中から引き、層を作り、居た process を最初の層へ移し、
  ;; 子に memory を掛け、層ごとの値を書いて、その dir を出す。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope LAYOUT))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip done.stdout) (str scope)) done.stdout)
  (assert (= (.strip (.read-text (/ scope "cgroup.subtree_control"))) "+memory"))
  ;; 模擬の cgroup.procs は file なので、移した pid は最後の 1 つが残る(本物は全部の pid が移る)。
  (assert (= (.strip (.read-text (/ scope "worker" "cgroup.procs"))) "4243"))
  (val written (dfor #(layer file) #(#("worker" "memory.min") #("services" "memory.min") #("jobs" "memory.max") #("commands" "memory.max"))
                     (+ layer "/" file) (.strip (.read-text (/ scope layer file)))))
  (assert (= written {"worker/memory.min" "2G" "services/memory.min" "24G" "jobs/memory.max" "36G" "commands/memory.max" "28G"}) written))


(deftest test-a-layout-with-cpu-files-gives-the-children-cpu-and-writes-each-weight [#^ Path tmp-path]
  ;; CPU の重み: cpu の file を書く層が在れば、子に memory と cpu を掛けてから重みを書く。失敗ケース: 子に cpu を
  ;; 掛けないまま cpu.weight を書くと、kernel は file を作らず(書けない)、常駐の層の thread が一括の層と同じ重みで実行待ちに並ぶ。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess
      (run-layout tmp-path scope "worker:memory.min=2G,cpu.weight=1000 services:cpu.weight=10000 jobs:memory.max=36G,cpu.weight=1 commands:cpu.idle=1"))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip (.read-text (/ scope "cgroup.subtree_control"))) "+memory +cpu"))
  (val written (dfor #(layer file) #(#("worker" "memory.min") #("worker" "cpu.weight") #("services" "cpu.weight") #("jobs" "cpu.weight")
                                     #("commands" "cpu.idle"))
                     (+ layer "/" file) (.strip (.read-text (/ scope layer file)))))
  (assert (= written {"worker/memory.min" "2G" "worker/cpu.weight" "1000" "services/cpu.weight" "10000" "jobs/cpu.weight" "1"
                      "commands/cpu.idle" "1"})
          written))


(deftest test-a-bad-cpu-value-is-refused-before-any-layer-is-made [#^ Path tmp-path]
  ;; 失敗ケース: kernel の範囲の外の重み(0・10001・数でない)や 0・1 の外の cpu.idle を、層を作った後の書きで初めて知ると、起動が層を半端に
  ;; 作ったまま止まる。宣言を読む段で全部を名指して断る。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess
      (run-layout tmp-path scope "worker:cpu.weight=0 services:cpu.weight=10001 jobs:cpu.weight=high commands:cpu.idle=2 x:cpu.weight="))
  (assert (= done.returncode 2) done)
  (for [part #("'0'" "'10001'" "'high'" "cpu.idle の値 '2'" "層 x の cpu.weight の値 ''")]
    (assert (in part done.stderr) done.stderr))
  (assert (= (sorted (gfor p (.iterdir scope) p.name)) ["cgroup.procs" "cgroup.subtree_control"]) "層を作らない"))


(deftest test-a-bad-layout-is-refused-with-every-problem-and-touches-nothing [#^ Path tmp-path]
  ;; 失敗ケース: 宣言の誤りを 1 つずつ直して起こし直すと、その度に Pod が起動で止まる。全部の誤りを 1 度に名指し、層は作らない。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope "Worker:memory.min=2G jobs:cpu.max=1 x:memory.max=lots"))
  (assert (= done.returncode 2) done)
  (for [part #("'Worker'" "cpu.max" "'lots'")]
    (assert (in part done.stderr) done.stderr))
  (assert (= (sorted (gfor p (.iterdir scope) p.name)) ["cgroup.procs" "cgroup.subtree_control"]) "層を作らない"))


(deftest test-an-undelegated-cgroup-is-refused-after-the-wait [#^ Path tmp-path]
  ;; 失敗ケース: 委ねる係が動かなかった Pod の worker が、層の無いまま(守りの無いまま)黙って起動する。待ちの上限で名指して断る。
  (when (= (os.geteuid) 0)
    (pytest.skip "root は持ち主によらず書けるので、委ねられていない形を作れない"))
  (<- scope Path (fake-container tmp-path))
  (.chmod (/ scope "cgroup.procs") 0o444)
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope LAYOUT :wait "1"))
  (assert (= done.returncode 2) done)
  (assert (in "委ねられていない" done.stderr) done.stderr)
  (assert (not (.exists (/ scope "worker"))) "層を作らない"))


(deftest test-a-host-view-without-the-container-cgroup-is-refused [#^ Path tmp-path]
  ;; 失敗ケース: host の cgroupfs を置き忘れた(か別の物を置いた)Pod で、層を作る dir を取り違える。名前空間の根と同じ dir が無ければ断る。
  (<- scope Path (fake-container tmp-path))
  (val elsewhere (/ tmp-path "elsewhere"))
  (.mkdir elsewhere)
  (<- done subprocess.CompletedProcess (run-layout tmp-path elsewhere LAYOUT))
  (assert (= done.returncode 2) done)
  (assert (in "同じ dir が無い" done.stderr) done.stderr)
  (assert (not (.exists (/ scope "worker"))) "層を作らない"))


;; 本物の検を走らせられる書ける委ねられた cgroup(DOEFF_WORKER_CGROUP_DIR — 子に memory を掛けた層の根)。無ければ None。
(val DELEGATED (let [named (os.environ.get CGROUP-DIR-VAR)]
                 (when (and named (os.access (os.path.join named "cgroup.procs") os.W_OK)
                            (in "memory" (.split (.read-text (Path named "cgroup.subtree_control")))))
                   (Path named))))


(deftest test-the-kernel-stops-a-runaway-child-while-the-guarded-child-keeps-beating
  {:marks ["realtime"]
   :skip-if (is DELEGATED None)
   :skip-reason "DOEFF_WORKER_CGROUP_DIR の書ける委ねられた cgroup が無い(層を作った worker の子だけで走る)"}
  ;; 本物の cgroup: 層の根の下に検の組を作り、守る子(memory.min 32M)で 0.05 秒ごとに拍を打つ子と、限る子(memory.max 64M)で memory を
  ;; 際限なく増やす子(/dev/zero を改行なしに tail へ流す)を、worker と同じ包み(placed-argv)で置く。kernel は限る子の中だけで OOM で止め、
  ;; 守る子は拍を打ち続ける。失敗ケース(10-24 06:03 JST): 1 つの cgroup では、48 GB の task が container の上限の手前で回収を詰まらせ、
  ;; 記録の service も worker の本体も 16.7 分止まった。
  (val root DELEGATED)
  (val scratch (Path (tempfile.mkdtemp)))
  (val group (/ root (.format "doeff-test-{}" (os.getpid))))
  (val guarded (/ group "guarded"))
  (val limited (/ group "limited"))
  (val beats (/ scratch "beats"))
  (.mkdir group)
  (var beat None)
  (try
    (.write-text (/ group "cgroup.subtree_control") "+memory")
    (.mkdir guarded)
    (.mkdir limited)
    (.write-text (/ guarded "memory.min") "32M")
    (.write-text (/ limited "memory.max") "64M")
    (:= beat (subprocess.Popen (list (! (placed-argv (str (/ guarded "cgroup.procs"))
                                                       #("sh" "-c" (.format "while :; do date +%s.%N >> {}; sleep 0.05; done" beats)))))))
    ;; ポーリングの訳: 拍の子(sh の loop)は始まりの知らせを持たないので、最初の拍が file に出るまで(5 秒まで)読んでから限る子を始める。読まずに始めると、64M は
    ;; 0.13 秒で満ち、最初の拍より先に限る子が止まる(10-24 11:39 JST の本番の zeus の worker)。
    (val first-beat (+ (time.monotonic) 5))
    (while (and (not (and (.exists beats) (.strip (.read-text beats)))) (< (time.monotonic) first-beat))
      (time.sleep 0.01))
    (val started (time.time))
    (val hog (subprocess.run (list (! (placed-argv (str (/ limited "cgroup.procs")) #("sh" "-c" "head -c 4G /dev/zero | tail > /dev/null"))))
                             :timeout 120 :capture-output True :text True))
    (val ended (time.time))
    ;; 止まった後の拍を 1 つ以上数えるため、拍の間隔の数倍だけ待つ(拍の子は知らせを持たない)。
    (time.sleep 0.3)
    (val events (dfor line (.splitlines (.read-text (/ limited "memory.events"))) :setv parts (.split line) (get parts 0) (int (get parts 1))))
    (val stamps (lfor line (.splitlines (.read-text beats)) :if (.strip line) (float line)))
    (val gaps (lfor #(a b) (zip stamps (cut stamps 1 None)) (- b a)))
    (assert (!= hog.returncode 0) hog)
    (assert (>= (get events "oom_kill") 1) events)
    (assert (is (.poll beat) None) "守る子は走り続ける")
    (assert (and stamps (< (get stamps 0) started) (> (get stamps -1) ended)) #(started ended (cut stamps 0 3) (cut stamps -3 None)))
    (assert (< (max gaps) 1.0) (max gaps))
    (finally
      ;; 拍の子の sh を止めても、その子の date・sleep が cgroup に残り、rmdir が EBUSY になる(10-24 11:39 JST)。組の cgroup.kill で子孫を
      ;; 全部止め、cgroup.events が populated 0 になるまで(5 秒まで)読んでから消す。ポーリングの訳: 子孫の退きの知らせは inotify の
      ;; cgroup.events の変更だけで、検の後片付けの 1 度に読み手を足すほどの物でない。
      (.write-text (/ group "cgroup.kill") "1")
      (when (is-not beat None)
        (.kill beat)
        (.wait beat))
      (val emptied (+ (time.monotonic) 5))
      (while (and (in "populated 1" (.read-text (/ group "cgroup.events"))) (< (time.monotonic) emptied))
        (time.sleep 0.01))
      (for [child #(guarded limited group)]
        (when (.exists child)
          (.rmdir child)))
      (shutil.rmtree scratch))))
