;;; HTTP の受付の handler(coordinator と記録の置き場が共に使う)— 受付の箱(foundation/coordinator_inbox の RequestInbox)が並べた
;;; 生の要求を Request に解き(NextRequests)、返事の本文を送る byte にして札に置く(Reply)。停止の合図(CoordinatorStopRequested)も
;;; ここ。層 protocol は foundation を読めないので、箱・札・停止の印は下の構造の型(InboxQueue・ReplyTarget・StopSignal)で受ける。
;;; coordinator だけの CoordinatorFault は coordinator/protocol/faults.hy の coordinator-faults(coordinator の entry で重ねる — #2563)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler defk <- var])
(import json)
(import sys)
(import time)
(import typing [Protocol runtime-checkable])
(import urllib.parse [unquote :as url-unquote])
(import doeff_core_effects.scheduler [Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply CoordinatorStopRequested PlainText NextRequests])


(defclass [runtime-checkable] ReplyTarget [Protocol]
  "受付の箱の返事の札の形(foundation/coordinator_inbox の ReplySlot)。protocol が status・data・content-type を置いて done を立てる。"
  (setv #^ object done None #^ int status 0 #^ float created 0.0 #^ bytes data b"" #^ str content-type ""))


(defclass InboxQueue [Protocol]
  "受付の箱の形(foundation/coordinator_inbox の RequestInbox)— 生の要求を limit 件まで取る。"
  (defn #^ list take [self #^ float timeout #^ int limit] (raise NotImplementedError)))


(defclass StopSignal [Protocol]
  "停止の合図の形(foundation/coordinator_inbox の StopState)。"
  (setv #^ bool requested False))


;; slot = 返事を待つ受付の側の物: 本番の HTTP の受付は ReplySlot、手元の宿(local.hy)は Promise、判断だけを見る検は None。
(defk http-request [method path query body [slot None] [actor None] [peer ""]]
  {:pre [(: method str) (: path str) (: query dict) (: body (| dict list str int float bool None))
         (: slot (| ReplyTarget Promise None)) (: actor (| str None)) (: peer str)]
   :post [(: % Request)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "受けた HTTP 要求 1 件を Request にするため。path を / で割り、区切りごとに percent の符号を戻して parts に載せる(符号を戻すのは
   HTTP の境のこの 1 か所 — 受け口の判断 api_policy.respond は parts だけを読む・#1636)。slot = 返事を待つ受付の側の物(判断は見ない)。"
  (Request method path query body (tuple (gfor p (.split (.strip path "/") "/") (url-unquote p)))
           :slot slot :actor actor :peer peer))


(defk requests-of [raws]
  {:pre [(: raws list)] :post [(: % (get tuple #(Request ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "受付の箱が並べた生の要求の列を、並びのまま Request の列にするため(NextRequests の答え)。"
  (var requests #())
  (for [raw raws]
    (<- request Request (http-request raw.method raw.path raw.query raw.body :slot raw.slot :actor raw.actor :peer raw.peer))
    (:= requests (+ requests #(request))))
  requests)


(defn #^ tuple encoded-reply [#^ object body]
  "返事の本文を送る byte と content-type の組にするため(PlainText はそのまま text・ほかは JSON)。受付の箱の HTTP の thread は
   この組をそのまま書く(coordinator と記録の置き場の受付が同じ 1 点を使う)。"
  (if (isinstance body PlainText)
      #((.encode body.text "utf-8") body.content-type)
      #((.encode (json.dumps body :ensure-ascii False) "utf-8") "application/json; charset=utf-8")))


(defhandler http-requests [#^ InboxQueue inbox]
  ;; 引数に残す理由: 受付の箱(HTTP の server の thread と列)は composition root が起動の時に 1 つ作って渡す
  (NextRequests [timeout-seconds limit]
    (<- requests (get tuple #(Request ...)) (requests-of (.take inbox timeout-seconds limit)))
    (resume (list requests)))
  (Reply [request status body]
    (setv slot request.slot)
    (assert (isinstance slot ReplyTarget) "http-requests の要求の札は受付の箱の ReplySlot")
    ;; 返事まで 1 秒を超えた要求を 1 行出す(調停ループが何かを待って止まった時の手がかり)。版の変化を待つ読み(GET /watch)は
    ;; 待つのが仕事なので出さない(#1933)。
    (setv waited (- (time.monotonic) slot.created))
    (when (and (> waited 1.0) (!= (tuple request.parts) #("watch")))
      (print (.format "coordinator: 遅い返事 {:.1f} 秒: {} {}" waited request.method request.path) :file sys.stderr :flush True))
    (setv #(data content-type) (encoded-reply body))
    (setv slot.status status slot.data data slot.content-type content-type)
    (.set slot.done)
    (resume None)))


(defhandler stop-flag [#^ StopSignal state]
  ;; 引数に残す理由: 停止の印は SIGTERM の handler(entry)と共有する 1 つ
  (CoordinatorStopRequested [] (resume state.requested)))
