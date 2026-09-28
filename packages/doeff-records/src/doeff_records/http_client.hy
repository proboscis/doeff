;;; 記録の service の HTTP の口に公開 effect 7 つで答える client の handler — 別の process の Hy / Python の Program が、
;;; memory や PostgreSQL の handler と同じ effect のまま記録の service を読み書きするため。
;;;
;;; 書き手の身元は effect の引数ではなく endpoint の token(handler を組む時に渡す)。service がその token を身元の名簿で書き手の名へ引く。
;;; 綴りは wire.hy(service と同じ 1 か所)。
;;;
;;; 答えの写し方:
;;;   200                    wire の本文の答え(Row・Page・Written・Conflict・Refused・Changes・WrittenRows・RowsConflict・RowsRefused …)
;;;   503 / 届かない          Unreachable(読みは撃ち直してよい・書きは期待つきなら撃ち直してよい)
;;;   401 / 403              RecordsUnauthorized を上げる(操作を問わない — 組み立ての誤り): handler を組んだ token が記録の service の
;;;                          身元の名簿に無い(401)・前に立つ口が名乗りを断った(403)。時間を置いて撃ち直しても晴れないので
;;;                          Unreachable(時間で晴れる届かなさ)と読ませない — 読みを Unreachable に写していた時は、token を誤った
;;;                          呼び手が落ちずに「届かなかった」として読みを撃ち直し続け、設定の誤りが見えなかった。
;;;                          書きを Refused にもしない — Refused は宣言がその書きを断った答えで、名簿に在る書き手が宣言の書き手で
;;;                          ない時は今も 200 の本文の Refused で返る(memory の handler と同じ)。身元が引けないのは宣言の判断ではない。
;;;                          status だけで決める(前に立つ口の 403 の本文は JSON の断りとは限らない)
;;;   404(宣言に無い表)      UndeclaredTable を上げる(組み立ての誤り — memory の handler と同じ)
;;;   400 / 500              WireError を上げる(client か service の実装の誤り)
;;;
;;; WatchChanges の待ちは client の側で回す(service へは timeout 0 で撃ち、空なら poll-seconds 眠って撃ち直す — doeff-time の Delay)。
;;; 時計は呼び手の時計なので、仮想の時計の下では memory の handler と同じに一瞬で進む。
;;;
;;; 要求の送り方は endpoint の transport が決める(閉じた 2 種):
;;;   BlockingTransport(既定)  呼び手の thread で urllib の urlopen を撃つ — 同期の run の中の client(送る間は VM が止まる)
;;;   EffectTransport          要求を doeff-core-effects の HttpRequest の effect として出す — 答え手は外側(本番 = 塞がない
;;;                            http-production-handler と await-handler)。処理ループと同じ scheduler の task から読む呼び手が、記録の
;;;                            service に届かない間も処理ループを止めないため。届かない(HttpFailed)は Unreachable に読む
(require doeff-hy.macros [defhandler defk <- val var])
(import dataclasses [dataclass])
(import json)
(import socket)
(import urllib.error [HTTPError URLError])
(import urllib.request [Request urlopen])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff_records.values [Unreachable UndeclaredTable])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges AppendEvent ReadEvents])
(import doeff_records.watching [wait-for-changes])
(import doeff_records.wire [PATH-PREFIX PublicEffect WireAnswer JsonValue encode-request decode-answer refusal-from])

(setv DEFAULT-REQUEST-TIMEOUT 30.0)
(setv DEFAULT-POLL-SECONDS 0.2)


(defclass WireError [RuntimeError]
  "service が 400 / 500 で答えた(client か service の実装の誤り — 値の失敗ではない)。")


(defclass RecordsUnauthorized [Exception]
  "記録の service が、handler を組んだ endpoint の token の身元を認めない(401 / 403 — 組み立ての誤り。値の失敗ではない)。
   token の file と記録の service の身元の名簿の食い違いで、時間を置いて撃ち直しても晴れないので、答えの値(Unreachable・Refused)に
   せず上げる — 読み手は撃ち直しを続けずに名指しで落ちる(file の頭の註)。Exception を直に継ぐ: 読み手が ValueError・RuntimeError・
   OSError を受ける所(値の検め・file の読み)で黙って呑まれないため。")

;; 身元の断りの status(file の頭の表)。
(val IDENTITY-REFUSED-STATUSES #(401 403))
;; 身元の断りの理由を例外の文へ写す字数の上限(前に立つ口の HTML の本文を丸ごと写さない)。
(val REASON-MAX-CHARS 300)


(defclass [(dataclass :frozen True)] BlockingTransport []
  "要求を呼び手の thread で urllib の urlopen に撃つ(既定 — file の頭の註)。")


(defclass [(dataclass :frozen True)] EffectTransport []
  "要求を HttpRequest の effect として出す(答え手は外側 — file の頭の註)。")


(defclass [(dataclass :frozen True)] RecordsEndpoint []
  "記録の service 1 つへの接続の組: base-url = http://host:port / token = 呼び手の身元の token(Bearer)/
   request-timeout = 要求 1 つの上限の秒 / poll-seconds = WatchChanges の待ちの読み直しの間隔 / transport = 要求の送り方(file の頭の註)。"
  (#^ str base-url)
  (#^ str token)
  (setv #^ float request-timeout DEFAULT-REQUEST-TIMEOUT)
  (setv #^ float poll-seconds DEFAULT-POLL-SECONDS)
  (setv #^ (| BlockingTransport EffectTransport) transport (BlockingTransport)))


(defclass [(dataclass :frozen True)] RawReply []
  "service の答え 1 つの生の形: status と本文の byte(JSON として読むかは status を見てから決める — 身元の断りの本文は JSON とは
   限らない)。"
  (#^ int status)
  (#^ bytes payload))


(defk service-url [endpoint operation]
  {:pre [(: endpoint RecordsEndpoint) (: operation str)] :post [(: % str)]}
  "操作 1 つの口の URL。"
  (+ (.rstrip endpoint.base-url "/") PATH-PREFIX operation))


(defk request-headers [endpoint]
  {:pre [(: endpoint RecordsEndpoint)] :post [(: % dict)]}
  "要求の header(本文の型と身元の token)。"
  {"Content-Type" "application/json; charset=utf-8"
   "Authorization" (+ "Bearer " endpoint.token)})


(defk request-bytes [body]
  {:pre [(: body dict)] :post [(: % bytes)]}
  "要求の本文の綴り(どちらの送り方も同じ byte を送る)。"
  (.encode (json.dumps body :ensure-ascii False :separators #("," ":")) "utf-8"))


(defk reply-json [operation reply]
  {:pre [(: operation str) (: reply RawReply)] :post [(: % JsonValue)]
   :tags {:context "records" :role "foundation"}}
  "答えの本文を JSON の値として読む(本文が JSON でなければ WireError)。"
  (try
    (json.loads (.decode reply.payload "utf-8"))
    (except [error #(UnicodeDecodeError json.JSONDecodeError)]
      (raise (WireError (.format "{} の答え(status {})が JSON でない: {}" operation reply.status error))))))


(defk exchange-blocking [endpoint operation body]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body dict)] :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを urllib の urlopen で送る(BlockingTransport)。届かなければ Unreachable。"
  (<- url str (service-url endpoint operation))
  (<- headers dict (request-headers endpoint))
  (<- data bytes (request-bytes body))
  (setv request (Request url :data data :method "POST" :headers headers))
  (try
    (with [response (urlopen request :timeout endpoint.request-timeout)]
      (setv status response.status payload (.read response)))
    (except [error HTTPError]
      (setv status error.code payload (.read error)))
    (except [error #(URLError ConnectionError socket.timeout)]
      (return (Unreachable (.format "記録の service に届かない: {}" error)))))
  (RawReply status payload))


(defk exchange-by-effect [endpoint operation body]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body dict)] :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを HttpRequest の effect として出す(EffectTransport)。撃ち直しは呼び手の読みが決めるので 0 回、届かない失敗は値で受けて
   Unreachable にする。"
  (<- url str (service-url endpoint operation))
  (<- headers dict (request-headers endpoint))
  (<- data bytes (request-bytes body))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest "POST" url :headers headers :body data :timeout-seconds endpoint.request-timeout :max-retries 0
                   :follow-redirects False :failures-as-values True))
  (when (isinstance answer HttpFailed)
    (return (Unreachable (.format "記録の service に届かない: {}" answer.detail))))
  (RawReply answer.status answer.content))


(defk exchange [endpoint operation body]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body dict)] :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを送り、status と JSON の本文を受ける(HTTP の境界の 1 か所 — 送り方は endpoint の transport)。届かなければ Unreachable。"
  (match endpoint.transport
    (BlockingTransport) (! (exchange-blocking endpoint operation body))
    (EffectTransport) (! (exchange-by-effect endpoint operation body))))


(defk refused-reason [payload]
  {:pre [(: payload bytes)] :post [(: % str)] :tags {:context "records" :role "foundation"}}
  "身元の断りの本文から人の読む理由を取り出す: 記録の service の断り(JSON の {error reason})なら reason、ほか(前に立つ口の本文)は
   頭の REASON-MAX-CHARS 字。"
  (val text (.strip (.decode payload "utf-8" :errors "replace")))
  (var body None)
  (try
    (:= body (json.loads text))
    (except [json.JSONDecodeError]
      (return (cut text 0 REASON-MAX-CHARS))))
  (if (and (isinstance body dict) (isinstance (.get body "reason") str))
      (get body "reason")
      (cut text 0 REASON-MAX-CHARS)))


(defk identity-refused [endpoint operation reply]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: reply RawReply)] :post [(: % RecordsUnauthorized)]
   :tags {:context "records" :role "foundation"}}
  "身元の断り(401 / 403)を、読み手が名指しで落ちる例外にする — 口・status・操作・理由と、確かめる所(token の file と身元の名簿)を
   文に置く(token そのものは文に写さない)。"
  (<- reason str (refused-reason reply.payload))
  (RecordsUnauthorized
    (.format "記録の service {} が token の身元を認めない({} {}): {} — handler を組んだ token(token の file)と記録の service の身元の名簿を確かめる"
             endpoint.base-url reply.status operation reason)))


(defk call-service [endpoint ask]
  {:pre [(: endpoint RecordsEndpoint) (: ask PublicEffect)] :post [(: % (| WireAnswer Unreachable))]}
  "公開 effect(ask)1 つを service へ撃ち、答えの値にする(status の写し方は file の頭の表)。"
  (<- request (encode-request ask))
  (<- reply (exchange endpoint request.operation request.body))
  (when (isinstance reply Unreachable) (return reply))
  (when (in reply.status IDENTITY-REFUSED-STATUSES)
    (raise (! (identity-refused endpoint request.operation reply))))
  (<- body (reply-json request.operation reply))
  (when (= reply.status 200)
    (return (! (decode-answer request.operation body))))
  (<- refusal (refusal-from body))
  (match refusal.error
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
  (PutRow [table key value expect]
    (<- answer (call-service endpoint effect))
    (resume answer))
  (PutRows [writes]
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
