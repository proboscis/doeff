;;; 模擬の worker の機体の memory の見張り: 本番の worker の入口だけが回す見張り(worker/protocol/memory_watch の
;;; memory-watch-step)を、sim の worker の世代ごとに、模擬の process の表と模擬の /proc/meminfo の上で回すための部品。回す係
;;; (sim/local の sim-memory-watch)が世代の宿の真実を読み、ここで表と file を作って本物の 1 回を走らせる。
;;;
;;; 何のためか: 2026-10-30 の node-h で、job の shim の直下に孤児が約 8.2 万本溜まり、見張りの 1 回(木の辿りが process の数の 2 乗)が
;;; worker の main の scheduler を何十分も塞いだ(heartbeat が止まり Pod が NotReady)。模擬の時計は仮想なので、純な計算は時計を進めない
;;; — 塞ぎを模擬で見るには、1 回の読みの手間を測って worker の処理の止まりに換える(回す係が StallWorker と同じ stalled-until-ms に載せる)。
;;;
;;;   SimProcFlood          job 1 つの根の直下に溜まった子 process の群れ(数・1 本の rss・命令の名 — 本番の shim に引き取られた孤児の形)。
;;;   SimMemoryWatch        SimWorker の memory-watch(None = 見張りを回さない — 既定): 群れ・読みの間隔・手間の上限・止まりに換える下限。
;;;   SimMemoryRead         1 回の読みの記録(MemoryReadsOf の答えの要素): 刻・表の行数・手間の CPU 秒・上限を越えたか・載せた止まり。
;;;   sim-proc-rows         宿の観測(生きている job の process)と群れから、process の表(ProcRow の列)を作る。
;;;   sim-meminfo-text      機体(memory の全体と空き)を /proc/meminfo の text にする。
;;;   sim-watch-answers     1 回の読みの effect(ReadText・ReadProcTable・ObserveJobRoots・SignalPid)に、作った表と file で答える。
;;;   limited-cpu-seconds  Program 1 つを CPU の上限つきで走らせ、使った CPU 秒を測る(上限を越えれば計算を切る)。
;;;
;;; 本番との差: 模擬の見張りは止めない(guard を偽にして回す — 模擬の process の木は群れの data で、止めの相手が居ない。SignalPid が届けば
;;; 頭の註のとおり断る)。PSI と container の cgroup の file は無い(本番の PSI の無い機体と同じく None で読む)。手間は CPU の秒で、機体の
;;; 込み具合で伸びる — 上限は直した見張り(8 万本で 1 秒前後)と直す前の見張り(8 万本で何十分)を十分に見分ける幅に置く。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; dataclass = defrecord の展開が名指す
(import signal)
(import threading)
(import time)
(import doeff [Program])
(import doeff_core_effects [ReadText FileFailed])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid])
(import doeff_cluster._worker.core.memory_rules [JobRoot MIB])
(import doeff_cluster._worker.protocol.observations [ObserveJobRoots])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings])

;; 群れの子 process の pid の始まり(sim の job の pid — NextPid の 1 から — と重ならない大きさ)。
(val FLOOD-BASE-PID 10000000)


(defrecord SimProcFlood
  "job 1 つの根の直下に溜まった子 process の群れ(頭の註): job = job の名(その job の生きている process の根の直下に置く — 根が無い間は
   表に出ない)・children = 子の数・child-rss-bytes = 子 1 本の rss・comm = 子の命令の名。"
  {:check [(> children 0) (>= child-rss-bytes 0)]}
  (#^ str job)
  (#^ int children)
  (setv #^ int child-rss-bytes MIB)
  (setv #^ str comm "python"))


(defrecord SimMemoryWatch
  "SimWorker の memory-watch(頭の註): floods = 子 process の群れ(SimProcFlood の列)・sample-seconds = 読みの間隔(仮想の秒・本番の
   MemoryWatchSettings の既定と同じ 5 秒)・cpu-limit-seconds = 1 回の読みの CPU の上限(越えた読みは切って、終わらない読みとして
   unfinished-stall-seconds の間 worker を止める)・stall-floor-seconds = 止まりに換える手間の下限(未満の手間は記録だけ — 仮想の時計を
   CPU の揺れで揺らさない)・unfinished-stall-seconds = 上限を越えた読みに載せる止まり(本番の何十分に当たる — 既定 1 時間)。"
  {:check [(> sample-seconds 0) (> cpu-limit-seconds 0) (>= stall-floor-seconds 0) (> unfinished-stall-seconds 0)]}
  (setv #^ (get tuple #(SimProcFlood ...)) floods #())
  (setv #^ float sample-seconds 5.0)
  (setv #^ float cpu-limit-seconds 10.0)
  (setv #^ float stall-floor-seconds 1.0)
  (setv #^ float unfinished-stall-seconds 3600.0))


(defrecord SimMemoryRead
  "模擬の見張りの 1 回の読みの記録(頭の註): worker・boot = 世代・at = 読んだ刻(epoch ms)・rows = 表の行数・cpu-seconds = 1 回の CPU の
   秒(上限を越えた読みは切った所までの秒)・finished = 上限の内で終わったか・stalled-ms = worker の処理の止まりに載せた ms(0 = 載せない)。"
  (#^ str worker)
  (#^ str boot)
  (#^ int at)
  (#^ int rows)
  (#^ float cpu-seconds)
  (#^ bool finished)
  (#^ int stalled-ms))


(defclass ReadOverLimit [BaseException]  ; class にする理由: 上限の信号で純な計算を切る例外の型(見張りの loop の except Exception に捕まらない — 欄も状態も足さない)
  "1 回の読みが CPU の上限を越えた(limited-cpu-seconds が信号の受け手から投げて計算を切る)。")


(defk sim-watch-settings [watch]
  {:pre [(: watch SimMemoryWatch)] :post [(: % MemoryWatchSettings)] :tags {:context "doeff-cluster" :role "judgment"}}
  "模擬の見張りの設定(頭の註の本番との差): 本番の既定のまま、読みの間隔を watch に合わせ、止めない(guard を偽)。"
  (MemoryWatchSettings :sample-seconds watch.sample-seconds :guard False))


(defk sim-proc-rows [roots task-rss-bytes floods]
  {:pre [(: roots tuple) (: task-rss-bytes int) (: floods tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "process の表(ProcRow の列・pid の昇順)を作るため: 生きている job の根 roots(JobRoot の列 — 表の頭・rss は task-rss-bytes)・群れ
   floods の子(その job の根の直下 — 根が無い群れは出さない。job の根が 2 つ在れば最初の根に置く)。container の頭(本番の tini)は
   置かない — sim の job の pid は 1 から振られ、頭の pid 1 と重なると木が自分を親に持つ輪になる。"
  (val heads (dict (gfor root (reversed roots) #(root.name root.pid))))
  (val base (tuple (sorted (gfor root roots (ProcRow :pid root.pid :ppid 0 :start-ticks root.pid :rss-bytes task-rss-bytes :comm "python"))
                           :key (fn [row] row.pid))))
  (var next-pid FLOOD-BASE-PID)
  (var flooded [])
  (for [flood floods]
    (val head (.get heads flood.job))
    (when (is-not head None)
      (.extend flooded (gfor n (range flood.children)
                             (ProcRow :pid (+ next-pid n) :ppid head :start-ticks (+ next-pid n) :rss-bytes flood.child-rss-bytes
                                      :comm flood.comm)))
      (:= next-pid (+ next-pid flood.children))))
  (+ base (tuple flooded)))


(defk sim-meminfo-text [total-mib available-mib]
  {:pre [(: total-mib int) (: available-mib int) (>= total-mib available-mib 0)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "機体の memory の全体 total-mib と空き available-mib を /proc/meminfo の text にするため(見張りが読む MemTotal・MemAvailable の欄)。"
  (.format "MemTotal:       {} kB\nMemFree:        {} kB\nMemAvailable:   {} kB\n" (* total-mib 1024) (* available-mib 1024) (* available-mib 1024)))


(defhandler sim-watch-answers [#^ MemoryWatchSettings settings #^ str meminfo #^ tuple roots #^ tuple rows]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 表と file は読みごとに宿の真実から作る値(読み 1 回の世界そのもの)で、Ask で読む設定ではない。
  ;; 見張りの 1 回の effect に答える(頭の註): /proc/meminfo は meminfo・PSI と container の cgroup の file は無い・job の根は roots・表は rows。
  ;; 止め(SignalPid)は模擬の見張りに無い(guard を偽で回す — 届けば設定の壊れなので名指して断る)。
  (ReadText [path]
    (resume (if (= path settings.meminfo-path)
                meminfo
                (FileFailed :path path :detail "模擬の見張りの機体に無い file"))))
  (ReadProcTable []
    (resume rows))
  (ObserveJobRoots []
    (resume roots))
  (SignalPid [pid start-ticks signal]
    (raise (RuntimeError (.format "模擬の見張りは止めない(guard を偽で回す)のに SignalPid が届いた: pid {}" pid)))))


(defk limited-cpu-seconds [body cpu-limit-seconds]
  {:pre [(: body Program) (: cpu-limit-seconds float) (> cpu-limit-seconds 0)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "program"}}
  "body を走らせ、使った CPU の秒を測るため(答え = #(秒 上限の内で終わったか))。主の thread では CPU の時計の信号(SIGPROF)で上限を
   越えた計算を切る(ReadOverLimit — 直す前の見張りは 8 万本で何十分なので、模擬が待ち続けない)。主の thread の外では信号を置けないので
   切らずに測るだけ(走り終えるまで待つ)。前の信号の受け手と時計は戻す。"
  (val armed (is (threading.current-thread) (threading.main-thread)))
  (var before-handler None)
  (when armed
    (:= before-handler (signal.signal signal.SIGPROF (fn [signum frame] (raise (ReadOverLimit)))))
    (signal.setitimer signal.ITIMER_PROF cpu-limit-seconds))
  (val started (time.process-time))
  (var finished True)
  (try
    (<- body)
    (except [ReadOverLimit]
      (:= finished False))
    (finally
      (when armed
        (signal.setitimer signal.ITIMER_PROF 0)
        (signal.signal signal.SIGPROF before-handler))))
  #((- (time.process-time) started) finished))
