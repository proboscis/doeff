;; 機体の memory の見張り(card ki-7b934e2538f9)— 模擬の検。
;;
;; 実例: atlas(10-16 07:25 UTC・10-11 23:48・10-12 17:22)と zeus(10-11 12:12〜12:52 UTC)で、1 つの job が memory を食うと機体の全体が
;; reclaim の thrash で止まり、worker も coordinator も応えなかった(OOM kill は起きない)。誰が食ったかは 10 分ごとの測りの間に落ちて
;; 読めなかった。
;;   - 本物の見張りの背景の task(memory-watch-loop)を仮想時計の下で 5 秒ごとに回し、機体の memory の file は memory の置き場・process の表は
;;     台本の表・job の根は偽の答え手で与える。
;;   - 余裕のある機体: 間ごとに job ごとの最大の行が出て(1 分の跳ねも落ちない最大)、終わった job の最大の行が 1 度出る。何も止めない。
;;   - 尽きかけの機体: 様子の行が DANGER を名指し、いちばん大きい単位(memory の集まった道具 1 つ — 子を並べる job の本体や子の親は
;;     残す)を止め、止めの間隔を置いて次に大きい単位(task)を止め、min-victim-bytes 未満しか残らなければ止めずに 1 度だけ言う。止めた
;;     process は表から消え、他は残る。
;;   - guard を切った見張りは様子の行だけで、何も止めない。
;;   - /proc/meminfo が読めない機体では訳を 1 度だけ言い、数えない・止めない。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff [with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.proc_table_effects [ProcRow ReadProcTable])
(import doeff_core_effects.scripted_proc_table [scripted-proc-table-handler])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.core.memory_rules [JobRoot KillUnit MemoryLevel MemoryLimits MIB UnitKind kill-units victim-of])
(import doeff_cluster.worker.protocol.observations [ObserveJobRoots])
(import doeff_cluster.worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop MEMORY-WINDOW-LOG JOB-PEAK-LOG
                                                   MEMORY-LEVEL-LOG MEMORY-KILL-LOG MEMORY-NO-VICTIM-LOG MEMORY-UNREAD-LOG])

(val GIB (* 1024 MIB))
;; 見張りの log の行の名(数える行)。
(val WATCH-LOGS #{MEMORY-WINDOW-LOG JOB-PEAK-LOG MEMORY-LEVEL-LOG MEMORY-KILL-LOG MEMORY-NO-VICTIM-LOG MEMORY-UNREAD-LOG})


(defk row [pid ppid mib comm]
  {:pre [(: pid int) (: ppid int) (: mib int) (: comm str)] :post [(: % ProcRow)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "台本の表の 1 行を作るため(起動の刻は pid と同じ値)。"
  (ProcRow :pid pid :ppid ppid :start-ticks pid :rss-bytes (* mib MIB) :comm comm))


(defk world-rows []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "台本の表: 子を並べる常駐の job svc(shim 10 → uv 11 → 本体 12 → 子 13・15 — 13 は 2000 MiB の道具 14 を持つ。shim は引き取った
   小さい孤児 16 も持つ)と task/1(shim 20 → 本体 21)。svc の和 = 10 + 5 + 100 + 300 + 2000 + 400 + 20 = 2835 MiB・task/1 = 710 MiB。"
  (<- tini ProcRow (row 1 0 1 "tini"))
  (<- shim ProcRow (row 10 1 10 "python"))
  (<- uv ProcRow (row 11 10 5 "uv"))
  (<- body ProcRow (row 12 11 100 "python"))
  (<- child-a ProcRow (row 13 12 300 "node"))
  (<- tool ProcRow (row 14 13 2000 "pytest"))
  (<- child-b ProcRow (row 15 12 400 "node"))
  (<- orphan ProcRow (row 16 10 20 "sleep"))
  (<- task-shim ProcRow (row 20 1 10 "python"))
  (<- task-body ProcRow (row 21 20 700 "python"))
  #(tini shim uv body child-a tool child-b orphan task-shim task-body))


(defk node-files [available-gib]
  {:pre [(: available-gib float)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体 32 GiB・空き available-gib の /proc/meminfo と、PSI・container の memory の file を memory の置き場に置くため。"
  (val meminfo (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" (* 32 1024 1024)
                        (int (* available-gib 1024 1024))))
  (val pressure "some avg10=1.50 avg60=1.00 avg300=0.50 total=1\nfull avg10=0.50 avg60=0.20 avg300=0.10 total=2\n")
  (MemoryFiles :dirs #("/proc" "/proc/pressure" "/sys" "/sys/fs" "/sys/fs/cgroup")
               :files #((MemoryFile :path "/proc/meminfo" :content (.encode meminfo))
                        (MemoryFile :path "/proc/pressure/memory" :content (.encode pressure))
                        (MemoryFile :path "/sys/fs/cgroup/memory.current" :content (.encode (str (* 4 GIB))))
                        (MemoryFile :path "/sys/fs/cgroup/memory.peak" :content (.encode (str (* 5 GIB)))))))


(defrecord WatchSeen
  "見張りを回した後の世界: lines = 見張りの log の行(#(名 欄の dict) の列 — 出した順)・table = 止めた後の process の表。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ tuple lines)
  (#^ tuple table))


(defeffect ReadWatchLines
  "見張りが出した log の行(#(名 欄の dict) の列 — 出した順)を問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler memory-world [#^ MemoryCell cell #^ (| int None) task-ends-ms #^ int run-until-ms]
  "見張りの外側の偽の世界: job の根は svc と task/1(task/1 は task-ends-ms から後は終わった — None = 終わらない)。見張りの log の行を
   溜める。仮想時計の Delay を数え、run-until-ms 以後の待ちで見張りに止めの合図を出す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 止めの合図を出す入れ物と、job の終わりと見張りを止める刻は検ごとに決める台本の値(Ask で読む設定ではない)。
  (session var lines #())
  (ObserveJobRoots []
    (<- now int (now-epoch-ms))
    (val ended (and (is-not task-ends-ms None) (>= now task-ends-ms)))
    (resume (+ #((JobRoot :name "svc" :pid 10)) (if ended #() #((JobRoot :name "task/1" :pid 20))))))
  (SlogEffect []
    (when (in effect.msg WATCH-LOGS)
      (:= lines (+ lines #(#(effect.msg (dict effect.kwargs))))))
    (resume None))
  (DelayEffect []
    (<- now int (now-epoch-ms))
    (when (>= now run-until-ms)
      (setv cell.closing True))
    (reperform effect))
  (ReadWatchLines []
    (resume lines)))


(defk watch-then-seen [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % WatchSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを止めの合図まで回し、log の行と止めた後の表を問うため。"
  (<- (memory-watch-loop settings cell))
  (<- lines tuple (ReadWatchLines))
  (<- table tuple (ReadProcTable))
  (WatchSeen :lines lines :table table))


(defk watched [settings files task-ends-ms run-until-ms]
  {:pre [(: settings MemoryWatchSettings) (: files MemoryFiles) (: task-ends-ms (| int None)) (: run-until-ms int)] :post [(: % WatchSeen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを偽の世界と仮想時計の下で止めの合図まで回し、log の行と止めた後の表を返すため。"
  (val cell (MemoryCell))
  (<- rows tuple (world-rows))
  (<- seen WatchSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler rows)
                                     (memory-world cell task-ends-ms run-until-ms)]
                       (watch-then-seen settings cell)))
  seen)


(defk lines-named [lines name]
  {:pre [(: lines tuple) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行から名 name の行の欄を出した順に引くため。"
  (tuple (gfor #(msg fields) lines :if (= msg name) fields)))


(deftest test-a-calm-node-logs-the-window-maximum-and-the-peak-of-an-ended-job-and-kills-nothing
  (<- files MemoryFiles (node-files 16.0))
  (<- seen WatchSeen (watched (MemoryWatchSettings :window-seconds 10.0) files 12000 31000))
  (val lines seen.lines)
  (val table seen.table)
  (<- windows tuple (lines-named lines MEMORY-WINDOW-LOG))
  (<- peaks tuple (lines-named lines JOB-PEAK-LOG))
  ;; 間ごとの最大: 最初の間は両方の job(大きい順)。task/1 が終わった後の間は svc だけ。
  (assert (>= (len windows) 2) lines)
  (assert (= (get (get windows 0) "jobs") "svc=2835 task/1=710") windows)
  (assert (= (get (get windows 0) "node_min_available_mib") (* 16 1024)) windows)
  (assert (= (get (get windows 0) "container_peak_mib") (* 5 1024)) windows)
  (assert (= (get (get windows -1) "jobs") "svc=2835") windows)
  ;; 終わった job の最大は 1 度だけ。
  (assert (= (len peaks) 1) peaks)
  (assert (= #((get (get peaks 0) "job") (get (get peaks 0) "pid") (get (get peaks 0) "peak_mib")) #("task/1" 20 710)) peaks)
  ;; 余裕のある機体では何も止めず、様子も変わらない。
  (<- kills tuple (lines-named lines MEMORY-KILL-LOG))
  (<- levels tuple (lines-named lines MEMORY-LEVEL-LOG))
  (assert (= kills #()) kills)
  (assert (= levels #()) levels)
  (assert (= (len table) 10) table))


(deftest test-a-node-running-out-kills-the-largest-unit-first-waits-between-kills-and-spares-small-units
  ;; 空き 1.6 GiB / 32 GiB = 5% < 10% — DANGER。
  (<- files MemoryFiles (node-files 1.6))
  (<- seen WatchSeen (watched (MemoryWatchSettings) files None 26000))
  (val lines seen.lines)
  (val table seen.table)
  (<- levels tuple (lines-named lines MEMORY-LEVEL-LOG))
  (assert (= (len levels) 1) levels)
  (val level (get levels 0))
  (assert (= #((get level "from") (get level "to")) #(MemoryLevel.CALM.value MemoryLevel.DANGER.value)) level)
  ;; 様子の行は大きい単位の上位を名指す(memory の集まった道具 14 の 2000 MiB が先)。
  (assert (.startswith (get level "units") "svc:14:pytest=2000 task/1:21:python=700 svc:15:node=400") level)
  (<- kills tuple (lines-named lines MEMORY-KILL-LOG))
  ;; 0 秒: いちばん大きい単位(svc の道具 14 — 親の 13 は残す)。5 秒: 止めの間隔の中 — 止めない。10 秒: 次に大きい task/1 の本体。
  (assert (= (tuple (gfor k kills #((get k "job") (get k "pid") (get k "unit_mib") (get k "processes") (get k "sent"))))
             #(#("svc" 14 2000 1 1) #("task/1" 21 700 1 1)))
          kills)
  ;; 残りのいちばん大きい単位(400 MiB)は min-victim-bytes(512 MiB)未満 — 止めずに 1 度だけ言う。
  (<- spared tuple (lines-named lines MEMORY-NO-VICTIM-LOG))
  (assert (= (len spared) 1) spared)
  (assert (= (get (get spared 0) "largest_mib") 400) spared)
  ;; 止めた木の process は表から消え、根(shim)と他の木は残る。
  (assert (= (sorted (gfor r table r.pid)) [1 10 11 12 13 15 16 20]) table))


(deftest test-a-watch-without-the-guard-names-the-danger-but-kills-nothing
  (<- files MemoryFiles (node-files 1.6))
  (<- seen WatchSeen (watched (MemoryWatchSettings :guard False) files None 16000))
  (val lines seen.lines)
  (val table seen.table)
  (<- levels tuple (lines-named lines MEMORY-LEVEL-LOG))
  (<- kills tuple (lines-named lines MEMORY-KILL-LOG))
  (<- spared tuple (lines-named lines MEMORY-NO-VICTIM-LOG))
  (assert (= (len levels) 1) levels)
  (assert (= #(kills spared) #(#() #())) lines)
  (assert (= (len table) 10) table))


(deftest test-an-unreadable-meminfo-is-named-once-and-nothing-is-counted
  (<- seen WatchSeen (watched (MemoryWatchSettings :window-seconds 5.0) (MemoryFiles) None 16000))
  (val lines seen.lines)
  (val table seen.table)
  (assert (= (tuple (gfor #(msg _) lines msg)) #(MEMORY-UNREAD-LOG)) lines)
  (assert (= (len table) 10) table))


(deftest test-a-tool-deep-in-a-single-child-chain-is-its-own-unit
  ;; 子が 1 つだけの鎖(shim 30 → uv 31 → 本体 32 → 子 33 → 道具 34)では、食った道具 34 まで下り、34 だけを止める(本体と子を道連れに
  ;; しない)。根の shim は単位に入らない。
  (<- shim ProcRow (row 30 1 10 "python"))
  (<- uv ProcRow (row 31 30 5 "uv"))
  (<- body ProcRow (row 32 31 100 "python"))
  (<- child ProcRow (row 33 32 50 "node"))
  (<- tool ProcRow (row 34 33 3000 "cargo"))
  (<- units tuple (kill-units #(shim uv body child tool) #((JobRoot :name "solo" :pid 30))))
  (assert (= (tuple (gfor u units #(u.kind u.head.pid (len u.members) (// u.bytes MIB)))) #(#(UnitKind.LEAF 34 1 3000))) units)
  (<- victim (| KillUnit None) (victim-of units (MemoryLimits)))
  (assert (= victim.head.pid 34) victim))


(deftest test-a-body-that-holds-more-than-its-only-child-is-stopped-at-and-killed-only-when-no-branch-is-large
  ;; 本体 41(6000 MiB)が子 42(300 MiB)より大きい常駐の job: 子へ下りず本体で止まり、子の木(BRANCH 300)は min-victim-bytes 未満なので、
  ;; 本体(BODY)を止める。子が大きければ(700 MiB の 43 を足す)、本体より先に子の木を止める。
  (<- shim ProcRow (row 40 1 10 "python"))
  (<- body ProcRow (row 41 40 6000 "python"))
  (<- small ProcRow (row 42 41 300 "node"))
  (<- big ProcRow (row 43 41 700 "node"))
  (val roots #((JobRoot :name "svc" :pid 40)))
  (<- alone tuple (kill-units #(shim body small) roots))
  (<- body-first (| KillUnit None) (victim-of alone (MemoryLimits)))
  (assert (= #(body-first.kind body-first.head.pid) #(UnitKind.BODY 41)) alone)
  (<- with-big tuple (kill-units #(shim body small big) roots))
  (<- branch-first (| KillUnit None) (victim-of with-big (MemoryLimits)))
  (assert (= #(branch-first.kind branch-first.head.pid) #(UnitKind.BRANCH 43)) with-big))


(deftest test-orphans-beside-the-job-body-are-their-own-units-and-do-not-hide-the-body
  ;; shim 50 が job の本体の木(uv 51 → 本体 52 → 子 53・54)の他に、引き取った大きい孤児 55(dev server 600 MiB)を持つ: 本体の木へ
  ;; 下り(孤児で止まらない)、孤児は BRANCH の単位 1 つ・本体の子も 1 本ずつの単位。
  (<- shim ProcRow (row 50 1 10 "python"))
  (<- uv ProcRow (row 51 50 5 "uv"))
  (<- body ProcRow (row 52 51 3000 "python"))
  (<- child-a ProcRow (row 53 52 900 "node"))
  (<- child-b ProcRow (row 54 52 800 "node"))
  (<- orphan ProcRow (row 55 50 600 "node"))
  (<- units tuple (kill-units #(shim uv body child-a child-b orphan) #((JobRoot :name "svc" :pid 50))))
  (assert (= (tuple (gfor u units #(u.kind u.head.pid (// u.bytes MIB))))
             #(#(UnitKind.BODY 52 3000) #(UnitKind.BRANCH 53 900) #(UnitKind.BRANCH 54 800) #(UnitKind.BRANCH 55 600)))
          units)
  (<- victim (| KillUnit None) (victim-of units (MemoryLimits)))
  (assert (= victim.head.pid 53) victim))
