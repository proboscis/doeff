;;; worker の周の間の待ち(AwaitNextTick)の本番の答え手(#3871 の単位 4)— 周期で眠らず、起きる物の早い 1 つまで 1 本で待つ:
;;; 期限(wakes.due — 計画の期限・heartbeat と柵の期限・準備と掃除の期限)・宣言の変化の呼び鈴(changed)と wakes の呼び鈴(掃除の終わり
;;; など)・待つ子の終わり(wakes.exits — AwaitProcessExit・AwaitWarmChildExit)・核の止めの合図(StopBell の呼び鈴 — 本番の答え手は
;;; os-signal-stop-handler)。呼び鈴で起きた時は、周の終わりから wake-gap-seconds が経つまで待ち足す(起こしが途切れなく続いても周は
;;; 1 秒に 1 / wake-gap-seconds 回まで — #2692)。本番の組(entry/main.production-handlers)が置き、模擬の世界の宿(sim/local.hy の
;;; run-sim-worker)も宿の内側に置く(#3871 の単位 5)。
;;; 仕事の無い worker も heartbeat の間隔(2.5 秒)ごとにこの待ちを回すので、周ごとに task を起こさない: 期限は時計の答え手が持つ
;;; (RaceWithin — 模擬の時計では時刻の列に 1 つ載せ、負ければ外す)・止めの合図は止めの答え手が session で 1 つ持つ呼び鈴を競わせる
;;; (StopBell)。task を起こすのは待つ子の終わりだけ(子の待ちの効果は Future を渡さない)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import doeff_time [Delay GetMonotonic RaceOutcome RaceWithin])
(import doeff_core_effects [Cancel Future Race Spawn Task])
(import doeff_core_effects [StopBell StopRequested])
(import doeff_core_effects [AwaitProcessExit])
(import doeff_core_effects [AwaitWarmChildExit])
(import doeff_cluster.shared.core.clock [now-epoch-ms monotonic-ms-of])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.worker.core.heartbeat_rules [silent-ms])
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick WakeSet WorkerPolicy])

;; 子の終わりの起こしの印 — 子の終わりを待つ task の答え。呼び鈴(Future)は task を立てずに競わせるので、競わせの答えがこの印なら
;; 呼び鈴で起きたのではない(待ち足すかを分ける — 呼び鈴だけが待ち足す)。
(defrecord OtherWake
  "呼び鈴でない起こし(子の終わり)の印(欄なし)。"
  {:tags {:context "worker" :role "protocol"}})


(defk child-ended [exit]
  {:pre [(: exit (| AwaitProcessExit AwaitWarmChildExit))] :post [(: % OtherWake)] :tags {:context "worker" :role "protocol"}}
  "子の終わりを待つ効果 exit(AwaitProcessExit・AwaitWarmChildExit)の答えが来るまで待つため(競わせる task の体)。"
  (<- _ended exit)
  (OtherWake))


(defk first-wake [racers due began began-mono-ms]
  {:pre [(: racers tuple) (: due (| DueAt DueNever)) (: began int) (: began-mono-ms int)] :post [(: % RaceOutcome)]
   :tags {:context "worker" :role "protocol"}}
  "racers(子の終わりを待つ task・止めの呼び鈴・呼び鈴)の早い 1 つか、期限 due の早い方まで待つため。答え = 競わせの答え(期限が先
   なら timed-out 真)。期限は task を立てず、時計の答え手の RaceWithin に持たせる。期限が無ければ racers だけを競わせる。
   待ちの長さ = 期限 −(周の頭の刻 began + そこからの経過)。経過は沈黙と同じ計り(heartbeat_rules.silent-ms — 壁の時計と単調な時計の
   長い方)で、周の頭の刻の対(began・began-mono-ms)から数える。「期限 − 今の壁の時計」で待つと、期限を組み立てた後・ここで時計を読む前に
   壁の時計が戻った時、戻った分だけ待ちが延びる(途絶の最中なら柵の判断がその分遅れる — card ki-069f4a5c07f7)。長い方を採るので、
   待ちは「期限 − 今の壁の時計」より長くならない(壁が進んだ・機体が眠った時は今までと同じく早く起きる)。"
  (when (isinstance due DueNever)
    (<- first (Race #* racers))
    (return (RaceOutcome :value first :timed-out False)))
  (<- now int (now-epoch-ms))
  (<- mono float (GetMonotonic))
  (<- elapsed int (silent-ms now (monotonic-ms-of mono) began began-mono-ms))
  (val seconds (/ (max 0 (- due.at (+ began elapsed))) 1000.0))
  (when (not racers)
    (<- (Delay seconds))
    (return (RaceOutcome :value None :timed-out True)))
  (<- outcome RaceOutcome (RaceWithin racers seconds))
  outcome)


(defk woke-by-bell [outcome stop-raced]
  {:pre [(: outcome RaceOutcome) (: stop-raced bool)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "起きたのが呼び鈴か(待ち足すか)を決めるため: 期限(timed-out)・子の終わり(OtherWake)・止めの合図は呼び鈴でない。止めの呼び鈴の
   値は理由でない(答え手ごとに違う)ので、止めの呼び鈴を競わせた(stop-raced)待ちは、起きた後に止めが立っているかを問い直して
   見分ける(呼び鈴と止めが重なれば止め — 待ち足さずに次の周の頭で止めを読む)。"
  (when (or outcome.timed-out (isinstance outcome.value OtherWake)) (return False))
  (when (not stop-raced) (return True))
  (<- reason (| str None) (StopRequested))
  (is reason None))


(defk await-wakes [policy changed wakes began began-mono-ms stopping]
  {:pre [(: policy WorkerPolicy) (: changed (| Future None)) (: wakes WakeSet) (: began int) (: began-mono-ms int) (: stopping bool)]
   :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "起きる物の早い 1 つまで 1 本で待つため(頭の註)。今すぐ(DueNow)なら待たない。周の頭で止めを知らなかった(stopping 偽)のに止めが
   来ていれば、待たずに戻る(周の頭の問いの後に来た合図)。止まりの手順の周(stopping 真 — 子の終わりを待つ間)は止めの合図を競わせない
   — 合図ですぐ起きる空回りをしない。どれも無い待ちは起きる手段が無いので、待たずに落ちる。"
  (when (isinstance wakes.due DueNow) (return None))
  (<- reason (| str None) (StopRequested))
  (when (and (is-not reason None) (not stopping)) (return None))
  (val bells (+ (if (is changed None) #() #(changed)) wakes.bells))
  (var stop-bells #())
  (when (is reason None)
    (<- stop-bell Future (StopBell))
    (:= stop-bells #(stop-bell)))
  (when (not (or wakes.exits bells stop-bells (isinstance wakes.due DueAt)))
    (raise (RuntimeError "worker の周の間の待ちに起きる物が無い(期限も呼び鈴も待つ子も止めの合図も無い)")))
  (<- slept-at float (GetMonotonic))
  ;; 子の終わりを待つ task は負けたら取り消して捨てる(daemon — 取り消しの巻き戻しを待たずに周へ戻る)。呼び鈴と止めの呼び鈴は task を
  ;; 立てずに競わせる。
  (var tasks #())
  (for [exit wakes.exits]
    (<- task Task (Spawn (child-ended exit) :daemon True))
    (:= tasks (+ tasks #(task))))
  (var outcome None)
  (try
    (<- raced RaceOutcome (first-wake (+ tasks stop-bells bells) wakes.due began began-mono-ms))
    (:= outcome raced)
    (finally
      (for [task tasks]
        (<- (Cancel task)))))
  (<- by-bell bool (woke-by-bell outcome (bool stop-bells)))
  (when by-bell
    (<- woke-at float (GetMonotonic))
    (val rest (- policy.wake-gap-seconds (- woke-at slept-at)))
    (when (> rest 0)
      (<- (Delay rest))))
  None)


(defhandler tick-pauses
  ;; 引数なし: 待つ物は effect の欄(policy・changed・wakes・周の頭の刻の対 began と began-mono-ms・stopping)が運ぶ。
  (AwaitNextTick [policy changed wakes began began-mono-ms stopping]
    (<- (await-wakes policy changed wakes began began-mono-ms stopping))
    (resume None)))
