;;; 常駐の job ごとの CPU と memory を heartbeat の measured の欄 jobUses に載せ、coordinator の GET /state の worker の行から読める事の検
;;; (2026-11-10)。実例: 測りの読み手は worker ごとに測りの task を置いて /proc を読んでいたが、task は memory の要りを名乗るので、空きの
;;; 少ない worker(node-e の空き 410 MiB・床 1536 MiB)には置けず、その worker の job を job ごとに測れなかった。worker の見張りは
;;; job ごとの木を既に 5 秒ごとに数えているので、その読みを heartbeat に載せれば、どの worker の job も 1 か所から測れる。
;;;   (1) 見張りは常駐の job(task でない根)ごとに、木の CPU の累計と rss の和を毎回数え、PSS は pss-seconds ごとにだけ読む
;;;   (2) 木の 1 本でも PSS を読めない・木が pss-process-limit を越える job の PSS は None(小さく欠けた和を作らない・孤児の群れを読まない)
;;;   (3) worker は見張りの新しい読みの時だけ名乗り、heartbeat の欄 jobUses の綴りを coordinator の受け口が読み、GET /state の worker の行に出す
;;;   (4) 模擬の worker は機体の service-mib が在れば常駐の job ごとに名乗る(測りの読み手の模擬の検の材料)
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [state ReadText])
(import doeff_core_effects [MemoryFile MemoryFiles memory-file-handler])
(import doeff_core_effects [ProcRow scripted-proc-table-handler])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_hy._table [table-of])
(import doeff_hy._wire [parse])
(import doeff_cluster [now-epoch-ms ObserveJobRoots WorkerInfo ClusterState ClusterTiming])
(import doeff_cluster._worker.core.memory_rules [JobRoot JobMeter MIB pss-bytes-of])
(import doeff_cluster._worker.core.load_rules [MachineLoad JOB-METERS-FRESH-MS job-meters-told])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import doeff_cluster._worker.intent.worker_model [ProcessView JobSpec])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad MeasuredJob])
(import doeff_cluster._coordinator.core.cluster_policy [state-view])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import doeff_cluster._sim.local [SimMachine machine-load-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))


;; 台本の表: 常駐の job svc(shim 10 → 本体 11 → 子 12)と task/1(shim 20 → 本体 21)。svc の CPU の累計 = 1000 + 2000 + 3000 ms・
;; rss = 10 + 20 + 30 MiB・PSS = 5 + 15 + 25 MiB。
(val ROWS (tuple (gfor #(pid ppid mib cpu-ms) #(#(1 0 1 0) #(10 1 10 1000) #(11 10 20 2000) #(12 11 30 3000) #(20 1 10 500) #(21 20 700 9000))
                       (ProcRow :pid pid :ppid ppid :start-ticks pid :rss-bytes (* mib MIB) :comm "python" :cpu-ms cpu-ms))))
(val SVC-PSS {10 5 11 15 12 25 20 1 21 600})


(defk files-of [pss]
  {:pre [(: pss dict)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体 32 GiB・空き 16 GiB の /proc/meminfo と、pid ごとの smaps_rollup(pss = pid → PSS の MiB)を memory の置き場に置くため。"
  (val meminfo (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" (* 32 1024 1024) (* 16 1024 1024)))
  (MemoryFiles :dirs (+ #("/proc") (tuple (gfor pid pss (.format "/proc/{}" pid))))
               :files (+ #((MemoryFile :path "/proc/meminfo" :content (.encode meminfo)))
                         (tuple (gfor #(pid mib) (.items pss)
                                      (MemoryFile :path (.format "/proc/{}/smaps_rollup" pid)
                                                  :content (.encode (.format "00400000-7fff [rollup]\nRss:  {} kB\nPss:  {} kB\nAnonymous:  {} kB\n"
                                                                             (* 2 mib 1024) (* mib 1024) (* (// mib 5) 1024)))))))))


(defeffect ReadPssReads
  "見張りが読んだ smaps_rollup の path(読んだ順)を問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler meter-world [#^ MemoryCell cell #^ int run-until-ms]
  "見張りの外側の偽の世界: job の根は svc と task/1。smaps_rollup の読みを数え、run-until-ms 以後の待ちで見張りに止めの合図を出す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 止めの合図を出す入れ物と見張りを止める刻は検ごとに決める台本の値(Ask で読む設定ではない)。
  (session var reads #())
  (ObserveJobRoots []
    (resume #((JobRoot :name "svc" :pid 10 :started-ms 777) (JobRoot :name "task/1" :pid 20))))
  (ReadText [path]
    (when (.endswith path "/smaps_rollup")
      (:= reads (+ reads #(path))))
    (reperform effect))
  (DelayEffect []
    (<- now int (now-epoch-ms))
    (when (>= now run-until-ms)
      (setv cell.closing True))
    (reperform effect))
  (ReadPssReads []
    (resume reads)))


(defk watch-then-reads [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを止めの合図まで回し、読んだ smaps_rollup の path を問うため。"
  (<- (memory-watch-loop settings cell))
  (<- reads tuple (ReadPssReads))
  reads)


(defk watched [settings pss run-until-ms]
  {:pre [(: settings MemoryWatchSettings) (: pss dict) (: run-until-ms int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを偽の世界と仮想時計の下で回し、#(入れ物 読んだ smaps_rollup の path) を返すため。"
  (val cell (MemoryCell))
  (<- files MemoryFiles (files-of pss))
  (<- reads tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler ROWS)
                                  (meter-world cell run-until-ms)]
                    (watch-then-reads settings cell)))
  #(cell reads))


(deftest test-the-watch-counts-resident-job-cpu-and-rss-every-round-and-pss-once-a-minute
  ;; (1) 130 秒・5 秒ごとの回: svc だけが数えられ(task/1 は名乗らない)、CPU と rss は木の和、PSS は 0・60・120 秒の 3 回だけ木の 3 本を読む。
  (<- seen tuple (watched (MemoryWatchSettings :guard False) SVC-PSS 130000))
  (val cell (get seen 0))
  (val reads (get seen 1))
  (val start (. (get cell.eviction.meters 0) pss-at-ms))
  (assert (= cell.eviction.meters #((JobMeter :job "svc" :pid 10 :cpu-ms 6000 :rss-bytes (* 60 MIB) :processes 3
                                              :pss-bytes (* 45 MIB) :pss-at-ms start :body-anonymous-bytes (* 5 MIB) :started-ms 777)))
          cell.eviction.meters)
  (assert (= (len reads) 9) reads)
  (assert (= (sorted (set reads)) ["/proc/10/smaps_rollup" "/proc/11/smaps_rollup" "/proc/12/smaps_rollup"]) reads)
  ;; 最後の回(130 秒)の CPU と rss に、120 秒の回に読んだ PSS が添う。
  (assert (= (- cell.eviction.meters-at-ms start) 10000) #(cell.eviction.meters-at-ms start)))


(deftest test-an-unreadable-member-or-a-tree-over-the-limit-leaves-pss-unknown
  ;; (2) 子 12 の smaps_rollup が無い: PSS は None で、CPU と rss は数える。
  (<- partial tuple (watched (MemoryWatchSettings :guard False) (dfor #(k v) (.items SVC-PSS) :if (!= k 12) k v) 10000))
  (val meter (get (. (get partial 0) eviction meters) 0))
  (assert (= #(meter.cpu-ms meter.pss-bytes meter.pss-at-ms) #(6000 None None)) meter)
  ;; 木が上限 2 本を越える: 読まずに None。
  (<- over tuple (watched (MemoryWatchSettings :guard False :pss-process-limit 2) SVC-PSS 10000))
  (assert (= (get over 1) #()) (get over 1))
  (assert (is (. (get (. (get over 0) eviction meters) 0) pss-bytes) None)))


(deftest test-pss-is-read-from-the-rollup-pss-line
  (<- size (| int None) (pss-bytes-of "55e0-7ffd ---p 00000000 00:00 0 [rollup]\nRss:   2048 kB\nPss:   1536 kB\nPss_Anon: 1000 kB\n"))
  (<- missing (| int None) (pss-bytes-of "Rss: 10 kB\n"))
  (assert (= #(size missing) #((* 1536 1024) None))))


(deftest test-the-heartbeat-names-fresh-meters-and-state-shows-them
  ;; (3) 新しい読みだけを名乗り、綴りを coordinator の受け口が読み、GET /state の worker の行に出す。
  (val meters #((JobMeter :job "rules" :pid 41 :cpu-ms 123456 :rss-bytes (* 300 MIB) :processes 2 :pss-bytes (* 210 MIB) :pss-at-ms 5000
                           :body-anonymous-bytes (* 150 MIB) :started-ms 900)
                (JobMeter :job "watch" :pid 42 :cpu-ms 10 :rss-bytes (* 80 MIB) :processes 1)))
  (<- fresh (| tuple None) (job-meters-told meters 1000 (+ 1000 JOB-METERS-FRESH-MS)))
  (<- stale (| tuple None) (job-meters-told meters 1000 (+ 1001 JOB-METERS-FRESH-MS)))
  (<- never (| tuple None) (job-meters-told meters None 1000))
  (assert (= #(fresh stale never) #(meters None None)))
  (<- spelled dict (measured-json (MachineLoad :at-ms 6000 :cpus 4 :cpu-busy 0.0 :memory-total-mib 2659 :memory-available-mib 410
                                                :cpu-some-avg10 None :cpu-full-avg10 None :memory-some-avg10 None :memory-full-avg10 None
                                                :job-meters fresh :job-meters-at-ms 6000)))
  (assert (= (get spelled "jobUsesAtMs") 6000) spelled)
  (assert (= (get spelled "jobUses")
             [{"job" "rules" "pid" 41 "cpuMs" 123456 "rssMiB" 300 "processes" 2 "pssMiB" 210 "pssAtMs" 5000 "bodyAnonymousMiB" 150 "startedAtMs" 900}
              {"job" "watch" "pid" 42 "cpuMs" 10 "rssMiB" 80 "processes" 1}])
          spelled)
  (<- read (parse MeasuredLoad spelled))
  (assert (= read.job-uses #((MeasuredJob :job "rules" :pid 41 :cpu-ms 123456 :rss-MiB 300 :processes 2 :pss-MiB 210 :pss-at-ms 5000
                                          :body-anonymous-MiB 150 :started-at-ms 900)
                             (MeasuredJob :job "watch" :pid 42 :cpu-ms 10 :rss-MiB 80 :processes 1)))
          read)
  (<- workers (table-from-rows {"node-e" (WorkerInfo "node-e" #("host-node-e") 6000 :measured read)}))
  (val view (! (state-view-json (! (state-view (ClusterState #() workers (table-of #()) (table-of #())) 6000 T)))))
  (val shown (get view "workers" "node-e" "measured"))
  (assert (= (get shown "jobUsesAtMs") 6000) shown)
  (assert (= (lfor j (get shown "jobUses") #((get j "job") (get j "cpuMs") (.get j "pssMiB"))) [#("rules" 123456 210) #("watch" 10 None)]) shown))


(deftest test-a-sim-machine-with-a-service-size-names-each-resident-job
  ;; (4) 機体の service-mib が在る模擬の worker は、生きている常駐の job ごとに CPU の累計(起こしてからの秒 × service-cores)と PSS を名乗る。
  (val machine (SimMachine :cpus 4 :memory-mib 8192 :service-mib 200 :service-cores 0.5))
  (val spec (JobSpec "rules" "jobs.rules" #() "rev-a"))
  (val services #((ProcessView "rules" spec 1 7 1000)))
  (<- load MachineLoad (machine-load-of machine 0 11000 :services services))
  (assert (= load.job-meters #((JobMeter :job "rules" :pid 7 :cpu-ms 5000 :rss-bytes (* 200 MIB) :processes 1 :pss-bytes (* 200 MIB)
                                         :pss-at-ms 11000 :body-anonymous-bytes (* 200 MIB) :started-ms 1000)))
          load.job-meters)
  (<- plain MachineLoad (machine-load-of (replace machine :service-mib None) 0 11000 :services services))
  (assert (is plain.job-meters None) plain))
