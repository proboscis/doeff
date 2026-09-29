;;; 書きで起きる待ちの 1 点 — 書き手が満たす Promise を、期限まで待つ(読み直さない)。
;;;
;;; 模擬の世界(local.hy)の process の始まり・終わりの待ちと切り離した task の呼び鈴(proboscis/doeff#631)、模擬の coordinator の要求の列の
;;; 待ち(coordinator_handler_sets.queued-requests)が使う。local.hy から分けたのは、handler の組の module
;;; (coordinator_handler_sets.hy)が local.hy を import すると循環するため(local.hy が組の module を import する)。
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
