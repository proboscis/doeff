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
;;; 会話のターンに残す線(2026-11-08): 機体の memory を、作り直せない会話のターンと作り直せる task が分け合う機体(zeus)では、
;;; 上に載る系の新しいターンを受ける係が、MemAvailable を保たせたい線(MiB の数 1 つ)を file に書く(cargo の中間物の掃除の係
;;; deploy/k8s/nodes/zeus/cargo-build-keeper.sh が読むのと同じ file — 枠の file の行の keepLineFile が名指す)。その file が読める間は、
;;; 名乗る memory の空きを min(追い出しまでの空き, MemAvailable − 線)にする(kept-memory)。訳: 追い出しの線は kubepods の working set
;;; から数え、係がターンを断る MemAvailable の線より 25 GiB ほど遠い。追い出しまでの空きだけで名乗ると、係が「足りない」と新しいターンを
;;; 返している memory にも coordinator は task を置き、伸びた task がその memory を取る(11-08 03:00〜04:06 UTC — 追い出しまでの空き
;;; 34,901〜50,313 MiB・係の余白 3,301〜23,579 MiB)。線の数はこの worker が持たない(同じ量を 2 か所に宣言すると別々に動く)。線は係が
;;; ターンを断る所より数本ぶん上なので、task が先に置かれなくなり、ターンは係の余白で受け続けられる。
;;; 線を割った worker には新しい task が置かれず、needs の合う別の worker へ置かれる。その機体でしか走れない task は、MemAvailable が線 +
;;; 要り + 床へ戻るまで待つ(TASK-NO-ROOM の文で待ちに残る — 作り直せる task が待ち、作り直せないターンを先に通す)。総量(線の大きさ —
;;; 積む時に要りを断る上限)は替えない: 線は今の取り合いの順で、機体が空いた時に受けうる大きさではない。file が無い・数でない間(係が
;;; まだ起きていない)と、keepLineFile の無い機体は、今までどおり追い出しまでの空き(か MemAvailable)で名乗る。
;;;
;;; 急ぎの task の空き(2026-11-10): 線は作り直せる task を待たせる線で、送り手が急ぎ(coordinator/core/measured_room の
;;; KEEP-NOW-PRIORITY 以上の優先)を名乗る task まで待たせると、その機体でしか走れない急ぎの task は MemAvailable が線を越えて戻るまで
;;; 置かれない(zeus の 11-10 — MemAvailable 約 42 GiB・線 37984 MiB で名乗る空きは約 4 GiB、要り 6144 MiB・その機体だけを要る task が
;;; queued のまま待ち、送り手の後の仕事が全部止まった)。線が名乗る空きを縮めている間は、線を外して追い出しの線だけで数えた空き
;;; (urgent-available-mib)と jobs の層の空き(urgent-jobs-room-mib — 見張りの jobs-intake-urgent)も名乗り、coordinator は急ぎの task だけを
;;; その空きで置く。置いた後の jobs の層の memory.max は、線の下でも取り置き(要り − 今の使い)まで広がる(worker/core/eviction_rules の
;;; kept-jobs-limit)。急ぎの task は線の内の memory を使い、その分だけ上に載る系は新しいターンを待たせる(作り直せない走っている
;;; ターンは追い出しの線の手前の空きで守られる)。線が名乗る空きを縮めていない回は名乗らない(None — 平常の空きと同じ)。
;;;
;;; /proc の無い機体(macOS): 入口が LoadPaths の代わりに印 HostLoad を渡し、同じ間ごとに問い ReadHostCounters で OS の口から CPU の
;;; 刻みと memory を読む(答え手 foundation/host_counters — mach の host_statistics・sysctl)。CPU の使いと nice の分は /proc/stat と同じく
;;; 前の読みとの間の刻みの増えの比で数え、memory は OS の数えた総量と空き(host-load-of)。PSI は OS に無いので None。測りを名乗らない
;;; worker の空きを coordinator は 0 と数える(measured_room)ので、/proc の無い機体もこの読みで他の機体と同じ式に載る。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster._worker.core.memory_rules [MIB NodeMemory JobUse JobMeter TASK-PREFIX node-memory-of])
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
;; 見張りが読んだ常駐の job ごとの CPU と memory を名乗りに使う古さの上限(task の使いと同じ — 止まった見張りの古い累計を今の値として
;; 名乗らない)。
(val JOB-METERS-FRESH-MS LINE-FRESH-MS)
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
   予約する・2026-11-02)・jobs-room-mib = task の層(jobs)の今の空き(MiB — 見張りが最後に決めた新しい受けの上限 − 読んだ
   使い(working set − slab_reclaimable)・jobs-room-of。負もありうる。None = 名乗らない〔線が無いか古い・層が無い〕— coordinator の置き先は、名乗った worker
   には要りがこの空きに収まる task だけを置く・2026-11-09)・prepare-wait-mib = keeper が版の準備を待つ間に新しい受けの外へ取り置いた量
   (MiB — worker/core/eviction_rules の prepare-wait-reserve。None = 待っていないか名乗らない〔線が無いか古い〕— coordinator の置き先は、
   名乗った worker に新しく常駐の job を置かない・2026-11-10)・job-meters = 常駐の job ごとの CPU と memory の今(worker/core/memory_rules の
   JobMeter の tuple — job-meters-told。None = 名乗らない〔見張りの読みが無いか古い〕— coordinator は保存せず GET /state の worker の行に
   写す・2026-11-10)・job-meters-at-ms = その見張りの読みの刻(epoch ms — job-meters が None の時は None)・urgent-available-mib /
   urgent-jobs-room-mib = 会話のターンに残す線を外して数えた memory の空きと jobs の層の空き(MiB — 頭の註の急ぎの task の空き。None =
   線が空きを縮めていない・名乗らない〔線が無いか古い〕— coordinator は急ぎの優先の task だけをこの空きで置く・2026-11-10)。"
  {:check [(> cpus 0) (or (is cpu-busy None) (<= 0 cpu-busy 1)) (>= memory-total-mib memory-available-mib 0)
           (or (is urgent-available-mib None) (>= memory-total-mib urgent-available-mib memory-available-mib))
           (or (is cpu-nice None) (and (is-not cpu-busy None) (<= 0 cpu-nice cpu-busy)))
           (or (is task-cpu-some-avg10 None) (>= task-cpu-some-avg10 0)) (or (is prepare-wait-mib None) (> prepare-wait-mib 0))
           (= (is job-meters None) (is job-meters-at-ms None))]}
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
  (setv #^ (| (get tuple #(TaskMemory ...)) None) task-uses None)
  (setv #^ (| int None) jobs-room-mib None)
  (setv #^ (| int None) prepare-wait-mib None)
  (setv #^ (| (get tuple #(JobMeter ...)) None) job-meters None)
  (setv #^ (| int None) job-meters-at-ms None)
  (setv #^ (| int None) urgent-available-mib None)
  (setv #^ (| int None) urgent-jobs-room-mib None))


(defrecord LoadReading
  "測りの 1 回の読みの答え: load = 名乗る測り・ticks = この読みの /proc/stat(次の読みが CPU の使いを数える起点)・keep-room-bytes = 機体の
   MemAvailable − 会話のターンに残す線(byte・負もありうる — None = 線が無い・読めない。見張りが jobs の層の上限をこの線の上にも収める —
   worker/core/eviction_rules の頭の註の上に載る系が残す線)。"
  (#^ MachineLoad load)
  (#^ CpuTicks ticks)
  (setv #^ (| int None) keep-room-bytes None))


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


(defk keep-line-of [text]
  {:pre [(: text (| str None))] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "会話のターンに残す線の file の中身 text(頭の註 — None = 読まない・読めない)を MiB の数にするため。前後の空白を除いて 1 以上の
   10 進の数でなければ None(線が無いと読む — 0 や既定の値で埋めない)。"
  (val value (if (is text None) "" (.strip text)))
  (if (and (.isdigit value) (.isascii value) (> (int value) 0)) (int value) None))


(defk kept-memory [memory machine keep-text]
  {:pre [(: memory NodeMemory) (: machine NodeMemory) (: keep-text (| str None))] :post [(: % NodeMemory)]
   :tags {:context "worker" :role "judgment"}}
  "名乗る memory(memory — line-memory の答え)の空きを、会話のターンに残す線(頭の註 — keep-text = 線の file の中身・None = 読まない)の
   上だけに絞るため: 空き = min(memory の空き, max(0, 機体の MemAvailable − 線))。総量と PSI は memory のまま。線が無い(keep-line-of が
   None)時は memory のまま。"
  (<- keep (| int None) (keep-line-of keep-text))
  (if (is keep None)
      memory
      (NodeMemory :total-bytes memory.total-bytes
                  :available-bytes (min memory.available-bytes (max 0 (- machine.available-bytes (* keep MIB))))
                  :some-avg10 memory.some-avg10 :full-avg10 memory.full-avg10)))


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


(defk job-meters-told [meters meters-at-ms now-ms]
  {:pre [(: meters tuple) (: meters-at-ms (| int None)) (: now-ms int)] :post [(: % (| tuple None))]
   :tags {:context "worker" :role "judgment"}}
  "見張りが最後に数えた常駐の job ごとの CPU と memory(meters = JobMeter の tuple・meters-at-ms = 数えた刻)を、heartbeat で名乗るかを
   決めるため。古い(JOB-METERS-FRESH-MS を越えた)・刻の無い・壁の時計が戻った読みは None(名乗らない — 止まった見張りの累計を
   今の値として出さない)。"
  (if (and (is-not meters-at-ms None) (<= 0 (- now-ms meters-at-ms) JOB-METERS-FRESH-MS)) meters None))


(defk jobs-room-of [line jobs-intake jobs-held]
  {:pre [(: line (| EvictionLine None)) (: jobs-intake (| int None)) (: jobs-held (| int None))] :post [(: % (| int None))]
   :tags {:context "worker" :role "judgment"}}
  "heartbeat で名乗る task の層(jobs)の今の空き(MiB)を知るため: 見張りが最後に決めた新しい受けの上限 jobs-intake − 最後に読んだ
   使い jobs-held(working set − slab_reclaimable・byte — 負もありうる。切り捨て)。回収できる page cache と slab は層の上限に当たると
   kernel が task を止める前に返すので、空きを塞がない(helios の 11-10 13:4x JST は inactive_file 2112 MiB を使いに数え、層が空でも要り
   5632 MiB の task を置けなかった。15:1x JST は task 0 本の層の working set 4212 MiB のうち slab_reclaimable が 1070 MiB)。受けの上限は、追い出しまでの空きから測った余裕・準備の取り置き・会話の
   CLI の層への貸しを引き、container の中の空きでも縛った値で、置いた task の取り置きを入れない(置き先が自分で予約を引く — worker/core/
   eviction_rules の頭の註の会話の CLI の層の帳面。jobs の層の memory.max はそれに取り置きを守る分を足す)。追い出しまでの
   空き(memory-available-mib)よりずっと小さい時が在る — atlas の 11-08 14:09 は追い出しまでの空き 7186 MiB・jobs の層の空きは負で、
   coordinator が空きを 5650 MiB と見て置いた要り 3584 MiB の task を、kernel が jobs の層の OOM で止めた。line = 名乗りに使える新しさの線
   (fresh-line — None は上限が今の線から決まった値と言えないので名乗らない)・上限か使いが None(まだ書いていない・読めなかった)も None。"
  (if (or (is line None) (is jobs-intake None) (is jobs-held None))
      None
      (// (- jobs-intake jobs-held) MIB)))


(defk prepare-wait-of [line prepare-wait]
  {:pre [(: line (| EvictionLine None)) (: prepare-wait int) (>= prepare-wait 0)] :post [(: % (| int None))]
   :tags {:context "worker" :role "judgment"}}
  "heartbeat で名乗る、keeper が版の準備を待つ間に新しい受けの外へ取り置いた量(MiB)を知るため: 見張りの最後の回の取り置き prepare-wait
   (byte — worker/core/eviction_rules の prepare-wait-reserve)を MiB に切り上げた値。待っていない(0)・名乗りに使える新しさの線が無い
   (fresh-line — None は取り置きが今の線から決まった値と言えない)時は None(欄を載せない)。切り上げは 1 byte の取り置きも名乗るため。"
  (if (or (is line None) (<= prepare-wait 0))
      None
      (- (// (- prepare-wait) MIB))))


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


(defk machine-load-of [stat meminfo cpu-pressure memory-pressure before at-ms [line None] [task-cpu-pressure None] [keep-line None]]
  {:pre [(: stat str) (: meminfo str) (: cpu-pressure (| str None)) (: memory-pressure (| str None)) (: before (| CpuTicks None)) (: at-ms int)
         (: line (| EvictionLine None)) (: task-cpu-pressure (| str None)) (: keep-line (| str None))]
   :post [(: % LoadReading)] :tags {:context "worker" :role "judgment"}}
  "/proc/stat・/proc/meminfo・PSI の 2 つの file の中身(読めない PSI の file は None)と前の /proc/stat の読み before を、機体の測った
   空きにするため。line = 機体の memory の見張りが最後に数えた追い出しの線(worker/core/eviction_rules — None = 線が無いか古い)。
   線が在れば memory の総量と空きは線の大きさと追い出しまでの空き(見張りの DANGER と同じ量 — coordinator はこの空きで task を置く)、
   無ければ MemTotal と MemAvailable。task-cpu-pressure = task の層の cpu.pressure の中身(None = 層が無いか読めない)。keep-line = 会話の
   ターンに残す線の file の中身(頭の註 — None = 読まない・読めない。読めれば空きを MemAvailable − 線の上に絞る — kept-memory)。形の
   違う中身は ValueError(読みの訳 — 呼び手が名乗りを外す)。"
  (<- ticks CpuTicks (cpu-ticks-of stat))
  (<- busy (| float None) (cpu-busy-between before ticks))
  (<- nice (| float None) (cpu-nice-between before ticks))
  (<- machine NodeMemory (node-memory-of meminfo memory-pressure))
  (<- by-line NodeMemory (line-memory machine line))
  (<- memory NodeMemory (kept-memory by-line machine keep-line))
  (<- keep (| int None) (keep-line-of keep-line))
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
                                  :task-cpu-some-avg10 (if (is task-cpu None) None task-cpu.some-avg10)
                                  ;; 急ぎの task の空き(頭の註): 線が空きを縮めた時だけ、線を外した空きも名乗る。
                                  :urgent-available-mib (if (< memory.available-bytes by-line.available-bytes)
                                                            (// by-line.available-bytes MIB) None))
               :ticks ticks
               :keep-room-bytes (if (is keep None) None (- machine.available-bytes (* keep MIB)))))


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
