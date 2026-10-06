;;; worker の拍の間の眠りの起こし方のまとめ wake-host(#3834)— 眠りの答え手(worker/protocol/tick_pauses)が眠る前に出す ArmWake に、
;;; 各言い換えの起こし方(子の終わりの見張りと時間の期限 — ProcessesWake・CodeWake・EnvsWake・ProbesWake・WarmWake)・coordinator への口の
;;; 次の heartbeat の刻(LinkDue)を問うてまとめて答え、子の終わりの見張り(汎用の WatchExits)と止めの合図(StopWake)を眠りの bell に掛ける。
;;; 起きた後の DisarmWake で両方を外す。I/O を持たない — 子の終わりを待つのは外側の汎用の答え手(本物 = subprocess-handler の pidfd の
;;; 見張り)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_core_effects.process_effects [WatchExits UnwatchExits])
(import doeff_cluster.worker.protocol.observations [ArmWake DisarmWake HostWake WorkerWake EveryTick ProcessesWake CodeWake EnvsWake ProbesWake
                                                    WarmWake LinkDue StopWake])


(defk earliest-due [link hosts]
  {:pre [(: link (| int EveryTick)) (: hosts tuple)] :post [(: % (| int EveryTick))]}
  "次に拍を打つべき最初の刻を決めるため: heartbeat を拍ごとに送る口か拍ごとに読み直す言い換え(EveryTick)が在れば拍ごと、それ以外は
   次の heartbeat の刻と言い換えの期限(在る物だけ)の早い方。"
  (val dues (+ #(link) (tuple (gfor h hosts :if (is-not h.due-ms None) h.due-ms))))
  (if (any (gfor due dues (isinstance due EveryTick)))
      (EveryTick)
      (min dues)))


(defhandler wake-host
  (ArmWake [bell]
    (<- processes HostWake (ProcessesWake))
    (<- codes HostWake (CodeWake))
    (<- envs HostWake (EnvsWake))
    (<- probes HostWake (ProbesWake))
    (<- warm HostWake (WarmWake))
    (val hosts #(processes codes envs probes warm))
    (<- (WatchExits :bell bell :targets (tuple (gfor h hosts target h.targets target))))
    (<- (StopWake bell))
    (<- link (| int EveryTick) (LinkDue))
    (<- due (| int EveryTick) (earliest-due link hosts))
    (resume (WorkerWake :due due)))
  (DisarmWake [bell]
    (<- (UnwatchExits :bell bell))
    (<- (StopWake None))
    (resume None)))
