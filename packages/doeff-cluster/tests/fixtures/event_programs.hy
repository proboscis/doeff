;;; sim-cluster の行き止まりの見張りの検の見本(#3078)— 出来事(doeff-events の WaitForEvent)を待つ service。
;;;
;;; 本番では、出来事の答え手(doeff-events の handler)は土台か外の世界が並べる。sim では sim の外の世界(SimOutside)に memory の
;;; event-handler を置き、柵は WaitForEventEffect と PublishEffect を外へ通す。
;;; 止めの合図の検(#3145)の service は、出来事の答え手(memory の購読者の bus)を本体の中に並べる — 柵の外の世界を要らない。
(require doeff-hy.macros [defk defsystem <- val])
(require doeff-hy.record [defrecord])
(require doeff-events.macros [event-loop])
(import collections.abc [Callable])
(import dataclasses [dataclass field])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_events [ArmTimer TimerFired WaitForEvent EventBus subscribed-event-handler])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_time [GetTime])
(import doeff_cluster.shared.intent.shared_model [WriteShared])


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


(defsystem ping-waiters [#^ Callable foundation]
  "合図 Ping を待つ 1 つの service"
  (waiter (ping-waiter-program foundation) :replicas 1 :needs #{"cluster-net"}))


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


(defsystem deadline-waiters [#^ Callable foundation]
  "自分の期限の timer を待つ 1 つの service"
  (waiter (deadline-waiter-program foundation) :replicas 1 :needs #{"cluster-net"}))


;; --- 止めの合図(#3145)— worker の TERM を止めの合図として受ける係と、止めの合図を無視する係 --------------------------------

(defk pings-until-stop []
  {:pre [] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "合図 Ping を数えながら待ち、止めの合図(StopArrived)が来たらその理由を値にして抜けるため(止めの節を持つ係の loop)。"
  (event-loop [pings 0]
    (:stop why) why
    (Ping note) (+ pings 1)))


(defk minding-stop [key]
  {:pre [(: key str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めの節を持つ係(doeff-events の event-loop): 合図 Ping を待ち、止めの合図(StopArrived)が来たら盤の key に止めの理由を書いて
   (後始末の印)自分で終わる — worker の TERM が取り消しでなく止めの合図として届くかを見るため。"
  (val bus (EventBus))
  (<- reason str ((subscribed-event-handler bus "minder" #(Ping)) (pings-until-stop)))
  (<- (WriteShared key (OpaqueJson.of {"reason" reason})))
  reason)


(defk ping-after-asking-stop []
  {:pre [] :post [(: % Ping)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めの問いを 1 度だけ出して(合図の受け手を据える)、答えを見ずに合図 Ping を待つため(止めの合図を無視する係の本体)。"
  (<- asked (| str None) (StopRequested))
  (<- ping Ping (WaitForEvent Ping))
  ping)


(defk ignoring-stop []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めの合図を無視する係: 止めの問いを 1 度だけ出して、その後は来ない合図 Ping を待ち続ける — 猶予の後の KILL で取り消されるかを
   見るため。"
  (val bus (EventBus))
  (<- ((subscribed-event-handler bus "deaf" #(Ping)) (ping-after-asking-stop)))
  None)


(defk stop-minder-program [foundation key]
  {:pre [(: foundation Callable) (: key str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: minding-stop を土台で包む。"
  (<- reason str (foundation (minding-stop key)))
  reason)


(defk stop-ignorer-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: ignoring-stop を土台で包む。"
  (<- (foundation (ignoring-stop)))
  None)


(defsystem stop-minders [#^ Callable foundation]
  "止めの節を持つ 1 つの service(止めの合図で後始末の印を書いて終わる)"
  (minder (stop-minder-program foundation "stop/minder") :replicas 1 :needs #{"cluster-net"}))


(defsystem stop-ignorers [#^ Callable foundation]
  "止めの合図を無視する 1 つの service(止めの問いを 1 度だけ出し、来ない合図を待ち続ける)"
  (ignorer (stop-ignorer-program foundation) :replicas 1 :needs #{"cluster-net"}))
