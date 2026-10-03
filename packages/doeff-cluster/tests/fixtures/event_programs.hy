;;; sim-cluster の行き止まりの見張りの検の見本(#3078)— 出来事(doeff-events の WaitForEvent)を待つ service。
;;;
;;; 本番では、出来事の答え手(doeff-events の handler)は土台か外の世界が並べる。sim では sim の外の世界(SimOutside)に memory の
;;; event-handler を置き、柵は WaitForEventEffect と PublishEffect を外へ通す。
(require doeff-hy.macros [defk defsystem <- val])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass field])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import doeff_events [ArmTimer TimerFired WaitForEvent])
(import doeff_time [GetTime])


(defrecord Ping
  "見本の合図(どこが変わったかだけを運ぶ合図の形 — 中身は note の 1 欄だけ)。"
  (setv #^ str note ""))


(defk wait-one-ping []
  {:pre [] :post [(: % Ping)] :tags {:context "doeff-cluster-test" :role "program"}}
  "合図 Ping を 1 つ待つ(待つ間は時計を進めない — 誰かが Publish するまで止まる)。"
  (<- ping Ping (WaitForEvent Ping))
  ping)


(defk ping-waiter-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "合図 Ping を 1 つ受けたら終わる service。"
  (<- (foundation (wait-one-ping)))
  None)


(defsystem ping-waiters [foundation]
  "合図 Ping を待つ 1 つの service"
  (waiter (ping-waiter-program foundation) :needs #{"cluster-net"}))


;; --- 業務の timer(#3093)— 期限を出来事にして待つ service ----------------------------------------------------

(defk wait-for-own-deadline [seconds]
  {:pre [(: seconds float)] :post [(: % TimerFired)] :tags {:context "doeff-cluster-test" :role "program"}}
  "自分の期限の timer を seconds 秒先に積み、その TimerFired を待つ(期限が来るまで出来事を待って止まる — 業務の timer が在るので
   行き止まりではない。時間を直に待たず、期限を出来事にする形)。"
  (<- now datetime (GetTime))
  (<- (ArmTimer "deadline" (+ now (timedelta :seconds seconds))))
  (<- fired TimerFired (WaitForEvent TimerFired))
  fired)


(defk deadline-waiter-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "20 秒先の自分の期限を積み、その TimerFired を受けたら終わる service。"
  (<- (foundation (wait-for-own-deadline 20.0)))
  None)


(defsystem deadline-waiters [foundation]
  "自分の期限の timer を待つ 1 つの service"
  (waiter (deadline-waiter-program foundation) :needs #{"cluster-net"}))
