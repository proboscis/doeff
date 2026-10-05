;;; 依る Service の短い停止を越える戻りの待ち(#3490)。AwaitServiceReady(#3470)は「名を挙げた Service が今 Ready か」を
;;; 読んで待つだけなので、呼び手の撃ち直しがまだ届かない時に、coordinator が古い Ready を見ている間は撃ち直しが回り続ける。ここの 2 つがそれを抑える:
;;;
;;;   service-back         前に読んだ coordinator の版から版が変わるのを上限 SERVICE-BACK-CHANGE-SECONDS まで待ち(上限で返ったら待ち直さない —
;;;                        process が生きたまま店だけ止まる止まりは Service が Ready のまま版が動かず、worker の生死も版に入らない)、それから
;;;                        AwaitServiceReady を撃つ。読んだ版は process の状態(Get / Put — 鍵は Service の名ごと)に置き、次の問いの「前の版」にする。
;;;                        上限は持たない(待つ側が持つ)。撃ち直しの間隔は「coordinator の変化 1 つか SERVICE-BACK-CHANGE-SECONDS に 1 回」で、
;;;                        戻りは遅くとも SERVICE-BACK-CHANGE-SECONDS で拾える。
;;;   service-back-within  service-back を見張りの task で撃ち、答えを約束で受けて seconds 秒まで待つ(上限が先なら None・待ち終えたら見張りを止める)
;;;                        — 時間で撃ち直さず、上限は約束の待ち 1 つ(promise-or-timeout)が持つ。
;;;
;;; 使い手 = 依る service への追記の書き手・読み手と、記録の合図の源の戻りの訳。外側に要る物: process の
;;; 状態の答え手(doeff_core_effects.handlers の state)・AwaitRunnersChange と AwaitServiceReady の答え手(本番の detached-cluster・sim の宿)。
(require doeff-hy.macros [defk <- val var])
(import doeff_core_effects.effects [Get Put])
(import doeff_core_effects.scheduler [Cancel CompletePromise CreatePromise Promise Spawn Task TaskCancelledError Wait])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange AwaitServiceReady RunnersChange RunnersUnreachable RunnersWatchMissing
                                                   ServiceReady])

(val MODULE-TAGS {:context "doeff-cluster" :role "program"})

;; 戻りの問いごとに、coordinator の版の変化を待つ上限(秒)— 版が動かない止まりでも、この秒ごとに 1 度は Ready を読み直して呼び手へ返す。
(val SERVICE-BACK-CHANGE-SECONDS 10.0)
;; 前に読んだ版を置く process の状態の鍵の頭(鍵 = #(この頭 Service の名))。
(val SERVICE-BACK-STATE "doeff-cluster/service-back")


(defk service-back [name]
  {:pre [(: name str)] :post [(: % ServiceReady)] :tags {:context "doeff-cluster" :role "program"}}
  "依る Service name の戻りを 1 度待つため(頭の註 — 前に読んだ版から版が変わるのを上限まで待ってから Ready を読む)。答え = Ready と読めた
   ServiceReady(revision = 見た版の大きい方 — 次の問いの前の版)。待つ口の無い coordinator(GET /watch が無い)は名指して落ちる。coordinator に
   届かない間は版を待たずに AwaitServiceReady へ進む(届かない間の待ちはその答え手の中)。"
  (val key #(SERVICE-BACK-STATE name))
  (<- seen (| int None) (Get key))
  (val before (if (is seen None) 0 seen))
  (<- change (| RunnersChange RunnersWatchMissing RunnersUnreachable) (AwaitRunnersChange before SERVICE-BACK-CHANGE-SECONDS))
  (val now (match change
             (RunnersChange :revision revision) revision
             (RunnersWatchMissing :detail detail)
               (raise (RuntimeError (.format "Service {!r} の戻りを版の変化で待てない(coordinator に GET /watch が無い): {}" name detail)))
             _ before))
  (<- ready ServiceReady (AwaitServiceReady name))
  (val kept (max now ready.revision))
  (<- (Put key kept))
  (ServiceReady :name name :revision kept))


(defk back-announced [name promise]
  {:pre [(: name str) (: promise Promise)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "service-back-within の見張りの task の本体: service-back の答えで約束 promise を満たすため(見張りは期限を持たない — 上限は約束の待ちが持つ)。"
  (<- ready ServiceReady (service-back name))
  (<- (CompletePromise promise ready))
  None)


(defk stopped [task]
  {:pre [(: task Task)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "見張りの task を止め、解け終わるまで待つため(上限が先なら Cancel の答え TaskCancelledError を飲む。見張りが自分で落ちていれば、
   その例外は Wait が上げる — 待つ口の無い coordinator を黙らせない)。"
  (<- (Cancel task))
  (try
    (<- (Wait task))
    (except [TaskCancelledError]
      None))
  None)


(defk service-back-within [name seconds]
  {:pre [(: name str) (: seconds (| int float))] :post [(: % (| ServiceReady None))] :tags {:context "doeff-cluster" :role "program"}}
  "依る Service name の戻りを seconds 秒まで待つため(頭の註)。答え = 戻った ServiceReady か、上限が先の None(呼び手は待った秒と最後の
   理由を名指して落ちる)。seconds が 0 以下なら待たずに None。"
  (when (<= seconds 0)
    (return None))
  (<- promise Promise (CreatePromise))
  (<- watcher Task (Spawn (back-announced name promise)))
  (var came None)
  ;; 見張りを止める効果は、待ちの例外(取り消しの TaskCancelledError を含む Exception)と通常の終わりでだけ撃つ。finally に置かない:
  ;; process が殺された時(Discard・世界の終わりの GC)に CPython が送る GeneratorExit の中で効果を yield すると「generator ignored
  ;; GeneratorExit」になる(Exception の外なので受けずに上げ、殺された process は見張りごと消える — #3557)。
  (try
    (<- waited (| ServiceReady None) (promise-or-timeout promise.future seconds))
    (:= came waited)
    (except [error Exception]
      (<- (stopped watcher))
      (raise error)))
  (<- (stopped watcher))
  came)
