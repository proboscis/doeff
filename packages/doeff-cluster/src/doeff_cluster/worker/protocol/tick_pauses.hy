;;; worker の拍と拍の間の待ち(AwaitNextTick)の本番の答え手 — 次に何かが変わる拍まで眠り、子の終わり・宣言の変化・止めの合図で起きる
;;; (#3834 — 前は拍 tick-seconds を宣言の変化の呼び鈴と競わせて眠るだけで〔core/program.tick-pause・#2692〕、子の終わりは次の拍の
;;; PollProcess まで知らず、子が走るだけの間も拍ごとに起きていた)。本番の組(entry/main.production-handlers)と、拍を打つ偽の宿の組が置く。
;;; 模擬の時計の下の宿(sim/local.hy)は、先の拍を本番の判断で試して静かな拍を一度に眠る(#2781)。
;;;
;;; 眠る長さ(rest-ms): 起こし方のまとめ(ArmWake — worker/protocol/wake)の答えの刻 due まで。ただし判断の側の時間で変わる物(止めの猶予の
;;; 段・起こし直しの間など)は、先の拍を本番の判断で試して(quiet-beats — 観測は拍の終わりのまま)最初に変わる拍で起きる。due が EveryTick・
;;; 止まり始めの拍は今までどおり tick-seconds。次の拍より手前の due(検めの時間切れ・準備の停滞の期限)だけは、その刻ちょうどに起きる。
;;; 起き方: 眠りの bell(子の終わりの見張り WatchExits と止めの合図 StopWake が満たす)・宣言の変化の呼び鈴 changed・期限。呼び鈴で起きた
;;; 時は拍の終わりから wake-gap-seconds が経つまで眠り足す(起こしが続いても拍は 1 秒に 1 / wake-gap-seconds 回まで — #2692)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import math)
(import doeff_time [Delay GetMonotonic WaitWithin])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Future Race Spawn Cancel PRIORITY-IDLE])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick WorkerPolicy WorkerState WorldView])
(import doeff_cluster.worker.core.quiet_policy [quiet-beats])
(import doeff_cluster.worker.protocol.observations [ArmWake DisarmWake WorkerWake EveryTick])


;; 一度の眠りで先の拍を試す数の上限(拍 0.5 秒で 60 秒)。本番の due は次の heartbeat(生存の窓の 1/4 = 2.5 秒)より先にならないので
;; 届かない上限 — heartbeat の刻を言わない口(検の偽の宿)でも、試しの費用を 1 回の眠りに上限 REST-BEATS-LIMIT 拍で抑える。
(val REST-BEATS-LIMIT 120)


(defrecord RestEnded
  "拍の間の眠りの期限が、どの呼び鈴よりも先に来た印(Race の答えで、満たされた呼び鈴の値と見分ける)。")


(defk rest-timer [seconds]
  {:pre [(: seconds float)] :post [(: % RestEnded)]}
  "拍の間の眠りの期限を、呼び鈴と競わせる task にするため(Race の 1 つ — 先に来れば RestEnded)。"
  (<- (Delay seconds))
  (RestEnded))


(defk world-as-observed [world at]
  {:pre [(: world WorldView) (: at int)] :post [(: % WorldView)]}
  "先の拍 at の観測を、拍の終わりの観測 world のまま読むため(quiet-beats の材料)。子の終わり・宣言の変化・止めの合図は呼び鈴が起こし、
   時間で変わる観測(準備の停滞・検めの時間切れ・待ちの子の印)は言い換えが due に載せるので、眠りの間の観測は変わらない。"
  world)


(defk rest-ms [policy state world wake now stopping]
  {:pre [(: policy WorkerPolicy) (: state WorkerState) (: world WorldView) (: wake WorkerWake) (: now int) (: stopping bool)]
   :post [(: % int) (> % 0)]}
  "この拍の後に眠る長さ(ms)を決めるため(頭の註): due が EveryTick・止まり始め・もう過ぎた due なら tick-seconds、due が次の拍の内なら
   due まで(期限の刻ちょうどに起きる)、それ以外は due までの拍の数を上限に先の拍を本番の判断で試し(quiet-beats)、最初に何かが変わる拍か
   due の早い方まで。"
  (val tick-ms (int (* 1000 policy.tick-seconds)))
  (val due wake.due)
  (match due
    (EveryTick) tick-ms
    _ :if (or stopping (<= due now)) tick-ms
    _ :if (<= (- due now) tick-ms) (- due now)
    _ (do (val limit (min REST-BEATS-LIMIT (math.ceil (/ (- due now) tick-ms))))
          (<- quiet int (quiet-beats state policy (fn [at] (world-as-observed world at)) now limit))
          (max tick-ms (min (- due now) (* quiet tick-ms))))))


(defk woken-or-ended [bell changed seconds]
  {:pre [(: bell ExternalPromise) (: changed (| Future None)) (: seconds float)] :post [(: % bool)]}
  "眠りの bell・宣言の変化の呼び鈴 changed・期限 seconds のどれかまで待つため。答え = 呼び鈴で起きたか。changed の無い拍は bell 1 つを
   期限と競わせる(WaitWithin — 待ち 1 回)。bell は外の約束なので park する(模擬の時計の下で、満たす者の居ない bell が時計を止めない)。"
  (match changed
    None (do (<- first (WaitWithin bell.future seconds :park True))
             (is-not first None))
    _ (do (<- timer (Spawn (rest-timer seconds) :daemon True))
          (<- first (Race bell.future changed timer :priority PRIORITY-IDLE))
          (<- (Cancel timer))
          (not (isinstance first RestEnded)))))


(defk wake-pause [policy changed state world stopping]
  {:pre [(: policy WorkerPolicy) (: changed (| Future None)) (: state WorkerState) (: world WorldView) (: stopping bool)] :post [(: % None)]}
  "拍と拍の間を眠るため(頭の註): 眠りの bell を作って起こし方を整え(ArmWake)、眠る長さ(rest-ms)まで呼び鈴と競わせて眠り、起きたら
   見張りを外して bell を閉じる。呼び鈴で起きた時は拍の終わりから wake-gap-seconds が経つまで眠り足す。stopping = この拍が止まり始めの
   拍か(止まる間は tick-seconds で打つ)。"
  (<- now int (now-epoch-ms))
  (<- bell ExternalPromise (CreateExternalPromise))
  (<- wake WorkerWake (ArmWake bell))
  (<- rest int (rest-ms policy state world wake now stopping))
  ;; 経過は単調の時計(GetMonotonic)で測る — 壁の時計が戻っても眠り足す秒が gap を超えない(#2692)。
  (<- slept-at float (GetMonotonic))
  (<- rung bool (woken-or-ended bell changed (/ rest 1000.0)))
  (<- (DisarmWake bell))
  (.complete bell True)
  (when rung
    (<- woke-at float (GetMonotonic))
    (val gap (min policy.wake-gap-seconds policy.tick-seconds))
    (val left (min gap (- gap (- woke-at slept-at))))
    (when (> left 0)
      (<- (Delay left))))
  None)


(defhandler tick-pauses
  ;; 引数なし: 拍の長さと呼び鈴と拍の終わりの記憶・観測・止まり始めは effect の欄(policy・changed・state・world・stopping)が運ぶ。
  (AwaitNextTick [policy changed state world stopping]
    (<- (wake-pause policy changed state world stopping))
    (resume None)))
