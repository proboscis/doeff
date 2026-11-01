;; kubelet の追い出しの線と jobs の層の上限 — 模擬の検。
;;
;; 実例: atlas の worker の Pod が 10-30〜11-01 に 5 回、kubelet に追い出され、常駐の job 14 本が Pod の起き直しまで止まった。どれも
;; allocatable の線(Pod の全部の working set の和 > allocatable 19.79 GiB)で、機体の MemAvailable は 8〜11 GiB 残っていた。直前の 90 秒に
;; task が約 5 GiB 跳ね、worker の memory の線(jobs の層の静的な上限 10752M・見張りの MemAvailable / MemTotal の DANGER・heartbeat の
;; MemAvailable)はどれも allocatable の線から決まっていなかった。
;;   - 判断(worker/core/eviction_rules): kubelet と同じ式の 2 つの線(allocatable・機体)と、その近い方の追い出しまでの空き。E は手で書かず
;;     kubepods の memory.max − allocatable。kernel の memory の綴り・k8s の量・cgroup の層の宣言・kubepods の dir の読み。jobs の層の
;;     上限 = jobs の使い + 空き − 余裕(静的な上限と 0 の間)。
;;   - 模擬の機体(atlas の値): 本物の見張りの背景の task を仮想時計の下で 5 秒ごとに回し、Node の宣言は偽の API server、kubepods・root・
;;     jobs の層の cgroup の file は模擬の kernel が答える。模擬の kernel は jobs の層の使いを、見張りが書いた memory.max で止める(越える
;;     要求の task は jobs の層の中で OOM kill され、準備の分に戻る)。
;;       task が 90 秒で 5.5 GiB 跳ねる・ほかの Pod が 30 秒で 2 GiB 伸びる、の 2 つで、全部の Pod の working set の和は読みのどの刻も
;;       allocatable を越えず(kubelet は追い出さない)、止まるのは jobs の層の task だけ。掛け直しの無い worker(線の設定が無い — 前の
;;       形)では、同じ機体で和が allocatable を越える(検が意味を持つ事の対照)。
;;   - 見張りの DANGER と heartbeat の memory の空きは追い出しまでの空きで判じる: MemAvailable が 34% 残っていても空きが線の 5% なら DANGER、
;;     MemAvailable が 5% でも空きが線の 50% なら CALM。heartbeat は線の大きさと空きを名乗り、古い線は名乗らない。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect ReadText WriteText HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ProcRow])
(import doeff_core_effects [scripted-proc-table-handler])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ObserveJobRoots])
(import doeff_cluster._worker.core.memory_rules [JobRoot MemoryLevel MemoryLimits NodeMemory MIB memory-level])
(import doeff_cluster._worker.core.eviction_rules [EVICTION-MARGIN-BYTES EvictionLine KubeletMemory LineKind PodsMemory kernel-bytes-of
                                                  quantity-bytes pods-memory-of root-working-set-of eviction-line line-memory jobs-limit
                                                  layer-memory-max pods-dir-of])
(import doeff_cluster._worker.core.load_rules [LINE-FRESH-MS fresh-line machine-load-of])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings JOBS-SQUEEZE-LOG EVICTION-LINE-LOG EVICTION-UNREAD-LOG])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop MEMORY-LEVEL-LOG])

(val GIB (* 1024 MIB))
;; atlas の Node の宣言(11-01 の kubelet の configz と Node の status): capacity 29662296Ki・systemReserved 7Gi・evictionHard 1.5Gi・
;; allocatable 20749400Ki(= capacity − 7Gi − 1.5Gi)。kubepods の memory.max = capacity − 7Gi。
(val CAPACITY (* 29662296 1024))
(val ALLOCATABLE (* 20749400 1024))
(val PODS-MAX (- CAPACITY (* 7 GIB)))
(val ATLAS (KubeletMemory :capacity-bytes CAPACITY :allocatable-bytes ALLOCATABLE))
;; atlas の cgroup の層の宣言(nodes/atlas/cgroup.yaml)。
(val ATLAS-LAYOUT "worker:memory.min=256M,cpu.weight=1000 services:memory.min=7168M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100")
;; 模擬の機体の path(worker の container の cgroup は systemd の driver の形)。
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/atlas")
;; 模擬の kernel が答える cgroup の file。
(val KERNEL-PATHS #((+ PODS "/memory.max") (+ PODS "/memory.current") (+ PODS "/memory.stat") (+ HOST "/memory.stat")
                    (+ JOBS "/memory.current")))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-kernel-sizes-and-k8s-quantities-are-read-as-bytes
  (<- layered (| int None) (kernel-bytes-of "10752M"))
  (<- seven (| int None) (kernel-bytes-of "7G\n"))
  (<- plain (| int None) (kernel-bytes-of "22857547776"))
  (<- unlimited (| int None) (kernel-bytes-of "max\n"))
  (assert (= #(layered seven plain unlimited) #((* 10752 MIB) (* 7 GIB) 22857547776 None)))
  (with [(pytest.raises ValueError)] (<- (kernel-bytes-of "12x")))
  (<- ki int (quantity-bytes "20749400Ki"))
  (<- half int (quantity-bytes "1.5Gi"))
  (<- decimal int (quantity-bytes "64M"))
  (<- exponent int (quantity-bytes "1e3"))
  (assert (= #(ki half decimal exponent) #(ALLOCATABLE (* 1536 MIB) 64000000 1000)))
  (with [(pytest.raises ValueError)] (<- (quantity-bytes "1.5 GiB"))))


(deftest test-the-static-jobs-cap-and-the-pods-cgroup-are-read-from-the-declarations
  (<- cap (| int None) (layer-memory-max ATLAS-LAYOUT "jobs"))
  (<- services (| int None) (layer-memory-max ATLAS-LAYOUT "services"))
  (<- missing (| int None) (layer-memory-max ATLAS-LAYOUT "clis"))
  (assert (= #(cap services missing) #((* 10752 MIB) None None)))
  (<- systemd (| str None) (pods-dir-of OWN HOST))
  (<- cgroupfs (| str None) (pods-dir-of "/host/cgroup/kubepods/burstable/pod1/abc" "/host/cgroup/"))
  (<- outside (| str None) (pods-dir-of "/sys/fs/cgroup/system.slice/doeff-worker.service" HOST))
  (<- unmanaged (| str None) (pods-dir-of "/host/cgroup/system.slice/doeff-worker.service" HOST))
  (assert (= #(systemd cgroupfs outside unmanaged) #(PODS "/host/cgroup/kubepods" None None))))


(deftest test-the-allocatable-line-is-the-pods-working-set-against-the-allocatable
  ;; 11-01 18:27 UTC の atlas: Pod の working set 13.74 GiB → 追い出しまでの空き 19.79 − 13.74 = 6.05 GiB(stats/summary の pods の
  ;; available 7.55 GiB − hard 1.5 GiB)。機体の線は root の working set 12 GiB で空き 28.29 − 1.5 − 12 = 14.79 GiB — 近いのは allocatable。
  (<- pods PodsMemory (pods-memory-of (str PODS-MAX) (str (+ (int (* 13.74 GIB)) (* 2 GIB))) (.format "anon 1\ninactive_file {}\n" (* 2 GIB))))
  (<- root int (root-working-set-of (.format "anon {}\nfile {}\ninactive_file {}\n" (* 11 GIB) (* 3 GIB) (* 2 GIB))))
  (<- line (| EvictionLine None) (eviction-line ATLAS pods root))
  (assert (= line.kind LineKind.ALLOCATABLE) line)
  (assert (= line.size-bytes ALLOCATABLE) line)
  (assert (= line.room-bytes (- ALLOCATABLE (int (* 13.74 GIB)))) line)
  ;; E は手で書かず導く: 機体の線の大きさ = capacity − (kubepods の memory.max − allocatable) = capacity − 1.5 GiB。
  (<- crowded int (root-working-set-of (.format "anon {}\nfile 0\ninactive_file 0\n" (* 26 GIB))))
  (<- node (| EvictionLine None) (eviction-line ATLAS pods crowded))
  (assert (= #(node.kind node.size-bytes node.room-bytes) #(LineKind.NODE (- CAPACITY (* 1536 MIB)) (- CAPACITY (* 1536 MIB) (* 26 GIB))))
          node)
  ;; root の memory.stat の無い kernel は allocatable の線だけ。
  (<- only (| EvictionLine None) (eviction-line ATLAS pods None))
  (assert (= only line) only))


(deftest test-a-node-without-the-pods-allocatable-has-no-line-and-a-contradiction-is-refused
  (<- unlimited PodsMemory (pods-memory-of "max\n" "1000" "inactive_file 0\n"))
  (<- none (| EvictionLine None) (eviction-line ATLAS unlimited None))
  (assert (is none None))
  (<- small PodsMemory (pods-memory-of (str (- ALLOCATABLE 1)) "1000" "inactive_file 0\n"))
  (with [(pytest.raises ValueError)] (<- (eviction-line ATLAS small None)))
  ;; 使いより inactive_file が大きい読み(cAdvisor と同じく working set 0)。
  (<- cached PodsMemory (pods-memory-of (str PODS-MAX) "1000" "inactive_file 4096\n"))
  (<- line (| EvictionLine None) (eviction-line ATLAS cached None))
  (assert (= #(line.used-bytes line.room-bytes) #(0 ALLOCATABLE)) line))


(deftest test-the-jobs-limit-follows-the-room-between-zero-and-the-static-cap
  (val line (fn [room] (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes ALLOCATABLE :used-bytes (- ALLOCATABLE room) :room-bytes room)))
  (val cap (* 10752 MIB))
  ;; 空きが広い時は静的な上限。
  (<- wide int (jobs-limit (* 2 GIB) (line (* 12 GIB)) EVICTION-MARGIN-BYTES cap))
  ;; 空きが余裕より広い時は jobs の使い + 空き − 余裕(使いより上 — 跳ねられる分は空き − 余裕)。
  (<- room int (jobs-limit (* 5 GIB) (line (* 3 GIB)) EVICTION-MARGIN-BYTES cap))
  ;; 空きが余裕を割った時は使いより下(kernel が jobs の中で回収し、足りなければ止める)。
  (<- squeeze int (jobs-limit (* 5 GIB) (line (* 512 MIB)) EVICTION-MARGIN-BYTES cap))
  ;; 線を越えた読み(空きが負)でも 0 より下には書かない。
  (<- floor int (jobs-limit (* 1 GIB) (line (- (* 3 GIB))) EVICTION-MARGIN-BYTES cap))
  (<- uncapped int (jobs-limit (* 2 GIB) (line (* 12 GIB)) EVICTION-MARGIN-BYTES None))
  (assert (= #(wide room squeeze floor uncapped) #(cap (* 7 GIB) (- (* 5 GIB) (* 512 MIB)) 0 (* 13 GIB)))))


(deftest test-the-watch-judges-the-danger-by-the-room-before-eviction-not-by-memavailable
  (val limits (MemoryLimits))
  (val line (fn [room] (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes ALLOCATABLE :used-bytes (- ALLOCATABLE room) :room-bytes room)))
  ;; 機体の MemAvailable は 9.68 GiB / 28.29 GiB(34% — 前の判じ方では CALM)。追い出しまでの空き 1 GiB は線の 5% — DANGER。
  (val roomy (NodeMemory :total-bytes CAPACITY :available-bytes (int (* 9.68 GIB)) :some-avg10 0.0 :full-avg10 0.0))
  (<- before MemoryLevel (memory-level roomy limits))
  (<- near NodeMemory (line-memory roomy (line (* 1 GIB))))
  (<- danger MemoryLevel (memory-level near limits))
  (assert (= #(before danger) #(MemoryLevel.CALM MemoryLevel.DANGER)))
  (assert (= #(near.total-bytes near.available-bytes) #(ALLOCATABLE (* 1 GIB))) near)
  ;; 逆: MemAvailable が 5%(前の判じ方では DANGER)でも、追い出しまでの空きが線の半分なら CALM。
  (val short (NodeMemory :total-bytes CAPACITY :available-bytes (int (* 0.05 CAPACITY)) :some-avg10 0.0 :full-avg10 0.0))
  (<- far NodeMemory (line-memory short (line (// ALLOCATABLE 2))))
  (<- calm MemoryLevel (memory-level far limits))
  (assert (= calm MemoryLevel.CALM) far)
  ;; 線を越えた読みは空き 0。線の無い機体は機体の読みのまま。
  (<- over NodeMemory (line-memory roomy (line (- GIB))))
  (<- machine NodeMemory (line-memory roomy None))
  (assert (= #(over.available-bytes machine) #(0 roomy))))


(deftest test-the-heartbeat-names-the-room-before-eviction-as-the-memory-room-and-drops-a-stale-line
  (val stat "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\n")
  (val meminfo (.format "MemTotal: {} kB\nMemFree: 1 kB\nMemAvailable: {} kB\n" (// CAPACITY 1024) (// (int (* 9.68 GIB)) 1024)))
  (val line (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes ALLOCATABLE :used-bytes (- ALLOCATABLE (* 3 GIB)) :room-bytes (* 3 GIB)))
  (<- fresh (| EvictionLine None) (fresh-line line 100000 (+ 100000 LINE-FRESH-MS)))
  (<- stale (| EvictionLine None) (fresh-line line 100000 (+ 100001 LINE-FRESH-MS)))
  (<- ahead (| EvictionLine None) (fresh-line line 100000 99999))
  (assert (= #(fresh stale ahead) #(line None None)))
  (<- named (machine-load-of stat meminfo None None None 0 fresh))
  (assert (= #(named.load.memory-total-mib named.load.memory-available-mib) #((// ALLOCATABLE MIB) (* 3 1024))) named)
  (<- machine (machine-load-of stat meminfo None None None 0 stale))
  (assert (= #(machine.load.memory-total-mib machine.load.memory-available-mib) #((// CAPACITY MIB) (// (int (* 9.68 GIB)) MIB))) machine)
  ;; coordinator が置き先を数える memory の床は、worker が jobs の層の上限で線の手前に残す余裕と、常駐の job の揺れの 512 MiB を覆う
  ;; (task が使えるのは追い出しまでの空きから余裕を引いた分まで)。
  (assert (>= (* MEMORY-FLOOR-MIB MIB) (+ EVICTION-MARGIN-BYTES (* 512 MIB))) MEMORY-FLOOR-MIB))


;; --- 模擬の機体 -----------------------------------------------------------------------------------------------------------------------

(defrecord Scene
  "模擬の atlas の memory の台本(byte と ms): others = 始めのほかの Pod の working set・others-rise / others-from / others-span = ほかの
   Pod が others-from から others-span の間に一様に伸びる量・resident = worker の本体と常駐の job(services の層)の使い・task-from /
   task-to / task-start / task-span = jobs の層の task の要求が task-start から task-span の間に task-from から task-to へ一様に伸びる・
   prepared = task が jobs の層の中で止められた後の jobs の層の使い(準備の分)・system = 機体の Pod の外の使い。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int others)
  (#^ int others-rise)
  (#^ int others-from)
  (#^ int others-span)
  (#^ int resident)
  (#^ int task-from)
  (#^ int task-to)
  (#^ int task-start)
  (#^ int task-span)
  (setv #^ int prepared (* 256 MIB))
  (setv #^ int system (* 2 GIB)))


(defrecord SceneSeen
  "模擬の機体を回した後の世界: highest = 読みの刻ごとの Pod の working set の和の最大・kernel-kills = jobs の層の中で止めた task の数・
   lines = 見張りと線の log の行(#(名 欄の dict) の列)・limits = 見張りが jobs の層に書いた上限の列(書いた順)。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int highest)
  (#^ int kernel-kills)
  (#^ tuple lines)
  (#^ tuple limits))


(defeffect ReadScene
  "模擬の機体の記録を問う — テストだけの effect。"
  {:fields [] :answer SceneSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defk ramp [start amount from span now]
  {:pre [(: start int) (: amount int) (: from int) (: span int) (> span 0) (: now int)] :post [(: % int)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "from から span の間に start から amount だけ一様に伸びる量の、刻 now の値を求めるため。"
  (+ start (// (* amount (max 0 (min span (- now from)))) span)))


(defrecord KernelNow
  "模擬の kernel の刻 1 つの様子: killed = jobs の層の task がもう止められた・jobs = jobs の層の使い・pods = Pod の working set の和。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ bool killed)
  (#^ int jobs)
  (#^ int pods))


(defk kernel-now [scene limit killed now]
  {:pre [(: scene Scene) (: limit int) (: killed bool) (: now int)] :post [(: % KernelNow)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の kernel の刻 now の様子を求めるため: 要求が jobs の層の上限 limit を越えた task は jobs の層の中で OOM kill され、jobs の層は
   準備の分に戻る(1 度止めた task は戻らない)。Pod の和 = ほかの Pod + 常駐の job と worker の本体 + jobs の層。"
  (<- wanted int (ramp scene.task-from (- scene.task-to scene.task-from) scene.task-start scene.task-span now))
  (val stopped (or killed (> wanted limit)))
  (val jobs (min limit (if stopped scene.prepared wanted)))
  (<- others int (ramp scene.others scene.others-rise scene.others-from scene.others-span now))
  (KernelNow :killed stopped :jobs jobs :pods (+ others scene.resident jobs)))


(defhandler atlas-world [#^ Scene scene #^ MemoryCell cell #^ int run-until-ms]
  "見張りの外側の模擬の atlas(頭の註): Node の宣言を答える偽の API server・kubepods と root と jobs の層の cgroup の file を答える模擬の
   kernel(jobs の層の使いを、見張りが書いた memory.max で止める)・job の根・log の行を溜める・run-until-ms 以後の待ちで止めの合図。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 台本と止めの合図の入れ物と止める刻は検ごとに決める値(Ask で読む設定ではない)。
  (session var limit (* 10752 MIB))
  (session var killed False)
  (session var kills 0)
  (session var highest 0)
  (session var lines #())
  (session var limits #())
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"metadata" {"name" "atlas"} "status" {"capacity" {"cpu" "16" "memory" "29662296Ki"}
                                                                  "allocatable" {"cpu" "14" "memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0)))
  (ReadText [path]
    :when (in path KERNEL-PATHS)
    (<- now int (now-epoch-ms))
    (<- seen KernelNow (kernel-now scene limit killed now))
    (when (and seen.killed (not killed))
      (:= kills (+ kills 1)))
    (:= killed seen.killed)
    (:= highest (max highest seen.pods))
    (val jobs seen.jobs)
    (val pods seen.pods)
    (resume (cond (= path (+ PODS "/memory.max")) (str PODS-MAX)
                  (= path (+ PODS "/memory.current")) (str pods)
                  (= path (+ PODS "/memory.stat")) (.format "anon {}\nfile 0\ninactive_file 0\n" pods)
                  (= path (+ HOST "/memory.stat")) (.format "anon {}\nfile 0\ninactive_file 0\n" (+ pods scene.system))
                  True (str jobs))))
  (WriteText [path text]
    :when (= path (+ JOBS "/memory.max"))
    (:= limit (int text))
    (:= limits (+ limits #(limit)))
    (resume None))
  (ObserveJobRoots []
    (resume #((JobRoot :name "svc" :pid 10) (JobRoot :name "task/1" :pid 20))))
  (SlogEffect []
    (:= lines (+ lines #(#(effect.msg (dict effect.kwargs)))))
    (resume None))
  (DelayEffect []
    ;; 待ちの刻にも数える(kubelet は worker の読みと別の刻に読む・掛け直しの無い worker は cgroup を読まない)。
    (<- now int (now-epoch-ms))
    (<- seen KernelNow (kernel-now scene limit killed now))
    (when (and seen.killed (not killed))
      (:= kills (+ kills 1)))
    (:= killed seen.killed)
    (:= highest (max highest seen.pods))
    (when (>= now run-until-ms)
      (setv cell.closing True))
    (reperform effect))
  (ReadScene []
    (resume (SceneSeen :highest highest :kernel-kills kills :lines lines :limits limits))))


(defk machine-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の atlas の file のうち変わらない物: /proc/meminfo(MemAvailable 9.68 GiB — 前の判じ方では CALM)・PSI・worker の container の
   memory・ServiceAccount の token。"
  (val meminfo (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" (// CAPACITY 1024)
                        (// (int (* 9.68 GIB)) 1024)))
  (val pressure "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=2\n")
  (MemoryFiles :dirs #("/proc" "/proc/pressure" "/sys" "/sys/fs" "/sys/fs/cgroup" "/sa")
               :files #((MemoryFile :path "/proc/meminfo" :content (.encode meminfo))
                        (MemoryFile :path "/proc/pressure/memory" :content (.encode pressure))
                        (MemoryFile :path "/sys/fs/cgroup/memory.current" :content (.encode (str (* 8 GIB))))
                        (MemoryFile :path TOKEN :content b"token\n"))))


(defk scene-rows []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の atlas の process の表(常駐の job svc と task/1 — 見張りの止めの単位はどれも min-victim-bytes 未満で、見張りは止めない。
   止めるのは模擬の kernel だけ)。"
  #((ProcRow :pid 1 :ppid 0 :start-ticks 1 :rss-bytes MIB :comm "tini")
    (ProcRow :pid 10 :ppid 1 :start-ticks 10 :rss-bytes (* 10 MIB) :comm "python")
    (ProcRow :pid 11 :ppid 10 :start-ticks 11 :rss-bytes (* 300 MIB) :comm "python")
    (ProcRow :pid 20 :ppid 1 :start-ticks 20 :rss-bytes (* 10 MIB) :comm "python")
    (ProcRow :pid 21 :ppid 20 :start-ticks 21 :rss-bytes (* 300 MIB) :comm "python")))


(defk scene-watched [scene eviction run-until-ms]
  {:pre [(: scene Scene) (: eviction bool) (: run-until-ms int)] :post [(: % SceneSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを模擬の atlas と仮想時計の下で止めの合図まで回すため。eviction = 追い出しの線の設定を渡すか(偽 = 前の形の worker —
   jobs の層は宣言の静的な上限のまま)。"
  (<- cap (| int None) (layer-memory-max ATLAS-LAYOUT "jobs"))
  (val settings (MemoryWatchSettings
                  :eviction (if eviction
                                (EvictionSettings :api "https://api.test" :token-file TOKEN :node "atlas" :pods-dir PODS :host-root HOST
                                                  :jobs-dir JOBS :jobs-cap-bytes cap)
                                None)))
  (val cell (MemoryCell))
  (<- rows tuple (scene-rows))
  (<- files MemoryFiles (machine-files))
  (<- seen SceneSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files)
                                     (scripted-proc-table-handler rows) (atlas-world scene cell run-until-ms)]
                       (watch-then-scene settings cell)))
  seen)


(defk watch-then-scene [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % SceneSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを止めの合図まで回し、模擬の機体の記録を問うため。"
  (<- (memory-watch-loop settings cell))
  (<- seen SceneSeen (ReadScene))
  seen)


(defk lines-named [lines name]
  {:pre [(: lines tuple) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行から名 name の行の欄を出した順に引くため。"
  (tuple (gfor #(msg fields) lines :if (= msg name) fields)))


;; 11-01 08:43〜08:44 UTC の形: ほかの Pod 8.7 GiB(2.5 分前の最大)・worker の本体と常駐の job 4.65 GiB・task が 90 秒で 1.5 → 7 GiB。
;; 和の最大は 8.7 + 4.65 + 7 = 20.35 GiB で allocatable 19.79 GiB を越える(前の形では追い出し)。
(val TASK-JUMP (Scene :others (int (* 8.7 GIB)) :others-rise 0 :others-from 0 :others-span 1 :resident (int (* 4.65 GIB))
                      :task-from (int (* 1.5 GIB)) :task-to (* 7 GIB) :task-start 10000 :task-span 90000))
;; ほかの Pod の伸び(10-30 の clickhouse-trial と garage — 30 秒で 2 GiB): task が 5.5 GiB で落ち着いた後、ほかの Pod が 8 → 10 GiB。
;; 和は 8 + 4.65 + 5.5 = 18.15 GiB から 20.15 GiB へ(前の形では追い出し)。
(val OTHERS-RISE (Scene :others (* 8 GIB) :others-rise (* 2 GIB) :others-from 60000 :others-span 30000 :resident (int (* 4.65 GIB))
                        :task-from (* 1 GIB) :task-to (int (* 5.5 GIB)) :task-start 10000 :task-span 30000))


(deftest test-a-task-jump-is-stopped-inside-the-jobs-layer-before-the-pods-cross-the-allocatable
  (<- seen SceneSeen (scene-watched TASK-JUMP True 150000))
  (assert (<= seen.highest ALLOCATABLE) #((/ seen.highest GIB) (/ ALLOCATABLE GIB)))
  ;; 止まったのは jobs の層の task 1 本(模擬の kernel)。常駐の job と worker の本体の使い(resident)は台本のまま。
  (assert (= seen.kernel-kills 1) seen.kernel-kills)
  ;; 書いた上限はどれも静的な上限以下で、線の手前に余裕を残す(ほかと常駐の和 + 上限 ≤ allocatable − 余裕)。
  (assert (all (gfor l seen.limits (<= (+ TASK-JUMP.others TASK-JUMP.resident l) (- ALLOCATABLE EVICTION-MARGIN-BYTES)))) seen.limits)
  (assert (>= (len seen.limits) 29) (len seen.limits))
  (<- told tuple (lines-named seen.lines EVICTION-LINE-LOG))
  (assert (= (len told) 1) told)
  (assert (= #((get (get told 0) "line") (get (get told 0) "allocatable_mib")) #("allocatable" (// ALLOCATABLE MIB))) told)
  (<- unread tuple (lines-named seen.lines EVICTION-UNREAD-LOG))
  (assert (= unread #()) unread)
  ;; 前の形(掛け直しの無い worker)の同じ機体は、和が allocatable を越える — kubelet が Pod ごと追い出す。
  (<- before SceneSeen (scene-watched TASK-JUMP False 150000))
  (assert (> before.highest ALLOCATABLE) #((/ before.highest GIB) (/ ALLOCATABLE GIB)))
  (assert (= #(before.kernel-kills before.limits) #(0 #())) before))


(deftest test-other-pods-growing-shrink-the-jobs-layer-first-and-the-pods-stay-under-the-allocatable
  (<- seen SceneSeen (scene-watched OTHERS-RISE True 150000))
  (assert (<= seen.highest ALLOCATABLE) #((/ seen.highest GIB) (/ ALLOCATABLE GIB)))
  (assert (= seen.kernel-kills 1) seen.kernel-kills)
  ;; 空きが余裕を割った回に、上限を jobs の使いより下げた事を 1 度だけ言う。
  (<- squeezed tuple (lines-named seen.lines JOBS-SQUEEZE-LOG))
  (assert (= (len squeezed) 1) squeezed)
  (val told (get squeezed 0))
  (assert (< (get told "jobs_limit_mib") (get told "jobs_current_mib")) told)
  (assert (= (get told "line") "allocatable") told)
  ;; 見張りの様子も同じ空きで判じた: MemAvailable は 34% のままでも、追い出しまでの空きが線の 10% を割った回に DANGER と言う。
  (<- levels tuple (lines-named seen.lines MEMORY-LEVEL-LOG))
  (assert (in #(MemoryLevel.DANGER.value "allocatable") (lfor l levels #((get l "to") (get l "line")))) levels)
  (<- before SceneSeen (scene-watched OTHERS-RISE False 150000))
  (assert (> before.highest ALLOCATABLE) #((/ before.highest GIB) (/ ALLOCATABLE GIB))))
