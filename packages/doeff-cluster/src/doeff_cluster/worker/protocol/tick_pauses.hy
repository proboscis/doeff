;;; worker の拍と拍の間の待ち(AwaitNextTick)の本番の答え手 — 拍 tick-seconds を宣言の変化の呼び鈴と競わせて眠る(core/program.tick-pause・
;;; #2692)。本番の組(entry/main.production-handlers)と、拍を 1 つずつ打つ偽の宿の組が置く。模擬の時計の下の宿(sim/local.hy)は、
;;; 先の拍を本番の判断で試して静かな拍を一度に眠る(#2781)。
;;; 眠りは核の止めの合図の待ち(AwaitStop — 本番の答え手は os-signal-stop-handler)とも競わせ、SIGTERM で拍の長さを待たずに起きる
;;; (#3871 の単位 3)。模擬の宿は止めの頼みで眠りの鈴を鳴らすので、この競わせを持たない。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_core_effects.scheduler [Cancel Future Race Spawn Task])
(import doeff_core_effects.stop_signal_effects [AwaitStop StopRequested])
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick WorkerPolicy])
(import doeff_cluster.worker.core.program [tick-pause])


(defk stop-reason []
  {:pre [] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "止めの合図が来るまで待ち、その理由を返すため(拍の眠りと競わせる task の体)。"
  (<- reason str (AwaitStop))
  reason)


(defk pause-or-stop [policy changed]
  {:pre [(: policy WorkerPolicy) (: changed (| Future None))] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "拍の眠り(tick-pause)を止めの合図と競わせ、先に来た方で起きるため。止めが既に来ていれば競わせずに眠る — 止まりの手順の拍(子の
   終わりを待つ間)は止めの合図ですぐ起きる空回りをしない。"
  (<- reason (| str None) (StopRequested))
  (if (is-not reason None)
      (<- (tick-pause policy changed))
      ;; 競わせの 2 つの task は負けた方を取り消して捨てる(daemon — 取り消しの巻き戻しを待たずに拍へ戻る。時計の handler の
      ;; WaitWithin の期限の task と同じ)。
      (do (<- pause Task (Spawn (tick-pause policy changed) :daemon True))
          (<- stop Task (Spawn (stop-reason) :daemon True))
          (try
            (<- (Race pause stop))
            (finally
              (<- (Cancel pause))
              (<- (Cancel stop))))))
  None)


(defhandler tick-pauses
  ;; 引数なし: 拍の長さと呼び鈴は effect の欄(policy・changed)が運ぶ。state は読まない(模擬の宿の材料)。
  (AwaitNextTick [policy changed state]
    (<- (pause-or-stop policy changed))
    (resume None)))
