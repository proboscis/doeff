;;; 止めの合図の待ち(AwaitStop)の、テストの偽の宿のための答え手(#3871 の単位 3)。
;;;
;;; 拍の間の眠りの本番の答え手 tick-pauses は、眠りを止めの合図の待ちと競わせる。止めを拍の頭の問い(StopRequested)だけで読む偽の宿は、
;;; tick-pauses の外側にこれを置く — 合図は来ないので、周の間の眠りは期限まで(止めは次の周の頭で読む)。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_core_effects.scheduler [CreatePromise Promise Wait])
(import doeff_core_effects.stop_signal_effects [AwaitStop])


(defhandler stop-signal-never-comes
  ;; 拍の眠りを縮める止めの合図が来ない世界のため(待ちは誰も満たさない Promise の上で、競わせの相手が勝てば取り消される)。
  (AwaitStop []
    (<- never Promise (CreatePromise))
    (<- reason str (Wait never.future))
    (resume reason)))
