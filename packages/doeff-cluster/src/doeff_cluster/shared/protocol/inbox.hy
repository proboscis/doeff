;;; HTTP の受付の handler(coordinator と記録の置き場が共に使う)— 受付の箱(foundation/coordinator_inbox の RequestInbox)が並べた
;;; 生の要求を Request に解き(NextRequests)、返事の本文を送る byte にして札に置く(Reply)。停止の合図(CoordinatorStopRequested)も
;;; ここ。層 protocol は foundation を読めないので、箱・札・停止の印は下の構造の型(InboxQueue・ReplyTarget・StopSignal)で受ける。
;;; coordinator だけの CoordinatorFault は coordinator/protocol/faults.hy の coordinator-faults(coordinator の entry で重ねる — #2563)。
;;;
;;; 要求の待ちは scheduler の上で待つ(raw-requests — #4270): 箱が要求を並べた時と起こしの時に鳴らす呼び鈴(外から満たす Promise)を、
;;; 期限まで待つ。待つ間、同じ scheduler の他の task(coordinator が Spawn した worker の生死の知らせの送り)が外からの完了(Redis の
;;; 送りの答え)を受けて進む。以前は箱の take が scheduler の外で thread を塞いだので、送りの task は次の Spawn まで回らず、知らせは
;;; 生死の動きのたびに 1 つずつしか出なかった。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler defk <- var])
(import json)
(import typing [Protocol runtime-checkable])
(import urllib.parse [unquote :as url-unquote])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise PRIORITY-IDLE Promise Wait])
(import doeff_time [WaitWithin])
(import doeff_cluster.shared.intent.protocol [Request Reply CoordinatorStopRequested PlainText NextRequests])


(defclass [runtime-checkable] ReplyTarget [Protocol]
  "受付の箱の返事の札の形(foundation/coordinator_inbox の ReplySlot)。protocol が status・data・content-type を置いて done を立てる。"
  (setv #^ object done None #^ int status 0 #^ bytes data b"" #^ str content-type ""))


(defclass [runtime-checkable] InboxQueue [Protocol]
  "受付の箱の形(foundation/coordinator_inbox の RequestInbox)。arm = 取り手の呼び鈴 bell を掛け、待ちの秒 timeout(None = 期限なし)を
   記し、既に何か(要求か起こし)が並んでいれば真を返す — 箱は以後、要求を並べた時と起こしの時に bell を満たす。taken = 並んでいる
   生の要求を limit 件まで待たずに取り、掛けていた呼び鈴を外す(起こしを受けたら、そこまでに取った要求だけ)。"
  (defn #^ bool arm [self #^ (| float None) timeout #^ ExternalPromise bell] (raise NotImplementedError))
  (defn #^ list taken [self #^ int limit] (raise NotImplementedError)))


(defclass StopSignal [Protocol]
  "停止の合図の形(foundation/coordinator_inbox の StopState)。"
  (setv #^ bool requested False))


;; slot = 返事を待つ受付の側の物: 本番の HTTP の受付は ReplySlot、手元の宿(local.hy)は Promise、判断だけを見る検は None。
(defk http-request [method path query body [slot None] [actor None] [peer ""] [queued-ms 0]]
  {:pre [(: method str) (: path str) (: query dict) (: body (| dict list str int float bool None))
         (: slot (| ReplyTarget Promise None)) (: actor (| str None)) (: peer str) (: queued-ms int)]
   :post [(: % Request)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "受けた HTTP 要求 1 件を Request にするため。path を / で割り、区切りごとに percent の符号を戻して parts に載せる(符号を戻すのは
   HTTP の境のこの 1 か所 — 受け口の判断 api_policy.respond は parts だけを読む・#1636)。slot = 返事を待つ受付の側の物(判断は見ない)。
   queued-ms = 受付の箱に並んでから取られるまでの ms(箱が測る — 箱を通らない要求は 0)。"
  (Request method path query body (tuple (gfor p (.split (.strip path "/") "/") (url-unquote p)))
           :slot slot :actor actor :peer peer :queued-ms queued-ms))


(defk requests-of [raws]
  {:pre [(: raws list)] :post [(: % (get tuple #(Request ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "受付の箱が並べた生の要求の列を、並びのまま Request の列にするため(NextRequests の答え)。箱が取りの刻に測った並びの ms も運ぶ。"
  (var requests #())
  (for [raw raws]
    (<- request Request (http-request raw.method raw.path raw.query raw.body :slot raw.slot :actor raw.actor :peer raw.peer
                                      :queued-ms raw.queued-ms))
    (:= requests (+ requests #(request))))
  requests)


(defn #^ tuple encoded-reply [#^ object body]
  "返事の本文を送る byte と content-type の組にするため(PlainText はそのまま text・ほかは JSON)。受付の箱の HTTP の thread は
   この組をそのまま書く(coordinator と記録の置き場の受付が同じ 1 点を使う)。"
  (if (isinstance body PlainText)
      #((.encode body.text "utf-8") body.content-type)
      #((.encode (json.dumps body :ensure-ascii False) "utf-8") "application/json; charset=utf-8")))


(defk raw-requests [inbox timeout-seconds limit]
  {:pre [(: inbox InboxQueue) (: timeout-seconds (| float None)) (: limit int)] :post [(: % list)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "受付の箱から、最初の 1 件を timeout-seconds 秒(None = 期限なし)まで待ち、その時点で並んでいる生の要求を limit 件まで取るため。
   待ちは scheduler の上(頭の註): 呼び鈴を掛け、何も並んでいなければ、箱が鳴らすか期限が来るまで待つ。呼び鈴は外の thread(HTTP の
   server・停止の合図の受け手)が満たす Promise なので、仮想の時計を止めずに待つ(park — 期限の刻へ時計が進める)。取った後は呼び鈴を
   満たして終わらせる(鳴らなかった呼び鈴を、待ち手の居ない外の Promise として scheduler に残さない — 遅れて鳴っても捨てられる)。"
  (<- bell ExternalPromise (CreateExternalPromise))
  (when (not (.arm inbox timeout-seconds bell))
    (match timeout-seconds
      None (<- (Wait bell.future :priority PRIORITY-IDLE))
      _ (<- (WaitWithin bell.future timeout-seconds :park True))))
  (val raws (.taken inbox limit))
  (.complete bell True)
  raws)


(defhandler http-requests [#^ InboxQueue inbox]
  ;; 引数に残す理由: 受付の箱(HTTP の server の thread と列)は composition root が起動の時に 1 つ作って渡す
  (NextRequests [timeout-seconds limit]
    (<- raws list (raw-requests inbox timeout-seconds limit))
    (<- requests (get tuple #(Request ...)) (requests-of raws))
    (resume (list requests)))
  (Reply [request status body]
    (setv slot request.slot)
    (assert (isinstance slot ReplyTarget) "http-requests の要求の札は受付の箱の ReplySlot")
    ;; 返事が遅かった時の 1 行は、札を作って返事を待つ受付の箱(foundation/coordinator_inbox)が出す。
    (setv #(data content-type) (encoded-reply body))
    (setv slot.status status slot.data data slot.content-type content-type)
    (.set slot.done)
    (resume None)))


(defhandler stop-flag [#^ StopSignal state]
  ;; 引数に残す理由: 停止の印は SIGTERM の handler(entry)と共有する 1 つ
  (CoordinatorStopRequested [] (resume state.requested)))
