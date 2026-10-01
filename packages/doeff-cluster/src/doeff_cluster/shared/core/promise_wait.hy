;;; 書きで起きる待ちの 1 点 — 書き手が満たす Promise を、期限まで待つ(読み直さない)。
;;;
;;; 模擬の世界(local.hy)の process の始まり・終わりの待ちと切り離した task の呼び鈴(proboscis/doeff#631)、模擬の coordinator の要求の列の
;;; 待ち(coordinator.protocol.request_queue.queued-requests)が使う。模擬の送り手の返事の待ち(sim.local.send-request)は安い形
;;; promise-or-cutoff を使う(#2596)。local.hy から分けたのは、handler の組の module
;;; (coordinator/entry/handler_sets.hy)が local.hy を import すると循環するため(local.hy が組の module を import する)。
;;; 時計は doeff-time の Delay(外側の sim-time-handler か async-time-handler が答える)。
(require doeff-hy.macros [defk <-])
(import doeff_core_effects.scheduler [Spawn Wait Cancel Race Task Future TaskCancelledError])
(import doeff_time [Delay])


(defk expire-after [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "待ちの期限の鳴らし: seconds 秒眠ってから None で終わるため(promise-or-timeout が呼び鈴と競わせる)。"
  (<- (Delay seconds))
  None)


(defk withdraw-timer [timer]
  {:pre [(: timer Task)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "期限の鳴らし timer を取り消し、解け終わるまで待つため(置き去りの眠りを残さない — 壁の時計では実時間で眠り続ける)。取り消しの
   TaskCancelledError はこの片付けの task の中でだけ飲む(待ち手の中で飲むと、待ち手自身への取り消しと見分けられない)。"
  (<- (Cancel timer))
  (try
    (<- (Wait timer))
    (except [TaskCancelledError]
      None))
  None)


(defk promise-or-timeout [future seconds]
  {:pre [(: future Future) (: seconds (| float int None))] :post [(: % "future の答え(時間切れは None)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "書き手が満たす Promise(future)を、seconds 秒(None = 上限なし)まで待つため — 読み直さずに書きで起きる待ちの 1 点。答え = future の
   答え(満たす側は None を渡さない)か、時間切れの None。期限の鳴らしは終わりに取り消して解け終わるまで待つ(待ち手が取り消された時も)。"
  (when (is seconds None)
    (<- value (Wait future))
    (return value))
  (<- timer Task (Spawn (expire-after (float seconds))))
  (try
    (<- first (Race future timer))
    first
    (finally
      (<- withdrawing Task (Spawn (withdraw-timer timer)))
      (try
        (<- (Wait withdrawing))
        (except [cancelled TaskCancelledError]
          (<- (Wait withdrawing))
          (raise cancelled))))))


(defk promise-or-cutoff [future seconds]
  {:pre [(: future Future) (: seconds (| float int))] :post [(: % "future の答え(時間切れは None)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "返事を待つ送り手のための安い期限つきの待ち — 書き手が満たす Promise(future)を seconds 秒まで待つ。答え = future の答え(満たす側は
   None を渡さない)か、時間切れの None。期限の鳴らしは daemon の task 1 つで、終わりに取り消すだけ(解け終わりを待たない — daemon なので
   根の終わりの置き去りの検めに数えられない)。promise-or-timeout との違い: 取り消した鳴らしの片付けの task を起こして待たないので、
   起きた後に scheduler へ順番を譲らない。同じ刻の書きを続けて積む書き手を待ってから取る取り手(要求の列の queued-requests)は
   promise-or-timeout を使う(譲らないと、同じ刻に積まれる残りを取り逃がしてまとまりが割れる)。待ち 1 回の scheduler の effect は
   Spawn・Race・Cancel の 3 つ(promise-or-timeout は片付けの task の Spawn と 2 つの Wait を足した 5〜6 つ・#2596)。"
  (<- timer Task (Spawn (expire-after (float seconds)) :daemon True))
  (try
    (<- first (Race future timer))
    first
    (finally
      (<- (Cancel timer)))))
