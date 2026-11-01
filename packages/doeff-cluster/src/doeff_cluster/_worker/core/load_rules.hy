;;; 機体の測った空き(CPU の使い・memory の空き・PSI)を heartbeat で名乗るための判断 — /proc の綴りを数にし、前の読みとの間の CPU の
;;; 使いを数える。I/O を持たない(読みは worker/protocol/coordinator_link が file の効果で LOAD-READ-EVERY-MS ごとに集める)。
;;;
;;; 何のためか: 置き先を固定の枠(枠の file の memoryMiB・taskReserve)でなく、機体の今の空きで決める下ごしらえ。この段では heartbeat に
;;; 数を載せ、coordinator の GET /state で読めるようにするだけで、置き方の判断は変えない。
;;;
;;; 読む file(どれも機体の全体の値 — container の中でも cgroup に縛られない):
;;;   /proc/stat             頭の行 cpu の刻みの和(user nice system idle iowait irq softirq steal)と、行 cpuN の数(CPU の数)。
;;;                          使い = 前の読みからの (和 − idle − iowait) の増え / 和の増え。guest と guest_nice は user と nice に含まれるので足さない。
;;;   /proc/meminfo          MemTotal と MemAvailable(memory の見張りと同じ読み — worker/core/memory_rules の node-memory-of)。
;;;   /proc/pressure/cpu     PSI の some と full の avg10(%)。full の行の無い古い kernel は full を None。
;;;   /proc/pressure/memory  PSI の some と full の avg10(%)。
;;; PSI の file の無い機体(PSI を切った kernel)は PSI の欄を None にし、/proc/stat か /proc/meminfo の読めない機体(Mac)は測りを名乗らない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster._worker.core.memory_rules [MIB NodeMemory node-memory-of])

;; 測りを読み直す間。PSI の avg10 が 10 秒の平均なので、その半分の間で読めば名乗る値の遅れは avg10 の窓の内に収まる。CPU の使いは
;; この間の平均になる。
(val LOAD-READ-EVERY-MS (* 5 1000))
;; /proc/stat の頭の行 cpu の欄のうち、使いに数えない刻み(欄の位置 — 0 = user)。3 = idle・4 = iowait。
(val IDLE-FIELDS #(3 4))
;; 頭の行 cpu の欄のうち、和に数える数(user nice system idle iowait irq softirq steal — 後ろの guest と guest_nice は user と nice に含まれる)。
(val TICK-FIELDS 8)


(defrecord LoadPaths
  "測りの読む file の置き場(頭の註): stat・meminfo・cpu-pressure・memory-pressure。既定は機体の /proc。"
  (setv #^ str stat "/proc/stat")
  (setv #^ str meminfo "/proc/meminfo")
  (setv #^ str cpu-pressure "/proc/pressure/cpu")
  (setv #^ str memory-pressure "/proc/pressure/memory"))


(defrecord CpuTicks
  "/proc/stat の 1 回の読み: busy = 頭の行 cpu の和から idle と iowait を除いた刻み・total = 頭の行 cpu の刻みの和(TICK-FIELDS)・
   cpus = 行 cpuN の数(今 online の CPU の数)。"
  {:check [(>= busy 0) (>= total busy) (> cpus 0)]}
  (#^ int busy)
  (#^ int total)
  (#^ int cpus))


(defrecord Pressure
  "PSI の file 1 つの avg10(%): some-avg10 = some の行・full-avg10 = full の行(行の無い kernel は None)。"
  {:check [(>= some-avg10 0) (or (is full-avg10 None) (>= full-avg10 0))]}
  (#^ float some-avg10)
  (#^ (| float None) full-avg10))


(defrecord MachineLoad
  "機体の測った空き 1 回分(heartbeat の欄 measured): at-ms = 読んだ刻(worker の壁の時計の epoch ms)・cpus = CPU の数・cpu-busy =
   前の読みからの CPU の使いの割合(0〜1 — 初めての読みと、刻みの進まない読みは None)・memory-total-mib / memory-available-mib =
   MemTotal / MemAvailable(MiB — 切り捨て)・cpu-some-avg10 / cpu-full-avg10 / memory-some-avg10 / memory-full-avg10 = PSI の avg10(%)
   (PSI の file の無い機体と、full の行の無い kernel の cpu は None)。"
  {:check [(> cpus 0) (or (is cpu-busy None) (<= 0 cpu-busy 1)) (>= memory-total-mib memory-available-mib 0)]}
  (#^ int at-ms)
  (#^ int cpus)
  (#^ (| float None) cpu-busy)
  (#^ int memory-total-mib)
  (#^ int memory-available-mib)
  (#^ (| float None) cpu-some-avg10)
  (#^ (| float None) cpu-full-avg10)
  (#^ (| float None) memory-some-avg10)
  (#^ (| float None) memory-full-avg10))


(defrecord LoadReading
  "測りの 1 回の読みの答え: load = 名乗る測り・ticks = この読みの /proc/stat(次の読みが CPU の使いを数える起点)。"
  (#^ MachineLoad load)
  (#^ CpuTicks ticks))


(defk load-read-due [read-ms now-ms]
  {:pre [(: read-ms (| int None)) (: now-ms int)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "この拍で測りを読み直すかを決めるため(read-ms = 前に読んだ刻・None = まだ読んでいない)。壁の時計が戻った時も読み直す。"
  (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) LOAD-READ-EVERY-MS)))


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
  (CpuTicks :busy (- total (sum (gfor i IDLE-FIELDS (get ticks i)))) :total total :cpus cpus))


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


(defk machine-load-of [stat meminfo cpu-pressure memory-pressure before at-ms]
  {:pre [(: stat str) (: meminfo str) (: cpu-pressure (| str None)) (: memory-pressure (| str None)) (: before (| CpuTicks None)) (: at-ms int)]
   :post [(: % LoadReading)] :tags {:context "worker" :role "judgment"}}
  "/proc/stat・/proc/meminfo・PSI の 2 つの file の中身(読めない PSI の file は None)と前の /proc/stat の読み before を、機体の測った
   空きにするため。形の違う中身は ValueError(読みの訳 — 呼び手が名乗りを外す)。"
  (<- ticks CpuTicks (cpu-ticks-of stat))
  (<- busy (| float None) (cpu-busy-between before ticks))
  (<- memory NodeMemory (node-memory-of meminfo memory-pressure))
  (var cpu None)
  (when (is-not cpu-pressure None)
    (<- read Pressure (pressure-of cpu-pressure "/proc/pressure/cpu"))
    (:= cpu read))
  (LoadReading :load (MachineLoad :at-ms at-ms :cpus ticks.cpus :cpu-busy busy
                                  :memory-total-mib (// memory.total-bytes MIB) :memory-available-mib (// memory.available-bytes MIB)
                                  :cpu-some-avg10 (if (is cpu None) None cpu.some-avg10)
                                  :cpu-full-avg10 (if (is cpu None) None cpu.full-avg10)
                                  :memory-some-avg10 memory.some-avg10 :memory-full-avg10 memory.full-avg10)
               :ticks ticks))
