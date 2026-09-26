;;; 記録の service の HTTP の口に公開 effect 6 つで答える client の handler — 別の process の Hy / Python の Program が、
;;; memory や PostgreSQL の handler と同じ effect のまま記録の service を読み書きするため。
;;;
;;; 書き手の身元は effect の引数ではなく endpoint の token(handler を組む時に渡す)。service がその token を身元の名簿で書き手の名へ引く。
;;; 綴りは wire.hy(service と同じ 1 か所)。
;;;
;;; 答えの写し方:
;;;   200                    wire の本文の答え(Row・Page・Written・Conflict・Refused・Changes …)
;;;   503 / 届かない          Unreachable(読みは撃ち直してよい・書きは期待つきなら撃ち直してよい)
;;;   401(名簿に無い token)  書き(PutRow・AppendEvent)は Refused、読みは Unreachable — 書き手でない呼び手の書きが確定しないことを
;;;                          memory の handler と同じ Refused の形で返す
;;;   404(宣言に無い表)      UndeclaredTable を上げる(組み立ての誤り — memory の handler と同じ)
;;;   400 / 500              WireError を上げる(client か service の実装の誤り)
;;;
;;; WatchChanges の待ちは client の側で回す(service へは timeout 0 で撃ち、空なら poll-seconds 眠って撃ち直す — doeff-time の Delay)。
;;; 時計は呼び手の時計なので、仮想の時計の下では memory の handler と同じに一瞬で進む。
(require doeff-hy.macros [defhandler defk <-])
(import dataclasses [dataclass])
(import json)
(import socket)
(import urllib.error [HTTPError URLError])
(import urllib.request [Request urlopen])
(import doeff_records.values [Refused Unreachable UndeclaredTable])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.watching [wait-for-changes])
(import doeff_records.wire [PATH-PREFIX WRITE-OPERATIONS PublicEffect WireAnswer JsonValue
                            encode-request decode-answer refusal-from])

(setv DEFAULT-REQUEST-TIMEOUT 30.0)
(setv DEFAULT-POLL-SECONDS 0.2)


(defclass WireError [RuntimeError]
  "service が 400 / 500 で答えた(client か service の実装の誤り — 値の失敗ではない)。")


(defclass [(dataclass :frozen True)] RecordsEndpoint []
  "記録の service 1 つへの接続の組: base-url = http://host:port / token = 呼び手の身元の token(Bearer)/
   request-timeout = 要求 1 つの上限の秒 / poll-seconds = WatchChanges の待ちの読み直しの間隔。"
  (#^ str base-url)
  (#^ str token)
  (setv #^ float request-timeout DEFAULT-REQUEST-TIMEOUT)
  (setv #^ float poll-seconds DEFAULT-POLL-SECONDS))


(defclass [(dataclass :frozen True)] RawReply []
  "service の答え 1 つの生の形: status と JSON の本文(読めた値)。"
  (#^ int status)
  (#^ JsonValue body))


(defk exchange [endpoint operation body]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body dict)] :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを送り、status と JSON の本文を受ける(HTTP の境界の 1 か所)。届かなければ Unreachable。"
  (setv request (Request (+ (.rstrip endpoint.base-url "/") PATH-PREFIX operation)
                         :data (.encode (json.dumps body :ensure-ascii False :separators #("," ":")) "utf-8")
                         :method "POST"
                         :headers {"Content-Type" "application/json; charset=utf-8"
                                   "Authorization" (+ "Bearer " endpoint.token)}))
  (try
    (with [response (urlopen request :timeout endpoint.request-timeout)]
      (setv status response.status payload (.read response)))
    (except [error HTTPError]
      (setv status error.code payload (.read error)))
    (except [error #(URLError ConnectionError socket.timeout)]
      (return (Unreachable (.format "記録の service に届かない: {}" error)))))
  (try
    (RawReply status (json.loads (.decode payload "utf-8")))
    (except [error #(UnicodeDecodeError json.JSONDecodeError)]
      (raise (WireError (.format "{} の答え(status {})が JSON でない: {}" operation status error))))))


(defk call-service [endpoint ask]
  {:pre [(: endpoint RecordsEndpoint) (: ask PublicEffect)] :post [(: % (| WireAnswer Unreachable))]}
  "公開 effect(ask)1 つを service へ撃ち、答えの値にする(status の写し方は file の頭の表)。"
  (<- request (encode-request ask))
  (<- reply (exchange endpoint request.operation request.body))
  (when (isinstance reply Unreachable) (return reply))
  (when (= reply.status 200)
    (return (! (decode-answer request.operation reply.body))))
  (<- refusal (refusal-from reply.body))
  (match refusal.error
    "unauthorized" (if (in request.operation WRITE-OPERATIONS)
                       (Refused (.format "記録の service が身元を認めない: {}" refusal.reason))
                       (Unreachable (.format "記録の service が身元を認めない: {}" refusal.reason)))
    "store-unavailable" (Unreachable refusal.reason)
    "not-found" (raise (UndeclaredTable refusal.reason))
    _ (raise (WireError (.format "{} が {} で断られた: {} {}" request.operation reply.status refusal.error refusal.reason)))))


(defhandler http-records-handler [#^ RecordsEndpoint endpoint]
  (ReadRow [table key]
    (<- answer (call-service endpoint effect))
    (resume answer))
  (ListRows [table where fields cursor limit]
    (<- answer (call-service endpoint effect))
    (resume answer))
  (PutRow [table key value expect approval]
    (<- answer (call-service endpoint effect))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    ;; 待ちは client の時計で回す — service へは待たない問い(timeout 0)だけを撃つ。
    (setv once (WatchChanges tables cursor :timeout 0.0 :limit limit))
    (<- answer (wait-for-changes (fn [now-ms] (call-service endpoint once)) endpoint.poll-seconds timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- answer (call-service endpoint effect))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- answer (call-service endpoint effect))
    (resume answer)))
