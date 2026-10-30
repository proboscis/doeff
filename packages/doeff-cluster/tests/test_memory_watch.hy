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
;;   - 平常の大きさ(card ki-649b1b2f5838 — 10-24 atlas で、機体の空きを worker の外の Pod が食った 2 時間に、平常の約 950 MiB で動く
;;     常駐の job が 11 回止められ、起こし直されては同じ大きさに戻った): 平常の大きさで動く常駐の job は尽きかけの機体でも止めず、
;;     止めなかった job と worker の外の使用量を 1 行で言う。平常の 2 倍の常駐の job と大きい task は止める。止めた job の間の最大は
;;     平常の大きさに残さない。
;;   - 群れの止め(card ki-d9953fe1d739 — 10-23 20:36〜20:46 UTC の zeus で、CLI を多数並べる常駐の job の木が約 64 GB に太ったのに、単位は
;;     どれも 512 MiB 未満で見張りは何も止めず、機体ごと約 3 分止まった): 常駐の job が 300 MiB の CLI の木 40 本と、shim に引き取られた
;;     200 MiB の孤児 40 本を持つ尽きかけの機体では、job の本体を残して、大きい単位から DANGER の線を戻す量まで まとめて止める。worker の
;;     外の使用量がその job より大きい時は止めずに 1 度だけ言う。
;;   - 孤児の溢れ(card ki-72f309f23e0f — 10-30 の zeus で、shim の直下に孤児が約 8.2 万本溜まり、木をたどる手間が process の数の 2 乗の
;;     見張りの 1 回が main の scheduler を何十分も塞いだ): 10 万本の孤児の表を尽きかけの機体で 1 回読み、止めるまでを CPU 1 秒未満で終える。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import pytest)
(import time)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ProcRow ReadProcTable])
(import doeff_core_effects [scripted-proc-table-handler])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.core.memory_rules [JobRoot JobSteady KillUnit MemoryLevel MemoryLimits MIB UnitKind _job-uses _kill-units
                                                spared-steady steady-sizes victim-of])
(import doeff_cluster.worker.protocol.observations [ObserveJobRoots])
(import doeff_cluster.worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop MEMORY-WINDOW-LOG JOB-PEAK-LOG
                                                   MEMORY-LEVEL-LOG MEMORY-KILL-LOG MEMORY-NO-VICTIM-LOG MEMORY-UNREAD-LOG MEMORY-SWARM-KILL-LOG])

(val GIB (* 1024 MIB))
;; 見張りの log の行の名(数える行)。
(val WATCH-LOGS #{MEMORY-WINDOW-LOG JOB-PEAK-LOG MEMORY-LEVEL-LOG MEMORY-KILL-LOG MEMORY-NO-VICTIM-LOG MEMORY-UNREAD-LOG MEMORY-SWARM-KILL-LOG})


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


(defk swarm-rows []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "群れの台本の表: 常駐の job svc(shim 10 → uv 11 → 本体 12 の 200 MiB → CLI 100〜139 の 300 MiB ずつ。shim は引き取った道具の孤児
   200〜239 の 200 MiB ずつも持つ)。svc の和 = 10 + 5 + 200 + 40 × 300 + 40 × 200 = 20215 MiB(32 GiB の 62%)。task/1 は無い。"
  (<- tini ProcRow (row 1 0 1 "tini"))
  (<- shim ProcRow (row 10 1 10 "python"))
  (<- uv ProcRow (row 11 10 5 "uv"))
  (<- body ProcRow (row 12 11 200 "python"))
  (var rows #(tini shim uv body))
  (for [n (range 40)]
    (<- cli ProcRow (row (+ 100 n) 12 300 "cli"))
    (<- orphan ProcRow (row (+ 200 n) 10 200 "bash"))
    (:= rows (+ rows #(cli orphan))))
  rows)


(defk swarm-files [available-gib container-gib]
  {:pre [(: available-gib float) (: container-gib int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "機体 32 GiB・空き available-gib・container の memory.current container-gib の file(node-files と同じ形)。"
  (<- files MemoryFiles (node-files available-gib))
  (MemoryFiles :dirs files.dirs
               :files (tuple (gfor file files.files
                                   (if (= file.path "/sys/fs/cgroup/memory.current")
                                       (MemoryFile :path file.path :content (.encode (str (* container-gib GIB))))
                                       file)))))


(defk watched-swarm [settings files run-until-ms]
  {:pre [(: settings MemoryWatchSettings) (: files MemoryFiles) (: run-until-ms int)] :post [(: % WatchSeen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "watched と同じ事を、群れの台本の表(swarm-rows — task/1 は始めから終わっている)で回すため。"
  (<- rows tuple (swarm-rows))
  (val cell (MemoryCell))
  (<- seen WatchSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler rows)
                                     (memory-world cell 0 run-until-ms)]
                       (watch-then-seen settings cell)))
  seen)


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
  (<- seen WatchSeen (watched-in settings files task-ends-ms run-until-ms (MemoryCell)))
  seen)


(defk watched-in [settings files task-ends-ms run-until-ms cell]
  {:pre [(: settings MemoryWatchSettings) (: files MemoryFiles) (: task-ends-ms (| int None)) (: run-until-ms int) (: cell MemoryCell)]
   :post [(: % WatchSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "watched と同じ事を、前の読みを持った入れ物 cell(平常の大きさの元を置いた物)で回すため。"
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
  (<- units tuple (_kill-units #(shim uv body child tool) #((JobRoot :name "solo" :pid 30))))
  (assert (= (tuple (gfor u units #(u.kind u.head.pid (len u.members) (// u.bytes MIB)))) #(#(UnitKind.LEAF 34 1 3000))) units)
  (<- victim (| KillUnit None) (victim-of units #() #() (MemoryLimits)))
  (assert (= victim.head.pid 34) victim))


(deftest test-a-body-that-holds-more-than-its-only-child-is-stopped-at-and-killed-only-when-no-branch-is-large
  ;; 本体 41(6000 MiB)が子 42(300 MiB)より大きい常駐の job: 子へ下りず本体で止まり、子の木(BRANCH 300)は min-victim-bytes 未満なので、
  ;; 本体(BODY)を止める。子が大きければ(700 MiB の 43 を足す)、本体より先に子の木を止める。
  (<- shim ProcRow (row 40 1 10 "python"))
  (<- body ProcRow (row 41 40 6000 "python"))
  (<- small ProcRow (row 42 41 300 "node"))
  (<- big ProcRow (row 43 41 700 "node"))
  (val roots #((JobRoot :name "svc" :pid 40)))
  (<- alone tuple (_kill-units #(shim body small) roots))
  (<- body-first (| KillUnit None) (victim-of alone #() #() (MemoryLimits)))
  (assert (= #(body-first.kind body-first.head.pid) #(UnitKind.BODY 41)) alone)
  (<- with-big tuple (_kill-units #(shim body small big) roots))
  (<- branch-first (| KillUnit None) (victim-of with-big #() #() (MemoryLimits)))
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
  (<- units tuple (_kill-units #(shim uv body child-a child-b orphan) #((JobRoot :name "svc" :pid 50))))
  (assert (= (tuple (gfor u units #(u.kind u.head.pid (// u.bytes MIB))))
             #(#(UnitKind.BODY 52 3000) #(UnitKind.BRANCH 53 900) #(UnitKind.BRANCH 54 800) #(UnitKind.BRANCH 55 600)))
          units)
  (<- victim (| KillUnit None) (victim-of units #() #() (MemoryLimits)))
  (assert (= victim.head.pid 53) victim))


(defk resident-rows [body-mib]
  {:pre [(: body-mib int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "常駐の job dispatch(shim 60 → uv 61 → 本体 62 — 本体は body-mib)と task/7(shim 70 → 本体 71 の 700 MiB)の表を作るため。dispatch の
   平常の大きさは本体 950 MiB の時の 965 MiB。"
  (<- shim ProcRow (row 60 1 10 "python"))
  (<- uv ProcRow (row 61 60 5 "uv"))
  (<- body ProcRow (row 62 61 body-mib "hy"))
  (<- task-shim ProcRow (row 70 1 10 "python"))
  (<- task-body ProcRow (row 71 70 700 "python"))
  #(shim uv body task-shim task-body))


(defk resident-victim [rows roots]
  {:pre [(: rows tuple) (: roots tuple)] :post [(: % (| KillUnit None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "dispatch の平常の大きさ(965 MiB)を持つ見張りが、表 rows・根 roots の時に止める単位を選ぶため。"
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- victim (| KillUnit None) (victim-of units uses #((JobSteady :job "dispatch" :bytes (* 965 MIB))) (MemoryLimits)))
  victim)


(deftest test-a-resident-job-at-its-steady-size-is-not-killed
  ;; 機体が尽きかけでも(victim-of は DANGER の時に呼ばれる)、平常の大きさで動く常駐の job は止める候補でない — 不足は worker の外から。
  (val roots #((JobRoot :name "dispatch" :pid 60)))
  (<- rows tuple (resident-rows 950))
  (<- victim (| KillUnit None) (resident-victim rows roots))
  (assert (is victim None) victim)
  ;; 止めなかった job を名指す。
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- spared tuple (spared-steady units uses #((JobSteady :job "dispatch" :bytes (* 965 MIB))) (MemoryLimits)))
  (assert (= (tuple (gfor s spared s.job)) #("dispatch")) spared))


(deftest test-a-resident-job-at-twice-its-steady-size-is-killed
  (val roots #((JobRoot :name "dispatch" :pid 60)))
  (<- rows tuple (resident-rows 1900))
  (<- victim (| KillUnit None) (resident-victim rows roots))
  (assert (= #(victim.job victim.kind victim.head.pid) #("dispatch" UnitKind.LEAF 62)) victim))


(deftest test-a-large-task-is-killed-beside-a-larger-resident-job-at-its-steady-size
  ;; task は平常の大きさに依らず止める候補: 平常の大きさの dispatch(950 MiB)より小さい task/7(700 MiB)を止める。
  (val roots #((JobRoot :name "dispatch" :pid 60) (JobRoot :name "task/7" :pid 70)))
  (<- rows tuple (resident-rows 950))
  (<- victim (| KillUnit None) (resident-victim rows roots))
  (assert (= #(victim.job victim.kind victim.head.pid) #("task/7" UnitKind.TASK 71)) victim))


(deftest test-a-resident-job-without-a-steady-size-yet-is-still-a-candidate
  ;; 平常の大きさがまだ無い job(起こしてから最大の行の間が 1 つも閉じていない)は、前と同じく止める候補。
  (val roots #((JobRoot :name "dispatch" :pid 60)))
  (<- rows tuple (resident-rows 950))
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- victim (| KillUnit None) (victim-of units uses #() (MemoryLimits)))
  (assert (= victim.head.pid 62) victim))


(deftest test-the-steady-size-is-the-largest-window-maximum-per-job
  (<- steadies tuple (steady-sizes #(#((JobSteady :job "b" :bytes 5) (JobSteady :job "a" :bytes 3)) #() #((JobSteady :job "a" :bytes 9)))))
  (assert (= (tuple (gfor s steadies #(s.job s.bytes))) #(#("a" 9) #("b" 5))) steadies)
  (with [(pytest.raises ValueError)] (MemoryLimits :steady-ratio 1.0)))


(deftest test-a-node-short-of-memory-outside-the-worker-spares-the-steady-job-and-names-the-outside-use
  ;; svc の平常の大きさ(前の間の最大)は今と同じ 2835 MiB: 尽きかけの機体で svc を止めず、平常の大きさの無い task/1 だけを止め、残りは
  ;; 止めずに 1 度言う — 機体の使用量(32 GiB − 1.6 GiB)と container の 4 GiB の差が worker の外の使用量。
  (<- files MemoryFiles (node-files 1.6))
  (val cell (MemoryCell))
  (setv cell.steady #(#((JobSteady :job "svc" :bytes (* 2835 MIB)))))
  (<- seen WatchSeen (watched-in (MemoryWatchSettings) files None 16000 cell))
  (val lines seen.lines)
  (<- kills tuple (lines-named lines MEMORY-KILL-LOG))
  (assert (= (tuple (gfor k kills #((get k "job") (get k "pid") (get k "unit_mib")))) #(#("task/1" 21 700))) kills)
  (<- spared tuple (lines-named lines MEMORY-NO-VICTIM-LOG))
  (assert (= (len spared) 1) spared)
  (val told (get spared 0))
  (val used (// (- (* 32 GIB) (* (int (* 1.6 1024 1024)) 1024)) MIB))
  (assert (= #((get told "spared_steady") (get told "node_used_mib") (get told "container_current_mib") (get told "outside_worker_mib"))
             #("svc=2835/2835" used 4096 (- used 4096)))
          told)
  ;; svc の process は 1 つも止めていない。
  (assert (= (sorted (gfor r seen.table r.pid)) [1 10 11 12 13 14 15 16 20]) seen.table))


(deftest test-the-maximum-of-a-job-killed-in-a-window-is-not-kept-as-its-steady-size
  ;; 10 秒の間: 0 秒に svc の道具・10 秒に task/1 を止めた間の最大は残さない。次の間(15〜25 秒)の止めた後の大きさを残す。
  (<- files MemoryFiles (node-files 1.6))
  (val cell (MemoryCell))
  (<- seen WatchSeen (watched-in (MemoryWatchSettings :window-seconds 10.0) files None 26000 cell))
  (<- kills tuple (lines-named seen.lines MEMORY-KILL-LOG))
  (assert (= (tuple (gfor k kills (get k "job"))) #("svc" "task/1")) kills)
  (assert (= (tuple (gfor window cell.steady (tuple (gfor s window #(s.job (// s.bytes MIB))))))
             #(#() #(#("svc" 835) #("task/1" 10))))
          cell.steady))



(deftest test-many-small-units-of-one-large-resident-job-are-killed-together-until-the-danger-line-returns
  ;; 失敗ケース(card ki-d9953fe1d739): 空き 2.0 GiB / 32 GiB = 6%(DANGER)・container 28 GiB(worker の外 2 GiB)。単位はどれも 512 MiB
  ;; 未満(CLI の木 300 MiB・孤児 200 MiB)で、直す前の見張りは「止める単位が無い」と言うだけで何も止めない。直した見張りは svc の本体を
  ;; 残して、大きい単位(CLI の木)から、空きを DANGER の線(3.2 GiB)の上へ 512 MiB 戻す量(約 1741 MiB)まで 6 本を まとめて止める。
  ;; 5 秒の読みは止めの間隔(10 秒)の中なので止めない(見張りは 5 秒の読みの後に止める)。
  (<- files MemoryFiles (swarm-files 2.0 28))
  (<- seen WatchSeen (watched-swarm (MemoryWatchSettings) files 4000))
  (<- spared tuple (lines-named seen.lines MEMORY-NO-VICTIM-LOG))
  (assert (= spared #()) spared)
  (<- rows tuple (swarm-rows))
  (val gone (sorted (- (sfor r rows r.pid) (sfor r seen.table r.pid))))
  (assert (= gone [100 101 102 103 104 105]) gone)
  (<- swarms tuple (lines-named seen.lines MEMORY-SWARM-KILL-LOG))
  (assert (= (len swarms) 1) swarms)
  (val told (get swarms 0))
  (assert (= #((get told "job") (get told "units") (get told "units_mib") (get told "sent")) #("svc" 6 1800 6)) told))


(deftest test-a-large-resident-job-is-not-swarmed-when-the-use-outside-the-worker-is-larger
  ;; container 6 GiB(worker の外 24 GiB > svc 約 19.7 GiB): 不足は worker の外から来たので、svc の子を止めても戻らない — 止めずに 1 度だけ言う。
  (<- files MemoryFiles (swarm-files 2.0 6))
  (<- seen WatchSeen (watched-swarm (MemoryWatchSettings) files 4000))
  (<- spared tuple (lines-named seen.lines MEMORY-NO-VICTIM-LOG))
  (<- swarms tuple (lines-named seen.lines MEMORY-SWARM-KILL-LOG))
  (assert (= #((len spared) swarms) #(1 #())) seen.lines)
  (<- rows tuple (swarm-rows))
  (assert (= (len seen.table) (len rows)) seen.table))


(defk orphan-flood-rows [orphans]
  {:pre [(: orphans int) (> orphans 0)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "孤児の溢れた表: 常駐の job svc(shim 10 → uv 11 → 本体 12 の 200 MiB → 道具 13 の 3000 MiB)の shim が、引き取った 1 MiB の孤児を
   orphans 本(pid 1000 から)持つ。"
  (+ #((ProcRow :pid 1 :ppid 0 :start-ticks 1 :rss-bytes MIB :comm "tini")
       (ProcRow :pid 10 :ppid 1 :start-ticks 10 :rss-bytes (* 10 MIB) :comm "python")
       (ProcRow :pid 11 :ppid 10 :start-ticks 11 :rss-bytes (* 5 MIB) :comm "uv")
       (ProcRow :pid 12 :ppid 11 :start-ticks 12 :rss-bytes (* 200 MIB) :comm "python")
       (ProcRow :pid 13 :ppid 12 :start-ticks 13 :rss-bytes (* 3000 MIB) :comm "cargo"))
     (tuple (gfor n (range orphans) (ProcRow :pid (+ 1000 n) :ppid 10 :start-ticks (+ 1000 n) :rss-bytes MIB :comm "sleep")))))


(deftest test-one-reading-of-a-hundred-thousand-orphans-holds-the-scheduler-under-a-second [#^ float deadline-scale-factor]
  ;; 失敗ケース(card ki-72f309f23e0f): 2026-10-30 の zeus で、job の shim の直下に孤児が約 8.2 万本溜まり、木をたどる手間が process の数の
  ;; 2 乗だった見張りの 1 回が main の scheduler を何十分も塞いだ(生存の合図が止まり Pod が NotReady)。直す前の手順は 1 万本で約 3 秒・
  ;; 2 万本で約 19 秒。直した見張りは、10 万本の孤児の表で尽きかけの機体を 1 回読み(job の memory・止める単位 10 万余り・止める単位の
  ;; 選び)、uv の木(3205 MiB)を止めるまでを CPU 1 秒未満で終える。
  ;; 閾の伸ばし: CPU の時間も、機体が込んでいると(hyperthread と cache の取り合い)伸びる — 込んだ機体の測りで 1 回 約 1 秒(load 150 /
  ;; 36 CPU)。閾は機体の込み具合の倍率(deadline-scale-factor — 空いた機体では 1)で伸ばす。2 乗の手順は 10 万本で何十分なので、伸ばしても
  ;; 見分けられる。
  (<- files MemoryFiles (node-files 1.6))
  (<- rows tuple (orphan-flood-rows 100000))
  (val cell (MemoryCell))
  (val started (time.process-time))
  (<- seen WatchSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler rows)
                                     (memory-world cell None 0)]
                       (watch-then-seen (MemoryWatchSettings) cell)))
  (val spent (- (time.process-time) started))
  (<- kills tuple (lines-named seen.lines MEMORY-KILL-LOG))
  (assert (= (tuple (gfor k kills #((get k "job") (get k "pid") (get k "unit_mib") (get k "processes") (get k "sent"))))
             #(#("svc" 11 3205 3 3)))
          kills)
  (assert (< spent (* 1.0 deadline-scale-factor)) #(spent deadline-scale-factor)))
