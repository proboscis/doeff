;;; 記録の service の HTTP の口の流れ — HTTP の要求 1 つを、呼び手の身元の書き手の handler の下で公開 effect 1 つにして答える Program。
;;;
;;; 判断は持たない: 綴りの読み書きは wire.hy、書き手の名は principals.hy、書きの許可・期待・保持は記録の handler(admission)。
;;; ここが持つのは順序だけ — 書き手の名 → 本文の読み → 宣言に在る表か → effect を撃つ → 答えを綴る。
;;; 答えの綴り(ADR-DOE-RECORDS-002 — loop_work.hy の頭の註): 束の読みの答え(Page・Changes・Events で行を 1 つ以上運ぶ答え)は、綴り
;;; (答え → JSON の文字列 → UTF-8 の byte 列 — reply-of)を Compute で scheduler の thread の外で回す(答え手は組み立ての側 —
;;; 本番 = thread の pool・台本の検と模擬 = inline-compute-handler)。ほかの答えは大きさが要求の本文か 1 行の上限で決まり、その場で綴る。
;;; 200 の答えの綴りは wire.answer-text で、行と変更の値は置き場の保存の綴りをそのまま継ぐ(値を解き直し綴り直さない — wire.hy の答えの註)。
;;; 書き手の名は effect の引数にしない: 呼び手の名乗り(X-Records-Writer)の名で handler を組み(handler-for)、その下で effect を撃つ。呼び手は断らない(#2988)。
;;;
;;;   POST /v1/records/<操作>   200 = effect の答え(kind で判別)/ 400 malformed / 404 not-found(宣言に無い表・
;;;                             知らない route)/ 503 store-unavailable(置き場に届かない = Unreachable)/ 500 internal
;;;   GET  /healthz             200(process が生きている)
;;; 綴りの正本 = wire.hy。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "protocol"})
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import json)
(import doeff [with_handlers])
(import doeff_records.values [RecordsSchema Unreachable])
(import doeff_records.principals [Principal writer-of])
(import doeff_records.loop_work [off-loop off-loop-answer?])
(import doeff_records.wire [PATH-PREFIX OPERATIONS PublicEffect WireAnswer WireRequest WireRefusal WireMalformed STATUS-OF-ERROR
                            ERROR-MALFORMED ERROR-NOT-FOUND ERROR-STORE-UNAVAILABLE
                            decode-request answer-text refusal-json undeclared-reason keyed-request])

(setv METHOD-GET "GET" METHOD-POST "POST")
(setv PATH-HEALTHZ "/healthz")


(defclass [(dataclass :frozen True)] HttpRequest []
  "HTTP の口が受けた要求 1 つ: method・path(query を除く)・body = 本文の byte・
   writer = 呼び手が名乗る書き手の名 X-Records-Writer の見出し(無ければ None — 無ければ名無しの anonymous)/
   caller = 呼び手が名乗る job の名 X-Records-Caller の見出し(無ければ None — 一覧の読みを呼び手ごとに数える計器だけが読む)/
   idempotency-key = 行の書きの冪等キーの見出し X-Records-Idempotency-Key(無ければ None — 撃つ PutRow・PutRows の鍵にする・wire の
   IDEMPOTENCY-HEADER の註)。"
  (#^ str method)
  (#^ str path)
  (#^ bytes body)
  (setv #^ (| str None) writer None)
  (setv #^ (| str None) caller None)
  (setv #^ (| str None) idempotency-key None))


(defclass [(dataclass :frozen True)] HttpAnswer []
  "HTTP の口の答え 1 つ: status と JSON の本文(綴り)/ data = 本文の UTF-8 の byte 列(束の読みの答えは scheduler の thread の外で綴りと
   一緒に作り、送りは綴り直さない — sendable-reply-of。None = 送る時に body から作る)。"
  (#^ int status)
  (#^ str body)
  (setv #^ (| bytes None) data None))


(defclass [(dataclass :frozen True)] RecordsService []
  "HTTP の口 1 つの組: schema = 置き場の宣言(宣言に無い表を 404 で断る)/
   handler-for = 書き手の名 → その書き手の記録の handler(composition root が置き場ごとに組む)。"
  (#^ RecordsSchema schema)
  (#^ Callable handler-for))


(defk records-service [schema handler-for]
  {:pre [(: schema RecordsSchema) (: handler-for Callable)] :post [(: % RecordsService)]}
  "名簿を取らずに RecordsService を組む(引数を並べて組む・#3008)。"
  (RecordsService schema handler-for))


(defk json-answer [status body]
  {:pre [(: status int) (: body dict)] :post [(: % HttpAnswer)]}
  "答えの本文を JSON の綴りにする(HTTP の境界の 1 か所)。"
  (HttpAnswer status (json.dumps body :ensure-ascii False :separators #("," ":"))))


(defk refusal-answer [error reason]
  {:pre [(: error str) (: reason str)] :post [(: % HttpAnswer)]}
  "断りの語と理由を、その語の status の答えにする。"
  (<- body (refusal-json (WireRefusal error reason)))
  (<- answer (json-answer (get STATUS-OF-ERROR error) body))
  answer)


(defk undeclared-name [schema ask]
  {:pre [(: schema RecordsSchema) (: ask PublicEffect)] :post [(: % (| str None))]}
  "公開 effect(ask)が名指す表・追記の列のうち、宣言に無い物の説明(全部在れば None)— 宣言に無い表を撃つと handler が組み立ての誤りで落ちるので先に断る。
   理由の綴りは wire.hy の undeclared-reason の 1 か所(client が同じ綴りから UndeclaredTable の欄を戻す)。"
  (<- reason (| str None) (undeclared-reason ask (tuple schema.tables) (tuple schema.streams)))
  reason)


(defk serve-operation [service principal operation body idempotency-key]
  {:pre [(: service RecordsService) (: principal Principal) (: operation str) (: body bytes) (: idempotency-key (| str None))]
   :post [(: % HttpAnswer)]}
  "書き手の名を引いた要求 1 つを、その書き手の handler の下で公開 effect にして撃ち、答えを HTTP の答えにする(本文の上限は待ち受けの
   HttpReadBody が読む前に判じる — http_server.hy)。idempotency-key = 見出しの冪等キー(行の書きの鍵にする — wire.keyed-request)。"
  (try
    (setv document (json.loads (.decode body "utf-8")))
    (except [error #(UnicodeDecodeError json.JSONDecodeError)]
      (return (! (refusal-answer ERROR-MALFORMED (.format "本文が UTF-8 の JSON でない: {}" error))))))
  (try
    (<- read-request (decode-request (WireRequest operation document)))
    (<- decoded (keyed-request read-request idempotency-key))
    (except [error WireMalformed]
      (return (! (refusal-answer ERROR-MALFORMED (str error)))))
    (except [error ValueError]
      (return (! (refusal-answer ERROR-MALFORMED (.format "冪等キーの見出しが形の外: {}" error))))))
  (<- undeclared (undeclared-name service.schema decoded.effect))
  (when undeclared (return (! (refusal-answer ERROR-NOT-FOUND undeclared))))
  (<- answer (with_handlers [(service.handler-for principal.name)] decoded.effect))
  (when (isinstance answer Unreachable)
    (return (! (refusal-answer ERROR-STORE-UNAVAILABLE answer.detail))))
  (<- reply (answer-reply answer))
  reply)


(defk reply-of [answer]
  {:pre [(: answer WireAnswer)] :post [(: % HttpAnswer)]}
  "記録の handler の答え(Unreachable を除く)を 200 の答え(綴り — 行と変更の値は保存の綴りを継ぐ・wire.answer-text)にするため。"
  (<- body (answer-text answer))
  (HttpAnswer 200 body))


(defk sendable-reply-of [answer]
  {:pre [(: answer WireAnswer)] :post [(: % HttpAnswer)]}
  "束の読みの答えを、送る byte 列(data)まで作った 200 の答えにするため(純粋 — answer-reply が Compute の program にして scheduler の
   thread の外で回す。送りは綴り直さない)。"
  (<- reply (reply-of answer))
  (HttpAnswer reply.status reply.body (.encode reply.body "utf-8")))


(defk answer-reply [answer]
  {:pre [(: answer WireAnswer)] :post [(: % HttpAnswer)]}
  "答えを 200 の答えにするため(頭の註の答えの綴り): 行を運ぶ束の読みの答えは Compute で scheduler の thread の外で byte 列まで綴り、ほかは
   その場で綴る(byte 列は送る時に作る)。"
  (if (! (off-loop-answer? answer))
      (! (off-loop (sendable-reply-of answer)))
      (! (reply-of answer))))


(defk respond [service request]
  {:pre [(: service RecordsService) (: request HttpRequest)] :post [(: % HttpAnswer)]}
  "HTTP の口の入口: 要求 1 つ → 答え 1 つ(route・操作の順に確かめ、書き手の名を引いて撃つ — 呼び手は断らない)。"
  (when (and (= request.method METHOD-GET) (= request.path PATH-HEALTHZ))
    (return (! (json-answer 200 {"status" "ok"}))))
  (setv operation (if (.startswith request.path PATH-PREFIX) (cut request.path (len PATH-PREFIX) None) None))
  (when (not-in operation OPERATIONS)
    (return (! (refusal-answer ERROR-NOT-FOUND (.format "知らない route: {} {}" request.method request.path)))))
  (when (!= request.method METHOD-POST)
    (return (! (refusal-answer ERROR-MALFORMED (.format "{} は POST だけ: {}" request.path request.method)))))
  (<- caller Principal (writer-of request.writer))
  (<- reply (serve-operation service caller operation request.body request.idempotency-key))
  reply)
