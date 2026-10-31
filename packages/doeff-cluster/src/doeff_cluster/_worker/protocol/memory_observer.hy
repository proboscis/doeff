;;; 機体の memory の逼迫の観測: worker の観測 ObserveMemoryTight に、機体の memory の見張り(worker/protocol/memory_watch)
;;; が前の読みで言った様子(MemoryCell.level)が TIGHT か DANGER かで答える答え手 memory-observer。見張りは調整ループの外の背景の task で、
;;; 様子は 5 秒ごとに入れ物に書かれる — 調整ループはその入れ物を拍ごとに読むだけ(読みも止めも見張りの側)。worker の入口(worker/entry/main)
;;; が並べる。見張りが止めた job の根の印(MemoryCell.kills)も同じ入れ物から ObserveMemoryKills に答える(続けて止められた job の数え)。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster._worker.protocol.observations [ObserveMemoryTight ObserveMemoryKills])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell])
(import doeff_cluster._worker.core.memory_rules [MemoryLevel])


(defhandler memory-observer [#^ MemoryCell cell]
  ;; 引数に残す理由: 見張りの背景の task と調整ループが分ける入れ物は、起動の時に 1 度だけ作る入口の値(worker/entry/main)。
  (ObserveMemoryTight []
    (resume (!= cell.level MemoryLevel.CALM)))
  (ObserveMemoryKills []
    (resume (frozenset (gfor k cell.kills #(k.job k.pid))))))
