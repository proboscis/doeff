;; worker の heartbeat に機体の測った空き(CPU の使い・memory の空き・PSI)を載せ、coordinator の GET /state で読めるようにする。
;; 置き先を固定の枠でなく機体の今の空きで決める下ごしらえ — この段では数を運ぶだけで、置き方の判断は変えない。
;;   (1) /proc/stat・/proc/meminfo・/proc/pressure/{cpu,memory} の中身を数にし、CPU の使いは前の読みとの間の刻みの増えの比で数える
;;       (初めての読み・刻みの戻った読みは None — 0 や前の値で埋めない)。形の違う中身は訳つきの ValueError
;;   (2) coordinator への口は 5 秒ごとに file を読み直して heartbeat の measured に名乗り、読めない間は名乗りを外す(古い値を名乗らない)。
;;       測らない worker は measured を名乗らない
;;   (3) coordinator は heartbeat の measured を Worker の記録に写し(保存しない)、GET /state の worker の行に出す。Worker の資源の行には
;;       入れない(入れると版が拍ごとに進む)。形の誤りは 400
;;   (4) /proc の無い機体(macOS)は OS の口(問い ReadHostCounters)で CPU の刻みと memory を読み、同じ式で使いを数えて名乗る(PSI は
;;       None)。coordinator はその測りから、測りを名乗らない worker と違って task の空きを数える(以前の macOS の worker は測りを名乗れず、
;;       空きがいつも 0 で、置いた task が走らなかった)
;; 失敗ケース = 累積の刻みをそのまま使いと読む・初めての読みで使い 0 を名乗る・読めなくなった後も前の値を名乗り続ける・/state に出ない・
;; 資源の行の版が heartbeat ごとに進む。
(require doeff-hy.macros [deff defk defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import ctypes)
(import json)
(import sys)
(import httpx)
(import pytest)
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster._worker.core.load_rules [LoadPaths HostLoad LoadReading MachineLoad CpuTicks Pressure LOAD-READ-EVERY-MS load-read-due
                                              cpu-ticks-of pressure-of cpu-busy-between cpu-nice-between machine-load-of host-load-of])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.intent.worker_model [HostCounters ReadHostCounters])
(import doeff_cluster._coordinator.core.measured_room [measured-room])
(import doeff_cluster._foundation.host_counters [VmStatistics64 read-host-counters])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat state-view])
(import doeff_cluster._coordinator.core.resource_policy [worker-row])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
;; CPU 2 個の機体の 2 回の読み: 和 1000 → 2000・使い(和 − idle − iowait)150 → 550 で、間の使いは 400 / 1000 = 0.4。guest と guest_nice
;; (後ろの 2 欄)は user と nice に含まれるので和に足さない — 足すと和が 1990 → 2990 になり 0.4 にならない。
(val STAT-A (+ "cpu  100 0 50 800 50 0 0 0 990 0\n"
               "cpu0 50 0 25 400 25 0 0 0 495 0\ncpu1 50 0 25 400 25 0 0 0 495 0\nintr 1 2 3\nctxt 5\nbtime 1700000000\n"))
(val STAT-B (+ "cpu  400 0 150 1300 150 0 0 0 990 0\n"
               "cpu0 200 0 75 650 75 0 0 0 495 0\ncpu1 200 0 75 650 75 0 0 0 495 0\nintr 1 2 3\nctxt 9\nbtime 1700000000\n"))
;; 8 GiB の機体に空き 2 GiB(MiB に切り捨て — 端数の kB は落ちる)。
(val MEMINFO "MemTotal:        8388608 kB\nMemFree:          524288 kB\nMemAvailable:    2097663 kB\nBuffers:           1024 kB\n")
(val CPU-PSI "some avg10=12.50 avg60=3.00 avg300=1.00 total=123456\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
(val MEMORY-PSI "some avg10=1.25 avg60=0.50 avg300=0.10 total=9876\nfull avg10=0.75 avg60=0.20 avg300=0.05 total=4321\n")
;; task の層の cgroup の cpu.pressure(cgroup の file も some と full の行を持つ)。
(val TASK-PSI "some avg10=3.50 avg60=2.00 avg300=1.00 total=555\nfull avg10=1.00 avg60=0.50 avg300=0.20 total=111\n")
;; STAT-B の nice の欄に刻みを足した読み(和 2000 → 3000 のうち nice の増え 300・使いの増え 600)。
(val STAT-NICE (+ "cpu  600 300 250 1600 250 0 0 0 990 0\n"
                  "cpu0 300 150 125 800 125 0 0 0 495 0\ncpu1 300 150 125 800 125 0 0 0 495 0\nintr 1 2 3\nctxt 9\nbtime 1700000000\n"))


;; /proc の無い機体(macOS)の OS の口の 2 回の読み: CPU 8 個・刻みの和 10000 → 18000・idle でない刻み 2000 → 6000 で、間の使いは
;; 4000 / 8000 = 0.5。nice の刻み 500 → 1300 で、nice の分は 800 / 8000 = 0.1。16 GiB の機体に追い出さずに使える量 6 GiB。
(val HOST-A (HostCounters :cpus 8 :busy-ticks 2000 :total-ticks 10000 :nice-ticks 500
                          :memory-total-bytes (* 16384 MIB) :memory-available-bytes (* 6144 MIB)))
(val HOST-B (HostCounters :cpus 8 :busy-ticks 6000 :total-ticks 18000 :nice-ticks 1300
                          :memory-total-bytes (* 16384 MIB) :memory-available-bytes (* 6144 MIB)))
;; 刻みの数え手(32 bit)が一巡りして小さい値に戻った読み。
(val HOST-WRAPPED (HostCounters :cpus 8 :busy-ticks 100 :total-ticks 400 :nice-ticks 0
                                :memory-total-bytes (* 16384 MIB) :memory-available-bytes (* 6144 MIB)))


(defhandler host-counters-from [#^ dict box]
  {:needs #{} :tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 検の機体の OS の口の代わり: 問い ReadHostCounters に箱の今の答え(HostCounters か読めない訳の str)で答える。
  (ReadHostCounters []
    (resume (get box "answer"))))


(deftest test-the-proc-files-become-the-measured-load
  ;; 初めての読みは CPU の使いを数えられない(None)。2 回目は前の読みとの間の増えの比。memory は MiB・PSI は avg10。
  (<- first LoadReading (machine-load-of STAT-A MEMINFO CPU-PSI MEMORY-PSI None 1000))
  (assert (= first.load (MachineLoad :at-ms 1000 :cpus 2 :cpu-busy None :memory-total-mib 8192 :memory-available-mib 2048
                                     :cpu-some-avg10 12.5 :cpu-full-avg10 0.0 :memory-some-avg10 1.25 :memory-full-avg10 0.75))
          first)
  (assert (= first.ticks (CpuTicks :busy 150 :total 1000 :cpus 2)) first)
  (<- second LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI first.ticks 6000))
  (assert (= #(second.load.at-ms second.load.cpu-busy) #(6000 0.4)) second)
  ;; 失敗ケース: 累積の刻みをそのまま使いと読む(150 / 1000 = 0.15・550 / 2000 = 0.275)と 0.4 にならない。
  (assert (not-in second.load.cpu-busy #(0.15 0.275)) second))


(deftest test-the-cpu-use-is-not-counted-without-a-moving-baseline
  ;; 前の読みが無い・和が進まない・刻みが戻った(機体が起き直した)間は None(0 や 1 で埋めない)。
  (val before (CpuTicks :busy 550 :total 2000 :cpus 2))
  (<- none-before (| float None) (cpu-busy-between None before))
  (<- same (| float None) (cpu-busy-between before before))
  (<- rebooted (| float None) (cpu-busy-between before (CpuTicks :busy 10 :total 100 :cpus 2)))
  (<- idle (| float None) (cpu-busy-between before (CpuTicks :busy 550 :total 3000 :cpus 2)))
  (<- full (| float None) (cpu-busy-between before (CpuTicks :busy 1550 :total 3000 :cpus 2)))
  (assert (= #(none-before same rebooted idle full) #(None None None 0.0 1.0))))


(deftest test-the-nice-share-and-the-task-layer-pressure-are-named-apart-from-the-machine-values
  ;; nice の分 = 和の増えに対する nice の増え(使いの内)。使いを数えられない読みと、nice の刻みが戻った読みは None。
  (<- b LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI None 1000))
  (assert (= b.ticks (CpuTicks :busy 550 :total 2000 :cpus 2 :nice 0)) b.ticks)
  (<- c LoadReading (machine-load-of STAT-NICE MEMINFO CPU-PSI MEMORY-PSI b.ticks 6000 :task-cpu-pressure TASK-PSI))
  (assert (= #(c.load.cpu-busy c.load.cpu-nice c.load.cpu-some-avg10 c.load.task-cpu-some-avg10) #(0.6 0.3 12.5 3.5)) c.load)
  (<- first-reading (| float None) (cpu-nice-between None c.ticks))
  (<- nice-back (| float None) (cpu-nice-between c.ticks (CpuTicks :busy 1500 :total 4000 :cpus 2 :nice 100)))
  (assert (= #(b.load.cpu-nice first-reading nice-back) #(None None None)))
  ;; task の層の file を渡さない worker は task の層の PSI を None で名乗る。形の違う中身は訳つきの ValueError。
  (<- plain LoadReading (machine-load-of STAT-NICE MEMINFO CPU-PSI MEMORY-PSI b.ticks 6000))
  (assert (is plain.load.task-cpu-some-avg10 None) plain.load)
  (with [e (pytest.raises ValueError)]
    (! (machine-load-of STAT-NICE MEMINFO CPU-PSI MEMORY-PSI b.ticks 6000 :task-cpu-pressure "full avg10=1.00\n")))
  (assert (= (str e.value) "task の層の cpu.pressure に some の avg10 が無いか数でない") e.value))


(deftest test-malformed-proc-text-is-refused-with-the-reason
  ;; 形の違う中身は訳つきの ValueError(黙って 0 にしない)。
  (val refused [[(fn [] (cpu-ticks-of "intr 1 2\n")) "/proc/stat の頭の行 cpu が無いか、刻みの欄が足りないか数でない"]
                [(fn [] (cpu-ticks-of "cpu  1 2 3\ncpu0 1 2 3\n")) "/proc/stat の頭の行 cpu が無いか、刻みの欄が足りないか数でない"]
                [(fn [] (cpu-ticks-of "cpu  1 2 3 x 5 6 7 8\ncpu0 1 2 3 4 5 6 7 8\n")) "/proc/stat の頭の行 cpu が無いか、刻みの欄が足りないか数でない"]
                [(fn [] (cpu-ticks-of "cpu  1 2 3 4 5 6 7 8\n")) "/proc/stat に行 cpuN が無い"]
                [(fn [] (pressure-of "full avg10=1.00\n" "/proc/pressure/cpu")) "/proc/pressure/cpu に some の avg10 が無いか数でない"]
                [(fn [] (pressure-of "some avg10=x avg60=0\n" "/proc/pressure/cpu")) "/proc/pressure/cpu に some の avg10 が無いか数でない"]
                [(fn [] (machine-load-of STAT-A "MemTotal: 8 kB\n" None None None 0)) "/proc/meminfo に MemAvailable の行が無いか数でない"]])
  (for [#(program reason) refused]
    (with [e (pytest.raises ValueError)]
      (! (program)))
    (assert (= (str e.value) reason) #(reason e.value))))


(deftest test-the-load-without-psi-names-the-rest
  ;; PSI の file の無い機体は PSI の欄を None で名乗り、full の行の無い kernel の cpu は full だけ None。
  (<- bare LoadReading (machine-load-of STAT-A MEMINFO None None None 1))
  (assert (= #(bare.load.cpu-some-avg10 bare.load.cpu-full-avg10 bare.load.memory-some-avg10 bare.load.memory-full-avg10 bare.load.cpus)
             #(None None None None 2))
          bare)
  (<- old-kernel Pressure (pressure-of "some avg10=3.10 avg60=1.00 avg300=0.50 total=10\n" "/proc/pressure/cpu"))
  (assert (= old-kernel (Pressure :some-avg10 3.1 :full-avg10 None)) old-kernel))


(deftest test-the-load-is-read-again-every-five-seconds
  (<- first bool (load-read-due None 0))
  (<- soon bool (load-read-due 0 (- LOAD-READ-EVERY-MS 1)))
  (<- later bool (load-read-due 0 LOAD-READ-EVERY-MS))
  (<- back bool (load-read-due LOAD-READ-EVERY-MS 0))
  (assert (= #(LOAD-READ-EVERY-MS first soon later back) #(5000 True False True True))))


(deftest test-the-heartbeat-names-the-measured-load-and-drops-it-while-unreadable [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  ;; 検の機体の /proc の代わりの file。
  (val root (/ tmp-path "proc"))
  (.mkdir (/ root "pressure") :parents True)
  (.write-text (/ root "stat") STAT-A)
  (.write-text (/ root "meminfo") MEMINFO)
  (.write-text (/ root "pressure" "cpu") CPU-PSI)
  (.write-text (/ root "pressure" "memory") MEMORY-PSI)
  (.write-text (/ root "jobs.cpu.pressure") TASK-PSI)
  (val paths (LoadPaths :stat (str (/ root "stat")) :meminfo (str (/ root "meminfo"))
                        :cpu-pressure (str (/ root "pressure" "cpu")) :memory-pressure (str (/ root "pressure" "memory"))
                        :task-cpu-pressure (str (/ root "jobs.cpu.pressure"))))
  (val link (LinkRig "http://coord" "atlas" #() 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.load-paths paths)
  (val measured (fn [] (.get (get sent -1) "measured")))
  ;; 初めての拍: 使いは数えられず null・残りは読んだ値。読んだ刻は worker の壁の時計。
  (.poll link)
  (val first (measured))
  (assert (= (dfor #(k v) (.items first) :if (!= k "atMs") k v)
             {"cpus" 2 "cpuBusy" None "memoryTotalMiB" 8192 "memoryAvailableMiB" 2048
              "cpuSomeAvg10" 12.5 "cpuFullAvg10" 0.0 "memorySomeAvg10" 1.25 "memoryFullAvg10" 0.75 "taskCpuSomeAvg10" 3.5})
          first)
  (assert (= (get first "atMs") link.state.load-read-ms) first)
  ;; 読み直しの後の拍は前の読みとの間の使いを名乗る。
  (.write-text (/ root "stat") STAT-B)
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((get (measured) "cpuBusy") (get (measured) "cpuNice")) #(0.4 0.0)) (measured))
  ;; task の層の file を読めない間は、その欄だけを名乗らない(機体の測りは名乗り続ける)。
  (.unlink (/ root "jobs.cpu.pressure"))
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((in "taskCpuSomeAvg10" (measured)) (get (measured) "cpuSomeAvg10")) #(False 12.5)) (measured))
  ;; /proc/stat を読めない間は名乗りを外す(前の値を今の値として名乗らない)。
  (.unlink (/ root "stat"))
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (is (measured) None) (get sent -1))
  (assert (.startswith link.state.load-reason (+ (str (/ root "stat")) " を読めない")) link.state.load-reason)
  ;; 戻った最初の読みは使いの起点を持たない(読めなかった間の前の起点で数えない)。
  (.write-text (/ root "stat") STAT-B)
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((get (measured) "cpuBusy") (get (measured) "cpus") link.state.load-reason) #(None 2 "")) (measured))
  ;; 測らない worker(load-paths を渡さず、名乗る値も置かない口)は measured を名乗らない。
  (val plain (LinkRig "http://coord" "zeus" #() 60000 :task-dir (str (/ tmp-path "plain")) :transport (httpx.MockTransport handle)
                     :measured None))
  (.poll plain)
  (assert (not-in "measured" (get sent -1)) (get sent -1)))


(deftest test-the-coordinator-shows-the-measured-load-in-state-but-not-in-the-worker-resource
  (<- load LoadReading (machine-load-of STAT-B MEMINFO CPU-PSI MEMORY-PSI (CpuTicks :busy 150 :total 1000 :cpus 2) 1700000000000))
  (<- spelled dict (measured-json load.load))
  (val body {"name" "helios" "provides" ["host-helios"]})
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of (| body {"measured" spelled}))) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "eos" "provides" []})) 10)))
  (val helios (.row both.workers "helios"))
  (assert (= #(helios.measured.cpus helios.measured.cpu-busy helios.measured.memory-available-MiB) #(2 0.4 2048)) helios.measured)
  (assert (is (. (.row both.workers "eos") measured) None))
  ;; GET /state の worker の行: 送った measured と同じ形と値。測れない worker の行には欄が無い。
  (val workers (get (! (state-view-json (! (state-view both 20 T)))) "workers"))
  (assert (= (get workers "helios" "measured") spelled) (get workers "helios"))
  (assert (not-in "measured" (get workers "eos")) (get workers "eos"))
  ;; Worker の資源の行には入れない(heartbeat ごとに変わる観測 — 入れると版と出来事が拍ごとに進む)。
  (val row (worker-row both helios 20 T))
  (assert (not-in "measured" (| (get row "spec") (get row "status"))) row)
  ;; 次の heartbeat の値で書き直す(前の値を持ち続けない)。欄の無い heartbeat は測りを外す。
  (val again (! (register-heartbeat both (! (heartbeat-of (| body {"measured" (| spelled {"cpuBusy" 0.9 "memoryAvailableMiB" 512})}))) 30)))
  (assert (= #((. (.row again.workers "helios") measured cpu-busy) (. (.row again.workers "helios") measured memory-available-MiB)) #(0.9 512)))
  (val dropped (! (register-heartbeat again (! (heartbeat-of body)) 40)))
  (assert (is (. (.row dropped.workers "helios") measured) None))
  ;; 形の誤りは本文の形の誤り(400)。
  (for [wrong [{"cpus" 0} {"cpuBusy" 1.5} {"memoryAvailableMiB" 9000} {"cpuSomeAvg10" -1.0} {"cpus" "2"}]]
    (with [(pytest.raises ValueError)]
      (! (heartbeat-of (| body {"measured" (| spelled wrong)}))))))


(deftest test-the-host-counters-become-the-measured-load
  ;; /proc の無い機体の OS の口の読みも、初めての読みは使いを数えられず(None)、2 回目は前の読みとの間の増えの比。PSI は None。
  (<- first LoadReading (host-load-of HOST-A None 1000))
  (assert (= first.load (MachineLoad :at-ms 1000 :cpus 8 :cpu-busy None :memory-total-mib 16384 :memory-available-mib 6144
                                     :cpu-some-avg10 None :cpu-full-avg10 None :memory-some-avg10 None :memory-full-avg10 None))
          first)
  (<- second LoadReading (host-load-of HOST-B first.ticks 6000))
  (assert (= #(second.load.cpu-busy second.load.cpu-nice) #(0.5 0.1)) second)
  ;; 失敗ケース: 起動からの累積をそのまま使いと読む(6000 / 18000)と 0.5 にならない。
  (assert (!= second.load.cpu-busy (/ 6000 18000)) second)
  ;; 数え手が一巡りして戻った読みは使いを None(1 回だけ — 次の読みはこの読みを起点に数える)。
  (<- wrapped LoadReading (host-load-of HOST-WRAPPED second.ticks 11000))
  (assert (is wrapped.load.cpu-busy None) wrapped))


(deftest test-a-worker-without-proc-names-the-os-counters-and-gets-task-room [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  ;; 検の機体の OS の口の答え(拍ごとに差し替える)。
  (val #^ (get dict #(str (| HostCounters str))) box {"answer" HOST-A})
  (val link (LinkRig "http://coord" "mac-native" #() 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)
                     :answerers #((host-counters-from box))))
  (setv link.state.load-paths (HostLoad :os "macOS"))
  (val measured (fn [] (.get (get sent -1) "measured")))
  ;; 初めての拍: 使いは数えられず null・CPU の数と memory は OS の口の値・PSI は null。
  (.poll link)
  (assert (= (dfor #(k v) (.items (measured)) :if (!= k "atMs") k v)
             {"cpus" 8 "cpuBusy" None "memoryTotalMiB" 16384 "memoryAvailableMiB" 6144
              "cpuSomeAvg10" None "cpuFullAvg10" None "memorySomeAvg10" None "memoryFullAvg10" None})
          (measured))
  ;; 読み直しの後の拍は前の読みとの間の使いを名乗る。
  (setv (get box "answer") HOST-B)
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((get (measured) "cpuBusy") (get (measured) "cpuNice")) #(0.5 0.1)) (measured))
  ;; coordinator はこの測りから task の空きを数える: 普通の使い 0.4 → CPU の空き ⌊8 × 0.6 − 1⌋ = 3・memory の枠 ⌊(6144 − 1536) / 1024⌋ = 4。
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "mac-native" "provides" ["host-mac-native"]
                                                                    "measured" (measured)}))
                                    10)))
  (<- room int (measured-room (. (.row heard.workers "mac-native") measured) 0 0 0))
  (assert (= room 3) room)
  ;; 失敗ケース(直す前の macOS の worker): 測りを名乗らない worker の空きは 0 — 置いた task が走らない。
  (val bare (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "mac-native" "provides" ["host-mac-native"]})) 10)))
  (<- none-room int (measured-room (. (.row bare.workers "mac-native") measured) 0 0 0))
  (assert (= none-room 0) none-room)
  ;; OS の口を読めない間は名乗りを外し(前の値を今の値として名乗らない)、訳を残す。
  (setv (get box "answer") "host_statistics(HOST_CPU_LOAD_INFO) が 5 で断った")
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (is (measured) None) (get sent -1))
  (assert (= link.state.load-reason "macOS の CPU と memory を読めない: host_statistics(HOST_CPU_LOAD_INFO) が 5 で断った")
          link.state.load-reason)
  ;; 戻った最初の読みは使いの起点を持たない(読めなかった間の前の起点で数えない)。
  (setv (get box "answer") HOST-B)
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= #((get (measured) "cpuBusy") (get (measured) "cpus") link.state.load-reason) #(None 8 "")) (measured)))


(deftest test-the-os-reader-keeps-the-mach-struct-and-names-a-machine-without-mach
  ;; 生の読み(foundation/host_counters)の struct vm_statistics64 の写しは C の宣言と同じ 152 byte(欄の幅と順を誤ると頁の数が隣の欄に
  ;; ずれる)。mach の口の無い機体(この検の Linux)は例外でなく訳の str で答える(口が測りを名乗らない訳として log に出す)。
  (assert (= (ctypes.sizeof VmStatistics64) 152))
  (val read (read-host-counters))
  (if (= sys.platform "darwin")
      (assert (and (isinstance read dict) (> (get read "cpus") 0) (>= (get read "total_ticks") (get read "busy_ticks"))) read)
      (assert (= read (.format "この OS({})に mach の host_statistics が無い" sys.platform)) read)))
