;;; 機体の memory の見張り(card ki-7b934e2538f9)— 調整ループの外の背景の task(Spawn の daemon)が sample-seconds ごとに、機体の
;;; memory(/proc/meminfo・/proc/pressure/memory)・worker の container の memory(cgroup v2)・job ごとの memory(process の表を job の根の
;;; 木で数える)を読み、worker の log に残す。機体の memory が尽きかけたら(worker/core/memory_rules の DANGER)、job の木の中のいちばん
;;; 大きい単位を SIGKILL で止める — kernel の OOM に至らない reclaim の thrash で機体の全体が止まる前に。
;;;
;;; log の行(名 + 欄の形):
;;;   MEMORY-WINDOW-LOG  window-seconds ごとに 1 行: その間の job ごとの memory の最大(report-floor-bytes 以上・大きい順に report-jobs 個)・
;;;                      機体の空きの最低・PSI の full avg10 の最大・container の memory.current と memory.peak。5 秒ごとの読みの最大なので、
;;;                      1 分の跳ねも落ちない(10 分ごとの 1 点の測りでは落ちた)。
;;;   JOB-PEAK-LOG       job が終わった(根が観測から消えた)時に 1 行: その job の memory の最大と、最大を読んだ刻。
;;;   MEMORY-LEVEL-LOG   機体の様子(CALM・TIGHT・DANGER)が変わった時に 1 行: 機体の空き・PSI・大きい単位の上位。
;;;   MEMORY-KILL-LOG    止めた時に 1 行: job・単位の頭の pid と命令の名・単位の memory・送った process の数・機体の空き。
;;;   MEMORY-SWARM-KILL-LOG  止める候補の単位が無く、1 つの job の木が機体の大きな割合を持つ時に、その job の小さい単位をまとめて止めた
;;;                      時に 1 行(worker/core/memory_rules の swarm-of — card ki-d9953fe1d739): job・止めた単位の数と和・大きい単位の
;;;                      上位・送った process の数・機体の空き。
;;;   MEMORY-NO-VICTIM-LOG  DANGER なのに止める単位が無い(止める候補の単位が無いか min-victim-bytes 未満で、群れの止めも当たらない — 食った
;;;                      物は job の外か、平常の大きさで動く小さい常駐の job だけ)時に、DANGER の 1 回につき 1 行: 機体の使用量・container の
;;;                      memory・その差(worker の外の使用量)と、平常の大きさなので止めなかった job。
;;;   MEMORY-UNREAD-LOG  読めない訳が変わった時に 1 行(読めない間は数えない・止めない)。
;;; 止めは guard が真の時だけ(偽は log だけ — 起動の引数 --memory-guard off)。止めた後 kill-cooldown-seconds は次を止めない(止めた木の
;;; memory が戻るのを待つ)。
;;; 平常の大きさ(card ki-649b1b2f5838): 最大の行の間を閉じるたびに、その間の job の名ごとの最大を steady-windows 個まで残し(見張りが
;;; 止めた job の最大は残さない)、その中のいちばん大きい物を job の平常の大きさとして止める単位の選びに渡す(worker/core/memory_rules の
;;; victim-of — 平常の大きさで動く常駐の job は止めない)。名ごとに残すので、起こし直された job も前の走りの大きさを平常とする。
;;; 止めた印: 止めた単位の属する job の根(名と pid)を、止めた刻とともに入れ物に kill-mark-seconds の間残す。調整ループは終わった process
;;; を回収する時にこの印を読み(worker/protocol/memory_observer)、見張りに止められた終わりを数える — 続けて止められた常駐の job は
;;; 起こし直さずに失敗として報告する(WorkerPolicy.memory-kill-limit)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.process_effects [ProcessSignal])
(import doeff_core_effects.proc_table_effects [ReadProcTable SignalPid])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.worker.protocol.observations [ObserveJobRoots])
(import doeff_cluster.worker.core.memory_rules [MemoryLevel MemoryLimits NodeMemory ContainerMemory KillUnit JobUse JobSteady node-memory-of
                                                cgroup-bytes-of memory-level job-uses kill-units steady-sizes spared-steady victim-of swarm-of])

(val MEMORY-WINDOW-LOG "worker: job ごとの memory")
(val JOB-PEAK-LOG "worker: job の memory の最大")
(val MEMORY-LEVEL-LOG "worker: 機体の memory の様子")
(val MEMORY-KILL-LOG "worker: 機体の memory の尽きかけで止めた")
(val MEMORY-SWARM-KILL-LOG "worker: 機体の memory の尽きかけで job の小さい単位をまとめて止めた")
(val MEMORY-NO-VICTIM-LOG "worker: 機体の memory が尽きかけだが止める単位が無い")
(val MEMORY-UNREAD-LOG "worker: 機体の memory を読めない")
;; 様子の行に並べる大きい単位の数。
(val LEVEL-UNITS 5)


(defrecord MemoryWatchSettings
  "見張りの設定(頭の註): meminfo-path・pressure-path・cgroup-dir = 読む file の置き場・sample-seconds = 読みの間隔・window-seconds = job ごとの
   最大の行の間隔・kill-cooldown-seconds = 止めた後に次を止めない秒・guard = 止めるか(偽は log だけ)・limits = 様子の閾・report-floor-bytes =
   最大の行に載せる job の最低・report-jobs = 最大の行に載せる job の数の上限・kill-mark-seconds = 止めた印を残す秒(調整ループが止めた
   process の終わりを回収するまでの間を十分に覆う)・steady-windows = 平常の大きさに数える最大の行の間の数(頭の註 — 既定 12 は 1 時間)。"
  {:check [(> sample-seconds 0) (>= window-seconds sample-seconds) (>= kill-cooldown-seconds 0) (>= report-floor-bytes 0) (> report-jobs 0)
           (> kill-mark-seconds 0) (> steady-windows 0)]}
  (setv #^ str meminfo-path "/proc/meminfo")
  (setv #^ str pressure-path "/proc/pressure/memory")
  (setv #^ str cgroup-dir "/sys/fs/cgroup")
  (setv #^ float sample-seconds 5.0)
  (setv #^ float window-seconds 300.0)
  (setv #^ float kill-cooldown-seconds 10.0)
  (setv #^ bool guard True)
  (setv #^ MemoryLimits limits (MemoryLimits))
  (setv #^ int report-floor-bytes (* 64 1024 1024))
  (setv #^ int report-jobs 12)
  (setv #^ float kill-mark-seconds 600.0)
  (setv #^ int steady-windows 12))


(defrecord MemoryKill
  "見張りが止めた単位の属する job の根 1 つの印: job = job(process)の名・pid = 根の pid・at-ms = 止めた刻(epoch ms — 印を残す間を数える)。"
  (#^ str job)
  (#^ int pid)
  (#^ int at-ms))


(defrecord JobPeak
  "job 1 つ(根の pid ごと)の memory の最大: job = job の名・pid = 根の pid・bytes = 最大の値・at-ms = 最大を読んだ刻(epoch ms)。"
  (#^ str job)
  (#^ int pid)
  (#^ int bytes)
  (#^ int at-ms))


(defrecord JobMax
  "最大の行の間の job 1 つ(名ごと)の memory の最大: job = job の名・bytes = 最大の値。"
  (#^ str job)
  (#^ int bytes))


(defrecord WindowMarks
  "最大の行の間の読みの最大と最低: since-ms = 間の始まり(epoch ms)・jobs = job の名ごとのその間の最大(JobMax の列)・min-available =
   機体の空きの最低・max-full = PSI の full avg10 の最大(PSI の無い機体は None)。"
  (#^ int since-ms)
  (#^ (get tuple #(JobMax ...)) jobs)
  (#^ int min-available)
  (#^ (| float None) max-full))


(defclass MemoryCell []
  "見張りの背景の task と組み立て(main)が分ける値。closing = 止めの合図・level = 前の読みの機体の様子・peaks = 動いている job の根ごとの
   最大(JobPeak の列 — 終わった job の最大の行を出して除く)・window = 最大の行の間の印(None = まだ読んでいない)・
   last-kill-ms = 最後に止めた刻・told-no-victim = 今の DANGER で止める単位が無いと言った・unread = 前の読めない訳("" = 読めた)・
   failure = 前の読みで起きた思わぬ例外の repr(同じ例外は 2 度言わない)・kills = 止めた印(MemoryKill の列 — kill-mark-seconds を
   過ぎた物は読みごとに除く・調整ループが memory_observer で読む)・steady = 閉じた最大の行の間ごとの job の最大(JobSteady の列の列 —
   新しい間が後・steady-windows 個まで)・window-killed = 今の最大の行の間に見張りが止めた job の名(その間の最大を steady に残さない)。
   scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.closing False self.level MemoryLevel.CALM self.peaks #() self.window None self.last-kill-ms None
          self.told-no-victim False self.unread "" self.failure "" self.kills #() self.steady #() self.window-killed (frozenset))))


(defrecord MemoryReading
  "1 回の読み: node = 機体・container = worker の container・roots = job の根(JobRoot の列)・rows = process の表。"
  (#^ NodeMemory node)
  (#^ ContainerMemory container)
  (#^ tuple roots)
  (#^ tuple rows))


(defrecord MemoryUnreadable
  "1 回の読みが機体の memory を読めなかった(reason = 訳)。"
  (#^ str reason))


(defk text-or-none [path]
  {:pre [(: path str)] :post [(: % (| str None))] :tags {:context "worker" :role "protocol"}}
  "file を 1 つ読み、読めなければ None にするため(PSI の無い機体・memory.peak の無い kernel の file — 呼び手が None の意味を決める)。"
  (<- text (| str FileFailed) (ReadText path))
  (if (isinstance text str) text None))


(defk memory-reading [settings]
  {:pre [(: settings MemoryWatchSettings)] :post [(: % (| MemoryReading MemoryUnreadable))] :tags {:context "worker" :role "protocol"}}
  "機体・container・job の根・process の表を 1 度ずつ読むため。/proc/meminfo が読めない・形が違う時は MemoryUnreadable(数えない・止めない)。"
  (<- meminfo (| str FileFailed) (ReadText settings.meminfo-path))
  (when (not (isinstance meminfo str))
    (return (MemoryUnreadable :reason (.format "{} を読めない: {}" settings.meminfo-path meminfo))))
  (<- pressure (| str None) (text-or-none settings.pressure-path))
  (var node None)
  (try
    (<- read NodeMemory (node-memory-of meminfo pressure))
    (:= node read)
    (except [error ValueError]
      (return (MemoryUnreadable :reason (str error)))))
  (<- current-text (| str None) (text-or-none (+ settings.cgroup-dir "/memory.current")))
  (<- peak-text (| str None) (text-or-none (+ settings.cgroup-dir "/memory.peak")))
  (<- current (| int None) (cgroup-bytes-of current-text))
  (<- peak (| int None) (cgroup-bytes-of peak-text))
  (<- roots tuple (ObserveJobRoots))
  (<- rows tuple (ReadProcTable))
  (MemoryReading :node node :container (ContainerMemory :current-bytes current :peak-bytes peak) :roots roots :rows rows))


(defk units-text [units count]
  {:pre [(: units tuple) (: count int)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "大きい単位の上位 count 個を log の欄 1 つの綴り(<job>:<頭の pid>:<命令の名>=<MiB> を空白で並べる)にするため。"
  (.join " " (gfor unit (cut units count) (.format "{}:{}:{}={}" unit.job unit.head.pid unit.head.comm (// unit.bytes (* 1024 1024))))))


(defk raised-peak [peaks use now-ms]
  {:pre [(: peaks tuple) (: use JobUse) (: now-ms int)] :post [(: % JobPeak)] :tags {:context "worker" :role "protocol"}}
  "job 1 つの今の読み use を、前の最大(peaks の中の同じ名と根の pid の物 — 無ければ今の読み)と比べた最大にするため。"
  (val before (next (gfor peak peaks :if (and (= peak.job use.job) (= peak.pid use.pid)) peak) None))
  (if (or (is before None) (> use.bytes before.bytes))
      (JobPeak :job use.job :pid use.pid :bytes use.bytes :at-ms now-ms)
      before))


(defk noted-peaks [cell uses roots now-ms]
  {:pre [(: cell MemoryCell) (: uses tuple) (: roots tuple) (: now-ms int)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "job ごとの最大を今の読みで更新し、根が観測から消えた job(終わった・入れ替えで退いて終わった)の最大を 1 行にして除くため。"
  (var raised #())
  (for [use uses]
    (<- peak JobPeak (raised-peak cell.peaks use now-ms))
    (:= raised (+ raised #(peak))))
  (val counted (sfor peak raised #(peak.job peak.pid)))
  (val kept (+ raised (tuple (gfor peak cell.peaks :if (not-in #(peak.job peak.pid) counted) peak))))
  (val alive (sfor root roots #(root.name root.pid)))
  (for [ended (sorted (gfor peak kept :if (not-in #(peak.job peak.pid) alive) peak) :key (fn [peak] #(peak.job peak.pid)))]
    (<- (slog JOB-PEAK-LOG :level "info" :job ended.job :pid ended.pid :peak-mib (// ended.bytes (* 1024 1024))
              :at (.isoformat (datetime-of-epoch-ms ended.at-ms)))))
  (setv cell.peaks (tuple (gfor peak kept :if (in #(peak.job peak.pid) alive) peak)))
  None)


(defk merged-maxima [maxima uses]
  {:pre [(: maxima tuple) (: uses tuple)] :post [(: % tuple)] :tags {:context "worker" :role "protocol"}}
  "最大の行の間の job の名ごとの最大 maxima(JobMax の列)に、今の読み uses を足した最大の列にするため(名の昇順)。"
  (val names (sorted (| (sfor m maxima m.job) (sfor use uses use.job))))
  (tuple (gfor name names
               (JobMax :job name :bytes (max (+ (lfor m maxima :if (= m.job name) m.bytes) (lfor use uses :if (= use.job name) use.bytes)))))))


(defk noted-window [settings cell reading uses now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: uses tuple) (: now-ms int)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "最大の行の間の印に今の読みを足し、間が window-seconds を越えたら 1 行にし、間の job の最大を平常の大きさの元に残して(この間に
   止めた job は除く)新しい間を始めるため。"
  (val node reading.node)
  (val marks (if (is cell.window None)
                 (WindowMarks :since-ms now-ms :jobs #() :min-available node.available-bytes :max-full node.full-avg10)
                 cell.window))
  (<- jobs tuple (merged-maxima marks.jobs uses))
  (val full (cond (is marks.max-full None) node.full-avg10
                  (is node.full-avg10 None) marks.max-full
                  True (max marks.max-full node.full-avg10)))
  (val merged (WindowMarks :since-ms marks.since-ms :jobs jobs :min-available (min marks.min-available node.available-bytes) :max-full full))
  (if (< (- now-ms merged.since-ms) (* settings.window-seconds 1000))
      (setv cell.window merged)
      (do (val listed (cut (sorted (gfor m jobs :if (>= m.bytes settings.report-floor-bytes) m) :key (fn [m] #((- m.bytes) m.job)))
                           settings.report-jobs))
          (<- (slog MEMORY-WINDOW-LOG :level "info" :seconds (// (- now-ms merged.since-ms) 1000)
                    :jobs (.join " " (gfor m listed (.format "{}={}" m.job (// m.bytes (* 1024 1024)))))
                    :jobs-not-listed (- (len jobs) (len listed))
                    :node-min-available-mib (// merged.min-available (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
                    :max-full-avg10 merged.max-full
                    :container-current-mib (if (is reading.container.current-bytes None) None (// reading.container.current-bytes (* 1024 1024)))
                    :container-peak-mib (if (is reading.container.peak-bytes None) None (// reading.container.peak-bytes (* 1024 1024)))))
          (val kept (tuple (gfor m jobs :if (not-in m.job cell.window-killed) (JobSteady :job m.job :bytes m.bytes))))
          (setv cell.steady (tuple (cut (+ cell.steady #(kept)) (- settings.steady-windows) None)))
          (setv cell.window-killed (frozenset))
          (setv cell.window None)))
  None)


(defk mib-or-none [size]
  {:pre [(: size (| int None))] :post [(: % (| int None))] :tags {:context "worker" :role "protocol"}}
  "log に書く byte の数(読めなければ None)を MiB にするため。"
  (if (is size None) None (// size (* 1024 1024))))


(defk unit-signalled [unit]
  {:pre [(: unit KillUnit)] :post [(: % int)] :tags {:context "worker" :role "protocol"}}
  "単位 1 つの process へ、頭から順に、読んだ時と同じ起動の刻の物だけへ SIGKILL を送るため(SignalPid — 使い回された pid の他人の
   process へ送らない)。答え = 送れた process の数。"
  (var sent 0)
  (for [member unit.members]
    (<- delivered bool (SignalPid :pid member.pid :start-ticks member.start-ticks :signal ProcessSignal.KILL))
    (when delivered
      (:= sent (+ sent 1))))
  sent)


(defk swarm-killed [settings cell reading swarm now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: swarm tuple) swarm (: now-ms int)]
   :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "群れの止め(worker/core/memory_rules の swarm-of — 同じ job の小さい単位の組)を止め、止めの刻・止めた印・その間の止めた job を残して
   1 行にするため(印と間の扱いは 1 つの単位の止めと同じ)。"
  (val job (. (get swarm 0) job))
  (var sent 0)
  (for [unit swarm]
    (<- count int (unit-signalled unit))
    (:= sent (+ sent count)))
  (setv cell.last-kill-ms now-ms)
  (val root (next (gfor r reading.roots :if (= r.name job) r) None))
  (when (and (> sent 0) (is-not root None))
    (setv cell.kills (+ cell.kills #((MemoryKill :job root.name :pid root.pid :at-ms now-ms)))))
  (when (> sent 0)
    (setv cell.window-killed (| cell.window-killed #{job})))
  (val node reading.node)
  (<- listed str (units-text swarm LEVEL-UNITS))
  (<- (slog MEMORY-SWARM-KILL-LOG :level "warning" :job job :units (len swarm) :units-mib (// (sum (gfor u swarm u.bytes)) (* 1024 1024))
            :largest listed :processes (sum (gfor u swarm (len u.members))) :sent sent
            :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
            :full-avg10 node.full-avg10))
  None)


(defk guarded [settings cell reading level units uses now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: level MemoryLevel) (: units tuple)
         (: uses tuple) (: now-ms int)]
   :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "DANGER の時に、止めの間隔を置いていちばん大きい単位を SIGKILL で止めるため(guard が偽なら止めない)。単位の process は頭から順に、
   読んだ時と同じ起動の刻の物だけへ送る(SignalPid — 使い回された pid の他人の process へ送らない)。止める単位は平常の大きさを
   越えた物だけ(頭の註)。止める単位が無ければ DANGER の 1 回につき 1 行で、不足が worker の外から来たかを読める欄とともに言う。"
  (when (!= level MemoryLevel.DANGER)
    (setv cell.told-no-victim False)
    (return None))
  (when (not settings.guard)
    (return None))
  (when (and (is-not cell.last-kill-ms None) (< (- now-ms cell.last-kill-ms) (* settings.kill-cooldown-seconds 1000)))
    (return None))
  (<- steadies tuple (steady-sizes cell.steady))
  (<- victim (| KillUnit None) (victim-of units uses steadies settings.limits))
  (val node reading.node)
  ;; 止める候補の単位が無ければ、群れの止め(頭の註の MEMORY-SWARM-KILL-LOG)を試す。
  (var swarm #())
  (when (is victim None)
    (<- found tuple (swarm-of units uses node reading.container.current-bytes settings.limits))
    (:= swarm found))
  (when swarm
    (<- (swarm-killed settings cell reading swarm now-ms))
    (return None))
  (when (is victim None)
    (when (not cell.told-no-victim)
      (<- spared tuple (spared-steady units uses steadies settings.limits))
      (val used (- node.total-bytes node.available-bytes))
      (val current reading.container.current-bytes)
      (<- current-mib (| int None) (mib-or-none current))
      (<- peak-mib (| int None) (mib-or-none reading.container.peak-bytes))
      (<- outside-mib (| int None) (mib-or-none (if (is current None) None (max 0 (- used current)))))
      (<- (slog MEMORY-NO-VICTIM-LOG :level "warning" :largest-mib (if units (// (. (get units 0) bytes) (* 1024 1024)) 0)
                :min-victim-mib (// settings.limits.min-victim-bytes (* 1024 1024))
                :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
                :node-used-mib (// used (* 1024 1024))
                :container-current-mib current-mib :container-peak-mib peak-mib :outside-worker-mib outside-mib
                :spared-steady (.join " " (gfor s spared (.format "{}={}/{}" s.job (// (sum (gfor u uses :if (= u.job s.job) u.bytes)) (* 1024 1024))
                                                                  (// s.bytes (* 1024 1024)))))
                :full-avg10 node.full-avg10))
      (setv cell.told-no-victim True))
    (return None))
  (<- sent int (unit-signalled victim))
  (setv cell.last-kill-ms now-ms)
  ;; 止めた単位の属する job の根に印を残す(調整ループが、その process の終わりを見張りに止められた終わりとして数える)。送れなかった
  ;; (単位の process が既に居ない)時は残さない。
  (val root (next (gfor r reading.roots :if (= r.name victim.job) r) None))
  (when (and (> sent 0) (is-not root None))
    (setv cell.kills (+ cell.kills #((MemoryKill :job root.name :pid root.pid :at-ms now-ms)))))
  (when (> sent 0)
    (setv cell.window-killed (| cell.window-killed #{victim.job})))
  (<- (slog MEMORY-KILL-LOG :level "warning" :job victim.job :pid victim.head.pid :comm victim.head.comm
            :unit-mib (// victim.bytes (* 1024 1024)) :processes (len victim.members) :sent sent
            :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
            :full-avg10 node.full-avg10))
  None)


(defk memory-watch-step [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "見張りの 1 回: 読み、job ごとの最大を更新し、様子が変わったら言い、DANGER なら止め、最大の行の間の印を更新するため。"
  (<- now-ms int (now-epoch-ms))
  ;; kill-mark-seconds を過ぎた止めた印を除く(調整ループは止めた process の終わりを秒の桁で回収するので、残す間はそれを十分に覆う)。
  (setv cell.kills (tuple (gfor k cell.kills :if (< (- now-ms k.at-ms) (* settings.kill-mark-seconds 1000)) k)))
  (<- reading (| MemoryReading MemoryUnreadable) (memory-reading settings))
  (when (isinstance reading MemoryUnreadable)
    (when (!= reading.reason cell.unread)
      (<- (slog MEMORY-UNREAD-LOG :level "warning" :reason reading.reason))
      (setv cell.unread reading.reason))
    (return None))
  (setv cell.unread "")
  (<- uses tuple (job-uses reading.rows reading.roots))
  (<- (noted-peaks cell uses reading.roots now-ms))
  (<- level MemoryLevel (memory-level reading.node settings.limits))
  (<- units tuple (kill-units reading.rows reading.roots))
  (when (!= level cell.level)
    (val node reading.node)
    (<- listed str (units-text units LEVEL-UNITS))
    (<- (slog MEMORY-LEVEL-LOG :level (if (= level MemoryLevel.CALM) "info" "warning") :from cell.level.value :to level.value
              :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
              :some-avg10 node.some-avg10 :full-avg10 node.full-avg10
              :container-current-mib (if (is reading.container.current-bytes None) None (// reading.container.current-bytes (* 1024 1024)))
              :units listed))
    (setv cell.level level))
  (<- (guarded settings cell reading level units uses now-ms))
  ;; 止めの後に最大の行の間を閉じる(今の読みで止めた job の最大を平常の大きさに残さない)。
  (<- (noted-window settings cell reading uses now-ms))
  None)


(defk memory-watch-loop [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "見張りの背景の task の本体(頭の註): 止めの合図まで sample-seconds ごとに 1 回読む。1 回の中の思わぬ例外は、違う例外の時だけ 1 行に
   して次の回へ進む(黙って止まらない・同じ例外を 5 秒ごとに書かない)。"
  (while (not cell.closing)
    (try
      (<- (memory-watch-step settings cell))
      (setv cell.failure "")
      (except [error Exception]
        (when (!= (repr error) cell.failure)
          (setv cell.failure (repr error))
          (<- (slog "worker: 機体の memory の見張りの 1 回が失敗しました — 次の回へ進みます" :level "warning" :error (repr error))))))
    (<- (Delay settings.sample-seconds)))
  None)
