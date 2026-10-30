;;; 期限の関数の答え(#3865)— 状態がこのままで判断の答えが変わる最初の刻を、「刻・今すぐ・無し」の閉じた型で返す。
;;;
;;; coordinator と worker が共用する(#3871 で coordinator の層から移した)。coordinator の期限の関数(cluster_policy の liveness-due・
;;; task-due・sweep-due・api_policy の tick-due・wake_policy の rollout-due)と、worker の期限の関数(worker/core/worker_due)が返す。
;;; 今まで「まだ落ち着いていない(すぐもう 1 歩)」を数の now + 1 で返していたので、1 秒の格子に乗らない待ちでは「1 ms 先の期限」と
;;; 見分けられなかった。答えを 3 つに閉じ、受け手は 3 つを名で分ける。合わせ方は shared/core/due_policy。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])


(defrecord DueAt
  "期限の答え: 刻 at(epoch ms)に判断の答えが変わり得る。at の 1 ms 前までは、状態がこのままなら判断は何も変えない。"
  (#^ int at))


(defrecord DueNow
  "期限の答え: まだ落ち着いていない — 今の刻で判断すれば状態が変わる(生きていないと数える名の求め直しが違う・期限が既に過ぎている・
   Rollout の判断が今読む物を持つ)。待たずにもう 1 歩。")


(defrecord DueNever
  "期限の答え: 状態がこのままなら、時刻では判断は何も変えない(要求か外の出来事でだけ変わる)。")
