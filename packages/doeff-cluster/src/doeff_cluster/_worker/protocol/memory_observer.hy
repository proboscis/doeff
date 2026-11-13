;;; 機体の memory の逼迫の観測: worker の観測 ObserveMemoryTight に、機体の memory の見張り(worker/protocol/memory_watch)
;;; が前の読みで言った様子(MemoryCell.level)が TIGHT か DANGER かで答える答え手 memory-observer。見張りは調整ループの外の背景の task で、
;;; 様子は 5 秒ごとに入れ物に書かれる — 調整ループはその入れ物を拍ごとに読むだけ(読みも止めも見張りの側)。worker の入口(worker/entry/main)
;;; が並べる。見張りが止めた job の根の印(MemoryCell.kills)も同じ入れ物から ObserveMemoryKills に答える(続けて止められた job の数え)。
;;; jobs の層の空き(ObserveJobsRoom)は、見張りの最後の回に決めた新しい受けの上限(jobs-intake — 置いた task の取り置きを入れない)と読んだ
;;; 使い(working set − slab_reclaimable)の差(worker/protocol/eviction_line の EvictionCell)。機体の memory の訳で止められた記録(ObserveMemoryStops — 見張りの止めの訳と、
;;; jobs の層の kernel の OOM kill の増えの刻)も答える。答える前に jobs の層の memory.events を読み直し、見張りがまだ刻を付けていない
;;; 増えも添える(kernel が止めた子の終わりを回収する拍が、見張りの次の回より先に来ても訳を落とさない)。この worker の持つ区画
;;; (ObserveCompartments)は、同じ入れ物が持つ heartbeat の返事の写し(EvictionCell.compartments)で答える。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster._worker.protocol.observations [ObserveMemoryTight ObserveMemoryKills ObserveMemoryStops ObserveJobsRoom ObserveUrgentJobsRoom
  ObserveCompartments])
(import doeff_cluster._worker.protocol.eviction_line [jobs-oom-count])
(import doeff_cluster._worker.intent.worker_model [MemoryStops])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell])
(import doeff_cluster._worker.core.memory_rules [MemoryLevel])


(defhandler memory-observer [#^ MemoryCell cell]
  ;; 引数に残す理由: 見張りの背景の task と調整ループが分ける入れ物は、起動の時に 1 度だけ作る入口の値(worker/entry/main)。
  (ObserveMemoryTight []
    (resume (!= cell.level MemoryLevel.CALM)))
  (ObserveMemoryKills []
    (resume (frozenset (gfor k cell.kills #(k.job k.pid)))))
  (ObserveMemoryStops []
    ;; 見張りがまだ刻を付けていない oom_kill の増え(見張りの前の回の後に kernel が止めた — 今の拍で回収する終わりの訳)。
    (val eviction cell.eviction)
    (<- count (| int None) (jobs-oom-count eviction))
    (resume (MemoryStops :kills (tuple (gfor k cell.kills :if k.reason #(k.job k.pid k.reason)))
                         :oom-at-ms eviction.oom-at-ms
                         :oom-unstamped (and (is-not count None) (is-not eviction.oom-count None) (> count eviction.oom-count))
                         :jobs-limit-bytes eviction.jobs-limit)))
  (ObserveJobsRoom []
    ;; 上限を掛けていない・使いを読めなかった回は None(空きで縛らない)。
    (val eviction cell.eviction)
    (resume (if (or (is eviction.jobs-intake None) (is eviction.jobs-held None))
                None
                (- eviction.jobs-intake eviction.jobs-held))))
  (ObserveUrgentJobsRoom []
    ;; 線が jobs の層を縮めていない回(jobs-intake-urgent が None — heartbeat も urgent-jobs-room を名乗らない)・使いを読めなかった回は None。
    (val eviction cell.eviction)
    (resume (if (or (is eviction.jobs-intake-urgent None) (is eviction.jobs-held None))
                None
                (- eviction.jobs-intake-urgent eviction.jobs-held))))
  (ObserveCompartments []
    ;; 最後に読めた heartbeat の返事の持つ区画(coordinator への口が入れ物へ写す — coordinator_link の accepted-compartments)。
    (resume cell.eviction.compartments)))
