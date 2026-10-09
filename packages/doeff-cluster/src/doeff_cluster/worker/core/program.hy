;; worker の調整ループ。毎拍「宣言・観測・記憶」から action を導いて実行する。
;; 子 process もコードの準備も観測で追うので、どの job の処理もループ(停止の経路)を塞がない。
;; 周の間は周期で眠らず、起きる物の組(WakeSet — 計画の期限・状態を持つ handler が WorkerWakes に足す期限と呼び鈴と待つ子)の早い 1 つまで
;; 待つ(#3871 の単位 4)。状態を変えた周の後は待たずにもう 1 周回り、今すぐが続けば訳を示して落ちる。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "program"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime])
(import dataclasses [replace])
(import doeff_time [GetTime epoch-ms-of])
(import doeff_core_effects [slog])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_cluster.shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView DesiredJobs DesiredUnreadable
  ReadDesired ObserveWorld PublishStatus EnvReport AwaitNextTick Undeclared WorkerStopping CutOff DeclarationRead WakeSet WorkerWakes
  WorkerUnsettled SweepEnvs TickMarks] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.worker.core.worker_due [plan-due wakes-with])
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
;; heartbeat の間の遅れの行の名と閾の比(#3850 — 名 + 欄 at = 今の heartbeat を送った刻(UTC ISO)・worker = 名乗った
;; worker の名・gap-ms = 前の heartbeat を送ってから今の heartbeat を送るまでの ms・slowest = その間でいちばん長かった区間の名・
;; slowest-ms = その長さ)。閾 = 送った時の生存の窓(HeartbeatSent.lease-ms)× 比。拍の遅れの行(TICK-LAG-MS)は拍 1 つの中しか測らず、
;; heartbeat を挟む隣り合う 2 つの拍の前後がどちらも 5 秒未満でも heartbeat の間は窓を越えうる(2026-10-07 13:18 JST の agent-worker-2 —
;; coordinator で生きている印が false に瞬いたのに、worker の log に遅れの行が無かった)。窓の 7 割で名指し、窓を越える前に見えるようにする。
(val HEARTBEAT-GAP-LOG "worker: heartbeat の間の遅れ")
(val HEARTBEAT-GAP-LEASE-RATIO 0.7)
;; 状態を変えた周(action を撃った周)の後の今すぐが続いてよい周の数(coordinator の UNSETTLED-STEP-LIMIT と同じ — #3871 の単位 4)。
(val UNSETTLED-TICK-LIMIT 100)
;; 撃っても「今すぐもう 1 周」に数えない action(#3871 の単位 4): 結果を次の周の観測でなく、起きる物の組で受ける物。掃除の係への固定の集合の
;; 受け渡し(SweepEnvs)は、掃除が走っている間は毎周撃たれるが、その進みは実行環境の handler が掃除の task の終わりの呼び鈴と数え直しの
;; 期限として WorkerWakes に足す(周ごとに見に来なくてよい)。
(val WAKE-DRIVEN-ACTIONS #(SweepEnvs))


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


(defrecord GapMark
  "heartbeat の間の区間の終わり 1 つ: name = 区間の名・at = 区間が終わった刻(epoch ms)。"
  (#^ str name)
  (#^ int at))


(defrecord HeartbeatGap
  "heartbeat の間の遅れの行の欄: gap-ms = 前の heartbeat を送ってから今の heartbeat を送るまで・slowest = いちばん長い区間の名・
   slowest-ms = その長さ。"
  (#^ int gap-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defk heartbeat-gap [since previous began env-reported sent-at]
  {:pre [(: since int) (: previous (| TickMarks None)) (: began int) (: env-reported int) (: sent-at int)] :post [(: % HeartbeatGap)]
   :tags {:context "worker" :role "judgment"}}
  "前の heartbeat を送った刻 since から今の heartbeat を送った刻 sent-at までを、前の拍の時刻 previous と今の拍の頭・EnvReport の後の
   時刻で区間に切り、遅れの行の欄(間と、いちばん長い区間の名と長さ)を導くため。区間 = 前の拍より前(EarlierTicks — 間に送らない拍を
   挟んだ時)・前の拍の EnvReport・ReadDesired(前の拍で送っていれば送った後の返事の待ち)・ObserveWorld・残り・拍の間の待ち
   (AwaitNextTick)・今の拍の EnvReport・ReadDesired(送るまで)。since より前の部分と sent-at より後の部分は数えない(拍の外の背景の
   task の送りは、前の拍の途中や拍の間の待ちの途中でも起きる — card ki-e38dbfca7671)。長さが並んだら先の区間を名指す。"
  (val earlier (match previous
    None #()
    _ #((GapMark :name "EarlierTicks" :at previous.began)
        (GapMark :name "Previous.EnvReport" :at previous.env-reported)
        (GapMark :name "Previous.ReadDesired" :at previous.desired-read)
        (GapMark :name "Previous.ObserveWorld" :at previous.observed)
        (GapMark :name (+ "Previous." ACTIONS-TO-PUBLISH) :at previous.ended))))
  (val ends (+ earlier #((GapMark :name "AwaitNextTick" :at began)
                         (GapMark :name "EnvReport" :at env-reported)
                         (GapMark :name "ReadDesired" :at sent-at))))
  (val starts (+ #(since) (tuple (gfor mark (cut ends -1) mark.at))))
  (val spans (tuple (gfor #(mark start) (zip ends starts)
                          (TickSpan :name mark.name :ms (max 0 (- (min mark.at sent-at) (max start since)))))))
  (val longest (max spans :key (fn [span] span.ms)))
  (HeartbeatGap :gap-ms (- sent-at since) :slowest longest.name :slowest-ms longest.ms))


(defk worker-tick [state policy stopping]
  {:pre [(: state WorkerState) (: policy WorkerPolicy) (: stopping bool)] :post [(: % tuple)]}
  ;; 結果 = #(次の状態 まだ終了を待つ子 process の数 宣言の変化の呼び鈴(Future か None) 計画の期限 実行した action の名の列
  ;;          tick の頭の時刻(epoch ms — handler が期限を組み立てる基準・WorkerWakes の began))
  ;; heartbeat に載せる root の姿は root の言い換えに問うて、宣言の読みに渡す(#2467・#2427)。止まり始めも渡す — heartbeat で名乗り、
  ;; coordinator がこの世代へ新しく置かない(#2819)。
  ;; 拍の遅れの計り(#3715): 拍の頭・重い 3 種の待ちの後・拍の終わりに時刻を読み、拍が TICK-LAG-MS を越えたらいちばん長い区間を名指す。
  (<- began-at datetime (GetTime))
  (<- env-report (| dict None) (EnvReport))
  (<- env-reported-at datetime (GetTime))
  (<- read (| DesiredJobs DesiredUnreadable) (ReadDesired :env-report env-report :stopping stopping))
  ;; この読みが拍の判断の now を兼ねる(#3715 より前から在る読み)。
  (<- now int (now-epoch-ms))
  (val began (epoch-ms-of began-at))
  (val env-reported (epoch-ms-of env-reported-at))
  ;; heartbeat の間の計り(#3850): 前の読みの後からこの読みまでの送り(この読みの送りと、拍の外の背景の task の送り — card
  ;; ki-e38dbfca7671)ごとに、前の送りからの間が送った時の生存の窓 × HEARTBEAT-GAP-LEASE-RATIO を越えたら、間のいちばん長い区間を
  ;; 名指す。時刻は拍で既に読んだ物と送りの刻だけを使う(時刻の読みを足さない)。
  (var last-sent-ms state.last-sent-ms)
  (for [sent read.sends]
    (when (and (is-not last-sent-ms None) (> (- sent.at last-sent-ms) (* sent.lease-ms HEARTBEAT-GAP-LEASE-RATIO)))
      (<- gap HeartbeatGap (heartbeat-gap last-sent-ms state.last-marks began env-reported sent.at))
      (<- (slog HEARTBEAT-GAP-LOG :level "info" :at (.isoformat (datetime-of-epoch-ms sent.at)) :worker sent.worker
                :gap-ms gap.gap-ms :slowest gap.slowest :slowest-ms gap.slowest-ms)))
    (:= last-sent-ms sent.at))
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
  (var fired (tuple actions))
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
      (:= fired (+ fired followups))
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
  (val marks (TickMarks :began began :env-reported env-reported :desired-read now :observed (epoch-ms-of observed-at)
                        :ended (epoch-ms-of ended-at)))
  (when (> (- marks.ended marks.began) TICK-LAG-MS)
    (<- lag TickLag (tick-lag marks))
    (<- (slog TICK-LAG-LOG :level "info" :elapsed-ms lag.elapsed-ms :slowest lag.slowest :slowest-ms lag.slowest-ms)))
  ;; 時刻だけで計画の答えが変わる最初の刻(周の後の観測と記憶・周の判断の now — worker_due の頭の註)。
  (<- planned-due (| DueAt DueNever) (plan-due now after records policy))
  #((WorkerState :declaration declaration :records records :last-marks marks :last-sent-ms last-sent-ms)
    ;; 停止を確認できない process は待ち続けない(状態表示に残す)。
    (len (lfor s report :if (in s.phase #(JobPhase.RUNNING JobPhase.STOPPING)) s))
    ;; 宣言の変化の呼び鈴(読めた宣言の物だけ — 周の間の待ちを起こす・#2692)。
    (match read
      (DesiredJobs :changed changed) changed
      _ None)
    planned-due
    (tuple (gfor action fired (. (type action) __name__)))
    began))

(defk next-tick-due [due acted]
  {:pre [(: due (| DueAt DueNow DueNever)) (: acted bool)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "worker" :role "judgment"}}
  "周の後にどこまで待つかを決めるため: 状態を変えた周(acted — action を撃った周)の後は今すぐもう 1 周(撃った action の結果を次の
   周の観測で見る)。それ以外は起きる物の組の期限 due まで(#3871 の単位 4 — coordinator の after-step と同じ形)。"
  (if acted (DueNow) due))


(defk count-unsettled-ticks [streak due fired]
  {:pre [(: streak int) (: due (| DueAt DueNow DueNever)) (: fired tuple)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "今すぐ(DueNow)が続いた周の数を数え、回り続ける調整ループを名指して止めるため: 今すぐなら 1 足し、それ以外は 0 に戻す。足した数が
   UNSETTLED-TICK-LIMIT を越えたら、最後の周で撃った action の名 fired を書いて WorkerUnsettled で落ちる。"
  (val counted (match due (DueNow) (+ streak 1) (DueAt) 0 (DueNever) 0))
  (when (> counted UNSETTLED-TICK-LIMIT)
    (raise (WorkerUnsettled (.format "worker の調整ループが {} 周 落ち着かない(状態を変える周が続いた)— 撃ち続けた action: {}" counted
                                     (or (.join "・" fired) "無し")))))
  counted)

(defk run-worker [policy]
  {:pre [(: policy WorkerPolicy)] :post [(: % WorkerState)]}
  ;; worker の停止要求を受けたら宣言を空として扱い、全 job を同じ停止の手順で回収する。
  (var state (WorkerState))
  (var streak 0)
  (while True
    ;; 止めは核の止めの効果で知る(本番 = os-signal-stop-handler の SIGTERM・SIGINT — #3871 の単位 3)。答え = 理由(None = 続ける)。
    (<- reason (| str None) (StopRequested))
    (val stopping (is-not reason None))
    (<- ticked tuple (worker-tick state policy stopping))
    (val alive (get ticked 1))
    (:= state (get ticked 0))
    (when (and stopping (= alive 0)) (return state))
    ;; 起きる物の組: 状態を持つ handler が足した期限・呼び鈴・待つ子に、計画の期限を合わせる(#3871 の単位 4)。handler は期限を
    ;; tick の頭の時刻を基準に組み立てる(tick の後半が長くても、まだ判定していない期限を捨てない — #4332)。
    (<- outer WakeSet (WorkerWakes :began (get ticked 5)))
    (<- gathered WakeSet (wakes-with outer (get ticked 3) #() #()))
    (val fired (get ticked 4))
    (val settling (tuple (gfor name fired :if (not-in name (tuple (gfor t WAKE-DRIVEN-ACTIONS t.__name__))) name)))
    (<- due (| DueAt DueNow DueNever) (next-tick-due gathered.due (bool settling)))
    (<- counted int (count-unsettled-ticks streak due fired))
    (:= streak counted)
    ;; 周の間の待ちは答え手 tick-pauses の 1 本の待ち(本番の組も模擬の世界の宿も同じ — #3871 の単位 4・5)。
    (<- (AwaitNextTick policy (get ticked 2) (replace gathered :due due) :stopping stopping))))
