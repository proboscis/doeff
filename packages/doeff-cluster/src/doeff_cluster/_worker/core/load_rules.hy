;;; 機体の測った空き(CPU の使い・memory の空き・PSI)を heartbeat で名乗るための判断 — /proc の綴りを数にし、前の読みとの間の CPU の
;;; 使いを数える。I/O を持たない(読みは worker/protocol/coordinator_link が file の効果で LOAD-READ-EVERY-MS ごとに集める)。
;;;
;;; 何のためか: 置き先を固定の枠(枠の file の memoryMiB・taskReserve)でなく、機体の今の空きで決める下ごしらえ。この段では heartbeat に
;;; 数を載せ、coordinator の GET /state で読めるようにするだけで、置き方の判断は変えない。
;;;
;;; 読む file(task の層の PSI のほかは機体の全体の値 — container の中でも cgroup に縛られない):
;;;   /proc/stat             頭の行 cpu の刻みの和(user nice system idle iowait irq softirq steal)と、行 cpuN の数(CPU の数)。
;;;                          使い = 前の読みからの (和 − idle − iowait) の増え / 和の増え。guest と guest_nice は user と nice に含まれるので足さない。
;;;                          nice の分 = 同じ間の nice の増え / 和の増え(使いの内 — 優先度を下げた process〔nice > 0・SCHED_IDLE と nice〕の
;;;                          使い。coordinator は使いからこの分を引いて、普通の優先度の task が使える CPU を数える — measured_room)。
;;;   /proc/meminfo          MemTotal と MemAvailable(memory の見張りと同じ読み — worker/core/memory_rules の node-memory-of)。kubelet の下の
;;;                          worker は、見張りが最後に数えた追い出しの線の大きさと追い出しまでの空き(worker/core/eviction_rules の
;;;                          line-memory — 見張りの DANGER と同じ量)を memory の総量と空きとして名乗る。
;;;   /proc/pressure/cpu     PSI の some と full の avg10(%)。full の行の無い古い kernel は full を None。
;;;   /proc/pressure/memory  PSI の some と full の avg10(%)。
;;;   <task の層>/cpu.pressure  task を置く cgroup の層(worker/core/job_cgroup の兄弟の cgroup)の CPU の PSI の some の avg10(%)。層の在る
;;;                          worker だけが読む(LoadPaths の task-cpu-pressure)。機体の PSI の some は、優先度を下げた process の実行の待ちでも
;;;                          立つ(2026-11-05 の zeus: 優先度を下げた命令が nice 19 core を使う間、機体の some 76%・task の層の some 5.6%)。
;;;                          task の層の値は、置いた task 自身が実行を待つかを測る。
;;; PSI の file の無い機体(PSI を切った kernel)は PSI の欄を None にし、/proc/stat か /proc/meminfo の読めない機体は測りを名乗らない。
;;;
;;; /proc の無い機体(macOS): 入口が LoadPaths の代わりに印 HostLoad を渡し、同じ間ごとに問い ReadHostCounters で OS の口から CPU の
;;; 刻みと memory を読む(答え手 foundation/host_counters — mach の host_statistics・sysctl)。CPU の使いと nice の分は /proc/stat と同じく
;;; 前の読みとの間の刻みの増えの比で数え、memory は OS の数えた総量と空き(host-load-of)。PSI は OS に無いので None。測りを名乗らない
;;; worker の空きを coordinator は 0 と数える(measured_room)ので、/proc の無い機体もこの読みで他の機体と同じ式に載る。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster._worker.core.memory_rules [MIB NodeMemory JobUse TASK-PREFIX node-memory-of])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine line-memory])
(import doeff_cluster._worker.intent.worker_model [HostCounters])

;; 測りを読み直す間。PSI の avg10 が 10 秒の平均なので、その半分の間で読めば名乗る値の遅れは avg10 の窓の内に収まる。CPU の使いは
;; この間の平均になる。
(val LOAD-READ-EVERY-MS (* 5 1000))
;; 見張りが数えた追い出しの線を名乗りに使う古さの上限(見張りの回の 5 秒の 3 回分 — 見張りが止まった・遅れた worker は古い空きを
;; 名乗らず、MemAvailable に戻る)。
(val LINE-FRESH-MS (* 15 1000))
;; 見張りが読んだ task ごとの memory の今の使いを名乗りに使う古さの上限(線と同じ — 見張りが止まった・遅れた worker は今の使いを
;; 名乗らず、coordinator は名乗られない task の要りの全部を予約する)。
(val TASK-USES-FRESH-MS LINE-FRESH-MS)
;; /proc/stat の頭の行 cpu の欄のうち、使いに数えない刻み(欄の位置 — 0 = user)。3 = idle・4 = iowait。
(val IDLE-FIELDS #(3 4))
;; /proc/stat の頭の行 cpu の欄のうち、nice > 0 の process の刻み(欄の位置)。使いの内に数え、別に nice の分としても名乗る。
(val NICE-FIELD 1)
;; 頭の行 cpu の欄のうち、和に数える数(user nice system idle iowait irq softirq steal — 後ろの guest と guest_nice は user と nice に含まれる)。
(val TICK-FIELDS 8)


(defrecord LoadPaths
  "測りの読む file の置き場(頭の註): stat・meminfo・cpu-pressure・memory-pressure。既定は機体の /proc。task-cpu-pressure = task を
   置く cgroup の層の cpu.pressure(None = 層の無い worker — 読まない)。"
  (setv #^ str stat "/proc/stat")
  (setv #^ str meminfo "/proc/meminfo")
  (setv #^ str cpu-pressure "/proc/pressure/cpu")
  (setv #^ str memory-pressure "/proc/pressure/memory")
  (setv #^ (| str None) task-cpu-pressure None))


(defk load-paths-under [root task-cpu-pressure]
  {:pre [(: root str) (: task-cpu-pressure (| str None))] :post [(: % LoadPaths)] :tags {:context "worker" :role "judgment"}}
  "測りの読む file を、/proc と同じ並びの dir root の下に置くため(root = /proc なら既定の LoadPaths と同じ)。入口が環境変数で受けた
   代わりの dir を渡す — 手元の 1 台の cluster(sim/machine の QuietMachine)が、混んだ機体の上でも静かな機体の測りを worker に読ませる。
   task-cpu-pressure は LoadPaths と同じ(task の層の cpu.pressure・None = 読まない)。"
  (LoadPaths :stat (+ root "/stat") :meminfo (+ root "/meminfo") :cpu-pressure (+ root "/pressure/cpu")
             :memory-pressure (+ root "/pressure/memory") :task-cpu-pressure task-cpu-pressure))


(defrecord HostLoad
  "測りを /proc の file でなく OS の口(問い ReadHostCounters)で読む印(頭の註 — /proc の無い機体の入口が LoadPaths の代わりに渡す)。
   os = 名乗りの訳に書く OS の名(読めない訳の log の 1 行)。"
  (#^ str os))


(defrecord CpuTicks
  "/proc/stat の 1 回の読み: busy = 頭の行 cpu の和から idle と iowait を除いた刻み・total = 頭の行 cpu の刻みの和(TICK-FIELDS)・
   cpus = 行 cpuN の数(今 online の CPU の数)・nice = 頭の行 cpu の nice の刻み(busy の内)。"
  {:check [(>= busy nice 0) (>= total busy) (> cpus 0)]}
  (#^ int busy)
  (#^ int total)
  (#^ int cpus)
  (setv #^ int nice 0))


(defrecord Pressure
  "PSI の file 1 つの avg10(%): some-avg10 = some の行・full-avg10 = full の行(行の無い kernel は None)。"
  {:check [(>= some-avg10 0) (or (is full-avg10 None) (>= full-avg10 0))]}
  (#^ float some-avg10)
  (#^ (| float None) full-avg10))


(defrecord TaskMemory
  "走っている task 1 本の memory の今の使い(heartbeat の measured の欄 taskMemoryMiB の 1 つ): id = task の id(job の名 task/<id> の
   頭を外した物)・mib = 見張りの最後の読みの task の木の rss の和(MiB — 切り捨て)。"
  {:check [(>= mib 0)]}
  (#^ str id)
  (#^ int mib))


(defrecord MachineLoad
  "機体の測った空き 1 回分(heartbeat の欄 measured): at-ms = 読んだ刻(worker の壁の時計の epoch ms)・cpus = CPU の数・cpu-busy =
   前の読みからの CPU の使いの割合(0〜1 — 初めての読みと、刻みの進まない読みは None)・memory-total-mib / memory-available-mib =
   MemTotal / MemAvailable(MiB — 切り捨て)・cpu-some-avg10 / cpu-full-avg10 / memory-some-avg10 / memory-full-avg10 = PSI の avg10(%)
   (PSI の file の無い機体と、full の行の無い kernel の cpu は None)・cpu-nice = cpu-busy の内の nice の分(0〜1 — cpu-busy と同じ読みで
   None)・task-cpu-some-avg10 = task の層の CPU の PSI の some の avg10(%— 層の無い・読めない worker は None)・task-uses = 走っている task ごとの memory の今の使い(TaskMemory
   の tuple — task-uses-of。None = 名乗らない〔見張りの読みが無いか古い〕— coordinator の置き先は名乗られない task の要りの全部を
   予約する・2026-11-02)。"
  {:check [(> cpus 0) (or (is cpu-busy None) (<= 0 cpu-busy 1)) (>= memory-total-mib memory-available-mib 0)
           (or (is cpu-nice None) (and (is-not cpu-busy None) (<= 0 cpu-nice cpu-busy)))
           (or (is task-cpu-some-avg10 None) (>= task-cpu-some-avg10 0))]}
  (#^ int at-ms)
  (#^ int cpus)
  (#^ (| float None) cpu-busy)
  (#^ int memory-total-mib)
  (#^ int memory-available-mib)
  (#^ (| float None) cpu-some-avg10)
  (#^ (| float None) cpu-full-avg10)
  (#^ (| float None) memory-some-avg10)
  (#^ (| float None) memory-full-avg10)
  (setv #^ (| float None) cpu-nice None)
  (setv #^ (| float None) task-cpu-some-avg10 None)
  (setv #^ (| (get tuple #(TaskMemory ...)) None) task-uses None))


(defrecord LoadReading
  "測りの 1 回の読みの答え: load = 名乗る測り・ticks = この読みの /proc/stat(次の読みが CPU の使いを数える起点)。"
  (#^ MachineLoad load)
  (#^ CpuTicks ticks))


(defk load-read-due [read-ms now-ms]
  {:pre [(: read-ms (| int None)) (: now-ms int)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "この拍で測りを読み直すかを決めるため(read-ms = 前に読んだ刻・None = まだ読んでいない)。壁の時計が戻った時も読み直す。"
  (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) LOAD-READ-EVERY-MS)))


(defk fresh-line [line line-at-ms now-ms]
  {:pre [(: line (| EvictionLine None)) (: line-at-ms (| int None)) (: now-ms int)] :post [(: % (| EvictionLine None))]
   :tags {:context "worker" :role "judgment"}}
  "見張りが最後に数えた追い出しの線 line(line-at-ms = 数えた刻)を、名乗りに使える新しさの時だけ返すため(LINE-FRESH-MS の内 —
   古い・刻の無い・壁の時計が戻った読みは None で、名乗りは MemAvailable に戻る)。"
  (if (and (is-not line None) (is-not line-at-ms None) (<= 0 (- now-ms line-at-ms) LINE-FRESH-MS)) line None))


(defk task-uses-of [uses uses-at-ms now-ms]
  {:pre [(: uses tuple) (: uses-at-ms (| int None)) (: now-ms int)] :post [(: % (| tuple None))]
   :tags {:context "worker" :role "judgment"}}
  "見張りが最後に読んだ job ごとの memory(uses = JobUse の tuple・uses-at-ms = 読んだ刻)を、heartbeat で名乗る task ごとの今の使い
   (TaskMemory の tuple — 名の頭 TASK-PREFIX の job だけ)にするため。coordinator の置き先が task の「要り − 今の使い」を予約するのに
   読む。古い(TASK-USES-FRESH-MS を越えた)・刻の無い・壁の時計が戻った読みは None(名乗らない — 古い小さな使いで予約を減らさない)。"
  (if (and (is-not uses-at-ms None) (<= 0 (- now-ms uses-at-ms) TASK-USES-FRESH-MS))
      (tuple (gfor u uses :if (.startswith u.job TASK-PREFIX)
                   (TaskMemory :id (cut u.job (len TASK-PREFIX) None) :mib (// u.bytes MIB))))
      None))


(defk cpu-ticks-of [text]
  {:pre [(: text str)] :post [(: % CpuTicks)] :tags {:context "worker" :role "judgment"}}
  "/proc/stat の中身を CPU の刻みの読みにするため。頭の行 cpu が無い・欄が TICK-FIELDS 個に満たない・数でない・行 cpuN が無いなら
   ValueError(黙って 0 にしない)。"
  (val rows (lfor line (.splitlines text) :setv parts (.split line) :if parts parts))
  (val heads (lfor parts rows :if (= (get parts 0) "cpu") (cut parts 1 None)))
  (val cpus (len (lfor parts rows :if (and (.startswith (get parts 0) "cpu") (.isdigit (cut (get parts 0) 3 None))) parts)))
  (when (or (not heads) (< (len (get heads 0)) TICK-FIELDS) (not (all (gfor v (cut (get heads 0) TICK-FIELDS) (.isdigit v)))))
    (raise (ValueError "/proc/stat の頭の行 cpu が無いか、刻みの欄が足りないか数でない")))
  (when (= cpus 0)
    (raise (ValueError "/proc/stat に行 cpuN が無い")))
  (val ticks (lfor v (cut (get heads 0) TICK-FIELDS) (int v)))
  (val total (sum ticks))
  (CpuTicks :busy (- total (sum (gfor i IDLE-FIELDS (get ticks i)))) :total total :cpus cpus :nice (get ticks NICE-FIELD)))


(defk pressure-of [text source]
  {:pre [(: text str) (: source str)] :post [(: % Pressure)] :tags {:context "worker" :role "judgment"}}
  "PSI の file 1 つ(source = 訳に書く path)の中身から some と full の avg10(%)を読むため。some の行か avg10 の欄が無い・数でないなら
   ValueError。full の行の無い file は full を None。"
  (val avg10 (fn [kind]
               (lfor line (.splitlines text) :setv parts (.split line) :if (and parts (= (get parts 0) kind))
                     field (cut parts 1 None) :if (.startswith field "avg10=") (cut field 6 None))))
  (val some (avg10 "some"))
  (val full (avg10 "full"))
  (val numeric (fn [v] (try (do (float v) True) (except [ValueError] False))))
  (when (or (not some) (not (numeric (get some 0))) (and full (not (numeric (get full 0)))))
    (raise (ValueError (.format "{} に some の avg10 が無いか数でない" source))))
  (Pressure :some-avg10 (float (get some 0)) :full-avg10 (if full (float (get full 0)) None)))


(defk cpu-busy-between [before after]
  {:pre [(: before (| CpuTicks None)) (: after CpuTicks)] :post [(: % (| float None))] :tags {:context "worker" :role "judgment"}}
  "前の読み before から今の読み after までの CPU の使いの割合(0〜1)を知るため。前の読みが無い・和が進まない(読みの間が刻みより
   短い)・和が戻った(機体が起き直した — 起き直した後の初めての読み)なら None(前の値や 0 で埋めない)。CPU の数が替わった間は、
   増えの比で数える(刻みは機体の全体の和なので、数の替わりは比に入る)。"
  (if (or (is before None) (<= after.total before.total) (< after.busy before.busy))
      None
      (min 1.0 (/ (- after.busy before.busy) (- after.total before.total)))))


(defk cpu-nice-between [before after]
  {:pre [(: before (| CpuTicks None)) (: after CpuTicks)] :post [(: % (| float None))] :tags {:context "worker" :role "judgment"}}
  "前の読み before から今の読み after までの CPU の使いのうち、nice > 0 の process の分の割合(0〜1 — 和の増えに対する nice の増え)を
   知るため。cpu-busy-between が None の読みと、nice の刻みが戻った読みは None。"
  (if (or (is before None) (<= after.total before.total) (< after.busy before.busy) (< after.nice before.nice))
      None
      (min 1.0 (/ (- after.nice before.nice) (- after.total before.total)))))


(defk machine-load-of [stat meminfo cpu-pressure memory-pressure before at-ms [line None] [task-cpu-pressure None]]
  {:pre [(: stat str) (: meminfo str) (: cpu-pressure (| str None)) (: memory-pressure (| str None)) (: before (| CpuTicks None)) (: at-ms int)
         (: line (| EvictionLine None)) (: task-cpu-pressure (| str None))]
   :post [(: % LoadReading)] :tags {:context "worker" :role "judgment"}}
  "/proc/stat・/proc/meminfo・PSI の 2 つの file の中身(読めない PSI の file は None)と前の /proc/stat の読み before を、機体の測った
   空きにするため。line = 機体の memory の見張りが最後に数えた追い出しの線(worker/core/eviction_rules — None = 線が無いか古い)。
   線が在れば memory の総量と空きは線の大きさと追い出しまでの空き(見張りの DANGER と同じ量 — coordinator はこの空きで task を置く)、
   無ければ MemTotal と MemAvailable。task-cpu-pressure = task の層の cpu.pressure の中身(None = 層が無いか読めない)。形の違う中身は
   ValueError(読みの訳 — 呼び手が名乗りを外す)。"
  (<- ticks CpuTicks (cpu-ticks-of stat))
  (<- busy (| float None) (cpu-busy-between before ticks))
  (<- nice (| float None) (cpu-nice-between before ticks))
  (<- machine NodeMemory (node-memory-of meminfo memory-pressure))
  (<- memory NodeMemory (line-memory machine line))
  (var cpu None)
  (when (is-not cpu-pressure None)
    (<- read Pressure (pressure-of cpu-pressure "/proc/pressure/cpu"))
    (:= cpu read))
  (var task-cpu None)
  (when (is-not task-cpu-pressure None)
    (<- read Pressure (pressure-of task-cpu-pressure "task の層の cpu.pressure"))
    (:= task-cpu read))
  (LoadReading :load (MachineLoad :at-ms at-ms :cpus ticks.cpus :cpu-busy busy
                                  :memory-total-mib (// memory.total-bytes MIB) :memory-available-mib (// memory.available-bytes MIB)
                                  :cpu-some-avg10 (if (is cpu None) None cpu.some-avg10)
                                  :cpu-full-avg10 (if (is cpu None) None cpu.full-avg10)
                                  :memory-some-avg10 memory.some-avg10 :memory-full-avg10 memory.full-avg10
                                  :cpu-nice (if (is nice None) None (min nice busy))
                                  :task-cpu-some-avg10 (if (is task-cpu None) None task-cpu.some-avg10))
               :ticks ticks))


(defk host-load-of [counters before at-ms [line None]]
  {:pre [(: counters HostCounters) (: before (| CpuTicks None)) (: at-ms int) (: line (| EvictionLine None))]
   :post [(: % LoadReading)] :tags {:context "worker" :role "judgment"}}
  "/proc の無い機体の OS の口の読み counters(問い ReadHostCounters の答え)と前の読みの刻み before を、機体の測った空きにするため(頭の
   註)。CPU の使いと nice の分は machine-load-of と同じく前の読みとの間の増えの比(cpu-busy-between・cpu-nice-between — 初めての読みと、
   刻みの数え手が一巡りして戻った読みは None)。memory は OS の数えた総量と空き(線 line が在れば線の大きさと追い出しまでの空き —
   machine-load-of と同じ)。PSI は OS に無いので None。"
  (val ticks (CpuTicks :busy counters.busy-ticks :total counters.total-ticks :cpus counters.cpus :nice counters.nice-ticks))
  (<- busy (| float None) (cpu-busy-between before ticks))
  (<- nice (| float None) (cpu-nice-between before ticks))
  (<- memory NodeMemory (line-memory (NodeMemory :total-bytes counters.memory-total-bytes
                                                 :available-bytes counters.memory-available-bytes :some-avg10 None :full-avg10 None)
                                     line))
  (LoadReading :load (MachineLoad :at-ms at-ms :cpus ticks.cpus :cpu-busy busy
                                  :memory-total-mib (// memory.total-bytes MIB) :memory-available-mib (// memory.available-bytes MIB)
                                  :cpu-some-avg10 None :cpu-full-avg10 None :memory-some-avg10 None :memory-full-avg10 None
                                  :cpu-nice (if (or (is nice None) (is busy None)) None (min nice busy)))
               :ticks ticks))
