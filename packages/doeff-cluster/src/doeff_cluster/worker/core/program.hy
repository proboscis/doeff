;; worker の調整ループ。毎拍「宣言・観測・記憶」から action を導いて実行する。
;; 子 process もコードの準備も観測で追うので、どの job の処理もループ(停止の経路)を塞がない。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(import doeff_time [Delay GetMonotonic])
(import doeff_core_effects.scheduler [Future])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView DesiredJobs DesiredUnreadable
  ReadDesired ObserveWorld WorkerStopRequested PublishStatus EnvReport AwaitNextTick] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.worker.core.policy [plan ready-followups records-after statuses])

;; 拍の action の後に、同じ拍のうちに揃いを追う回数の上限(#2719・#3646): 1 回目 = 木が揃った job と root の待ちの子の起こし・2 回目 =
;; 起こした刻に揃った待ちの子から分ける task。揃いの連なりはこれより長くならない(3 回目に進める物の形が無い)。
(val FOLLOWUP-ROUNDS 2)

(defk worker-tick [state policy stopping]
  {:pre [(: state WorkerState) (: policy WorkerPolicy) (: stopping bool)] :post [(: % tuple)]}
  ;; 結果 = #(次の状態 まだ終了を待つ子 process の数 宣言の変化の呼び鈴(Future か None))
  ;; heartbeat に載せる root の姿は root の言い換えに問うて、宣言の読みに渡す(#2467・#2427)。止まり始めも渡す — heartbeat で名乗り、
  ;; coordinator がこの世代へ新しく置かない(#2819)。
  (<- env-report (| dict None) (EnvReport))
  (<- read (| DesiredJobs DesiredUnreadable) (ReadDesired :env-report env-report :stopping stopping))
  ;; 読めない宣言を空と読まない。直前に読めた宣言を使い続ける。
  (setv desired (cond
    stopping #()
    (isinstance read DesiredJobs) read.jobs
    True state.desired))
  (<- now int (now-epoch-ms))
  (<- world WorldView (ObserveWorld))
  (setv warm (cond stopping #() (isinstance read DesiredJobs) read.warm True state.warm))
  (<- actions tuple (plan now desired world state.records policy :warm warm))
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
      (<- followups tuple (ready-followups now desired before after records policy :warm warm))
      (when (not followups) (break))
      (for [action followups] (<- action))
      (<- followed dict (records-after now records followups policy))
      (:= records followed)
      (:= before after)
      (<- settled WorldView (ObserveWorld))
      (:= after settled)))
  (<- report tuple (statuses now desired after records policy))
  (<- (PublishStatus report (if (isinstance read DesiredUnreadable) read.reason "")))
  #((WorkerState (if (isinstance read DesiredJobs) read.jobs state.desired) records warm)
    ;; 停止を確認できない process は待ち続けない(状態表示に残す)。
    (len (lfor s report :if (in s.phase #(JobPhase.RUNNING JobPhase.STOPPING)) s))
    ;; 宣言の変化の呼び鈴(読めた宣言の物だけ — 拍の間の眠りが競わせる・#2692)。
    (match read
      (DesiredJobs :changed changed) changed
      _ None)))

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
    (<- ticked tuple (worker-tick state policy stopping))
    (val alive (get ticked 1))
    (:= state (get ticked 0))
    (when (and stopping (= alive 0)) (return state))
    ;; 拍の間の眠りは答え手が決める(本番 = tick-pauses の tick-pause・模擬の時計の下の宿は静かな拍を一度に眠れる — #2781)。
    (<- (AwaitNextTick policy (get ticked 2) state))))
