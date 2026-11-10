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


(defk run-layout [tmp-path ns-root layout [wait "2"] [init-layer ""]]
  {:pre [(: tmp-path Path) (: ns-root Path) (: layout str) (: wait str) (: init-layer str)] :post [(: % subprocess.CompletedProcess)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "ROLE=cgroup で boot.sh を起こす(host の見えと名前空間の根を検の tmp に向ける・init-layer = WORKER_CGROUP_INIT_LAYER — 空なら渡さない)
   — 層が作る物と断りを外から読むため。"
  (subprocess.run ["sh" BOOT-SH]
                  :env (| {"PATH" (os.environ.get "PATH" "") "HOME" (str tmp-path) "ROLE" "cgroup"
                           "WORKER_CGROUP_HOST" (str (/ tmp-path "host")) "WORKER_CGROUP_NS_ROOT" (str ns-root)
                           "WORKER_CGROUP_WAIT" wait "WORKER_CGROUP_LAYOUT" layout}
                          (if init-layer {"WORKER_CGROUP_INIT_LAYER" init-layer} {}))
                  :capture-output True :text True :timeout 30))


(val INIT-LAYOUT "worker:memory.min=2G services:memory.min=24G jobs:memory.max=36G exec:memory.max=1G")


(deftest test-the-init-layer-takes-only-the-init-and-the-first-layer-takes-the-rest [#^ Path tmp-path]
  ;; init の層(boot.sh の「init の層」): container の init(pid 1)だけを init の層へ、ほかを最初の層へ移す — runc は kubectl exec・
  ;; readinessProbe・preStop を init の cgroup へ入れるので、その memory は init の層の memory.max で止まる。失敗ケース(11-04 12:59 UTC の
  ;; node-h): init が worker の本体と同じ上限の無い層に居て、exec の grep 2 本の 69 GB が機体の全体の OOM まで止まらなかった。
  (<- scope Path (fake-container tmp-path))
  (.write-text (/ scope "cgroup.procs") "1\n4242\n4243\n")
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope INIT-LAYOUT :init-layer "exec"))
  (assert (= done.returncode 0) done.stderr)
  ;; 模擬の cgroup.procs は file なので、移した pid は最後の 1 つが残る。
  (assert (= (.strip (.read-text (/ scope "exec" "cgroup.procs"))) "1"))
  (assert (= (.strip (.read-text (/ scope "worker" "cgroup.procs"))) "4243"))
  (assert (= (.strip (.read-text (/ scope "exec" "memory.max"))) "1G")))


(deftest test-a-bad-init-layer-is-refused-before-any-layer-is-made [#^ Path tmp-path]
  ;; 失敗ケース: init の層が宣言に無い・memory.max を持たない・最初の層と同じだと、exec の命令は上限の無い層に入ったまま起動する。
  ;; 宣言を読む段で名指して断り、層は作らない。
  (<- scope Path (fake-container tmp-path))
  (.write-text (/ scope "cgroup.procs") "1\n4242\n")
  (for [#(layout layer part) #(#(INIT-LAYOUT "probe" "init の層 probe が WORKER_CGROUP_LAYOUT に無い")
                               #("worker:memory.min=2G exec:cpu.weight=1000" "exec" "init の層 exec に memory.max が無い")
                               #("worker:memory.max=2G jobs:memory.max=1G" "worker" "最初の層")
                               ;; 後ろの層の memory.max を init の層の物と読まない。
                               #("worker:memory.min=2G exec:memory.min=1G jobs:memory.max=1G" "exec" "init の層 exec に memory.max が無い"))]
    (<- done subprocess.CompletedProcess (run-layout tmp-path scope layout :init-layer layer))
    (assert (= done.returncode 2) done)
    (assert (in part done.stderr) done.stderr)
    (assert (= (sorted (gfor p (.iterdir scope) p.name)) ["cgroup.procs" "cgroup.subtree_control"]) "層を作らない")))


(deftest test-an-init-outside-the-container-cgroup-is-refused-before-any-layer-is-made [#^ Path tmp-path]
  ;; 失敗ケース: init(pid 1)が container の cgroup に居ない(Pod の中で pid の名前空間を分け合い、pid 1 が別の container の pause)と、
  ;; exec の命令は init の層でなく worker の本体の層へ入り続ける。層を作る前に断る。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope INIT-LAYOUT :init-layer "exec"))
  (assert (= done.returncode 2) done)
  (assert (in "init(pid 1)が" done.stderr) done.stderr)
  (assert (= (sorted (gfor p (.iterdir scope) p.name)) ["cgroup.procs" "cgroup.subtree_control"]) "層を作らない"))


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


(deftest test-the-layers-get-io-only-when-the-parent-offers-it [#^ Path tmp-path]
  ;; io の読み(io.stat): 親が io を渡す機体(cgroup.controllers に io)では、子に io も掛け、層ごとの device 別の書きを読めるようにする。
  ;; 失敗ケース: 親が io を渡さない機体で +io を書くと kernel が断り、層の作りが 5 回の試しの後に止まって worker が起きない。
  (<- scope Path (fake-container tmp-path))
  (.write-text (/ scope "cgroup.controllers") "cpuset cpu io memory pids\n")
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope "worker:memory.min=2G,cpu.weight=1000 commands:memory.max=28G"))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip (.read-text (/ scope "cgroup.subtree_control"))) "+memory +cpu +io"))
  (<- bare Path (fake-container (/ tmp-path "bare")))
  (.write-text (/ bare "cgroup.controllers") "cpuset cpu memory pids\n")
  (<- plain subprocess.CompletedProcess (run-layout (/ tmp-path "bare") bare LAYOUT))
  (assert (= plain.returncode 0) plain.stderr)
  (assert (= (.strip (.read-text (/ bare "cgroup.subtree_control"))) "+memory")))


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


(deftest test-a-layout-with-cpu-max-writes-the-kernel-form-of-each-cap [#^ Path tmp-path]
  ;; CPU の枠(boot.sh の「CPU の枠」): cpu.max を書く層が在れば、子に cpu を掛けてから、宣言の <quota>/<period> を kernel の書式
  ;; 「<quota> <period>」で書く。失敗ケース(11-08 の node-c): 枠を掛けられないと、会話の層が container の上限 12 core を埋めた周期ごとに
  ;; 常駐の job の thread も周期の残りの間止められ、常駐の service の答えの組みが 0.1 秒から 4.2 秒に伸びた。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess
      (run-layout tmp-path scope "worker:memory.min=1G services:memory.min=24G jobs:memory.max=8G,cpu.max=200000/100000 clis:cpu.max=max/100000"))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip (.read-text (/ scope "cgroup.subtree_control"))) "+memory +cpu"))
  (val written (dfor layer #("jobs" "clis") layer (.strip (.read-text (/ scope layer "cpu.max")))))
  (assert (= written {"jobs" "200000 100000" "clis" "max 100000"}) written)
  (assert (not (.exists (/ scope "services" "cpu.max"))) "枠を宣言しない層には書かない"))


(deftest test-a-layout-with-swap-max-keeps-the-guarded-layers-out-of-swap [#^ Path tmp-path]
  ;; swap の上限(boot.sh の「swap の上限」): 守る層に memory.swap.max=0 を書き、宣言しない層には書かない(container の枠の内で使える)。
  ;; 失敗ケース: swap と zswap の在る機体で守る層に 0 を書けないと、回収が常駐の job の匿名の memory を圧縮の pool と SSD へ出し、
  ;; 常駐の service の答えが page の読み戻しを待つ。値の誤りは宣言を読む段で断る。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess
      (run-layout tmp-path scope "worker:memory.min=1G,memory.swap.max=0 services:memory.min=24G,memory.swap.max=0 clis:memory.max=8G"))
  (assert (= done.returncode 0) done.stderr)
  (val written (dfor layer #("worker" "services") layer (.strip (.read-text (/ scope layer "memory.swap.max")))))
  (assert (= written {"worker" "0" "services" "0"}) written)
  (assert (not (.exists (/ scope "clis" "memory.swap.max"))) "swap の上限を宣言しない層には書かない")
  (<- bad Path (fake-container (/ tmp-path "bad")))
  (<- refused subprocess.CompletedProcess (run-layout (/ tmp-path "bad") bad "worker:memory.swap.max=none"))
  (assert (= refused.returncode 2) refused)
  (assert (in "memory.swap.max の値 'none'" refused.stderr) refused.stderr))


(deftest test-a-bad-cpu-max-is-refused-before-any-layer-is-made [#^ Path tmp-path]
  ;; 失敗ケース: kernel の書式の空白を宣言に書けず(層の区切り)、周期の範囲(1 ms〜1 秒)の外や 1 ms 未満の quota を、層を作った後の書きで
  ;; 初めて知ると、起動が層を半端に作ったまま止まる。宣言を読む段で全部を名指して断る。
  (<- scope Path (fake-container tmp-path))
  (val bad #("600000" "600000/" "/100000" "600000/100/1" "999/100000" "600000/999" "600000/1000001" "6x/100000" "060000/100000"))
  (<- done subprocess.CompletedProcess
      (run-layout tmp-path scope (.join " " (lfor #(i value) (enumerate bad) (.format "l{}:cpu.max={}" i value)))))
  (assert (= done.returncode 2) done)
  (for [value bad]
    (assert (in (.format "cpu.max の値 '{}'" value) done.stderr) #(value done.stderr)))
  (assert (= (sorted (gfor p (.iterdir scope) p.name)) ["cgroup.procs" "cgroup.subtree_control"]) "層を作らない"))


(deftest test-a-bad-layout-is-refused-with-every-problem-and-touches-nothing [#^ Path tmp-path]
  ;; 失敗ケース: 宣言の誤りを 1 つずつ直して起こし直すと、その度に Pod が起動で止まる。全部の誤りを 1 度に名指し、層は作らない。
  (<- scope Path (fake-container tmp-path))
  (<- done subprocess.CompletedProcess (run-layout tmp-path scope "Worker:memory.min=2G jobs:cpu.burst=1 x:memory.max=lots"))
  (assert (= done.returncode 2) done)
  (for [part #("'Worker'" "cpu.burst" "'lots'")]
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
    ;; 0.13 秒で満ち、最初の拍より先に限る子が止まる(10-24 11:39 JST の本番の node-h の worker)。
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
