;;; 記録の service の wire の綴り(I/O なし)— 公開 effect 8 つの要求と答えを JSON の値へ写し、JSON の値から読む。
;;;
;;; JSON(dict / list)と凍らせた値(FrozenMap・tuple・frozen の dataclass)の行き来はこの file の 1 か所だけ。
;;; HTTP の口(service.hy)と client の handler(http_client.hy)は両方ここを呼ぶ — 綴りを 2 か所に写さない。
;;; 契約: この file の綴りが正本。呼び手の系が契約の file(route・要求・答え・断り)と golden を持つ時は、その golden をこの口に通して照合する。
;;;
;;; 読みは境界の検め: 知らない鍵・足りない鍵・型の違う値は WireMalformed(黙って既定へ倒さない)。
;;; effect の答えの失敗(Conflict・Refused・NotIndexed・Reset・Missing・RowsConflict・RowsRefused)は答えの値で、`kind` の欄で判別する。
;;; 置き場に届かない(Unreachable)は答えの本文ではなく HTTP の 503 の断りで運ぶ(service.hy)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "protocol"})
(require doeff-hy.record [defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_hy.frozen [FrozenMap thaw-json])
;; JSON の値の型の定義は doeff_hy.json_value の 1 か所だけ — ここは import して、この module の読み手へも同じ名で見せる。
(import doeff_hy.json_value [JsonValue])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny WatchCursor ListCursor Row Missing Page Written WrittenRows
                              RowChanged RowRemoved Changes Appended Event Events Conflict Refused NotIndexed Reset
                              RowsConflict RowsRefused StreamEnd StreamEmpty UndeclaredTable])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges AppendEvent ReadEvents ReadStreamEnd])

(setv PATH-PREFIX "/v1/records/")
(setv OP-READ-ROW "read-row" OP-LIST-ROWS "list-rows" OP-PUT-ROW "put-row" OP-WATCH-CHANGES "watch-changes"
      OP-APPEND-EVENT "append-event" OP-READ-EVENTS "read-events")
;; 複数行を全部か 0 で書く操作(本文 = {writes: [{table key value expect} …]})— 前の 6 つの綴りは変えずに足した。
(val OP-PUT-ROWS "put-rows")
;; 追記の列の末尾の番号を 1 回で読む操作(本文 = {stream})。前の 7 つの綴りは変えずに足した。
(val OP-READ-STREAM-END "read-stream-end")
(setv OPERATIONS #(OP-READ-ROW OP-LIST-ROWS OP-PUT-ROW OP-WATCH-CHANGES OP-APPEND-EVENT OP-READ-EVENTS OP-PUT-ROWS OP-READ-STREAM-END))
;; 書きの操作(行か出来事を書く — 計器の要求の種 write)。OPERATIONS の残りは読み(read)。
(val WRITE-OPERATIONS (frozenset #(OP-PUT-ROW OP-PUT-ROWS OP-APPEND-EVENT)))

;; 要求の種(計器 /metrics の counter の名に畳む種 — #2709): WRITE = 書きの操作の route・READ = 残りの記録の操作の route・
;; OTHER = 記録の操作でない route(/healthz・/readyz・/metrics・知らない route)。
(defenum RequestKind WRITE READ OTHER)

;; 断りの語(契約 $defs.refusal の error の語彙のうち、この口が使う物)と HTTP の status。
(setv ERROR-MALFORMED "malformed" ERROR-UNAUTHORIZED "unauthorized" ERROR-NOT-FOUND "not-found"
      ERROR-STORE-UNAVAILABLE "store-unavailable" ERROR-INTERNAL "internal")
(setv STATUS-OF-ERROR {ERROR-MALFORMED 400 ERROR-UNAUTHORIZED 401 ERROR-NOT-FOUND 404 ERROR-STORE-UNAVAILABLE 503
                       ERROR-INTERNAL 500})

;; 計器(GET /metrics)の counter の名の綴り(#2709): 要求の種 × 答えの status ごとに records_requests_<種>_<status>(描く名は末尾に
;; _total)。label は使わず、種と status を名に畳む。系列は種 3 × status(200 と上の断りの status)で閉じていて、口は起動の時に全部を
;; 0 で置く(読み手が「無い」と「0」を区別しなくて済み、区間の差が最初の 1 つ目の増えを取りこぼさない)。置き場に届かなかった数 =
;; 503 の系列(store-unavailable — 表の用意の前と /readyz の不達を含む)・答えの途中で落ちた数 = 500 の系列(internal)— 断りの status は
;; 語ごとに 1 つずつなので、別の counter を持たずに status の系列で読む。種 other には /healthz・/readyz・/metrics 自身の読み(kubelet の
;; 見張りと計器の取り込み)が入る。
(val ANSWER-METRIC "records_requests_{}_{}")
(val ANSWER-STATUSES (tuple (sorted (| (frozenset #(200)) (frozenset (.values STATUS-OF-ERROR))))))
(val ANSWER-METRICS (tuple (gfor kind RequestKind status ANSWER-STATUSES (.format ANSWER-METRIC kind status))))
;; 各系列の # HELP の説明(名 → 説明)。
(val ANSWER-METRIC-HELPS
  (FrozenMap (gfor kind RequestKind status ANSWER-STATUSES
                   #((.format ANSWER-METRIC kind status)
                     (.format "記録の service が {}({})の要求に status {}{} で答えた数(起動からの累計){}"
                              kind
                              (cond (= kind RequestKind.WRITE) "書きの操作の route"
                                    (= kind RequestKind.READ) "読みの操作の route"
                                    True "記録の操作でない route — /healthz・/readyz・/metrics 自身の読みを含む")
                              status
                              (.join "" (gfor #(word code) (.items STATUS-OF-ERROR) :if (= code status) (+ " " word)))
                              (cond (= status (get STATUS-OF-ERROR ERROR-STORE-UNAVAILABLE))
                                      "・置き場に届かなかった(表の用意の前と /readyz の不達を含む)"
                                    (= status (get STATUS-OF-ERROR ERROR-INTERNAL)) "・答えの途中で落ちた"
                                    True ""))))))

;; 記録の client の計器の counter の名の綴り(#2740): client が送った要求 1 つごとに、要求の種 × 結果で records_client_requests_<種>_<結果>
;; (描く名は末尾に _total — 上の service の系列と並べて読む)。種は write と read(client は記録の操作だけを送る — other は無い)。
;; 結果 = service が答えた status(ANSWER-STATUSES — 503 は service に届いたが置き場に届かなかった)・unreachable(要求が service に
;; 届かなかった — 接続できない・時間切れ。service の計器には出ない数)・other(それ以外の status — 間の proxy の 502 / 504・前に立つ口の
;; 403 など)。頁送りの読み直しの合図(Reset)は 200 の本文の答えなので 200 に入る(Conflict・Refused と同じ)。系列は閉じていて、使い手は
;; 起動の時に全部を 0 で置ける(http_client.zero-client-metrics)。
(val CLIENT-ANSWER-METRIC "records_client_requests_{}_{}")
(val CLIENT-UNREACHABLE "unreachable")
(val CLIENT-OTHER-STATUS "other")
(val CLIENT-KINDS #(RequestKind.WRITE RequestKind.READ))
(val CLIENT-OUTCOMES (+ (tuple (gfor status ANSWER-STATUSES (str status))) #(CLIENT-UNREACHABLE CLIENT-OTHER-STATUS)))
(val CLIENT-ANSWER-METRICS (tuple (gfor kind CLIENT-KINDS outcome CLIENT-OUTCOMES (.format CLIENT-ANSWER-METRIC kind outcome))))

;; 操作ごとに答えてよい kind(契約の records.routes の 200 の答えと同じ)。
(setv ANSWER-KINDS {OP-READ-ROW #("row" "missing")
                    OP-LIST-ROWS #("page" "reset" "notIndexed")
                    OP-PUT-ROW #("written" "conflict" "refused")
                    OP-WATCH-CHANGES #("changes" "reset")
                    OP-APPEND-EVENT #("appended" "refused")
                    OP-READ-EVENTS #("events")
                    OP-PUT-ROWS #("writtenRows" "rowsConflict" "rowsRefused")
                    OP-READ-STREAM-END #("streamEnd" "streamEmpty")})

;; 境界の値の型(公開 effect・wire の本文で運ぶ答え)。JSON の値の型 JsonValue は上の import(doeff_hy.json_value)。
(setv PublicEffect (| ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents PutRows ReadStreamEnd))
(setv WireAnswer (| Row Missing Page Written Conflict Refused NotIndexed Reset Changes Appended Events
                    WrittenRows RowsConflict RowsRefused StreamEnd StreamEmpty))


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


;; --- 要求の種と計器の counter の名 ---------------------------------------------------------------------------

(defk request-kind [path]
  {:pre [(: path str)] :post [(: % RequestKind)]}
  "要求の path(query を除く)を要求の種にする(計器が書きと読みを分けて数えるため)。method は問わない — POST でない記録の操作の
   400 も、その操作の種で数える。"
  (val operation (if (.startswith path PATH-PREFIX) (cut path (len PATH-PREFIX) None) None))
  (match operation
    name :if (in name WRITE-OPERATIONS) RequestKind.WRITE
    name :if (in name OPERATIONS) RequestKind.READ
    _ RequestKind.OTHER))


(defk answer-metric [path status]
  {:pre [(: path str) (: status int)] :post [(: % str)]}
  "path の要求に status で答えた数の counter の名を作るため(ANSWER-METRIC の綴り — 種は request-kind)。"
  (<- kind RequestKind (request-kind path))
  (.format ANSWER-METRIC kind status))


(defk client-answer-metric [operation outcome]
  {:pre [(: operation str) (: outcome str)] :post [(: % str)]}
  "client が送った操作 operation の結果 outcome(CLIENT-OUTCOMES の 1 つ)を数える counter の名を作るため(CLIENT-ANSWER-METRIC の綴り)。"
  (when (not-in outcome CLIENT-OUTCOMES)
    (raise (ValueError (.format "client の結果は {} のどれか: {!r}" CLIENT-OUTCOMES outcome))))
  (.format CLIENT-ANSWER-METRIC (if (in operation WRITE-OPERATIONS) RequestKind.WRITE RequestKind.READ) outcome))


(defk client-status-outcome [status]
  {:pre [(: status int)] :post [(: % str)]}
  "service の答えの status を client の結果の語にするため(閉じた系列の外の status は other に畳む — 間の proxy の 502 / 504 など)。"
  (if (in status ANSWER-STATUSES) (str status) CLIENT-OTHER-STATUS))


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

(defk row-write-json [write]
  {:pre [(: write RowWrite)] :post [(: % dict)]}
  "PutRows の束の書き 1 つを wire の object(put-row の本文と同じ鍵 table・key・value・expect)にする。"
  (<- expect (expect-json write.expect))
  {"table" write.table "key" (list write.key) "value" (thaw-json write.value) "expect" expect})


(defk row-write-from [value]
  {:pre [(: value JsonValue)] :post [(: % RowWrite)]}
  "wire の object から PutRows の束の書き 1 つを読む(鍵の組は put-row の本文と同じ・approval は受けない)。"
  (<- body (object-of value "put-rows の書き" #("table" "key" "value" "expect") #()))
  (<- table (string-of (get body "table") "writes[].table"))
  (<- key (strings-of (get body "key") "writes[].key"))
  (<- fields (json-object-in (get body "value") "writes[].value"))
  (<- expect (expect-from (get body "expect")))
  (RowWrite table key fields expect))


(defk row-writes-from [body]
  {:pre [(: body JsonValue)] :post [(: % tuple)]}
  "put-rows の本文 {writes: [...]} から束の書き(RowWrite の tuple)を読む(同じ行が 2 度出る束・空の束は PutRows が作る時に断る)。"
  (<- (object-of body "put-rows の本文" #("writes") #()))
  (<- items (list-in (get body "writes") "writes"))
  (val writes [])
  (for [item items] (.append writes (! (row-write-from item))))
  (tuple writes))


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
      (WireRequest OP-READ-EVENTS {"stream" stream "after" after "limit" limit})
    (ReadStreamEnd :stream stream)
      (WireRequest OP-READ-STREAM-END {"stream" stream})
    (PutRows :writes writes)
      (do (val write-items [])
          (for [write writes] (.append write-items (! (row-write-json write))))
          (WireRequest OP-PUT-ROWS {"writes" write-items}))))


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
      "put-rows"
        (PutRows (! (row-writes-from body)))
      "read-stream-end"
        (do (<- (object-of body "read-stream-end の本文" #("stream") #()))
            (ReadStreamEnd (! (string-of (get body "stream") "stream"))))
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
    (ReadEvents :stream stream) (NamedStores #() #(stream))
    (ReadStreamEnd :stream stream) (NamedStores #() #(stream))
    (PutRows :writes writes) (NamedStores (tuple (sorted (sfor write writes write.table))) #())))


;; --- 宣言に無い名の断り(404 not-found)の理由の綴り---------------------------------------------------------
;; service が理由の文を組み、client がその文から UndeclaredTable の欄(宣言に無いと分かった名)を戻す — 綴りはこの 2 つの 1 か所。
;; 断りの本文(契約 $defs.refusal = error・reason)には欄を足さない: 旧い client は本文の知らない鍵を WireMalformed にするので、
;; 本文を変えると版のずれの時(client と service の版が違う時 — 欄が要るのはまさにこの時)に読めなくなる。理由の文は名を
;; Python の repr(引用符つき)で並べる。表・追記の列の名は英小文字・数字・_・- に限られ引用符を含まないので、client は要求が
;; 名指した名のうち「'名'」が理由に載った物だけを欄にする — この綴りは欄を足す前の service(「宣言に無い表: ['x']」)とも同じ。
;; 知らない route の断り(「知らない route: …」)は名を載せないので欄は空。

(defk undeclared-reason [ask tables streams]
  {:pre [(: ask PublicEffect) (: tables tuple) (: streams tuple)] :post [(: % (| str None))]}
  "要求 ask が名指した名のうち宣言に在る表 tables・追記の列 streams に無い物を、404 の断りの理由の文にする(全部在れば None —
   service が effect を撃つ前に断るため)。"
  (<- named NamedStores (named-stores ask))
  (setv missing-tables (lfor name named.tables :if (not-in name tables) name)
        missing-streams (lfor name named.streams :if (not-in name streams) name))
  (cond
    missing-tables (.format "宣言に無い表: {}" missing-tables)
    missing-streams (.format "宣言に無い追記の列: {}" missing-streams)
    True None))


(defk undeclared-refusal [ask reason]
  {:pre [(: ask PublicEffect) (: reason str)] :post [(: % UndeclaredTable)]}
  "service の 404 の断り(理由 reason)を、要求 ask が名指した名のうち理由に載った物を欄に持つ UndeclaredTable にするため
   (上の註 — 使い手が欄でどの表の断りかを照らせるように)。"
  (<- named NamedStores (named-stores ask))
  (UndeclaredTable reason
                   :tables (tuple (gfor name named.tables :if (in (repr name) reason) name))
                   :streams (tuple (gfor name named.streams :if (in (repr name) reason) name))))


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
    (Reset :epoch epoch :floor floor) {"kind" "reset" "epoch" epoch "floor" floor}
    (Changes :items items :cursor cursor)
      (do (setv encoded [])
          (for [item items] (.append encoded (! (change-json item))))
          {"kind" "changes" "items" encoded "cursor" (! (watch-cursor-json cursor))})
    (Appended :sequence sequence) {"kind" "appended" "sequence" sequence}
    (Events :items items :last_sequence last-sequence)
      (do (setv encoded [])
          (for [event items] (.append encoded (! (event-json event))))
          {"kind" "events" "items" encoded "lastSequence" last-sequence})
    (WrittenRows :items items)
      (do (val written-items [])
          (for [written items] (.append written-items (! (encode-answer written))))
          {"kind" "writtenRows" "items" written-items})
    (RowsConflict :index index :table table :key key :current current)
      {"kind" "rowsConflict" "index" index "table" table "key" (list key) "current" (! (encode-answer current))}
    (RowsRefused :index index :table table :key key :reason reason)
      {"kind" "rowsRefused" "index" index "table" table "key" (list key) "reason" reason}
    (StreamEnd :sequence sequence) {"kind" "streamEnd" "sequence" sequence}
    (StreamEmpty) {"kind" "streamEmpty"}))


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


(defk written-rows-from [value]
  {:pre [(: value JsonValue)] :post [(: % WrittenRows)]}
  "wire の object から PutRows の確定(束の順の written の列)を読む。"
  (<- (object-of value "writtenRows" #("kind" "items") #()))
  (val items [])
  (for [item (! (list-in (get value "items") "writtenRows.items"))] (.append items (! (answer-from item))))
  (when (not (all (gfor item items (isinstance item Written))))
    (raise (WireMalformed (.format "writtenRows.items は written の列: {!r}" value))))
  (WrittenRows (tuple items)))


(defk rows-conflict-from [value]
  {:pre [(: value JsonValue)] :post [(: % RowsConflict)]}
  "wire の object から PutRows の衝突(束の中の位置・行・今の値)を読む。"
  (<- (object-of value "rowsConflict" #("kind" "index" "table" "key" "current") #()))
  (<- current (answer-from (get value "current")))
  (when (not (isinstance current #(Row Missing)))
    (raise (WireMalformed (.format "rowsConflict.current は row | missing: {!r}" value))))
  (RowsConflict (! (integer-of (get value "index") "rowsConflict.index")) (! (string-of (get value "table") "rowsConflict.table"))
                (! (strings-of (get value "key") "rowsConflict.key")) current))


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
        (do (<- (object-of value "reset" #("kind" "epoch" "floor") #()))
            (Reset (! (integer-of (get value "epoch") "reset.epoch")) (! (integer-of (get value "floor") "reset.floor"))))
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
      {"kind" "streamEnd"}
        (do (<- (object-of value "streamEnd" #("kind" "sequence") #()))
            (StreamEnd (! (integer-of (get value "sequence") "streamEnd.sequence"))))
      {"kind" "streamEmpty"} (do (<- (object-of value "streamEmpty" #("kind") #())) (StreamEmpty))
      {"kind" "writtenRows"} (! (written-rows-from value))
      {"kind" "rowsConflict"} (! (rows-conflict-from value))
      {"kind" "rowsRefused"}
        (do (<- (object-of value "rowsRefused" #("kind" "index" "table" "key" "reason") #()))
            (RowsRefused (! (integer-of (get value "index") "rowsRefused.index")) (! (string-of (get value "table") "rowsRefused.table"))
                         (! (strings-of (get value "key") "rowsRefused.key")) (! (string-of (get value "reason") "rowsRefused.reason"))))
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
