;;; worker の生死の出来事(#3864・#3850 の問い 5 のア): coordinator が、worker の生死の期限(最後の heartbeat +
;;; lease-ms — cluster_policy.liveness-deadline の 1 か所)の切れと戻りを、doeff-events の出来事として process の外へ出す。doeff の
;;; 汎用の出来事で、使い手の業務の語を持たない。
;;;
;;; 約束(#3864 の本文の表が正本):
;;;   - 「変わった時に 1 度」: 前後の状態の沈黙の集合(ClusterState.silent)を比べ、入った名に WorkerGone・出た名のうち名簿に残る名に
;;;     WorkerBack(長い沈黙で名簿から消えた worker は戻った事にしない)。
;;;   - coordinator が起きた時に、今の状態(沈黙の worker の WorkerGone・生きている worker の WorkerBack)を 1 度出す(沈黙の集合は
;;;     保存の形に無い — 受け手の追いつきにも成る)。
;;;   - 受け手は boot で古い物を捨てる(保存しない知らせなので、起き直しの後に出し直した古い WorkerGone が WorkerBack より後に届く事が在る)。
;;;   - 出し損ねは doeff-events の notice_events_handler が鍵(worker の名)ごとに持ち、broker が戻った時に出す(ADR-DOE-EVENTS-002 R5)。
;;; 受け手は coordinator/protocol/worker_notices の WORKER-NOTICE-ROUTES を import して reads を付ける。
(require doeff-hy.macros [val])
(require doeff-hy.record [defwire])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass])


(defwire WorkerGone
  "worker が居なく成った: worker = coordinator の名簿の名・boot = 期限が切れた時に coordinator が知っていたその worker の process の
   起動の印(WorkerInfo.boot — 名乗らない古い worker は None)・deadline-ms = 切れた期限(最後の heartbeat + lease-ms・epoch ms)。"
  {:tags {:context "coordinator" :role "type"} :names :camel :unknown :reject}
  (#^ str worker)
  (#^ (| str None) boot)
  (#^ int deadline-ms))


(defwire WorkerBack
  "worker が生きている(戻った・coordinator の起動の時に生きていた): worker = coordinator の名簿の名・boot = その process の起動の印
   (WorkerInfo.boot)・seen-ms = coordinator が最後に heartbeat を受けた刻(epoch ms)。"
  {:tags {:context "coordinator" :role "type"} :names :camel :unknown :reject}
  (#^ str worker)
  (#^ (| str None) boot)
  (#^ int seen-ms))
