;; 会話の CLI の層(clis)の CPU の PSI を、task の層の PSI と同じ道で名乗る(worker/core/load_rules の頭の註)。上に載る系の会話の置き先が、
;; 層に優先度の譲り(cpu.idle)を掛けた機体の CPU が常駐の job で埋まった時に、その機体へ CLI を起こす手番を置かないための材料。
;;   (1) worker の測りは層の cpu.pressure の some の avg10 を cli-cpu-some-avg10 にし、heartbeat の measured の欄 cliCpuSomeAvg10 で名乗る。
;;       層の file を読めない間はその欄だけを外す(機体の測りは名乗り続ける)
;;   (2) coordinator は欄を Worker の記録に写して GET /state に出し、名簿の読み(runner-facts-of-view)が RunnerFact.cli-cpu-some-avg10 にする。
;;       欄の無い担い手は None(0 に倒さない)。負の値は本文の形の誤り
;;   (3) 層の file の置き場は、層の宣言に clis が在り層の親の dir が在る機体だけ <層の親>/clis/cpu.pressure(無い機体は読まない)
;;   (4) task の置き先は読まない — 同じ機体の task の空きは欄の有無で変わらない
;; 失敗ケース = 機体の PSI や task の層の PSI を CLI の層の値として名乗る・読めない間に前の値を名乗る・欄の無い担い手を 0 と読む。
(require doeff-hy.macros [deff deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import pytest)
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster._worker.core.load_rules [LoadPaths LoadReading CpuTicks load-paths-under machine-load-of])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_cluster._worker.entry.main [cli-layer-cpu-pressure])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat state-view])
(import doeff_cluster._coordinator.core.measured_room [measured-room])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import doeff_cluster._shared.protocol.detached [runner-facts-of-view])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val STAT-A (+ "cpu  100 0 50 800 50 0 0 0 990 0\n"
               "cpu0 50 0 25 400 25 0 0 0 495 0\ncpu1 50 0 25 400 25 0 0 0 495 0\nintr 1 2 3\nctxt 5\nbtime 1700000000\n"))
(val STAT-B (+ "cpu  400 0 150 1300 150 0 0 0 990 0\n"
               "cpu0 200 0 75 650 75 0 0 0 495 0\ncpu1 200 0 75 650 75 0 0 0 495 0\nintr 1 2 3\nctxt 9\nbtime 1700000000\n"))
(val MEMINFO "MemTotal:        8388608 kB\nMemFree:          524288 kB\nMemAvailable:    2097663 kB\nBuffers:           1024 kB\n")
;; 機体・task の層・会話の CLI の層の cpu.pressure を違う値にする(どの file の値を名乗ったかを見分ける)。
(val CPU-PSI "some avg10=12.50 avg60=3.00 avg300=1.00 total=123456\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
(val MEMORY-PSI "some avg10=1.25 avg60=0.50 avg300=0.10 total=9876\nfull avg10=0.75 avg60=0.20 avg300=0.05 total=4321\n")
(val TASK-PSI "some avg10=3.50 avg60=2.00 avg300=1.00 total=555\nfull avg10=1.00 avg60=0.50 avg300=0.20 total=111\n")
(val CLI-PSI "some avg10=64.25 avg60=50.00 avg300=40.00 total=777\nfull avg10=30.00 avg60=20.00 avg300=10.00 total=222\n")


(deftest test-the-cli-layer-pressure-is-named-apart-from-the-machine-and-the-task-layer
  (<- c LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI None 1000 :task-cpu-pressure TASK-PSI :cli-cpu-pressure CLI-PSI))
  (assert (= #(c.load.cpu-some-avg10 c.load.task-cpu-some-avg10 c.load.cli-cpu-some-avg10) #(12.5 3.5 64.25)) c.load)
  ;; 層の file を渡さない worker は None で名乗る。形の違う中身は訳つきの ValueError。
  (<- plain LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI None 1000 :task-cpu-pressure TASK-PSI))
  (assert (is plain.load.cli-cpu-some-avg10 None) plain.load)
  (with [e (pytest.raises ValueError)]
    (! (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI None 1000 :cli-cpu-pressure "full avg10=1.00\n")))
  (assert (= (str e.value) "会話の CLI の層の cpu.pressure に some の avg10 が無いか数でない") e.value)
  ;; load-paths-under は層の file を LoadPaths にそのまま運ぶ(既定は読まない)。
  (<- under LoadPaths (load-paths-under "/proc" "/sys/fs/cgroup/jobs/cpu.pressure" :cli-cpu-pressure "/sys/fs/cgroup/clis/cpu.pressure"))
  (assert (= under.cli-cpu-pressure "/sys/fs/cgroup/clis/cpu.pressure") under)
  (<- bare LoadPaths (load-paths-under "/proc" None))
  (assert (is bare.cli-cpu-pressure None) bare))


(deftest test-the-cli-layer-file-is-read-only-where-the-layout-declares-the-layer
  (<- declared (| str None) (cli-layer-cpu-pressure {"DOEFF_WORKER_CGROUP_DIR" "/sys/fs/cgroup/worker"
                                                     "WORKER_CGROUP_LAYOUT" "services:memory.min=24G jobs:memory.max=10G clis:memory.max=8G"}))
  (assert (= declared "/sys/fs/cgroup/worker/clis/cpu.pressure") declared)
  ;; 層の宣言に clis が無い機体・層の親の dir を知らない機体は読まない。
  (<- no-layer (| str None) (cli-layer-cpu-pressure {"DOEFF_WORKER_CGROUP_DIR" "/sys/fs/cgroup/worker" "WORKER_CGROUP_LAYOUT" "jobs:memory.max=10G"}))
  (<- no-dir (| str None) (cli-layer-cpu-pressure {"WORKER_CGROUP_LAYOUT" "clis:memory.max=8G"}))
  (<- nothing (| str None) (cli-layer-cpu-pressure {}))
  (assert (= #(no-layer no-dir nothing) #(None None None))))


(deftest test-the-heartbeat-names-the-cli-layer-pressure-and-drops-it-while-unreadable [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; cannot be a defk: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val root (/ tmp-path "proc"))
  (.mkdir (/ root "pressure") :parents True)
  (.write-text (/ root "stat") STAT-A)
  (.write-text (/ root "meminfo") MEMINFO)
  (.write-text (/ root "pressure" "cpu") CPU-PSI)
  (.write-text (/ root "pressure" "memory") MEMORY-PSI)
  (.write-text (/ root "jobs.cpu.pressure") TASK-PSI)
  (.write-text (/ root "clis.cpu.pressure") CLI-PSI)
  (val paths (LoadPaths :stat (str (/ root "stat")) :meminfo (str (/ root "meminfo"))
                        :cpu-pressure (str (/ root "pressure" "cpu")) :memory-pressure (str (/ root "pressure" "memory"))
                        :task-cpu-pressure (str (/ root "jobs.cpu.pressure")) :cli-cpu-pressure (str (/ root "clis.cpu.pressure"))))
  (val link (LinkRig "http://coord" "node-a" #() 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.load-paths paths)
  (val measured (fn [] (.get (get sent -1) "measured")))
  (.poll link)
  (assert (= #((get (measured) "cliCpuSomeAvg10") (get (measured) "taskCpuSomeAvg10") (get (measured) "cpuSomeAvg10")) #(64.25 3.5 12.5))
          (measured))
  ;; 層の file を読めない間は、その欄だけを名乗らない(前の値を今の値として名乗らない・機体の測りは名乗り続ける)。
  (.unlink (/ root "clis.cpu.pressure"))
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((in "cliCpuSomeAvg10" (measured)) (get (measured) "taskCpuSomeAvg10")) #(False 3.5)) (measured)))


(deftest test-the-coordinator-carries-the-cli-layer-pressure-to-the-roster-without-placing-on-it
  (<- load LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI (CpuTicks :busy 150 :total 1000 :cpus 2) 1700000000000
                                        :cli-cpu-pressure CLI-PSI))
  (<- spelled dict (measured-json load.load))
  (val with-layer {"name" "node-h" "provides" ["host-node-h"]})
  (val without-layer {"name" "node-z" "provides" ["host-node-z"]})
  (<- bare-load LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI (CpuTicks :busy 150 :total 1000 :cpus 2) 1700000000000))
  (<- bare dict (measured-json bare-load.load))
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of (| with-layer {"measured" spelled}))) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of (| without-layer {"measured" bare}))) 10)))
  (assert (= (. (.row both.workers "node-h") measured cli-cpu-some-avg10) 64.25))
  ;; GET /state の worker の行に出て、名簿の読みが RunnerFact の欄にする。欄の無い担い手は None(0 に倒さない)。
  (val workers (get (! (state-view-json (! (state-view both 20 T)))) "workers"))
  (assert (= (get workers "node-h" "measured" "cliCpuSomeAvg10") 64.25) (get workers "node-h"))
  (assert (not-in "cliCpuSomeAvg10" (get workers "node-z" "measured")) (get workers "node-z"))
  (val facts (runner-facts-of-view workers))
  (assert (= (dfor f facts f.name f.cli-cpu-some-avg10) {"node-h" 64.25 "node-z" None}) facts)
  ;; task の置き先は読まない: 同じ測りの task の空きは欄の有無で変わらない。
  (<- room-with int (measured-room (. (.row both.workers "node-h") measured) 0 0 0))
  (<- room-without int (measured-room (. (.row both.workers "node-z") measured) 0 0 0))
  (assert (= room-with room-without) #(room-with room-without))
  ;; 負の値は本文の形の誤り(400)。
  (with [(pytest.raises ValueError)]
    (! (heartbeat-of (| with-layer {"measured" (| spelled {"cliCpuSomeAvg10" -1.0})})))))
