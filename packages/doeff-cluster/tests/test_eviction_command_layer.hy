;; 命令の層(commands)の memory の上限を、MemAvailable と上に載る系の線から掛け直す — 判断・worker の書き・node-a の値の模擬の検。
;;
;; 失敗ケース(11-10 の node-a): 命令の層の memory.max は 48G の固定で、機体の空きを見ない。命令の層が 35.6 GiB を持ち(ターンが走っている会話の
;; 命令は 1 本ぶん)、MemAvailable は新しいターンを受ける係の床(MemTotal の 20 %)の前後に何時間も居続け、新しいターンの入力が 65 分待った。
;;   - 判断(worker/core/eviction_rules の command-layer-use-of・command-layer-limit): 上限 = tmpfs の量 + max(床, 止めて返る量 +
;;     (MemAvailable − 線))を宣言の静的な値で切る。page cache の回収で次の回の上限が下がり続けない・tmpfs の量のために層の process を止めない。
;;   - worker の書き(worker/protocol/eviction_line の commands-limited): 線の宣言の在る機体だけ書く・線を読めない回は宣言の静的な値に戻す・
;;     guard が偽の見張りは書かない・回収で返らない量より下げた続きの頭で 1 行言う。
;;   - 模擬の機体(node-a の値 — MemTotal 125.5 GiB・命令の層の宣言の上限 48G・命令の層の外の使い 69.4 GiB): 本物の commands-limited を 5 秒ごとに
;;     回し、命令の層の file は模擬の kernel が答える。模擬の kernel は使いが書かれた memory.max を越えると、印の付いた長い命令の大きい順に
;;     止め、短い命令は長い命令が尽きるまで止めない。build 4 本(各 8 GiB)を走らせても MemAvailable は線を割らず、短い命令は止まらない。線の
;;     宣言の無い前の形は、同じ機体で MemAvailable が係の床を割る(対照)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [ReadText SlogEffect WriteText])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles memory-file-handler slog-discard-handler state])
(import doeff_time [SimClock sim-time-handler])
(import tests.clock_fixtures [clock-at])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [CommandLayerUse command-layer-limit command-layer-use-of])
(import doeff_cluster._worker.core.room_rules [RoomReading RoomState WorkerRoom room-of-text])
(import doeff_cluster._worker.protocol.eviction_line [COMMANDS-LIMIT-FAILED-LOG COMMANDS-SQUEEZE-LOG CommandLayerSettings EvictionCell EvictionSettings
                                                     BoxLayerLimit commands-limited])
(import doeff_cluster._worker.entry.main [eviction-settings-of])
(import doeff_cluster._worker.protocol.coordinator_link [LinkState room-noted])

(val GIB (* 1024 MIB))
;; 本番 node-a の値: MemTotal・命令の層の宣言の memory.max・短い命令の余地(枠の file の commandFloorMiB)。線 = 係の床 25,696 MiB(MemTotal × 0.20 の
;; 切り捨て)+ ターン 1 本 1,024 MiB × 4 本(上に載る系が書く値)。
(val NODE-A-TOTAL-KIB 131567420)
(val NODE-A-TOTAL (* NODE-A-TOTAL-KIB 1024))
(val NODE-A-CAP (* 48 GIB))
(val NODE-A-COMMAND-FLOOR (* 8 GIB))
(val HOST-FLOOR (* 25696 MIB))
(val LINE-MIB (+ 25696 (* 1024 4)))
(val LINE (* LINE-MIB MIB))
;; 11-10 05:10 の内訳のうち、命令の層の外の使いの和(worker の Pod の残り 22.8・データベース 9.6・ほかの Pod 約 12・container の外 約 25 GiB)。
(val OUTSIDE (int (* 69.4 GIB)))
(val COMMANDS "/host/cgroup/own/commands")
(val LINE-FILE "/cargo-build/.command-keep-available-mib")
(val MEMINFO "/proc/meminfo")
(val SETTINGS (CommandLayerSettings :dir COMMANDS :cap-bytes NODE-A-CAP :meminfo-path MEMINFO))
(val RAMP-MS 60000)
(val TICK-MS 5000)


(defk stat-text [anon file shmem slab]
  {:pre [(: anon int) (: file int) (: shmem int) (: slab int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "命令の層の memory.stat の中身を作るため(file は shmem を含む — cgroup v2 の数え方)。"
  (.format "anon {}\nfile {}\nshmem {}\nslab_reclaimable {}\ninactive_file 0\n" anon file shmem slab))


(defk meminfo-text [available]
  {:pre [(: available int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "MemAvailable が available byte の node-a の /proc/meminfo の中身を作るため。"
  (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" NODE-A-TOTAL-KIB (// available 1024)))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-limit-keeps-the-line-and-stays-inside-the-declared-cap
  (val use (CommandLayerUse :held-bytes (* 20 GIB) :pinned-bytes (* 8 GIB)))
  ;; 線の上に 5 GiB: 止めて返る量 12 GiB + 5 GiB + tmpfs 8 GiB。
  (<- growing int (command-layer-limit use (* 5 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (assert (= growing (* 25 GIB)) (/ growing GIB))
  ;; 線を 2 GiB 割った: 使いより 2 GiB 下げる(kernel が返させる)。
  (<- short int (command-layer-limit use (* -2 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (assert (= short (* 18 GIB)) (/ short GIB))
  ;; 空きが大きくても宣言の静的な値を越えない。
  (<- wide int (command-layer-limit use (* 90 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (assert (= wide NODE-A-CAP) (/ wide GIB)))


(deftest test-the-floor-keeps-room-for-short-commands-when-the-line-is-broken
  ;; 線を大きく割っても、tmpfs の量 + 床までは命令の層に残す。
  (val use (CommandLayerUse :held-bytes (* 20 GIB) :pinned-bytes (* 8 GIB)))
  (<- deep int (command-layer-limit use (* -40 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (assert (= deep (+ (* 8 GIB) NODE-A-COMMAND-FLOOR)) (/ deep GIB)))


(deftest test-tmpfs-is-never-squeezed-because-killing-processes-cannot-return-it
  ;; 失敗ケース: tmpfs の量を上限から引くと、消せない page のために層の process を全部 OOM で止めても目標に届かない。
  (val use (CommandLayerUse :held-bytes (* 14 GIB) :pinned-bytes (* 14 GIB)))
  (<- limit int (command-layer-limit use (* -30 GIB) 0 NODE-A-CAP))
  (assert (>= limit use.pinned-bytes) (/ limit GIB)))


(deftest test-reclaiming-page-cache-does-not-lower-the-next-limit
  ;; 失敗ケース: memory.current を土台にすると、回収した page cache の分だけ上限が下がり、page cache が尽きた後に process を止める。
  (<- before CommandLayerUse (command-layer-use-of (str (* 30 GIB)) (! (stat-text (* 18 GIB) (* 10 GIB) (* 8 GIB) GIB))))
  (<- after CommandLayerUse (command-layer-use-of (str (* 28 GIB)) (! (stat-text (* 18 GIB) (* 8 GIB) (* 8 GIB) GIB))))
  (assert (= before after) #(before after))
  (assert (= #(before.held-bytes before.pinned-bytes) #((* 27 GIB) (* 8 GIB))) before)
  (<- first int (command-layer-limit before (* -3 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (<- second int (command-layer-limit after (* -3 GIB) NODE-A-COMMAND-FLOOR NODE-A-CAP))
  (assert (= first second) #(first second)))


(deftest test-the-layer-use-refuses-readings-that-are-not-numbers
  (var refused False)
  (try
    (<- (command-layer-use-of "max" (! (stat-text 0 0 0 0))))
    (except [ValueError]
      (:= refused True)))
  (assert refused)
  ;; tmpfs の行が読めない memory.stat は 0 と読まない。
  (:= refused False)
  (try
    (<- (command-layer-use-of "10" "anon 1\nfile 2\n"))
    (except [ValueError]
      (:= refused True)))
  (assert refused))


;; --- 枠の file --------------------------------------------------------------------------------------------------------------------------

(deftest test-the-room-file-row-carries-the-command-line-and-floor-as-a-pair
  (val path "/cargo-build/.command-keep-available-mib")
  (<- read RoomReading (room-of-text (json.dumps {"node-a" {"memoryMiB" 1 "commandLineFile" path "commandFloorMiB" 8192}}) "node-a"))
  (assert (= read.room (WorkerRoom :memory-mib 1 :command-line-file path :command-floor-mib 8192)) read)
  ;; 欄の無い行は掛け直さない。
  (<- plain RoomReading (room-of-text (json.dumps {"node-a" {"memoryMiB" 1}}) "node-a"))
  (assert (= #(plain.room.command-line-file plain.room.command-floor-mib) #("" 0)) plain)
  ;; 片方だけ・形の違う値は malformed(黙って掛け直さない側へ倒れない)。
  (for [fields [{"commandLineFile" path} {"commandFloorMiB" 8192} {"commandLineFile" "x" "commandFloorMiB" 1}
                {"commandLineFile" path "commandFloorMiB" -1} {"commandLineFile" path "commandFloorMiB" True}]]
    (<- wrong RoomReading (room-of-text (json.dumps {"node-a" {"memoryMiB" 1 #** fields}}) "node-a"))
    (assert (= wrong.state RoomState.MALFORMED) #(fields wrong))))


(deftest test-the-worker-hands-the-room-file-command-floor-to-the-watch-in-bytes
  ;; 枠の file の行の commandFloorMiB を、worker の本物の読み直し(coordinator_link の room-noted)が見張りの入れ物へ byte で渡す。
  ;; 失敗ケース(11-10 の node-c): MIB を import していない版の worker は、起動の直後の読み直しで NameError に落ちた。
  (val path "/cargo-build/.command-keep-available-mib")
  (val files (MemoryFiles :dirs #("/etc") :files #((MemoryFile :path "/etc/worker-room.json"
                                                               :content (.encode (json.dumps {"node-a" {"memoryMiB" 1 "commandLineFile" path
                                                                                                      "commandFloorMiB" 8192}}))))))
  (val cell (EvictionCell))
  (val link (LinkState "node-a" #("cpu") 10000 "/var/tasks" "boot" 0 0 0 :room-file "/etc/worker-room.json" :eviction cell))
  ;; room-noted は読み直しの刻を GetTime で読む(時計の無い検は単位の換算の前に UnhandledEffect で止まり、MIB の import 漏れを見ない)。
  ;; 刻はこの検の固定の 1 点(2026-11-10 02:00 UTC)— 前に読んだ刻が無い(room-read-ms = None)ので、読み直しの判断は必ず読む側に倒れる。
  (<- clock SimClock (clock-at 1794276000000))
  (<- (with-handlers [(sim-time-handler :clock clock) (state) slog-discard-handler (memory-file-handler files)] (room-noted link)))
  (assert (= #(cell.command-line-file cell.command-floor-bytes) #(path (* 8192 MIB))) #(cell.command-line-file cell.command-floor-bytes)))


(deftest test-the-room-file-row-declares-the-command-line-and-floor
  ;; 枠の file の 1 行(現場の宣言と同じ形の例 — 現場の枠の file は上に載る系の repo が持つ)。
  (val text (json.dumps {"node-a" {"warmServices" True "memoryMiB" 63488 "keepLineFile" "/cargo-build/.keep-available-mib"
                                   "commandLineFile" LINE-FILE "commandFloorMiB" (// NODE-A-COMMAND-FLOOR MIB)}}))
  (<- read RoomReading (room-of-text text "node-a"))
  (assert (= read.state RoomState.OK) read)
  (assert (= #(read.room.command-line-file read.room.command-floor-mib) #(LINE-FILE (// NODE-A-COMMAND-FLOOR MIB))) read))


(deftest test-a-layout-with-a-limited-command-layer-gets-the-settings
  (val env {"WORKER_CGROUP_HOST" "/host/cgroup"
            "DOEFF_WORKER_CGROUP_DIR" "/host/cgroup/kubepods.slice/a.scope"
            "DOEFF_WORKER_JOB_CGROUP" "jobs"
            "WORKER_CGROUP_LAYOUT" "jobs:memory.max=36G commands:memory.max=48G"})
  (<- found (| EvictionSettings None) (eviction-settings-of env "node-a" True))
  (assert (= #(found.commands.dir found.commands.cap-bytes)
             #("/host/cgroup/kubepods.slice/a.scope/commands" NODE-A-CAP))
          found)
  ;; 命令の層が無い(node-b の形)・memory.max が無い機体は掛け直さない。
  (<- absent (| EvictionSettings None) (eviction-settings-of (| env {"WORKER_CGROUP_LAYOUT" "jobs:memory.max=36G"}) "node-b" True))
  (assert (is absent.commands None) absent))


;; --- worker の書き(commands-limited)--------------------------------------------------------------------------------------------------

(defk layer-files [held available line]
  {:pre [(: held int) (: available int) (: line (| str None))] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "命令の層の file と meminfo と線の file を作るため(使いは全部 anon・line = 線の file の中身・None = file が無い)。"
  (val files #((MemoryFile :path (+ COMMANDS "/memory.current") :content (.encode (str held)))
               (MemoryFile :path (+ COMMANDS "/memory.stat") :content (.encode (! (stat-text held 0 0 0))))
               (MemoryFile :path (+ COMMANDS "/memory.max") :content (.encode (str NODE-A-CAP)))
               (MemoryFile :path MEMINFO :content (.encode (! (meminfo-text available))))))
  (MemoryFiles :dirs #("/host" "/host/cgroup" "/host/cgroup/own" COMMANDS "/proc" "/cargo-build")
               :files (+ files (if (is line None) #() #((MemoryFile :path LINE-FILE :content (.encode line)))))))


(defk armed-cell [line-file]
  {:pre [(: line-file str)] :post [(: % EvictionCell)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "枠の file の行から線の file と床を受けた入れ物を作るため。"
  (val cell (EvictionCell))
  (setv cell.command-line-file line-file cell.command-floor-bytes NODE-A-COMMAND-FLOOR)
  cell)


(defk limited-then-files [cell guard]
  {:pre [(: cell EvictionCell) (: guard bool)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "命令の層を 1 回掛け直して模擬の file を読むため。"
  (<- (commands-limited SETTINGS cell guard))
  (<- files MemoryFiles (ReadMemoryFiles))
  files)


(defk written-limit [files]
  {:pre [(: files MemoryFiles)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file から命令の層の memory.max の中身を読むため。"
  (int (.decode (. (next (gfor f files.files :if (= f.path (+ COMMANDS "/memory.max")) f)) content) "utf-8")))


(deftest test-the-worker-writes-the-limit-from-the-available-memory-and-the-line
  (<- start MemoryFiles (layer-files (* 20 GIB) (+ LINE (* 5 GIB)) (.format "{}\n" LINE-MIB)))
  (<- cell EvictionCell (armed-cell LINE-FILE))
  (<- files MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files cell True)))
  (<- limit int (written-limit files))
  (assert (= limit (* 25 GIB)) (/ limit GIB))
  (assert (= cell.command-limit (* 25 GIB)) cell.command-limit))


(deftest test-the-line-limit-never-writes-above-the-limit-the-container-room-was-counted-with
  ;; 同じ機体で命令の層が container の中の限る層としても掛け直された回(入れ物の box-limits — worker/core/eviction_rules の頭の註の
  ;; container の中の限る層)は、線から決まる上限 25 GiB がその値(10 GiB)を越えても、その値で切る(container の中の空きを数えた上限の
  ;; 上へ書くと、同じ空きを jobs の層にも貸す)。
  (<- start MemoryFiles (layer-files (* 20 GIB) (+ LINE (* 5 GIB)) (.format "{}\n" LINE-MIB)))
  (<- cell EvictionCell (armed-cell LINE-FILE))
  (setv cell.box-limits #((BoxLayerLimit :name "commands" :limit-bytes (* 10 GIB))))
  (<- files MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files cell True)))
  (<- limit int (written-limit files))
  (assert (= limit (* 10 GIB)) (/ limit GIB))
  ;; ほかの層の名の値では切らない。
  (<- other EvictionCell (armed-cell LINE-FILE))
  (setv other.box-limits #((BoxLayerLimit :name "exec" :limit-bytes GIB)))
  (<- other-files MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files other True)))
  (<- other-limit int (written-limit other-files))
  (assert (= other-limit (* 25 GIB)) (/ other-limit GIB)))


(deftest test-a-machine-without-the-line-or-a-guard-off-watch-writes-nothing
  (<- start MemoryFiles (layer-files (* 20 GIB) LINE (.format "{}\n" LINE-MIB)))
  ;; 枠の file に線の宣言の無い機体(今までの形)は書かない。
  (<- plain MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files (EvictionCell) True)))
  (<- plain-limit int (written-limit plain))
  (assert (= plain-limit NODE-A-CAP) (/ plain-limit GIB))
  ;; guard が偽の見張りは書かない。
  (<- cell EvictionCell (armed-cell LINE-FILE))
  (<- off MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files cell False)))
  (<- off-limit int (written-limit off))
  (assert (= off-limit NODE-A-CAP) (/ off-limit GIB)))


(deftest test-an-unreadable-line-puts-the-layer-back-on-the-declared-cap
  ;; 失敗ケース: 係が起きていない間に、前の回の小さい上限で命令の層を縛り続けない。
  (for [line [None "not-a-number\n"]]
    (<- start MemoryFiles (layer-files (* 20 GIB) (* 2 GIB) line))
    (<- cell EvictionCell (armed-cell LINE-FILE))
    (setv cell.command-limit (* 10 GIB))
    (<- files MemoryFiles (with-handlers [(state) (memory-file-handler start) slog-discard-handler] (limited-then-files cell True)))
    (<- limit int (written-limit files))
    (assert (= limit NODE-A-CAP) #(line limit))))


(defclass ToldLines []  ; class にする理由: log の答え手が溜める入れ物(答え手の節と検の本体が同じ物を読み書きする)
  "log の行 #(名 欄の dict) の列を持つ入れ物。"
  (defn #^ None __init__ [self]
    (setv self.lines #())))


(defhandler recording-log [#^ ToldLines told]
  "log の行を #(名 欄の dict) で溜める。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 溜める入れ物は検ごとに決める値(Ask で読む設定ではない)。
  (SlogEffect []
    (setv told.lines (+ told.lines #(#(effect.msg (dict effect.kwargs)))))
    (resume None)))


(deftest test-a-squeeze-is-told-once-per-stretch-and-a-write-failure-by-its-reason
  ;; 線を 8 GiB 割った(上限 = 床の側へ下がる)— 2 回続いても 1 行。
  (<- start MemoryFiles (layer-files (* 20 GIB) (- LINE (* 8 GIB)) (.format "{}\n" LINE-MIB)))
  (<- cell EvictionCell (armed-cell LINE-FILE))
  (val told-lines (ToldLines))
  (<- (with-handlers [(state) (memory-file-handler start) (recording-log told-lines)]
        (twice cell)))
  (val told (lfor #(msg fields) told-lines.lines :if (= msg COMMANDS-SQUEEZE-LOG) fields))
  (assert (= (len told) 1) told)
  (assert (= (get (get told 0) "limit_mib") (// (* 12 GIB) MIB)) told)
  (assert cell.command-squeezed)
  ;; 層の dir が無い機体の書きの失敗は訳を 1 行言い、落ちない。
  (val empty (MemoryFiles :dirs #("/proc" "/cargo-build") :files #((MemoryFile :path MEMINFO :content (.encode (! (meminfo-text LINE))))
                                                                  (MemoryFile :path LINE-FILE :content b"29792\n"))))
  (<- broken EvictionCell (armed-cell LINE-FILE))
  (val failed (ToldLines))
  (<- (with-handlers [(state) (memory-file-handler empty) (recording-log failed)]
        (commands-limited SETTINGS broken True)))
  (assert (= (len (lfor #(msg _) failed.lines :if (= msg COMMANDS-LIMIT-FAILED-LOG) msg)) 1) failed.lines)
  (assert (is broken.command-limit None) broken.command-limit))


(defk twice [cell]
  {:pre [(: cell EvictionCell)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "命令の層を 2 回続けて掛け直すため。"
  (<- (commands-limited SETTINGS cell True))
  (<- (commands-limited SETTINGS cell True))
  None)


;; --- 模擬の node-a ------------------------------------------------------------------------------------------------------------------------

(defrecord SimCommand
  "模擬の命令 1 本: name = 名・long = 長い命令の印(上に載る系が付ける — 模擬の kernel が先に止める)・bytes = 伸びきった使い・from-ms と until-ms = 走る間。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ str name)
  (#^ bool long)
  (#^ int bytes)
  (#^ int from-ms)
  (#^ int until-ms))


(defclass ZeusWorld []  ; class にする理由: 模擬の kernel が刻ごとに書き換える入れ物(答え手の節と判断の defk が同じ物を読み書きする)
  "模擬の node-a の様子: outside = 命令の層の外の使い(刻 ms から byte を返す関数)・commands = 命令の列・alive = 生きている命令の名・limit = 命令の層の
   memory.max・now = 刻・killed = 止めた命令の名の列・lowest = MemAvailable の最小。"
  (defn #^ None __init__ [self outside #^ tuple commands]
    (setv self.outside outside self.commands commands self.alive (sfor c commands c.name) self.limit NODE-A-CAP self.now 0 self.killed #()
          self.lowest NODE-A-TOTAL)))


;; 刻 now の命令 1 本の使い(RAMP-MS で伸びきり、走る間の外は 0)。内包の中で呼ぶので効果の無い関数の値にする。
(val command-use (fn [command now]
                   (if (<= command.from-ms now command.until-ms)
                       (// (* command.bytes (min RAMP-MS (- now command.from-ms))) RAMP-MS)
                       0)))


(defk layer-use [world]
  {:pre [(: world ZeusWorld)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "今の命令の層の使いを求めるため(生きている命令の和)。"
  (sum (gfor c world.commands :if (in c.name world.alive) (command-use c world.now))))


(defk kernel-stepped [world]
  {:pre [(: world ZeusWorld)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "層の使いが memory.max を越えている間、長い命令の大きい順に止め(短い命令は長い命令が尽きてから)、MemAvailable の最小を残すため。"
  (while True
    (<- used int (layer-use world))
    (when (<= used world.limit)
      (break))
    (val running (lfor c world.commands :if (and (in c.name world.alive) (> (command-use c world.now) 0)) c))
    (when (not running)
      (break))
    (val victim (max running :key (fn [c] #(c.long (command-use c world.now)))))
    (.discard world.alive victim.name)
    (setv world.killed (+ world.killed #(victim.name))))
  (<- used int (layer-use world))
  (setv world.lowest (min world.lowest (- NODE-A-TOTAL (world.outside world.now) used)))
  None)


(defhandler node-a-kernel [#^ ZeusWorld world]
  "模擬の node-a の kernel: 命令の層の file・/proc/meminfo・線の file を今の様子で答え、memory.max の書きで層の中の process を止める。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 模擬の機体の入れ物は検ごとに決める値(Ask で読む設定ではない)。
  (ReadText [path]
    :when (in path #(MEMINFO LINE-FILE (+ COMMANDS "/memory.current") (+ COMMANDS "/memory.stat")))
    (<- used int (layer-use world))
    (val available (- NODE-A-TOTAL (world.outside world.now) used))
    (resume (cond (= path MEMINFO) (! (meminfo-text available))
                  (= path LINE-FILE) (.format "{}\n" LINE-MIB)
                  (= path (+ COMMANDS "/memory.current")) (str used)
                  True (! (stat-text used 0 0 0)))))
  (WriteText [path text]
    :when (= path (+ COMMANDS "/memory.max"))
    (setv world.limit (int text))
    (<- (kernel-stepped world))
    (resume None)))


(defk scene-run [world cell until-ms]
  {:pre [(: world ZeusWorld) (: cell EvictionCell) (: until-ms int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "刻を 5 秒ずつ進めて、そのたびに本物の commands-limited を回すため(進んだ後は書かれた上限のまま kernel が止める)。"
  (while (< world.now until-ms)
    (setv world.now (+ world.now TICK-MS))
    (<- (kernel-stepped world))
    (<- (commands-limited SETTINGS cell True)))
  None)


(val FOUR-BUILDS (tuple (gfor i (range 4) (SimCommand :name (.format "build-{}" i) :long True :bytes (* 8 GIB) :from-ms 0 :until-ms 3600000))))
(val SHORT-COMMAND (SimCommand :name "short" :long False :bytes (* 2 GIB) :from-ms 100000 :until-ms 150000))


(defk scene-of [outside commands line-file]
  {:pre [(: outside Callable) (: commands tuple) (: line-file str)] :post [(: % ZeusWorld)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の node-a を 600 秒まわした後の世界を答えるため(line-file = \"\" は線の宣言の無い前の形)。"
  (val world (ZeusWorld outside commands))
  (<- cell EvictionCell (armed-cell line-file))
  (<- (with-handlers [(state) slog-discard-handler (node-a-kernel world)]
        (scene-run world cell 600000)))
  world)


(deftest test-four-builds-cannot-take-the-room-for-new-turns
  (<- world ZeusWorld (scene-of (fn [_] OUTSIDE) (+ FOUR-BUILDS #(SHORT-COMMAND)) LINE-FILE))
  ;; MemAvailable は線を割らない — 新しいターンを受ける係の床の上にターン 4 本ぶんが残る。
  (assert (>= world.lowest LINE) (/ (- world.lowest LINE) GIB))
  (assert (>= (- world.lowest HOST-FLOOR) (* 4 GIB)) (/ (- world.lowest HOST-FLOOR) GIB))
  ;; 譲るのは長い命令で、短い命令は止めない。
  (assert (in "short" world.alive) world.killed)
  (assert (>= (len world.killed) 1) world.killed)
  (assert (not-in "short" world.killed) world.killed)
  ;; 前の形(線の宣言が無い — 48G の固定): 誰も止まらず、MemAvailable は係の床を割る(新しいターンを受ける余白が 0)。
  (<- before ZeusWorld (scene-of (fn [_] OUTSIDE) (+ FOUR-BUILDS #(SHORT-COMMAND)) ""))
  (assert (= before.killed #()) before.killed)
  (assert (< before.lowest HOST-FLOOR) (/ (- before.lowest HOST-FLOOR) GIB)))


;; 刻 now の命令の層の外の使い: 120 秒から 30 秒で 25 GiB 跳ねる(ほかの Pod・常駐の job の山)。模擬の kernel が刻ごとに呼ぶので効果の無い関数の値にする。
(val outside-spike (fn [now] (+ OUTSIDE (// (* 25 GIB (max 0 (min 30000 (- now 120000)))) 30000))))


(deftest test-an-outside-spike-is-paid-by-the-long-commands-not-the-short-one
  (<- world ZeusWorld (scene-of outside-spike (+ FOUR-BUILDS #(SHORT-COMMAND)) LINE-FILE))
  ;; 長い命令は全部止まっても、短い命令(床の内)は止まらない。
  (assert (in "short" world.alive) world.killed)
  (assert (= (len world.killed) 4) world.killed))
