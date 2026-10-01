;;; 書きで起きる待ちの 1 点 — 書き手が満たす Promise を、期限まで待つ(読み直さない)。
;;;
;;; 模擬の世界(local.hy)の process の始まり・終わりの待ちと切り離した task の呼び鈴(proboscis/doeff#631)、模擬の coordinator の要求の列の
;;; 待ち(coordinator.protocol.request_queue.queued-requests)、模擬の送り手の返事の待ち(sim.local.send-request・#2596)が使う。
;;; local.hy から分けたのは、handler の組の module(coordinator/entry/handler_sets.hy)が local.hy を import すると循環するため
;;; (local.hy が組の module を import する)。期限は doeff-time の WaitWithin(外側の sim-time-handler・async-time-handler・
;;; sync-time-handler が答える)。
(require doeff-hy.macros [defk <-])
(import doeff_core_effects.scheduler [Wait Future])
(import doeff_time [WaitWithin])


(defk promise-or-timeout [future seconds]
  {:pre [(: future Future) (: seconds (| float int None))] :post [(: % "future の答え(時間切れは None)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "書き手が満たす Promise(future)を、seconds 秒(None = 上限なし)まで待つため — 読み直さずに書きで起きる待ちの 1 点。答え = future の
   答え(満たす側は None を渡さない)か、時間切れの None。期限は時計の handler が持つ(WaitWithin — 模擬の時計では時計の列の 1 項を
   future と競わせ、future が先なら列から外す。期限の task を起こさないので、待ち 1 回は Delay 1 回と同じ費用・#2618)。"
  (if (is seconds None)
      (do (<- value (Wait future))
          value)
      (do (<- first (WaitWithin future (float seconds)))
          first)))
