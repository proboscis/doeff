;;; worker の周の間の待ち(AwaitNextTick)の本番の答え手(#3871 の単位 4)— 周期で眠らず、起きる物の早い 1 つまで 1 本で待つ:
;;; 期限(wakes.due — 計画の期限・heartbeat と柵の期限・準備と掃除の期限)・宣言の変化の呼び鈴(changed)と wakes の呼び鈴(掃除の終わり
;;; など)・待つ子の終わり(wakes.exits — AwaitProcessExit・AwaitWarmChildExit)・核の止めの合図(AwaitStop — 本番の答え手は
;;; os-signal-stop-handler)。呼び鈴で起きた時は、周の終わりから wake-gap-seconds が経つまで待ち足す(起こしが途切れなく続いても周は
;;; 1 秒に 1 / wake-gap-seconds 回まで — #2692)。本番の組(entry/main.production-handlers)が置き、模擬の世界の宿(sim/local.hy の
;;; run-sim-worker)も宿の内側に置く(#3871 の単位 5)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_time [Delay GetMonotonic])
(import doeff_core_effects.scheduler [Cancel Future Race Spawn Task Wait])
(import doeff_core_effects.stop_signal_effects [AwaitStop StopRequested])
(import doeff_core_effects.process_effects [AwaitProcessExit])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick WakeSet WorkerPolicy])

;; 起こした物の種類(待ち足すかを分ける — 呼び鈴だけが待ち足す)。
(val BY-BELL "bell")
(val BY-OTHER "other")


(defk until-due [at]
  {:pre [(: at int)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "期限の刻 at まで眠るため(競わせる task の体)。"
  (<- now int (now-epoch-ms))
  (<- (Delay (/ (max 0 (- at now)) 1000.0)))
  BY-OTHER)


(defk bell-rung [bell]
  {:pre [(: bell Future)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "呼び鈴 bell が満ちるまで待つため(競わせる task の体)。"
  (<- (Wait bell))
  BY-BELL)


(defk child-ended [exit]
  {:pre [(: exit (| AwaitProcessExit AwaitWarmChildExit))] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "子の終わりを待つ効果 exit(AwaitProcessExit・AwaitWarmChildExit)の答えが来るまで待つため(競わせる task の体)。"
  (<- _ended exit)
  BY-OTHER)


(defk stop-signalled []
  {:pre [] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "止めの合図が来るまで待つため(競わせる task の体)。"
  (<- _reason str (AwaitStop))
  BY-OTHER)


(defk await-wakes [policy changed wakes stopping]
  {:pre [(: policy WorkerPolicy) (: changed (| Future None)) (: wakes WakeSet) (: stopping bool)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "起きる物の早い 1 つまで 1 本で待つため(頭の註)。今すぐ(DueNow)なら待たない。周の頭で止めを知らなかった(stopping 偽)のに止めが
   来ていれば、待たずに戻る(周の頭の問いの後に来た合図)。止まりの手順の周(stopping 真 — 子の終わりを待つ間)は止めの合図を競わせない
   — 合図ですぐ起きる空回りをしない。どれも無い待ちは起きる手段が無いので、待たずに落ちる。"
  (when (isinstance wakes.due DueNow) (return None))
  (<- reason (| str None) (StopRequested))
  (when (and (is-not reason None) (not stopping)) (return None))
  (val bells (+ (if (is changed None) #() #(changed)) wakes.bells))
  (var bodies (+ (tuple (gfor bell bells (bell-rung bell))) (tuple (gfor exit wakes.exits (child-ended exit)))))
  (match wakes.due
    (DueAt :at at) (:= bodies (+ bodies #((until-due at))))
    (DueNever) None)
  (when (is reason None)
    (:= bodies (+ bodies #((stop-signalled)))))
  (when (not bodies)
    (raise (RuntimeError "worker の周の間の待ちに起きる物が無い(期限も呼び鈴も待つ子も止めの合図も無い)")))
  (<- slept-at float (GetMonotonic))
  ;; 競わせの task は負けた方を取り消して捨てる(daemon — 取り消しの巻き戻しを待たずに周へ戻る)。
  (var tasks #())
  (for [body bodies]
    (<- task Task (Spawn body :daemon True))
    (:= tasks (+ tasks #(task))))
  (var woke-by BY-OTHER)
  (try
    (<- first str (Race #* tasks))
    (:= woke-by first)
    (finally
      (for [task tasks]
        (<- (Cancel task)))))
  (when (= woke-by BY-BELL)
    (<- woke-at float (GetMonotonic))
    (val rest (- policy.wake-gap-seconds (- woke-at slept-at)))
    (when (> rest 0)
      (<- (Delay rest))))
  None)


(defhandler tick-pauses
  ;; 引数なし: 待つ物は effect の欄(policy・changed・wakes・stopping)が運ぶ。
  (AwaitNextTick [policy changed wakes stopping]
    (<- (await-wakes policy changed wakes stopping))
    (resume None)))
