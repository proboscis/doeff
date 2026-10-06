;;; 拍の間の眠りの起こし方(ArmWake・DisarmWake — #3834)の代役。本番の眠りの答え手 tick-pauses を偽の宿の上で回す検が、眠りの答え手の
;;; 外側に置く: 起こし方は「拍ごと」(EveryTick)・子の終わりの見張りは掛けない — 眠りは今までどおり tick-seconds と宣言の変化の呼び鈴で
;;; 決まる(偽の宿は子を持たず、heartbeat の刻も知らない)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster.worker.protocol.observations [ArmWake DisarmWake WorkerWake EveryTick])


(defhandler every-tick-wake
  (ArmWake [bell] (resume (WorkerWake :due (EveryTick))))
  (DisarmWake [bell] (resume None)))
