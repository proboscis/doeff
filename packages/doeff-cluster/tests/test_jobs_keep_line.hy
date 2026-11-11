;; 上に載る系が残す線(線の種類 KEEP)で jobs の層を縮めても、要りの内で走る task を止めない — 模擬の検。
;;
;; 失敗ケース(2026-11-09 23:57〜11-10 01:33 UTC の node-h): worker は jobs の層の memory.max を「jobs の使い + (MemAvailable − 残す線)− 余裕
;; − 取り置き」に掛け直した。MemAvailable が線を割る訳の大半は jobs の層の外(上に載る系の命令の層 22〜26 GiB・会話の CLI の層 8〜11 GiB)
;; に在ったのに、上限は jobs の層だけを縮めて使いより下(多くは 0)にし、kernel は伸びた task でなく層の中の全部の task を OOM kill で
;; 止めた(要り 1024 MiB・山 128 MiB の task を含む 13 本が、縮めの 1〜10 秒後に SIGKILL。lost の訳は「子 process が結果を書かずに
;; 終わった」だけ)。
;;   - 判断(worker/core/eviction_rules): 線 KEEP の上限は「jobs の今の使い + 置いた task の取り置き」より下げない(kept-jobs-limit — 追い出しの
;;     線の縮めは今のまま)。線を割った量に届くまで、要りを越えた task だけを越えの大きい順に選ぶ(over-need-tasks)。
;;   - 模擬の機体(node-a の Node の大きさ): 本物の見張りを仮想時計の下で回す。jobs の層に task 2 本(a = 要り 4 GiB・使い 3 GiB で落ち着く、
;;     b = 要り 1 GiB)。模擬の kernel は jobs の層の使いが見張りの書いた memory.max を越えると、層の中のいちばん大きい task を止める(本物の
;;     kernel の層の OOM kill と同じ選び — 伸びた task とは限らない)。
;;       jobs の層の外が伸びて MemAvailable が線を割る: どの task も止まらず、jobs の層の上限は使いより下がらず、新しい受けの上限は使いを
;;       越えない(新しい task を置かない)。
;;       b が要りを越えて伸びて MemAvailable が線を割る: 見張りが b だけを止め、a は走り続ける。止めた訳は止めた印に残る。
;;   - 止めた訳(worker/core/policy の stop-reason-of): 見張りの止めの訳と、process の走る間に jobs の層の oom_kill が増えて SIGKILL で終わった
;;     時の kernel の止めの訳が、回収の action と状態の報告の訳(task の終わりは coordinator の lost の訳)に載る。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect ReadText WriteText HttpRequest HttpResponse FileFailed])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ObserveJobRoots])
(import doeff_cluster [JobSpec])
(import doeff_cluster [CodeState CodeView ProcessView ReapJob WorkerPolicy WorldView MemoryStops]
        doeff_cluster._worker.intent.worker_model [Outcome])
(import doeff_cluster [plan records-after statuses])
(import doeff_cluster._worker.core.memory_rules [JobRoot JobUse MIB])
(import doeff_cluster._worker.core.eviction_rules [TaskNeed TaskOver kept-jobs-limit over-need-tasks layer-memory-max])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell jobs-oom-noted])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop OVER-NEED-KILL-LOG])

(val GIB (* 1024 MIB))
;; node-a の Node の宣言(tests/test_eviction_line と同じ値): capacity 29662296Ki・allocatable 20749400Ki・kubepods の memory.max = capacity − 7Gi。
(val CAPACITY (* 29662296 1024))
(val PODS-MAX (- CAPACITY (* 7 GIB)))
(val LAYOUT "worker:memory.min=256M,cpu.weight=1000 services:memory.min=7168M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100")
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val JOBS (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope/jobs"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/node-a")
(val KERNEL-PATHS #((+ PODS "/memory.max") (+ PODS "/memory.current") (+ PODS "/memory.stat") (+ HOST "/memory.stat")
                    (+ JOBS "/memory.current") (+ JOBS "/memory.stat") (+ JOBS "/memory.events")))
;; 上に載る系が残す MemAvailable の線と、機体の Pod の外の使い。
(val KEEP-LINE (* 13 GIB))
(val SYSTEM (* 2 GIB))
;; 置き先が置いた task の要り(a は要りの内で落ち着き、b は要り 1 GiB)。
(val NEEDS #((TaskNeed :id "a" :bytes (* 4 GIB)) (TaskNeed :id "b" :bytes (* 1 GIB))))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-kept-line-does-not-take-the-jobs-limit-below-the-use-and-the-placed-needs
  ;; 線 KEEP で数えた上限が 0 でも、使い 4 GiB + 取り置き 2 GiB の床まで残す(要りの内の task は今の使いを保ち、要りまで伸びられる)。
  (<- floored int (kept-jobs-limit (* 8 GIB) 0 (* 4 GIB) (* 2 GIB) False))
  (assert (= floored (* 6 GIB)) (/ floored GIB))
  ;; 追い出しの線で数えた上限がそれより小さい時は、追い出しの線のまま(kubelet の追い出しの手前の縮めは今のまま)。
  (<- evicted int (kept-jobs-limit (* 3 GIB) 0 (* 4 GIB) (* 2 GIB) False))
  (assert (= evicted (* 3 GIB)) (/ evicted GIB))
  ;; 線 KEEP の上限が床より大きい(線の上に空きが在る)時は、その上限のまま。
  (<- roomy int (kept-jobs-limit (* 9 GIB) (* 7 GIB) (* 4 GIB) (* 2 GIB) False))
  (assert (= roomy (* 7 GIB)) (/ roomy GIB)))


(deftest test-the-kept-line-does-not-narrow-the-jobs-limit-while-an-urgent-task-is-placed
  ;; 失敗ケース: 置き先は急ぎの task を線 KEEP を外した空きで置くのに、worker が層の上限を線 KEEP の床(使い 3 GiB + 取り置き 0)まで
  ;; 絞り、置いた急ぎの task が伸びた所で層の中で OOM kill された(2026-11-12)。急ぎの task が居る間は追い出しの線の上限のまま。
  (<- urgent int (kept-jobs-limit (* 8 GIB) 0 (* 3 GIB) 0 True))
  (assert (= urgent (* 8 GIB)) (/ urgent GIB))
  ;; 追い出しの線の縮めは急ぎの task にも掛かる(kubelet の追い出しの手前)。
  (<- evicted int (kept-jobs-limit (* 2 GIB) 0 (* 3 GIB) 0 True))
  (assert (= evicted (* 2 GIB)) (/ evicted GIB)))


(deftest test-only-the-tasks-grown-past-their-needs-are-chosen-in-the-order-of-their-excess
  (val needs #((TaskNeed :id "a" :bytes (* 4 GIB)) (TaskNeed :id "b" :bytes (* 1 GIB)) (TaskNeed :id "c" :bytes (* 2 GIB))))
  (val uses #((JobUse :job "task/a" :pid 20 :bytes (* 3 GIB) :processes 2) (JobUse :job "task/b" :pid 30 :bytes (* 3 GIB) :processes 2)
              (JobUse :job "task/c" :pid 40 :bytes (* 5 GIB) :processes 2) (JobUse :job "svc" :pid 10 :bytes (* 9 GIB) :processes 2)))
  ;; 割った量が小さければ越えのいちばん大きい c(3 GiB 越え)だけ。a は要りの内、常駐の job は task でないので選ばない。
  (<- small tuple (over-need-tasks needs uses (* 1 GIB)))
  (assert (= small #((TaskOver :id "c" :need-bytes (* 2 GIB) :use-bytes (* 5 GIB)))) small)
  ;; c の使いでは届かない量なら、次に越えの大きい b も。それでも a は選ばない。
  (<- large tuple (over-need-tasks needs uses (* 6 GIB)))
  (assert (= (tuple (gfor t large t.id)) #("c" "b")) large)
  (<- huge tuple (over-need-tasks needs uses (* 100 GIB)))
  (assert (= (tuple (gfor t huge t.id)) #("c" "b")) huge)
  ;; 線を割っていない・要りを越えた task が無ければ選ばない(線を割った訳が jobs の層の外なら、新しい受けを絞るだけ)。
  (<- unbroken tuple (over-need-tasks needs uses 0))
  (<- inside tuple (over-need-tasks #((TaskNeed :id "a" :bytes (* 4 GIB))) uses (* 6 GIB)))
  (assert (= #(unbroken inside) #(#() #())) #(unbroken inside)))


;; --- 模擬の機体 -----------------------------------------------------------------------------------------------------------------------

(defrecord KeepScene
  "模擬の機体の memory の台本(byte と ms): others = 始めの jobs の層の外の Pod(上に載る系の命令と会話の CLI の層を含む)の使い・
   others-rise / others-from / others-span = その外が others-from から others-span の間に一様に伸びる量・resident = worker の本体と常駐の
   job の使い・a-use = task a の使い(落ち着いた値)・b-from / b-to / b-start / b-span = task b の使いが b-start から b-span の間に b-from
   から b-to へ一様に伸びる。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int others)
  (#^ int others-rise)
  (#^ int others-from)
  (#^ int others-span)
  (#^ int resident)
  (#^ int a-use)
  (#^ int b-from)
  (#^ int b-to)
  (#^ int b-start)
  (#^ int b-span))


(defrecord KeepSeen
  "模擬の機体を回した後の世界: stopped = 止められた task(#(id 止めた者) の列 — 止めた者は kernel か watch・止めた順)・limits = 見張りが
   jobs の層に書いた上限の列・lowest = 機体の MemAvailable の最小・lowest-limit-over-use = 書いた上限 − その時の jobs の層の使いの最小・
   lines = log の行(#(名 欄の dict) の列)・cell = 見張りの入れ物。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ tuple stopped)
  (#^ tuple limits)
  (#^ int lowest)
  (#^ int lowest-limit-over-use)
  (#^ tuple lines)
  (#^ MemoryCell cell))


(defeffect ReadKeepScene
  "模擬の機体の記録を問う — テストだけの effect。"
  {:fields [] :answer KeepSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defk ramp [start amount from span now]
  {:pre [(: start int) (: amount int) (: from int) (: span int) (> span 0) (: now int)] :post [(: % int)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "from から span の間に start から amount だけ一様に伸びる量の、刻 now の値を求めるため。"
  (+ start (// (* amount (max 0 (min span (- now from)))) span)))


(defk task-uses [scene stopped now]
  {:pre [(: scene KeepScene) (: stopped tuple) (: now int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now の走っている task の使い(id → byte — 止められた task は載せない)を求めるため。"
  (<- b int (ramp scene.b-from (- scene.b-to scene.b-from) scene.b-start scene.b-span now))
  (val gone (sfor #(id _) stopped id))
  (dfor #(id use) (.items {"a" scene.a-use "b" b}) :if (not-in id gone) id use))


(defk kernel-stopped [scene limit stopped now]
  {:pre [(: scene KeepScene) (: limit int) (: stopped tuple) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の kernel の刻 now の止め(頭の註): jobs の層の使いが上限 limit を越える間、層の中のいちばん大きい task を止める。答え = 止めた後の
   止められた task の列。"
  (var gone stopped)
  (var uses (! (task-uses scene gone now)))
  (while (and uses (> (sum (.values uses)) limit))
    (val largest (max (sorted uses) :key (fn [id] (get uses id))))
    (:= gone (+ gone #(#(largest "kernel"))))
    (:= uses (! (task-uses scene gone now))))
  gone)


(defhandler keep-world [#^ KeepScene scene #^ MemoryCell cell #^ int run-until-ms]
  "見張りの外側の模擬の機体(頭の註): Node の宣言を答える偽の API server・kubepods と root と jobs の層の cgroup の file を答える模擬の kernel・
   task の process の表と止めの signal・job の根・log の行を溜める・待ちごとに「機体の MemAvailable − 残す線」を見張りの入れ物へ書く・
   run-until-ms 以後の待ちで止めの合図。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 台本と止めの合図の入れ物と止める刻は検ごとに決める値(Ask で読む設定ではない)。
  (session var limit (* 10752 MIB))
  (session var stopped #())
  (session var lowest CAPACITY)
  (session var gap CAPACITY)
  (session var lines #())
  (session var limits #())
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"metadata" {"name" "node-a"} "status" {"capacity" {"cpu" "16" "memory" "29662296Ki"}
                                                                  "allocatable" {"cpu" "14" "memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0)))
  (ReadText [path]
    :when (in path KERNEL-PATHS)
    (<- now int (now-epoch-ms))
    (:= stopped (! (kernel-stopped scene limit stopped now)))
    (<- uses dict (task-uses scene stopped now))
    (val jobs (sum (.values uses)))
    (<- others int (ramp scene.others scene.others-rise scene.others-from scene.others-span now))
    (val pods (+ others scene.resident jobs))
    (val oom (len (tuple (gfor #(_ who) stopped :if (= who "kernel") who))))
    (resume (cond (= path (+ PODS "/memory.max")) (str PODS-MAX)
                  (= path (+ PODS "/memory.current")) (str pods)
                  (= path (+ PODS "/memory.stat")) (.format "anon {}\nfile 0\ninactive_file 0\n" pods)
                  (= path (+ HOST "/memory.stat")) (.format "anon {}\nfile 0\ninactive_file 0\n" (+ pods SYSTEM))
                  (= path (+ JOBS "/memory.stat")) (.format "anon {}\ninactive_file 0\n" jobs)
                  (= path (+ JOBS "/memory.events")) (.format "low 0\nhigh 0\nmax 0\noom {}\noom_kill {}\n" oom oom)
                  True (str jobs))))
  (WriteText [path text]
    :when (= path (+ JOBS "/memory.max"))
    (<- now int (now-epoch-ms))
    (:= limit (int text))
    (:= limits (+ limits #(limit)))
    (<- uses dict (task-uses scene stopped now))
    (:= gap (min gap (- limit (sum (.values uses)))))
    (resume None))
  (ReadProcTable []
    (<- now int (now-epoch-ms))
    (<- uses dict (task-uses scene stopped now))
    (val roots #((ProcRow :pid 1 :ppid 0 :start-ticks 1 :rss-bytes MIB :comm "tini")
                 (ProcRow :pid 20 :ppid 1 :start-ticks 20 :rss-bytes MIB :comm "shim")
                 (ProcRow :pid 30 :ppid 1 :start-ticks 30 :rss-bytes MIB :comm "shim")))
    (resume (+ roots (tuple (gfor #(id pid) #(#("a" 21) #("b" 31)) :if (in id uses)
                                  (ProcRow :pid pid :ppid (- pid 1) :start-ticks pid :rss-bytes (get uses id) :comm "python"))))))
  (SignalPid []
    ;; 見張りの止め: task の子へ SIGKILL が届けば、その task は止まる。
    (val id (cond (= effect.pid 21) "a" (= effect.pid 31) "b" True None))
    (<- now int (now-epoch-ms))
    (<- uses dict (task-uses scene stopped now))
    (val alive (and (is-not id None) (in id uses)))
    (when alive
      (:= stopped (+ stopped #(#(id "watch")))))
    (resume alive))
  (ObserveJobRoots []
    (resume #((JobRoot :name "task/a" :pid 20) (JobRoot :name "task/b" :pid 30))))
  (SlogEffect []
    (:= lines (+ lines #(#(effect.msg (dict effect.kwargs)))))
    (resume None))
  (DelayEffect []
    (<- now int (now-epoch-ms))
    (:= stopped (! (kernel-stopped scene limit stopped now)))
    (<- uses dict (task-uses scene stopped now))
    (<- others int (ramp scene.others scene.others-rise scene.others-from scene.others-span now))
    (val available (- CAPACITY SYSTEM others scene.resident (sum (.values uses))))
    (:= lowest (min lowest available))
    ;; heartbeat の測りの代わり(worker/protocol/coordinator_link の load-noted と同じ書き)。
    (setv cell.eviction.keep-room (- available KEEP-LINE) cell.eviction.keep-room-at-ms now)
    (when (>= now run-until-ms)
      (setv cell.closing True))
    (reperform effect))
  (ReadKeepScene []
    (resume (KeepSeen :stopped stopped :limits limits :lowest lowest :lowest-limit-over-use gap :lines lines :cell cell))))


(defk machine-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の機体の file のうち変わらない物: /proc/meminfo・PSI・worker の container の memory・ServiceAccount の token。"
  (val meminfo (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" (// CAPACITY 1024) (* 15 1024 1024)))
  (val pressure "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=2\n")
  (MemoryFiles :dirs #("/proc" "/proc/pressure" "/sys" "/sys/fs" "/sys/fs/cgroup" "/sa")
               :files #((MemoryFile :path "/proc/meminfo" :content (.encode meminfo))
                        (MemoryFile :path "/proc/pressure/memory" :content (.encode pressure))
                        (MemoryFile :path "/sys/fs/cgroup/memory.current" :content (.encode (str (* 8 GIB))))
                        (MemoryFile :path TOKEN :content b"token\n"))))


(defk keep-watched [scene run-until-ms]
  {:pre [(: scene KeepScene) (: run-until-ms int)] :post [(: % KeepSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを模擬の機体と仮想時計の下で止めの合図まで回すため(置き先が置いた task の要りは NEEDS)。"
  (<- cap (| int None) (layer-memory-max LAYOUT "jobs"))
  (val settings (MemoryWatchSettings
                  :eviction (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST
                                              :jobs-dir JOBS :jobs-cap-bytes cap)))
  (val cell (MemoryCell))
  (setv cell.eviction.needs NEEDS)
  (<- files MemoryFiles (machine-files))
  (<- seen KeepSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (keep-world scene cell run-until-ms)]
                      (watch-then-scene settings cell)))
  seen)


(defk watch-then-scene [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % KeepSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを止めの合図まで回し、模擬の機体の記録を問うため。"
  (<- (memory-watch-loop settings cell))
  (<- seen KeepSeen (ReadKeepScene))
  seen)


;; jobs の層の外の伸び(node-h の 11-10 00:58 の形を node-a の大きさに縮めた台本): 外 3 GiB が 20 秒から 20 秒で 5 GiB 伸びる・worker の本体と
;; 常駐の job 4 GiB・task a 3 GiB(要り 4 GiB)と b 0.9 GiB(要り 1 GiB)は伸びない。MemAvailable は 15.4 GiB から 10.4 GiB へ落ち、線 13 GiB を
;; 2.6 GiB 割る。追い出しまでの空きは 3.8 GiB 残る(追い出しの線では縮めない)。
(val OUTSIDE-RISE (KeepScene :others (* 3 GIB) :others-rise (* 5 GIB) :others-from 20000 :others-span 20000 :resident (* 4 GIB)
                             :a-use (* 3 GIB) :b-from (* 900 MIB) :b-to (* 900 MIB) :b-start 0 :b-span 1))
;; 要りを越えた伸び(node-h の 11-09 16:52 の形を縮めた台本): 外は伸びず、b が 20 秒から 50 秒で 1 → 6 GiB へ伸びる(要り 1 GiB)。
(val OVERGROWN (KeepScene :others (* 3 GIB) :others-rise 0 :others-from 0 :others-span 1 :resident (* 4 GIB)
                          :a-use (* 3 GIB) :b-from (* 1 GIB) :b-to (* 6 GIB) :b-start 20000 :b-span 50000))


(defk lines-named [lines name]
  {:pre [(: lines tuple) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行から名 name の行の欄を出した順に引くため。"
  (tuple (gfor #(msg fields) lines :if (= msg name) fields)))


(deftest test-the-outside-crossing-the-kept-line-stops-no-task-and-only-closes-the-intake
  (<- seen KeepSeen (keep-watched OUTSIDE-RISE 90000))
  ;; どの task も止まらない: 書いた上限はどの回も jobs の層の使い以上(前は 0 まで下げ、kernel が層の task を全部止めた)。
  (assert (= seen.stopped #()) #(seen.stopped (lfor l seen.limits (// l MIB))))
  ;; 線は割れた(外の伸びで)。
  (assert (< seen.lowest (- KEEP-LINE (* 2 GIB))) (/ seen.lowest GIB))
  (assert (>= seen.lowest-limit-over-use 0) (/ seen.lowest-limit-over-use GIB))
  (assert (>= (len seen.limits) 17) (len seen.limits))
  (<- over tuple (lines-named seen.lines OVER-NEED-KILL-LOG))
  (assert (= over #()) over)
  (assert (= seen.cell.kills #()) seen.cell.kills)
  (assert (= seen.cell.eviction.oom-at-ms #()) seen.cell.eviction.oom-at-ms)
  ;; 新しい受けの上限は使いを越えない(置き先に名乗る jobs の層の空きは 0 以下 — 新しい task を置かない)。
  (val eviction seen.cell.eviction)
  (assert (<= eviction.jobs-intake eviction.jobs-current) #((/ eviction.jobs-intake GIB) (/ eviction.jobs-current GIB))))


(deftest test-a-task-grown-past-its-need-is-stopped-alone-and-the-task-inside-its-need-keeps-running
  (<- seen KeepSeen (keep-watched OVERGROWN 120000))
  ;; 止まったのは b だけで、止めたのは見張り(kernel の層の OOM kill ではない)。a は走り続ける。
  (assert (= seen.stopped #(#("b" "watch"))) seen.stopped)
  (<- told tuple (lines-named seen.lines OVER-NEED-KILL-LOG))
  (assert (= (tuple (gfor t told (get t "job"))) #("task/b")) told)
  ;; 止めた印に訳が残る(状態の報告の訳と coordinator の lost の訳に載る)。
  (val marks (tuple (gfor k seen.cell.kills #(k.job k.pid))))
  (assert (= marks #(#("task/b" 30))) marks)
  (assert (in "要りを越えて伸びた task を見張りが止めた" (. (get seen.cell.kills 0) reason)) seen.cell.kills)
  ;; MemAvailable は線を見張りの 1 回の伸び(5 秒で 0.5 GiB)より大きく割らない。
  (assert (>= seen.lowest (- KEEP-LINE GIB)) (/ seen.lowest GIB))
  (assert (>= seen.lowest-limit-over-use 0) (/ seen.lowest-limit-over-use GIB)))


;; --- jobs の層の kernel の OOM kill の数え -------------------------------------------------------------------------------------------

(defhandler events-file [#^ list counts]
  "jobs の層の memory.events を、読みごとに counts の頭から順に oom_kill の数を答える(読めない回は None)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 読みごとに替わる数の台本。
  (ReadText [path]
    :when (= path "/jobs/memory.events")
    (val count (.pop counts 0))
    (resume (if (is count None) (FileFailed :path path :detail "読めない") (.format "oom 0\noom_kill {}\n" count)))))


(defk oom-noted-at [cell times]
  {:pre [(: cell EvictionCell) (: times tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の jobs の層の oom_kill の読みを、刻 times ごとに 1 回ずつ回すため。"
  (for [at times]
    (<- (jobs-oom-noted cell at)))
  None)


(deftest test-the-watch-stamps-each-rise-of-the-jobs-layer-oom-kills
  (val cell (EvictionCell))
  (setv cell.jobs-dir "/jobs")
  ;; 最初の読みは起点(Pod の起動からの数は増えに数えない)・増えた回だけ刻を残す・読めない回は数を残す・古い刻は除く。
  (<- (with-handlers [(events-file [138 140 None 141 141])] (oom-noted-at cell #(1000 6000 11000 16000 (+ 6000 600001)))))
  (assert (= cell.oom-count 141) cell.oom-count)
  (assert (= cell.oom-at-ms #(16000)) cell.oom-at-ms))


;; --- 止めた訳を状態の報告の訳へ ----------------------------------------------------------------------------------------------------

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500 :restart-backoff-ms 2000 :restart-backoff-max-ms 60000))
(val TASK (JobSpec "task/t1" "jobs.t" #() "rev1" :once True))
(val READY (CodeView "rev1" CodeState.READY "/c/rev1"))


(defk reaped-detail [world]
  {:pre [(: world WorldView)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "task の process(pid 70・刻 1000 に起こした)が終わった観測 world を回収し、回収の action の止めた訳と状態の報告の訳を求める
   ため。"
  (<- reaped tuple (plan 20000 #(TASK) world {} POLICY))
  (val reap (next (gfor a reaped :if (isinstance a ReapJob) a)))
  (<- records dict (records-after 20000 {} reaped POLICY))
  (<- reported tuple (statuses 20000 #(TASK) (WorldView #(READY) #()) records POLICY))
  #(reap.stop-reason (. (get reported 0) detail)))


(deftest test-the-reason-a-task-was-stopped-for-memory-reaches-the-status-detail
  (val ended (ProcessView "task/t1" TASK 1 70 1000 :exit-code 137))
  ;; 見張りが止めた: その訳。
  (val watched (WorldView #(READY) #(ended) :memory-killed (frozenset #(#("task/t1" 70)))
                          :memory-stops (MemoryStops :kills #(#("task/t1" 70 "要りを越えて伸びた task を見張りが止めた(使い 3 MiB)")))))
  (<- by-watch tuple (reaped-detail watched))
  (assert (= (get by-watch 0) "要りを越えて伸びた task を見張りが止めた(使い 3 MiB)") by-watch)
  (assert (and (.startswith (get by-watch 1) "last=exited") (.endswith (get by-watch 1) " — 要りを越えて伸びた task を見張りが止めた(使い 3 MiB)"))
          by-watch)
  ;; 走る間に jobs の層の oom_kill が増え、SIGKILL で終わった: kernel が層の上限で止めた訳(その時の上限を添える)。
  (val oomed (WorldView #(READY) #(ended) :memory-stops (MemoryStops :oom-at-ms #(500 4000) :jobs-limit-bytes 0)))
  (<- by-kernel tuple (reaped-detail oomed))
  (assert (.startswith (get by-kernel 0) "SIGKILL で終わった — この process の走る間に kernel が jobs の層の memory.max で OOM kill した(1 回・今の上限 0 MiB)")
          by-kernel)
  (assert (in "OOM kill" (get by-kernel 1)) by-kernel)
  ;; 見張りがまだ刻を付けていない増えも、今の拍の増えとして読む。
  (val fresh (WorldView #(READY) #(ended) :memory-stops (MemoryStops :oom-unstamped True)))
  (<- by-fresh tuple (reaped-detail fresh))
  (assert (in "(1 回・今の上限 なし)" (get by-fresh 0)) by-fresh)
  ;; 自分で終わった(SIGKILL でない)・起こす前の増えは、memory の訳と読まない。
  (val clean (WorldView #(READY) #((ProcessView "task/t1" TASK 1 70 1000 :exit-code 0)) :memory-stops (MemoryStops :oom-at-ms #(4000))))
  (<- by-self tuple (reaped-detail clean))
  (val earlier (WorldView #(READY) #(ended) :memory-stops (MemoryStops :oom-at-ms #(500))))
  (<- by-earlier tuple (reaped-detail earlier))
  (assert (= by-self #(None "last=exited")) by-self)
  (assert (and (is (get by-earlier 0) None) (not-in " — " (get by-earlier 1))) by-earlier))
