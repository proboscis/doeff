;;; 記録の service の HTTP の口の流れ — HTTP の要求 1 つを、呼び手の身元の書き手の handler の下で公開 effect 1 つにして答える Program。
;;;
;;; 判断は持たない: 綴りの読み書きは wire.hy、身元は principals.hy、書きの許可・期待・保持は記録の handler(admission)。
;;; ここが持つのは順序だけ — 身元 → 本文の読み → 宣言に在る表か → effect を撃つ → 答えを綴る。
;;; 書き手の名は effect の引数にしない: 身元の名簿で引いた名で handler を組み(handler-for)、その下で effect を撃つ。
;;;
;;;   POST /v1/records/<操作>   200 = effect の答え(kind で判別)/ 400 malformed / 401 unauthorized / 404 not-found(宣言に無い表・
;;;                             知らない route)/ 503 store-unavailable(置き場に届かない = Unreachable)/ 500 internal
;;;   GET  /healthz             200(process が生きている)
;;; 綴りの正本 = wire.hy。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import json)
(import doeff [with_handlers])
(import doeff_records.values [RecordsSchema Unreachable])
(import doeff_records.principals [Roster Principal Unauthorized identify])
(import doeff_records.wire [PATH-PREFIX OPERATIONS PublicEffect WireRequest WireRefusal WireMalformed STATUS-OF-ERROR
                            ERROR-MALFORMED ERROR-UNAUTHORIZED ERROR-NOT-FOUND ERROR-STORE-UNAVAILABLE
                            decode-request encode-answer named-stores refusal-json])

(setv METHOD-GET "GET" METHOD-POST "POST")
(setv PATH-HEALTHZ "/healthz")
;; 要求の本文の上限(行の値の上限は表の宣言の size-budget が決める — これは口の読みの上限)。
(setv REQUEST-MAX-BYTES (* 16 1024 1024))


(defclass [(dataclass :frozen True)] HttpRequest []
  "HTTP の口が受けた要求 1 つ: method・path(query を除く)・authorization = Authorization の見出し(無ければ None)・body = 本文の byte。"
  (#^ str method)
  (#^ str path)
  (#^ (| str None) authorization)
  (#^ bytes body))


(defclass [(dataclass :frozen True)] HttpAnswer []
  "HTTP の口の答え 1 つ: status と JSON の本文(綴り)。"
  (#^ int status)
  (#^ str body))


(defclass [(dataclass :frozen True)] RecordsService []
  "HTTP の口 1 つの組: schema = 置き場の宣言(宣言に無い表を 404 で断る)/ roster = 身元の名簿 /
   handler-for = 書き手の名 → その書き手の記録の handler(composition root が置き場ごとに組む)。"
  (#^ RecordsSchema schema)
  (#^ Roster roster)
  (#^ Callable handler-for))


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
  "公開 effect(ask)が名指す表・追記の列のうち、宣言に無い物の説明(全部在れば None)— 宣言に無い表を撃つと handler が組み立ての誤りで落ちるので先に断る。"
  (<- named (named-stores ask))
  (setv tables (lfor name named.tables :if (not-in name schema.tables) name)
        streams (lfor name named.streams :if (not-in name schema.streams) name))
  (cond
    tables (.format "宣言に無い表: {}" tables)
    streams (.format "宣言に無い追記の列: {}" streams)
    True None))


(defk serve-operation [service principal operation body]
  {:pre [(: service RecordsService) (: principal Principal) (: operation str) (: body bytes)] :post [(: % HttpAnswer)]}
  "身元の引けた要求 1 つを、その書き手の handler の下で公開 effect にして撃ち、答えを HTTP の答えにする。"
  (when (> (len body) REQUEST-MAX-BYTES)
    (return (! (refusal-answer ERROR-MALFORMED (.format "本文が {} byte を超える" REQUEST-MAX-BYTES)))))
  (try
    (setv document (json.loads (.decode body "utf-8")))
    (except [error #(UnicodeDecodeError json.JSONDecodeError)]
      (return (! (refusal-answer ERROR-MALFORMED (.format "本文が UTF-8 の JSON でない: {}" error))))))
  (try
    (<- decoded (decode-request (WireRequest operation document)))
    (except [error WireMalformed]
      (return (! (refusal-answer ERROR-MALFORMED (str error))))))
  (<- undeclared (undeclared-name service.schema decoded.effect))
  (when undeclared (return (! (refusal-answer ERROR-NOT-FOUND undeclared))))
  (<- answer (with_handlers [(service.handler-for principal.name)] decoded.effect))
  (when (isinstance answer Unreachable)
    (return (! (refusal-answer ERROR-STORE-UNAVAILABLE answer.detail))))
  (<- encoded (encode-answer answer))
  (<- reply (json-answer 200 encoded))
  reply)


(defk respond [service request]
  {:pre [(: service RecordsService) (: request HttpRequest)] :post [(: % HttpAnswer)]}
  "HTTP の口の入口: 要求 1 つ → 答え 1 つ(route・身元・操作の順に確かめる)。"
  (when (and (= request.method METHOD-GET) (= request.path PATH-HEALTHZ))
    (return (! (json-answer 200 {"status" "ok"}))))
  (setv operation (if (.startswith request.path PATH-PREFIX) (cut request.path (len PATH-PREFIX) None) None))
  (when (not-in operation OPERATIONS)
    (return (! (refusal-answer ERROR-NOT-FOUND (.format "知らない route: {} {}" request.method request.path)))))
  (when (!= request.method METHOD-POST)
    (return (! (refusal-answer ERROR-MALFORMED (.format "{} は POST だけ: {}" request.path request.method)))))
  (<- caller (identify service.roster request.authorization))
  (match caller
    (Unauthorized :reason reason) (! (refusal-answer ERROR-UNAUTHORIZED reason))
    (Principal) (! (serve-operation service caller operation request.body))))
