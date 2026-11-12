;;; 機体の memory の見張り— 調整ループの外の背景の task(Spawn の daemon)が sample-seconds ごとに、機体の
;;; memory(/proc/meminfo・/proc/pressure/memory)・worker の container の memory(cgroup v2)・job ごとの memory(process の表を job の根の
;;; 木で数える)を読み、worker の log に残す。機体の memory が尽きかけたら(worker/core/memory_rules の DANGER)、job の木の中のいちばん
;;; 大きい単位を SIGKILL で止める — kernel の OOM に至らない reclaim の thrash で機体の全体が止まる前に。
;;; 追い出しの線: kubelet の下の worker(設定 eviction が在る — 入口が host の cgroupfs と Node の名から作る)は、回の頭で kubelet が Pod を
;;; 追い出す線までの空きを数え、jobs の層の memory.max をその空きから掛け直す(worker/protocol/eviction_line)。その回の様子(CALM・TIGHT・
;;; DANGER)・群れの止めの量・log の機体の空きと総量は、機体の MemAvailable と MemTotal でなく、追い出しまでの空きと線の大きさで判じる
;;; (worker/core/eviction_rules の line-memory — kubelet は機体の MemAvailable でなく線で Pod を追い出す)。線の無い機体と読めない回は
;;; 機体の MemAvailable と MemTotal で判じる。
;;;
;;; log の行(名 + 欄の形):
;;;   MEMORY-WINDOW-LOG  window-seconds ごとに 1 行: その間の job ごとの memory の最大(report-floor-bytes 以上・大きい順に report-jobs 個)・
;;;                      機体の空きの最低・PSI の full avg10 の最大・container の memory.current と memory.peak。5 秒ごとの読みの最大なので、
;;;                      1 分の跳ねも落ちない(10 分ごとの 1 点の測りでは落ちた)。
;;;   JOB-PEAK-LOG       job が終わった(根が観測から消えた)時に 1 行: その job の memory の最大と、最大を読んだ刻。
;;;   MEMORY-LEVEL-LOG   機体の様子(CALM・TIGHT・DANGER)が変わった時に 1 行: 機体の空き・PSI・大きい単位の上位。
;;;   MEMORY-KILL-LOG    止めた時に 1 行: job・単位の頭の pid と命令の名・単位の memory・送った process の数・機体の空き。
;;;   MEMORY-SWARM-KILL-LOG  止める候補の単位が無く、1 つの job の木が機体の大きな割合を持つ時に、その job の小さい単位をまとめて止めた
;;;                      時に 1 行(worker/core/memory_rules の swarm-of): job・止めた単位の数と和・大きい単位の
;;;                      上位・送った process の数・機体の空き。
;;;   MEMORY-NO-VICTIM-LOG  DANGER なのに止める単位が無い(止める候補の単位が無いか min-victim-bytes 未満で、群れの止めも当たらない — 食った
;;;                      物は job の外か、平常の大きさで動く小さい常駐の job だけ)時に、DANGER の 1 回につき 1 行: 機体の使用量・container の
;;;                      memory・その差(worker の外の使用量)と、平常の大きさなので止めなかった job。
;;;   MEMORY-UNREAD-LOG  読めない訳が変わった時に 1 行(読めない間は数えない・止めない)。
;;; 様子の行・止める単位が無い行・最大の行は、層ごとの memory.current(欄 layers — <層>=<MiB> を宣言の順に空白で並べる・読めない層は ?)と、
;;; job の木の外で rss のいちばん大きい process(欄 outside-largest — <pid>:<命令の名>=<MiB>・無ければ空)も載せる。訳: 見張りは job の木の外
;;; (worker の本体・init と、kubectl exec・readinessProbe・preStop の命令)を止めないので、尽きかけの訳が木の外に在る時に、誰がどの層で
;;; 食ったかを残す(11-04 12:59 UTC の node-h — exec の grep 2 本の 69 GB は、kernel が OOM で止めた 1 本しか記録に残らなかった)。
;;; 止めは guard が真の時だけ(偽は log だけ — 起動の引数 --memory-guard off)。止めた後 kill-cooldown-seconds は次を止めない(止めた木の
;;; memory が戻るのを待つ)。
;;; 平常の大きさ: 最大の行の間を閉じるたびに、その間の job の名ごとの最大を steady-windows 個まで残し(見張りが
;;; 止めた job の最大は残さない)、その中のいちばん大きい物を job の平常の大きさとして止める単位の選びに渡す(worker/core/memory_rules の
;;; victim-of — 平常の大きさで動く常駐の job は止めない)。名ごとに残すので、起こし直された job も前の走りの大きさを平常とする。
;;; 宣言の内(2026-11-11): 回ごとに、job の根へ最後に読めた heartbeat の返事の宣言の memory の山(入れ物 eviction の declared —
;;; worker/protocol/coordinator_link が書く)を添え、今の memory が宣言の内の常駐の job は止めない(worker/core/memory_rules の頭の註の
;;; 宣言の内)。止める単位が無い行は、宣言の内なので止めなかった job も名指す(欄 spared-declared — <job>=<今の MiB>/<宣言の MiB>)。
;;; 宣言を越えた job は、平常の大きさと宣言の山の大きい方(基準の大きさ — worker/core/memory_rules の steady-base)の steady-ratio 倍未満
;;; なら止めない(欄 spared-steady — <job>=<今の MiB>/<基準の MiB>)。平常の大きさは worker の起動で空になるが、宣言の山は残る。
;;; 止めた印: 止めた単位の属する job の根(名と pid)を、止めた刻と止めた訳とともに入れ物に kill-mark-seconds の間残す。調整ループは終わった
;;; process を回収する時にこの印を読み(worker/protocol/memory_observer)、見張りに止められた終わりを数える — 続けて止められた常駐の job は
;;; 起こし直さずに失敗として報告する(WorkerPolicy.memory-kill-limit)。止めた訳は状態の報告の訳に載る(task の終わりは coordinator の
;;; lost の訳 — 前は「子 process が結果を書かずに終わった」とだけ出て、誰がなぜ止めたかが分からなかった)。
;;; 常駐の job ごとの CPU と memory(heartbeat の measured の欄 jobUses): 回ごとに task でない根の木の CPU の累計(ProcRow.cpu-ms の和)と
;;; rss の和を数え、pss-seconds ごとに木の process の /proc/<pid>/smaps_rollup の PSS を足す(worker/core/memory_rules の job-meters-of)。
;;; PSS の読みは process 1 本に数 ms かかる(11-10 の測り — 機体の 421 本で 2.8 秒・stat の 40 倍)ので回ごとには読まず、木が
;;; pss-process-limit 本を越える job(shim に引き取られた孤児の群れ)は読まない(None)。読みの答えは入れ物 eviction に置き、heartbeat の
;;; 測りが名乗る — coordinator の GET /state の worker の行から、測りの task を置かずにどの worker の job も読める。
;;; 要りの越えの止め(OVER-NEED-KILL-LOG): 上に載る系が残す線(worker/core/eviction_rules の頭の註)を機体の MemAvailable が割った回は、
;;; 様子に依らず、名乗った要りを越えて伸びた task だけを越えの大きい順に、割った量に届くまで止める(over-need-stopped)。jobs の層の
;;; memory.max はこの線では絞らないので、要りの内で走る task・要りを名乗らずに置かれた task・実行環境の準備は、線を割っても止まらない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])  ; dataclass = defrecord の展開が名指す
(import doeff_core_effects [slog])
(import doeff_core_effects [ReadText FileFailed])
(import doeff_core_effects [ProcessSignal])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid])
(import doeff_time [Delay])
(import doeff_cluster._shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster._worker.protocol.observations [ObserveJobRoots])
(import doeff_cluster._worker.core.memory_rules [MIB TASK-PREFIX MemoryLevel MemoryLimits NodeMemory ContainerMemory LayerMemory KillUnit JobUse
                                                JobSteady UnitKind node-memory-of cgroup-bytes-of memory-level _job-walks _uses-of _units-of
                                                largest-outside steady-sizes spared-steady victim-of swarm-of resident-walks job-meters-of
                                                pss-bytes-of anonymous-bytes-of _JobWalk declared-roots spared-declared])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine line-memory line-name fresh-keep-room over-need-tasks urgent-jobs])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted jobs-oom-noted])

(val MEMORY-WINDOW-LOG "worker: job ごとの memory")
(val JOB-PEAK-LOG "worker: job の memory の最大")
(val MEMORY-LEVEL-LOG "worker: 機体の memory の様子")
(val MEMORY-KILL-LOG "worker: 機体の memory の尽きかけで止めた")
(val MEMORY-SWARM-KILL-LOG "worker: 機体の memory の尽きかけで job の小さい単位をまとめて止めた")
(val MEMORY-NO-VICTIM-LOG "worker: 機体の memory が尽きかけだが止める単位が無い")
(val MEMORY-UNREAD-LOG "worker: 機体の memory を読めない")
(val OVER-NEED-KILL-LOG "worker: 上に載る系の残す線を割ったので、要りを越えて伸びた task を止めた")
;; 様子の行に並べる大きい単位の数。
(val LEVEL-UNITS 5)


(defrecord MemoryWatchSettings
  "見張りの設定(頭の註): meminfo-path・pressure-path・cgroup-dir = 読む file の置き場・sample-seconds = 読みの間隔・window-seconds = job ごとの
   最大の行の間隔・kill-cooldown-seconds = 止めた後に次を止めない秒・guard = 止めるか(偽は log だけ)・limits = 様子の閾・report-floor-bytes =
   最大の行に載せる job の最低・report-jobs = 最大の行に載せる job の数の上限・kill-mark-seconds = 止めた印を残す秒(調整ループが止めた
   process の終わりを回収するまでの間を十分に覆う)・steady-windows = 平常の大きさに数える最大の行の間の数(頭の註 — 既定 12 は 1 時間)・
   eviction = 追い出しの線の読みの設定(頭の註 — None = kubelet の下に居ない worker・線を数えない)・layers = memory.current を log に出す
   cgroup-dir の下の層の名(deploy/boot.sh の「cgroup の層」の宣言の順 — 空 = 層の無い worker)・pss-seconds = 常駐の job の PSS を読み直す
   間隔(頭の註)・pss-process-limit = PSS を読む木の process の数の上限(越えた job の PSS は読まない)・proc-dir = PSS を読む /proc の置き場。"
  {:check [(> sample-seconds 0) (>= window-seconds sample-seconds) (>= kill-cooldown-seconds 0) (>= report-floor-bytes 0) (> report-jobs 0)
           (> kill-mark-seconds 0) (> steady-windows 0) (> pss-seconds 0) (> pss-process-limit 0)]}
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
  (setv #^ int steady-windows 12)
  (setv #^ (| EvictionSettings None) eviction None)
  (setv #^ (get tuple #(str ...)) layers #())
  (setv #^ float pss-seconds 60.0)
  (setv #^ int pss-process-limit 64)
  (setv #^ str proc-dir "/proc"))


(defrecord MemoryKill
  "見張りが止めた単位の属する job の根 1 つの印: job = job(process)の名・pid = 根の pid・at-ms = 止めた刻(epoch ms — 印を残す間を数える)・
   reason = 止めた訳の文(状態の報告がその job の終わりの訳に載せる — 頭の註の止めた印)。"
  (#^ str job)
  (#^ int pid)
  (#^ int at-ms)
  (setv #^ str reason ""))


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
   新しい間が後・steady-windows 個まで)・window-killed = 今の最大の行の間に見張りが止めた job の名(その間の最大を steady に残さない)・
   eviction = 追い出しの線の読みの入れ物(worker/protocol/eviction_line の EvictionCell — heartbeat の測りも最後の線を読む)・
   pss = 常駐の job の木ごとの最後に読めた PSS(#(job の名 根の pid) → #(byte 読んだ刻 本体の Anonymous の byte) — 頭の註)・pss-read-ms = 最後に PSS を読んだ刻
   (None = まだ)。
   scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.closing False self.level MemoryLevel.CALM self.peaks #() self.window None self.last-kill-ms None
          self.told-no-victim False self.unread "" self.failure "" self.kills #() self.steady #() self.window-killed (frozenset)
          self.eviction (EvictionCell) self.pss {} self.pss-read-ms None)))


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
  (var layers #())
  (for [name settings.layers]
    (<- layer-text (| str None) (text-or-none (+ settings.cgroup-dir "/" name "/memory.current")))
    (<- layer-bytes (| int None) (cgroup-bytes-of layer-text))
    (:= layers (+ layers #((LayerMemory :name name :current-bytes layer-bytes)))))
  (<- roots tuple (ObserveJobRoots))
  (<- rows tuple (ReadProcTable))
  (MemoryReading :node node :container (ContainerMemory :current-bytes current :peak-bytes peak :layers layers) :roots roots :rows rows))


(defk units-text [units count]
  {:pre [(: units tuple) (: count int)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "大きい単位の上位 count 個を log の欄 1 つの綴り(<job>:<頭の pid>:<命令の名>=<MiB> を空白で並べる)にするため。"
  (.join " " (gfor unit (cut units count) (.format "{}:{}:{}={}" unit.job unit.head.pid unit.head.comm (// unit.bytes (* 1024 1024))))))


(defk layers-text [container]
  {:pre [(: container ContainerMemory)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "層ごとの memory.current を log の欄 1 つの綴り(<層>=<MiB> を宣言の順に空白で並べる・読めない層は ?)にするため(頭の註)。"
  (.join " " (gfor layer container.layers
                   (.format "{}={}" layer.name (if (is layer.current-bytes None) "?" (// layer.current-bytes (* 1024 1024)))))))


(defk outside-text [rows walks]
  {:pre [(: rows tuple) (: walks tuple)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "job の木の外で rss のいちばん大きい process を log の欄 1 つの綴り(<pid>:<命令の名>=<MiB>・無ければ空)にするため(頭の註)。"
  (<- largest (| ProcRow None) (largest-outside rows walks))
  (if (is largest None) "" (.format "{}:{}={}" largest.pid largest.comm (// largest.rss-bytes (* 1024 1024)))))


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


(defk noted-window [settings cell reading walks uses now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: walks tuple) (: uses tuple) (: now-ms int)]
   :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "最大の行の間の印に今の読みを足し、間が window-seconds を越えたら 1 行にし(walks = 今の読みの job の木 — 木の外の最大の欄)、間の job の
   最大を平常の大きさの元に残して(この間に止めた job は除く)新しい間を始めるため。"
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
                    :line (! (line-name cell.eviction.line)) :jobs-limit-mib (! (mib-or-none cell.eviction.jobs-limit))
                    :clis-limit-mib (! (mib-or-none cell.eviction.clis-limit))
                    :commands-limit-mib (! (mib-or-none cell.eviction.command-limit))
                    :margin-mib (! (mib-or-none cell.eviction.margin)) :max-full-avg10 merged.max-full
                    :container-current-mib (if (is reading.container.current-bytes None) None (// reading.container.current-bytes (* 1024 1024)))
                    :container-peak-mib (if (is reading.container.peak-bytes None) None (// reading.container.peak-bytes (* 1024 1024)))
                    :layers (! (layers-text reading.container)) :outside-largest (! (outside-text reading.rows walks))))
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
  (val node reading.node)
  (when (and (> sent 0) (is-not root None))
    (val reason (.format "機体の memory の尽きかけで見張りが job の小さい単位 {} 個(和 {} MiB)をまとめて止めた(空き {} MiB / {} MiB)"
                         (len swarm) (// (sum (gfor u swarm u.bytes)) (* 1024 1024)) (// node.available-bytes (* 1024 1024))
                         (// node.total-bytes (* 1024 1024))))
    (setv cell.kills (+ cell.kills #((MemoryKill :job root.name :pid root.pid :at-ms now-ms :reason reason)))))
  (when (> sent 0)
    (setv cell.window-killed (| cell.window-killed #{job})))
  (<- listed str (units-text swarm LEVEL-UNITS))
  (<- (slog MEMORY-SWARM-KILL-LOG :level "warning" :job job :units (len swarm) :units-mib (// (sum (gfor u swarm u.bytes)) (* 1024 1024))
            :largest listed :processes (sum (gfor u swarm (len u.members))) :sent sent
            :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
            :full-avg10 node.full-avg10))
  None)


(defk guarded [settings cell reading walks level units uses now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: walks tuple) (: level MemoryLevel) (: units tuple)
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
  ;; 急ぎの優先の task(置き先が返事の行で運ぶ優先 — eviction_rules の urgent-jobs)は、ほかの候補が残る間は止めない(2026-11-11)。
  (<- urgent frozenset (urgent-jobs cell.eviction.needs))
  (<- victim (| KillUnit None) (victim-of units uses steadies settings.limits :urgent urgent))
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
      (<- declared tuple (spared-declared uses settings.limits))
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
                :spared-declared (.join " " (gfor u declared (.format "{}={}/{}" u.job (// (sum (gfor v uses :if (= v.job u.job) v.bytes)) MIB)
                                                                     (// u.declared-bytes MIB))))
                :full-avg10 node.full-avg10 :layers (! (layers-text reading.container))
                :outside-largest (! (outside-text reading.rows walks))))
      (setv cell.told-no-victim True))
    (return None))
  (<- sent int (unit-signalled victim))
  (setv cell.last-kill-ms now-ms)
  ;; 止めた単位の属する job の根に印を残す(調整ループが、その process の終わりを見張りに止められた終わりとして数える)。送れなかった
  ;; (単位の process が既に居ない)時は残さない。
  (val root (next (gfor r reading.roots :if (= r.name victim.job) r) None))
  (when (and (> sent 0) (is-not root None))
    (val reason (.format "機体の memory の尽きかけで見張りが止めた(単位 {} MiB・空き {} MiB / {} MiB)" (// victim.bytes (* 1024 1024))
                         (// node.available-bytes (* 1024 1024)) (// node.total-bytes (* 1024 1024))))
    (setv cell.kills (+ cell.kills #((MemoryKill :job root.name :pid root.pid :at-ms now-ms :reason reason)))))
  (when (> sent 0)
    (setv cell.window-killed (| cell.window-killed #{victim.job})))
  (<- (slog MEMORY-KILL-LOG :level "warning" :job victim.job :pid victim.head.pid :comm victim.head.comm
            :unit-mib (// victim.bytes (* 1024 1024)) :processes (len victim.members) :sent sent
            :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
            :full-avg10 node.full-avg10))
  None)


(defk over-need-stopped [settings cell reading units now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: reading MemoryReading) (: units tuple) (: now-ms int)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "機体の MemAvailable が上に載る系の残す線を割った回に、名乗った要りを越えて伸びた task だけを、越えの大きい順に割った量に届くまで SIGKILL で
   止めるため(頭の註の要りの越えの止め・worker/core/eviction_rules の over-need-tasks)。要りの内で走る task と、要りを名乗らずに置かれた
   task は止めない — 線を割った訳が
   jobs の層の外なら、新しい受けの上限が絞られるだけ。線の無い・古い回・guard が偽・止めの間隔の内は止めない。止めた task の根に訳つきの
   印を残し、1 本ごとに 1 行言う。"
  (when (not settings.guard)
    (return None))
  (when (and (is-not cell.last-kill-ms None) (< (- now-ms cell.last-kill-ms) (* settings.kill-cooldown-seconds 1000)))
    (return None))
  (val eviction cell.eviction)
  (<- keep-room (| int None) (fresh-keep-room eviction.keep-room eviction.keep-room-at-ms now-ms))
  (when (or (is keep-room None) (>= keep-room 0))
    (return None))
  (<- over tuple (over-need-tasks eviction.needs eviction.uses (- keep-room)))
  (for [task over]
    (val job (+ TASK-PREFIX task.id))
    (val unit (next (gfor u units :if (and (= u.job job) (= u.kind UnitKind.TASK)) u) None))
    (val root (next (gfor r reading.roots :if (= r.name job) r) None))
    (when (and (is-not unit None) (is-not root None))
      (<- sent int (unit-signalled unit))
      (when (> sent 0)
        (val reason (.format "要りを越えて伸びた task を見張りが止めた(使い {} MiB・要り {} MiB — 機体の MemAvailable が上に載る系の残す線を {} MiB 割った)"
                             (// task.use-bytes MIB) (// task.need-bytes MIB) (// (- keep-room) MIB)))
        (setv cell.last-kill-ms now-ms
              cell.kills (+ cell.kills #((MemoryKill :job root.name :pid root.pid :at-ms now-ms :reason reason)))
              cell.window-killed (| cell.window-killed #{job})))
      (<- (slog OVER-NEED-KILL-LOG :level "warning" :job job :pid root.pid :use-mib (// task.use-bytes MIB) :need-mib (// task.need-bytes MIB)
                :keep-room-mib (// keep-room MIB) :processes (len unit.members) :sent sent))))
  None)


(defk tree-pss [settings walk]
  {:pre [(: settings MemoryWatchSettings) (: walk _JobWalk)] :post [(: % (| tuple None))] :tags {:context "worker" :role "protocol"}}
  "job の木 1 本(walk = _JobWalk)の process の PSS の和と、PSS の最も大きい process(job の本体)の Anonymous を、/proc/<pid>/smaps_rollup を
   1 本ずつ読んで数えるため(答え = #(PSS の和 本体の Anonymous) の byte)。1 本でも読めない(読む間に終わった・形が違う)か、木が
   pss-process-limit 本を越えれば None(小さく欠けた和を作らない — 頭の註)。"
  (val members walk.walk.members)
  (when (> (len members) settings.pss-process-limit)
    (return None))
  (var total 0)
  (var body #(-1 0))
  (for [member members]
    (<- text (| str None) (text-or-none (.format "{}/{}/smaps_rollup" settings.proc-dir member.pid)))
    (when (is text None)
      (return None))
    (<- size (| int None) (pss-bytes-of text))
    (<- anonymous (| int None) (anonymous-bytes-of text))
    (when (or (is size None) (is anonymous None))
      (return None))
    (when (> size (get body 0))
      (:= body #(size anonymous)))
    (:= total (+ total size)))
  #(total (get body 1)))


(defk noted-meters [settings cell walks now-ms]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell) (: walks tuple) (: now-ms int)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "常駐の job ごとの CPU と memory を数えて入れ物 eviction に置くため(頭の註 — heartbeat の測りが名乗る)。PSS は前に読んだ刻から
   pss-seconds を過ぎた回(か壁の時計が戻った回)だけ読み直し、今の木に無い job の PSS は捨てる。"
  (<- residents tuple (resident-walks walks))
  (val read-ms cell.pss-read-ms)
  (when (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) (* settings.pss-seconds 1000)))
    (var read {})
    (for [w residents]
      (<- sizes (| tuple None) (tree-pss settings w))
      (when (is-not sizes None)
        (:= read (| read {#(w.root.name w.root.pid) #((get sizes 0) now-ms (get sizes 1))}))))
    (setv cell.pss read cell.pss-read-ms now-ms))
  (<- meters tuple (job-meters-of residents cell.pss))
  (setv cell.eviction.meters meters cell.eviction.meters-at-ms now-ms)
  None)


(defk memory-watch-step [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "見張りの 1 回: 読み、job ごとの最大を更新し、様子が変わったら言い、DANGER なら止め、最大の行の間の印を更新するため。"
  (<- now-ms int (now-epoch-ms))
  ;; kill-mark-seconds を過ぎた止めた印を除く(調整ループは止めた process の終わりを秒の桁で回収するので、残す間はそれを十分に覆う)。
  (setv cell.kills (tuple (gfor k cell.kills :if (< (- now-ms k.at-ms) (* settings.kill-mark-seconds 1000)) k)))
  ;; 追い出しの線を数え、jobs の層の上限を掛け直す(process の表の読みより先 — 表が大きい回も掛け直しを遅らせない)。
  (<- line (| EvictionLine None) (eviction-noted settings.eviction cell.eviction settings.guard now-ms))
  (<- read (| MemoryReading MemoryUnreadable) (memory-reading settings))
  (when (isinstance read MemoryUnreadable)
    (when (!= read.reason cell.unread)
      (<- (slog MEMORY-UNREAD-LOG :level "warning" :reason read.reason))
      (setv cell.unread read.reason))
    (return None))
  (setv cell.unread "")
  ;; 様子・止め・log の機体の空きと総量は、線が在れば追い出しまでの空きと線の大きさ(頭の註)。
  (<- line-node NodeMemory (line-memory read.node line))
  ;; job の根に宣言の memory の山を添える(頭の註の宣言の内 — 宣言の内の常駐の job を止めない)。
  (<- roots tuple (declared-roots read.roots cell.eviction.declared))
  (val reading (replace read :node line-node :roots roots))
  ;; 表を並べ直して木をたどるのは 1 回の読みに 1 度(job ごとの memory と止める単位で分けて使う)。
  (<- walks tuple (_job-walks reading.rows reading.roots))
  (<- uses tuple (_uses-of walks))
  ;; heartbeat の測りが task ごとの今の使いとして名乗る(coordinator の置き先の予約 — load_rules.task-uses-of・2026-11-02)。
  (setv cell.eviction.uses uses cell.eviction.uses-at-ms now-ms)
  (<- (noted-peaks cell uses reading.roots now-ms))
  (<- level MemoryLevel (memory-level reading.node settings.limits))
  (<- units tuple (_units-of walks))
  (when (!= level cell.level)
    (val node reading.node)
    (<- listed str (units-text units LEVEL-UNITS))
    (<- (slog MEMORY-LEVEL-LOG :level (if (= level MemoryLevel.CALM) "info" "warning") :from cell.level.value :to level.value
              :node-available-mib (// node.available-bytes (* 1024 1024)) :node-total-mib (// node.total-bytes (* 1024 1024))
              :some-avg10 node.some-avg10 :full-avg10 node.full-avg10 :line (! (line-name line))
              :container-current-mib (if (is reading.container.current-bytes None) None (// reading.container.current-bytes (* 1024 1024)))
              :units listed :layers (! (layers-text reading.container)) :outside-largest (! (outside-text reading.rows walks))))
    (setv cell.level level))
  (<- (guarded settings cell reading walks level units uses now-ms))
  ;; 上に載る系の残す線を割った回は、要りを越えた task だけを止める(頭の註の要りの越えの止め — 今の読みの使いで選ぶ)。
  (<- (over-need-stopped settings cell reading units now-ms))
  ;; kernel が jobs の層の memory.max で止めた数の増えを残す(状態の報告が、その間に SIGKILL で終わった task の訳に載せる)。
  (<- (jobs-oom-noted cell.eviction now-ms))
  ;; 止めの後に最大の行の間を閉じる(今の読みで止めた job の最大を平常の大きさに残さない)。
  (<- (noted-window settings cell reading walks uses now-ms))
  ;; 常駐の job ごとの CPU と memory を heartbeat の測りへ渡す(頭の註 — 重い PSS の読みは止めの後に置き、止めを遅らせない)。
  (<- (noted-meters settings cell walks now-ms))
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
