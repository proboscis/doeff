;;; 記録の service の wire の綴り(I/O なし)— 公開 effect 6 つの要求と答えを JSON の値へ写し、JSON の値から読む。
;;;
;;; JSON(dict / list)と凍らせた値(FrozenMap・tuple・frozen の dataclass)の行き来はこの file の 1 か所だけ。
;;; HTTP の口(service.hy)と client の handler(http_client.hy)は両方ここを呼ぶ — 綴りを 2 か所に写さない。
;;; 契約: この file の綴りが正本。呼び手の系が契約の file(route・要求・答え・断り)と golden を持つ時は、その golden をこの口に通して照合する。
;;;
;;; 読みは境界の検め: 知らない鍵・足りない鍵・型の違う値は WireMalformed(黙って既定へ倒さない)。
;;; effect の答えの失敗(Conflict・Refused・NotIndexed・Reset・Missing)は答えの値で、`kind` の欄で判別する。
;;; 置き場に届かない(Unreachable)は答えの本文ではなく HTTP の 503 の断りで運ぶ(service.hy)。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny WatchCursor ListCursor Row Missing Page Written
                              RowChanged RowRemoved Changes Appended Event Events Conflict Refused NotIndexed Reset])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])

(setv PATH-PREFIX "/v1/records/")
(setv OP-READ-ROW "read-row" OP-LIST-ROWS "list-rows" OP-PUT-ROW "put-row" OP-WATCH-CHANGES "watch-changes"
      OP-APPEND-EVENT "append-event" OP-READ-EVENTS "read-events")
(setv OPERATIONS #(OP-READ-ROW OP-LIST-ROWS OP-PUT-ROW OP-WATCH-CHANGES OP-APPEND-EVENT OP-READ-EVENTS))
;; 書きの操作(身元の名簿に無い呼び手の書きを、client の handler が Refused の答えにする操作)。
(setv WRITE-OPERATIONS #(OP-PUT-ROW OP-APPEND-EVENT))

;; 断りの語(契約 $defs.refusal の error の語彙のうち、この口が使う物)と HTTP の status。
(setv ERROR-MALFORMED "malformed" ERROR-UNAUTHORIZED "unauthorized" ERROR-NOT-FOUND "not-found"
      ERROR-STORE-UNAVAILABLE "store-unavailable" ERROR-INTERNAL "internal")
(setv STATUS-OF-ERROR {ERROR-MALFORMED 400 ERROR-UNAUTHORIZED 401 ERROR-NOT-FOUND 404 ERROR-STORE-UNAVAILABLE 503
                       ERROR-INTERNAL 500})

;; 操作ごとに答えてよい kind(契約の records.routes の 200 の答えと同じ)。
(setv ANSWER-KINDS {OP-READ-ROW #("row" "missing")
                    OP-LIST-ROWS #("page" "reset" "notIndexed")
                    OP-PUT-ROW #("written" "conflict" "refused")
                    OP-WATCH-CHANGES #("changes" "reset")
                    OP-APPEND-EVENT #("appended" "refused")
                    OP-READ-EVENTS #("events")})

;; 境界の値の型(JSON の値・公開 effect・wire の本文で運ぶ答え)。
(setv JsonValue (| dict list str int float bool None))
(setv PublicEffect (| ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents))
(setv WireAnswer (| Row Missing Page Written Conflict Refused NotIndexed Reset Changes Appended Events))


(defclass WireMalformed [ValueError]
  "wire の JSON が契約の形でない(知らない鍵・足りない鍵・型の違う値・知らない操作や答えの kind)。")


(defclass [(dataclass :frozen True)] WireRequest []
  "要求 1 つの wire の形: operation = 操作の名(OPERATIONS)/ body = JSON の本文(JSON の境界へ渡す dict)。"
  (#^ str operation)
  (#^ dict body))


(defclass [(dataclass :frozen True)] WireRefusal []
  "HTTP の断り 1 つ: error = 断りの語(STATUS-OF-ERROR の鍵)/ reason = 人の読む理由。"
  (#^ str error)
  (#^ str reason)
  (defn #^ None __post_init__ [self]
    (when (not-in self.error STATUS-OF-ERROR)
      (raise (ValueError (.format "WireRefusal.error は {} のどれか: {!r}" (sorted STATUS-OF-ERROR) self.error))))))


(defclass [(dataclass :frozen True)] DecodedRequest []
  "読めた要求: effect = これから撃つ公開 effect(値として運ぶ — まだ撃っていない)。"
  (#^ PublicEffect effect)
  (defn #^ None __post_init__ [self]
    (when (not (isinstance self.effect PublicEffect))
      (raise (TypeError (.format "DecodedRequest.effect は公開 effect: {!r}" self.effect))))))


(defclass [(dataclass :frozen True)] NamedStores []
  "要求が名指す表と追記の列(置き場の宣言に在るかを service が effect を撃つ前に確かめるため)。"
  (#^ tuple tables)
  (#^ tuple streams))


;; --- 境界の読みの部品 ---------------------------------------------------------------------------------------

(defk object-of [value what required optional]
  {:pre [(: value JsonValue) (: what str) (: required tuple) (: optional tuple)] :post [(: % dict)]}
  "外から来た JSON の object が契約の鍵の組ちょうどであることを確かめる(足りない鍵・知らない鍵を黙って通さない)。"
  (when (not (isinstance value dict))
    (raise (WireMalformed (.format "{} は JSON の object: {!r}" what value))))
  (setv missing (sorted (gfor name required :if (not-in name value) name))
        unknown (sorted (gfor name value :if (not-in name (+ required optional)) name)))
  (when missing (raise (WireMalformed (.format "{} に鍵が足りない: {}" what missing))))
  (when unknown (raise (WireMalformed (.format "{} に知らない鍵: {}" what unknown))))
  value)


(defk string-of [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % str)]}
  "文字列であるべき JSON の値を検める。"
  (when (not (isinstance value str)) (raise (WireMalformed (.format "{} は文字列: {!r}" what value))))
  value)


(defk integer-of [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % int)]}
  "整数であるべき JSON の値を検める(真偽値を整数として通さない)。"
  (when (or (isinstance value bool) (not (isinstance value int)))
    (raise (WireMalformed (.format "{} は整数: {!r}" what value))))
  value)


(defk seconds-of [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % float)]}
  "秒の数であるべき JSON の値を検める(整数も秒として受ける)。"
  (when (or (isinstance value bool) (not (isinstance value #(int float))))
    (raise (WireMalformed (.format "{} は秒の数: {!r}" what value))))
  (float value))


(defk strings-of [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % tuple)]}
  "文字列の配列であるべき JSON の値(行の鍵・表の名の列・欄の名の列)を tuple にする。"
  (when (not (and (isinstance value list) (all (gfor part value (isinstance part str)))))
    (raise (WireMalformed (.format "{} は文字列の配列: {!r}" what value))))
  (tuple value))


(defk json-object-in [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % dict)]}
  "行の値・欄の差分・where のような任意の鍵の JSON の object を検める(中身の検めは値の型が作る時に行う)。"
  (when (not (isinstance value dict)) (raise (WireMalformed (.format "{} は JSON の object: {!r}" what value))))
  value)


(defk malformed [what error]
  {:pre [(: what str) (: error Exception)] :post [(: % WireMalformed)]}
  "値の型の組み立て(__post_init__ の検め)の失敗を、wire の形の誤りとして呼び手へ返すための例外にする。"
  (WireMalformed (.format "{}: {}" what error)))


;; --- 位置・期待・承認 ----------------------------------------------------------------------------------------

(defk watch-cursor-json [cursor]
  {:pre [(: cursor WatchCursor)] :post [(: % dict)]}
  "変更の列の位置を wire の object にする。"
  {"epoch" cursor.epoch "sequence" cursor.sequence})


(defk watch-cursor-from [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % WatchCursor)]}
  "wire の object から変更の列の位置を読む。"
  (<- body (object-of value what #("epoch" "sequence") #()))
  (WatchCursor (! (integer-of (get body "epoch") (+ what ".epoch"))) (! (integer-of (get body "sequence") (+ what ".sequence")))))


(defk list-cursor-json [cursor]
  {:pre [(: cursor (| ListCursor None))] :post [(: % (| dict None))]}
  "一覧の次の頁の位置を wire の値にする(None = 終わり / 最初から)。"
  (if (is cursor None) None {"epoch" cursor.epoch "afterKey" cursor.after-key}))


(defk list-cursor-from [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % (| ListCursor None))]}
  "wire の値から一覧の次の頁の位置を読む(null = 無し)。"
  (when (is value None) (return None))
  (<- body (object-of value what #("epoch" "afterKey") #()))
  (ListCursor (! (integer-of (get body "epoch") (+ what ".epoch"))) (! (string-of (get body "afterKey") (+ what ".afterKey")))))


(defk expect-json [expect]
  {:pre [(: expect (| ExpectAbsent ExpectVersion ExpectAny))] :post [(: % dict)]}
  "書きの期待を wire の object(kind = absent | version | any)にする。"
  (match expect
    (ExpectAbsent) {"kind" "absent"}
    (ExpectVersion :version version) {"kind" "version" "version" version}
    (ExpectAny) {"kind" "any"}))


(defk expect-from [value]
  {:pre [(: value JsonValue)] :post [(: % (| ExpectAbsent ExpectVersion ExpectAny))]}
  "wire の object から書きの期待を読む。"
  (match value
    {"kind" "absent"} (do (<- (object-of value "expect" #("kind") #())) (ExpectAbsent))
    {"kind" "version"} (do (<- (object-of value "expect" #("kind" "version") #()))
                           (<- version (integer-of (get value "version") "expect.version"))
                           (try (ExpectVersion version)
                                (except [error ValueError] (raise (! (malformed "expect" error))))))
    {"kind" "any"} (do (<- (object-of value "expect" #("kind") #())) (ExpectAny))
    _ (raise (WireMalformed (.format "expect は kind = absent | version | any の object: {!r}" value)))))


;; --- 要求 ------------------------------------------------------------------------------------------------

(defk encode-request [ask]
  {:pre [(: ask PublicEffect)] :post [(: % WireRequest)]}
  "client の handler が撃つ公開 effect(ask)を、HTTP で送る操作の名と本文にする。"
  (match ask
    (ReadRow :table table :key key)
      (WireRequest OP-READ-ROW {"table" table "key" (list key)})
    (ListRows :table table :where where :fields fields :cursor cursor :limit limit)
      (WireRequest OP-LIST-ROWS {"table" table "where" (thaw-json where) "fields" (if (is fields None) None (list fields))
                                 "cursor" (! (list-cursor-json cursor)) "limit" limit})
    (PutRow :table table :key key :value value :expect expect)
      (WireRequest OP-PUT-ROW {"table" table "key" (list key) "value" (thaw-json value) "expect" (! (expect-json expect))})
    (WatchChanges :tables tables :cursor cursor :timeout timeout :limit limit)
      (WireRequest OP-WATCH-CHANGES {"tables" (list tables) "cursor" (! (watch-cursor-json cursor)) "timeout" (float timeout)
                                     "limit" limit})
    (AppendEvent :stream stream :idempotency_key idempotency-key :body body)
      (WireRequest OP-APPEND-EVENT {"stream" stream "idempotencyKey" idempotency-key "body" (thaw-json body)})
    (ReadEvents :stream stream :after after :limit limit)
      (WireRequest OP-READ-EVENTS {"stream" stream "after" after "limit" limit})))


(defk decode-request [request]
  {:pre [(: request WireRequest)] :post [(: % DecodedRequest)]}
  "HTTP の口が受けた操作の名と本文を、記録の handler へ撃つ公開 effect にする(形が違えば WireMalformed)。"
  (setv body request.body)
  (try
    (setv made (match request.operation
      "read-row"
        (do (<- (object-of body "read-row の本文" #("table" "key") #()))
            (ReadRow (! (string-of (get body "table") "table")) (! (strings-of (get body "key") "key"))))
      "list-rows"
        (do (<- (object-of body "list-rows の本文" #("table") #("where" "fields" "cursor" "limit")))
            (setv keywords {})
            (when (in "where" body) (setv (get keywords "where") (! (json-object-in (get body "where") "where"))))
            (when (in "fields" body)
              (setv (get keywords "fields") (if (is (get body "fields") None) None (! (strings-of (get body "fields") "fields")))))
            (when (in "cursor" body) (setv (get keywords "cursor") (! (list-cursor-from (get body "cursor") "cursor"))))
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            (ListRows (! (string-of (get body "table") "table")) #** keywords))
      "put-row"
        (do (<- (object-of body "put-row の本文" #("table" "key" "value" "expect") #("approval")))
            ;; approval は契約で deprecated(承認トークンは廃止 — operator の宣言の欄は書き手の主体で判じる)。前の版の client が
            ;; 添える null だけを読み飛ばし、値のある印は効かない承認を黙って捨てないよう malformed で断る。次の契約の版で鍵ごと消す。
            (when (is-not (.get body "approval") None)
              (raise (WireMalformed "put-row の approval は廃止(operator の宣言の欄は書き手の主体で判じる — 印は効かない)")))
            (PutRow (! (string-of (get body "table") "table")) (! (strings-of (get body "key") "key"))
                    (! (json-object-in (get body "value") "value")) (! (expect-from (get body "expect")))))
      "watch-changes"
        (do (<- (object-of body "watch-changes の本文" #("tables" "cursor") #("timeout" "limit")))
            (setv keywords {})
            (when (in "timeout" body) (setv (get keywords "timeout") (! (seconds-of (get body "timeout") "timeout"))))
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            (WatchChanges (! (strings-of (get body "tables") "tables")) (! (watch-cursor-from (get body "cursor") "cursor"))
                          #** keywords))
      "append-event"
        (do (<- (object-of body "append-event の本文" #("stream" "idempotencyKey" "body") #()))
            (AppendEvent (! (string-of (get body "stream") "stream")) (! (string-of (get body "idempotencyKey") "idempotencyKey"))
                         (get body "body")))
      "read-events"
        (do (<- (object-of body "read-events の本文" #("stream") #("after" "limit")))
            (setv keywords {})
            (when (in "after" body) (setv (get keywords "after") (! (integer-of (get body "after") "after"))))
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            (ReadEvents (! (string-of (get body "stream") "stream")) #** keywords))
      _ (raise (WireMalformed (.format "知らない操作: {!r}(操作 = {})" request.operation OPERATIONS)))))
    (except [error WireMalformed] (raise error))
    (except [error #(TypeError ValueError)] (raise (! (malformed request.operation error)))))
  (DecodedRequest made))


(defk named-stores [ask]
  {:pre [(: ask PublicEffect)] :post [(: % NamedStores)]}
  "公開 effect(ask)が名指す表と追記の列を返す(宣言に無い名を 404 で断るため)。"
  (match ask
    (ReadRow :table table) (NamedStores #(table) #())
    (ListRows :table table) (NamedStores #(table) #())
    (PutRow :table table) (NamedStores #(table) #())
    (WatchChanges :tables tables) (NamedStores tables #())
    (AppendEvent :stream stream) (NamedStores #() #(stream))
    (ReadEvents :stream stream) (NamedStores #() #(stream))))


;; --- 答え ------------------------------------------------------------------------------------------------

(defk row-json [row]
  {:pre [(: row Row)] :post [(: % dict)]}
  "行 1 つを wire の object にする。"
  {"kind" "row" "key" (list row.key) "value" (thaw-json row.value) "version" row.version})


(defk change-json [change]
  {:pre [(: change (| RowChanged RowRemoved))] :post [(: % dict)]}
  "変更 1 つを wire の object(kind = rowChanged | rowRemoved)にする。"
  (match change
    (RowChanged :table table :key key :version version :value value :sequence sequence :at at)
      {"kind" "rowChanged" "table" table "key" (list key) "version" version "value" (thaw-json value) "sequence" sequence
       "at" at}
    (RowRemoved :table table :key key :sequence sequence)
      {"kind" "rowRemoved" "table" table "key" (list key) "sequence" sequence}))


(defk event-json [event]
  {:pre [(: event Event)] :post [(: % dict)]}
  "追記の列の出来事 1 つを wire の object にする。"
  {"stream" event.stream "sequence" event.sequence "idempotencyKey" event.idempotency-key "body" (thaw-json event.body)
   "writer" event.writer "at" event.at})


(defk encode-answer [answer]
  {:pre [(: answer WireAnswer)] :post [(: % dict)]}
  "HTTP の口が記録の handler から受けた答え(Unreachable を除く)を、200 の本文にする。"
  (match answer
    (Row) (! (row-json answer))
    (Missing) {"kind" "missing"}
    (Page :rows rows :next_cursor next-cursor :epoch epoch :sequence sequence)
      (do (setv encoded [])
          (for [row rows] (.append encoded (! (row-json row))))
          {"kind" "page" "rows" encoded "nextCursor" (! (list-cursor-json next-cursor)) "epoch" epoch "sequence" sequence})
    (Written :version version :value value) {"kind" "written" "version" version "value" (thaw-json value)}
    (Conflict :current current) {"kind" "conflict" "current" (! (encode-answer current))}
    (Refused :reason reason) {"kind" "refused" "reason" reason}
    (NotIndexed :fields fields) {"kind" "notIndexed" "fields" (list fields)}
    (Reset :epoch epoch) {"kind" "reset" "epoch" epoch}
    (Changes :items items :cursor cursor)
      (do (setv encoded [])
          (for [item items] (.append encoded (! (change-json item))))
          {"kind" "changes" "items" encoded "cursor" (! (watch-cursor-json cursor))})
    (Appended :sequence sequence) {"kind" "appended" "sequence" sequence}
    (Events :items items :last_sequence last-sequence)
      (do (setv encoded [])
          (for [event items] (.append encoded (! (event-json event))))
          {"kind" "events" "items" encoded "lastSequence" last-sequence})))


(defk row-from [value]
  {:pre [(: value JsonValue)] :post [(: % Row)]}
  "wire の object から行 1 つを読む。"
  (<- body (object-of value "row" #("kind" "key" "value" "version") #()))
  (<- key (strings-of (get body "key") "row.key"))
  (<- fields (json-object-in (get body "value") "row.value"))
  (<- version (integer-of (get body "version") "row.version"))
  (try (Row key fields version) (except [error #(TypeError ValueError)] (raise (! (malformed "row" error))))))


(defk change-from [value]
  {:pre [(: value JsonValue)] :post [(: % (| RowChanged RowRemoved))]}
  "wire の object から変更 1 つを読む。"
  (match value
    {"kind" "rowChanged"}
      (do (<- (object-of value "rowChanged" #("kind" "table" "key" "version" "value" "sequence" "at") #()))
          (RowChanged (! (string-of (get value "table") "table")) (! (strings-of (get value "key") "key"))
                      (! (integer-of (get value "version") "version")) (! (json-object-in (get value "value") "value"))
                      (! (integer-of (get value "sequence") "sequence")) (! (integer-of (get value "at") "at"))))
    {"kind" "rowRemoved"}
      (do (<- (object-of value "rowRemoved" #("kind" "table" "key" "sequence") #()))
          (RowRemoved (! (string-of (get value "table") "table")) (! (strings-of (get value "key") "key"))
                      (! (integer-of (get value "sequence") "sequence"))))
    _ (raise (WireMalformed (.format "変更は kind = rowChanged | rowRemoved: {!r}" value)))))


(defk event-from [value]
  {:pre [(: value JsonValue)] :post [(: % Event)]}
  "wire の object から追記の列の出来事 1 つを読む。"
  (<- body (object-of value "event" #("stream" "sequence" "idempotencyKey" "body" "writer" "at") #()))
  (Event (! (string-of (get body "stream") "event.stream")) (! (integer-of (get body "sequence") "event.sequence"))
         (! (string-of (get body "idempotencyKey") "event.idempotencyKey")) (get body "body")
         (! (string-of (get body "writer") "event.writer")) (! (integer-of (get body "at") "event.at"))))


(defk list-in [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % list)]}
  "配列であるべき JSON の値を検める(行・変更・出来事の列)。"
  (when (not (isinstance value list)) (raise (WireMalformed (.format "{} は配列: {!r}" what value))))
  value)


(defk answer-from [value]
  {:pre [(: value JsonValue)] :post [(: % WireAnswer)]}
  "200 の本文から答えの値を読む(kind で判別)。"
  (try
    (match value
      {"kind" "row"} (! (row-from value))
      {"kind" "missing"} (do (<- (object-of value "missing" #("kind") #())) (Missing))
      {"kind" "page"}
        (do (<- (object-of value "page" #("kind" "rows" "nextCursor" "epoch" "sequence") #()))
            (setv rows [])
            (for [item (! (list-in (get value "rows") "page.rows"))] (.append rows (! (row-from item))))
            (Page (tuple rows) (! (list-cursor-from (get value "nextCursor") "page.nextCursor"))
                  (! (integer-of (get value "epoch") "page.epoch")) (! (integer-of (get value "sequence") "page.sequence"))))
      {"kind" "written"}
        (do (<- (object-of value "written" #("kind" "version" "value") #()))
            (Written (! (integer-of (get value "version") "written.version")) (! (json-object-in (get value "value") "written.value"))))
      {"kind" "conflict"}
        (do (<- (object-of value "conflict" #("kind" "current") #()))
            (<- current (answer-from (get value "current")))
            (when (not (isinstance current #(Row Missing)))
              (raise (WireMalformed (.format "conflict.current は row | missing: {!r}" value))))
            (Conflict current))
      {"kind" "refused"}
        (do (<- (object-of value "refused" #("kind" "reason") #())) (Refused (! (string-of (get value "reason") "refused.reason"))))
      {"kind" "notIndexed"}
        (do (<- (object-of value "notIndexed" #("kind" "fields") #()))
            (NotIndexed (! (strings-of (get value "fields") "notIndexed.fields"))))
      {"kind" "reset"}
        (do (<- (object-of value "reset" #("kind" "epoch") #())) (Reset (! (integer-of (get value "epoch") "reset.epoch"))))
      {"kind" "changes"}
        (do (<- (object-of value "changes" #("kind" "items" "cursor") #()))
            (setv items [])
            (for [item (! (list-in (get value "items") "changes.items"))] (.append items (! (change-from item))))
            (Changes (tuple items) (! (watch-cursor-from (get value "cursor") "changes.cursor"))))
      {"kind" "appended"}
        (do (<- (object-of value "appended" #("kind" "sequence") #()))
            (Appended (! (integer-of (get value "sequence") "appended.sequence"))))
      {"kind" "events"}
        (do (<- (object-of value "events" #("kind" "items" "lastSequence") #()))
            (setv items [])
            (for [item (! (list-in (get value "items") "events.items"))] (.append items (! (event-from item))))
            (Events (tuple items) (! (integer-of (get value "lastSequence") "events.lastSequence"))))
      _ (raise (WireMalformed (.format "知らない答え: {!r}" value))))
    (except [error WireMalformed] (raise error))
    (except [error #(TypeError ValueError)] (raise (! (malformed "答え" error))))))


(defk decode-answer [operation value]
  {:pre [(: operation str) (: value JsonValue)] :post [(: % WireAnswer)]}
  "client の handler が受けた 200 の本文を、その操作が答えてよい kind の答えとして読む(外れれば WireMalformed)。"
  (when (not-in operation ANSWER-KINDS) (raise (WireMalformed (.format "知らない操作: {!r}" operation))))
  (when (not (and (isinstance value dict) (in (.get value "kind") (get ANSWER-KINDS operation))))
    (raise (WireMalformed (.format "{} の答えの kind は {} のどれか: {!r}" operation (get ANSWER-KINDS operation) value))))
  (<- answer (answer-from value))
  answer)


;; --- 断り ------------------------------------------------------------------------------------------------

(defk refusal-json [refusal]
  {:pre [(: refusal WireRefusal)] :post [(: % dict)]}
  "HTTP の断りを本文(契約 $defs.refusal)にする。"
  {"error" refusal.error "reason" refusal.reason})


(defk refusal-from [value]
  {:pre [(: value JsonValue)] :post [(: % WireRefusal)]}
  "HTTP の断りの本文を読む(client の handler が status と合わせて答えへ写すため)。"
  (<- body (object-of value "断り" #("error" "reason") #("principal")))
  (<- error (string-of (get body "error") "error"))
  (<- reason (string-of (get body "reason") "reason"))
  (try (WireRefusal error reason) (except [problem ValueError] (raise (! (malformed "断り" problem))))))
