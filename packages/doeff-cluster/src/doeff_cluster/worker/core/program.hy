;; worker の調整ループ。毎拍「宣言・観測・記憶」から action を導いて実行する。
;; 子 process もコードの準備も観測で追うので、どの job の処理もループ(停止の経路)を塞がない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "program"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime])
(import doeff_time [Delay GetMonotonic GetTime])
(import doeff_core_effects [slog])
(import doeff_core_effects.scheduler [Future])
(import doeff_cluster.shared.core.clock [now-epoch-ms epoch-ms-of])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView DesiredJobs DesiredUnreadable
  ReadDesired ObserveWorld WorkerStopRequested PublishStatus EnvReport AwaitNextTick Undeclared WorkerStopping CutOff DeclarationRead] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.worker.core.policy [plan ready-followups records-after statuses start-holds noted-holds held-records declared-jobs
  declared-warm sweep-actions])

;; 拍の action の後に、同じ拍のうちに揃いを追う回数の上限(#2719・#3646): 1 回目 = 木が揃った job と root の待ちの子の起こし・2 回目 =
;; 起こした刻に揃った待ちの子から分ける task。揃いの連なりはこれより長くならない(3 回目に進める物の形が無い)。
(val FOLLOWUP-ROUNDS 2)
;; 起こしの見送りの行の名(#3713 — 名 + 欄 job・reason の形。reason = StartHold の値)。
(val START-HOLD-LOG "worker: job の起こしの見送り")
;; 拍の遅れの行の名と閾(#3715 — 名 + 欄 elapsed-ms = 拍の頭から終わりまでの ms・slowest = その拍でいちばん長かった区間の名・
;; slowest-ms = その区間の ms)。閾は coordinator との途絶の柵(本番 20 秒)より小さく、柵を越える前に遅れを名指す。
(val TICK-LAG-LOG "worker: 拍の遅れ")
(val TICK-LAG-MS 5000)
;; 拍の区間の名(#3715): 時刻は拍の頭と、重い 3 種(EnvReport・ReadDesired・最初の ObserveWorld)の待ちの後と、拍の終わりにだけ読む
;; — 時刻の読みは action の数に依らず拍 1 つに 5 回(うち ReadDesired の後の 1 回は拍の判断の now)。最初の ObserveWorld の後から
;; PublishStatus の後までは 1 つの区間(action・揃いの追いの観測・PublishStatus をまとめた区間)で、その名が ACTIONS-TO-PUBLISH。
;; 計りだけの読みは GetTime を直に出す(now-epoch-ms の Program の呼びを挟まない — 拍ごとの費用を時刻の effect 1 つに留める)。
(val ACTIONS-TO-PUBLISH "ActionsToPublishStatus")


(defrecord TickMarks
  "拍 1 つで読んだ時刻(epoch ms): began = 拍の頭・env-reported = EnvReport の後・desired-read = ReadDesired の後・observed = 最初の
   ObserveWorld の後・ended = PublishStatus の後(拍の終わり)。"
  (#^ int began)
  (#^ int env-reported)
  (#^ int desired-read)
  (#^ int observed)
  (#^ int ended))


(defrecord TickSpan
  "拍の区間 1 つ: name = 区間の名(待った effect の名か ACTIONS-TO-PUBLISH)・ms = 長さ。"
  (#^ str name)
  (#^ int ms))


(defrecord TickLag
  "拍の遅れの行の欄: elapsed-ms = 拍の頭から終わりまで・slowest = いちばん長い区間の名・slowest-ms = その長さ。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defk tick-lag [marks]
  {:pre [(: marks TickMarks)] :post [(: % TickLag)] :tags {:context "worker" :role "judgment"}}
  "拍の読んだ時刻から、拍の遅れの行の欄(経過と、いちばん長い区間の名と長さ)を導くため。長さが並んだら先の区間を名指す。"
  (val spans #((TickSpan :name "EnvReport" :ms (- marks.env-reported marks.began))
               (TickSpan :name "ReadDesired" :ms (- marks.desired-read marks.env-reported))
               (TickSpan :name "ObserveWorld" :ms (- marks.observed marks.desired-read))
               (TickSpan :name ACTIONS-TO-PUBLISH :ms (- marks.ended marks.observed))))
  (val longest (max spans :key (fn [span] span.ms)))
  (TickLag :elapsed-ms (- marks.ended marks.began) :slowest longest.name :slowest-ms longest.ms))


(defrecord TickEnd
  "拍 1 つの終わり: state = 次の状態・alive = まだ終了を待つ子 process の数・changed = 宣言の変化の呼び鈴(Future か None)・world = 拍の
   終わりの観測(拍の間の眠りが、先の拍を本番の判断で試す材料 — #3834)。"
  (#^ WorkerState state)
  (#^ int alive)
  (#^ (| Future None) changed)
  (#^ WorldView world))


(defk worker-tick [state policy stopping]
  {:pre [(: state WorkerState) (: policy WorkerPolicy) (: stopping bool)] :post [(: % TickEnd)]}
  ;; heartbeat に載せる root の姿は root の言い換えに問うて、宣言の読みに渡す(#2467・#2427)。止まり始めも渡す — heartbeat で名乗り、
  ;; coordinator がこの世代へ新しく置かない(#2819)。
  ;; 拍の遅れの計り(#3715): 拍の頭・重い 3 種の待ちの後・拍の終わりに時刻を読み、拍が TICK-LAG-MS を越えたらいちばん長い区間を名指す。
  (<- began-at datetime (GetTime))
  (<- env-report (| dict None) (EnvReport))
  (<- env-reported-at datetime (GetTime))
  (<- read (| DesiredJobs DesiredUnreadable) (ReadDesired :env-report env-report :stopping stopping))
  ;; この読みが拍の判断の now を兼ねる(#3715 より前から在る読み)。
  (<- now int (now-epoch-ms))
  ;; 読めない宣言を空と読まない。直前に読めた宣言を使い続ける(#3731 — 読めた拍だけ持ち替え、途絶で絞った宣言も読んだ側。まだ一度も
  ;; 読めていなければ NotYetRead のまま)。
  (val declaration (match read
    (DesiredJobs) (DeclarationRead :jobs read.jobs :warm read.warm)
    _ state.declaration))
  (<- jobs tuple (declared-jobs declaration))
  (val desired (if stopping #() jobs))
  ;; 宣言に無い job を止める訳(#3713): worker の停止・途絶で宣言を絞った(返事の宣言の cut-off)・それ以外は宣言から外れた。
  (val absent (match read
    _ :if stopping (WorkerStopping)
    (DesiredJobs :cut-off (CutOff)) read.cut-off
    _ (Undeclared)))
  (<- world WorldView (ObserveWorld))
  (<- observed-at datetime (GetTime))
  (<- warm-read tuple (declared-warm declaration))
  (val warm (if stopping #() warm-read))
  (<- planned tuple (plan now desired world state.records policy :warm warm :absent absent))
  ;; 掃除は最後に読めた宣言で判じる(まだ読めていない拍は撃たない・止まる拍も宣言の root を消さない — #3731)。
  (<- sweeping tuple (sweep-actions declaration world))
  (val actions (+ planned sweeping))
  (for [action actions] (<- action))
  (<- counted dict (records-after now state.records actions policy))
  (var records counted)
  ;; 状態の表示は action の後の観測から作る(起動・回収を 1 拍遅れで見せない)。
  (var after world)
  (when actions
    (<- observed WorldView (ObserveWorld))
    (:= after observed)
    ;; この拍の準備で揃った物は、同じ拍のうちに進める(最初の task が拍 1 つ待たない — #2719)。揃いは 2 段まで続けて追う: 木が揃って
    ;; 待ちの子を起こし、その待ちの子が揃って task を分ける(#3646 — 起こした刻に揃う宿の時だけ 2 段目が在る)。
    (var before world)
    (for [_ (range FOLLOWUP-ROUNDS)]
      (<- followups tuple (ready-followups now desired before after records policy :warm warm :absent absent))
      (when (not followups) (break))
      (for [action followups] (<- action))
      (<- followed dict (records-after now records followups policy))
      (:= records followed)
      (:= before after)
      (<- settled WorldView (ObserveWorld))
      (:= after settled)))
  ;; 起こしの見送り(#3713): 拍の終わりの観測で起こさない・起こせない宣言の job を、訳が前の拍と替わった時だけ 1 行にし、訳を記憶に書く
  ;; (同じ訳が続く間は出さない)。
  (<- holds tuple (start-holds now desired after records policy))
  (<- fresh tuple (noted-holds records holds))
  (for [h fresh]
    (<- (slog START-HOLD-LOG :level "info" :job h.name :reason h.hold.value)))
  (<- held dict (held-records records holds))
  (:= records held)
  (<- report tuple (statuses now desired after records policy))
  (<- (PublishStatus report (if (isinstance read DesiredUnreadable) read.reason "")))
  (<- ended-at datetime (GetTime))
  (val began (epoch-ms-of began-at))
  (when (> (- (epoch-ms-of ended-at) began) TICK-LAG-MS)
    (<- lag TickLag (tick-lag (TickMarks :began began :env-reported (epoch-ms-of env-reported-at) :desired-read now
                                         :observed (epoch-ms-of observed-at) :ended (epoch-ms-of ended-at))))
    (<- (slog TICK-LAG-LOG :level "info" :elapsed-ms lag.elapsed-ms :slowest lag.slowest :slowest-ms lag.slowest-ms)))
  (TickEnd :state (WorkerState :declaration declaration :records records)
           ;; 停止を確認できない process は待ち続けない(状態表示に残す)。
           :alive (len (lfor s report :if (in s.phase #(JobPhase.RUNNING JobPhase.STOPPING)) s))
           ;; 宣言の変化の呼び鈴(読めた宣言の物だけ — 拍の間の眠りが競わせる・#2692)。
           :changed (match read
                      (DesiredJobs :changed changed) changed
                      _ None)
           :world after))

(defk tick-pause [policy changed]
  {:pre [(: policy WorkerPolicy) (: changed (| Future None))] :post [(: % None)]}
  "拍と拍の間を眠るため(#2692)。上限は tick-seconds。宣言の変化の呼び鈴(changed)が在れば眠りを呼び鈴と競わせ、変化を次の拍の境まで
   待たずに起きる — 変化の刻の位相で 0〜tick-seconds 待つ形をやめる。呼び鈴で起きた時は拍の終わりから wake-gap-seconds が経つまで
   眠り足すので、起こしが続いても拍は 1 秒に 1 / wake-gap-seconds 回まで。呼び鈴が鳴らない(起こしを取りこぼした)時も tick-seconds で
   起きる。静かな拍の費用は今までの Delay 1 回と同じ待ち 1 回(時計の列の 1 項)と時計の読み 1 回 — 眠り足す Delay は起きた時だけ。"
  (match changed
    None (<- (Delay policy.tick-seconds))
    ;; 経過は単調の時計(GetMonotonic)で測る — 壁の時計が戻っても眠り足す秒が gap を超えない(査読の指摘・#2692)。
    _ (do (<- slept-at float (GetMonotonic))
          (<- rung (promise-or-timeout changed policy.tick-seconds))
          (when (is-not rung None)
            (<- woke-at float (GetMonotonic))
            (val gap (min policy.wake-gap-seconds policy.tick-seconds))
            (val rest (min gap (- gap (- woke-at slept-at))))
            (when (> rest 0)
              (<- (Delay rest))))))
  None)

(defk run-worker [policy]
  {:pre [(: policy WorkerPolicy)] :post [(: % WorkerState)]}
  ;; worker の停止要求を受けたら宣言を空として扱い、全 job を同じ停止の手順で回収する。
  (var state (WorkerState))
  (while True
    (<- stopping bool (WorkerStopRequested))
    (<- ticked TickEnd (worker-tick state policy stopping))
    (:= state ticked.state)
    (when (and stopping (= ticked.alive 0)) (return state))
    ;; 拍の間の眠りは答え手が決める(本番 = tick-pauses — 次に何かが変わる刻まで眠り、子の終わり・宣言の変化・止めの合図で起きる
    ;; 〔#3834〕・模擬の時計の下の宿は静かな拍を一度に眠れる — #2781)。
    (<- (AwaitNextTick policy ticked.changed state ticked.world stopping))))
