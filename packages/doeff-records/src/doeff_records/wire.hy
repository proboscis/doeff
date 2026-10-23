;;; 記録の service の wire の綴り(I/O なし)— 公開 effect 10 個と列の待ち WatchEvents(#3074)の要求と答えを JSON の値へ写し、JSON の
;;; 値から読む。
;;;
;;; JSON(dict / list)と凍らせた値(FrozenMap・tuple・frozen の dataclass)の行き来はこの file の 1 か所だけ。
;;; HTTP の口(service.hy)と client の handler(http_client.hy)は両方ここを呼ぶ — 綴りを 2 か所に写さない。
;;; 契約: この file の綴りが正本。呼び手の系が契約の file(route・要求・答え・断り)と golden を持つ時は、その golden をこの口に通して照合する。
;;;
;;; 読みは境界の検め: 足りない鍵・型の違う値は WireMalformed(黙って既定へ倒さない)。知らない鍵の扱いは向きで分ける:
;;;   要求の読み(service が読む)= 知らない鍵を断る — 要求の鍵は読みの意味を変える(絞り・名指す列)ので、読み捨てると違う答えを返す。
;;;     鍵を足す要求は、使う client より先に service を新しい版で起こす(下の encode-request の註)。
;;;   答えの読み(client が読む)= 知らない鍵を読み捨てる(answer-object-of)— 書き手(service)は答えに鍵を足すだけにし、1 版前の client も
;;;     新しい版の service の答えを読める(service を先に起こす順と両立する)。鍵を消す・綴りを変える・新しい鍵を必須にする変更はしない
;;;     (検 tests/test_wire_versions.hy が凍らせた答えの見本で断る)。知らない答えの kind は読み捨てられないので断る。
;;; effect の答えの失敗(Conflict・Refused・NotIndexed・Reset・Missing・RowsConflict・RowsRefused)は答えの値で、`kind` の欄で判別する。
;;; 置き場に届かない(Unreachable)は答えの本文ではなく HTTP の 503 の断りで運ぶ(service.hy)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "protocol"})
(require doeff-hy.record [defenum defrecord])
(import re)
(import collections [Counter])
(import dataclasses [dataclass replace])
(import enum [StrEnum])
(import doeff_hy.frozen [FrozenMap thaw-json])
;; JSON の値の型の定義は doeff_hy.json_value の 1 か所だけ — ここは import して、この module の読み手へも同じ名で見せる。
(import doeff_hy.json_value [JsonValue])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny WatchCursor ListCursor Row Missing Page Written WrittenRows
                              RowChanged RowRemoved Changes Appended AppendedEvents Event Events Conflict Refused NotIndexed Reset
                              RowsConflict RowsRefused StreamEnd StreamEmpty StreamTail StreamTailEmpty UndeclaredTable EventsMoved
                              EventsQuiet EventAbsent EventRetired])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent AppendEvents EventAppend
                               ReadEvents ReadStreamEnd ReadEventByKey])

(setv PATH-PREFIX "/v1/records/")
;; 呼び手が名乗る書き手の名の見出し(口は確かめない — client は名乗り、口は名乗りをそのまま書き手の名に使う・#2988)。
(setv WRITER-HEADER "X-Records-Writer")
(setv OP-READ-ROW "read-row" OP-LIST-ROWS "list-rows" OP-PUT-ROW "put-row" OP-WATCH-CHANGES "watch-changes"
      OP-APPEND-EVENT "append-event" OP-READ-EVENTS "read-events")
;; 複数行を全部か 0 で書く操作(本文 = {writes: [{table key value expect} …]})— 前の 6 つの綴りは変えずに足した。
(val OP-PUT-ROWS "put-rows")
;; 追記の列の末尾の番号を 1 回で読む操作(本文 = {stream})。前の 7 つの綴りは変えずに足した。
(val OP-READ-STREAM-END "read-stream-end")
;; 追記の列の頭が after より進むのを待つ操作(本文 = {stream after timeout} — 答え = eventsMoved | eventsQuiet)。前の 8 つの綴りは
;; 変えずに足した(#3074 — client が列の待ちを ReadEvents の読み直しでなく、service の中の置き場の待ちの long-poll で待つため)。
(val OP-WATCH-EVENTS "watch-events")
;; 追記の列の出来事を冪等キーで 1 つ引く操作(本文 = {stream idempotencyKey} — 答え = event | eventAbsent | eventRetired)。前の 9 つの
;; 綴りは変えずに足した(#3750 — 鍵 1 つの問いのために列を頭から読み切らないため)。
(val OP-READ-EVENT-BY-KEY "read-event-by-key")
;; 追記の束を 1 往復で積む操作(本文 = {appends: [{stream idempotencyKey body} …]} — 答え = appendedEvents{items: 追記ごとの appended |
;; refused})。前の 10 個の綴りは変えずに足した(card ki-48c34994ed15 — 1 つの要求で原因と本体の 2 つの出来事を積む書き手が、書きの錠と
;; 往復を 2 回順に待たないため。使う client より先に service を新しい版で起こす — 古い service は知らない操作を 404 で断る)。
(val OP-APPEND-EVENTS "append-events")
(setv OPERATIONS #(OP-READ-ROW OP-LIST-ROWS OP-PUT-ROW OP-WATCH-CHANGES OP-APPEND-EVENT OP-READ-EVENTS OP-PUT-ROWS OP-READ-STREAM-END
                   OP-WATCH-EVENTS OP-READ-EVENT-BY-KEY OP-APPEND-EVENTS))
;; 変化の待ち(watch-changes・watch-events)1 回の要求で service が待つ秒の上限(#3074 の long-poll)。要求の読み(decode-request)が
;; timeout をこの秒で切り、service は置き場の待ちへそのまま渡す — 変化が無ければこの秒で空の答え(changes の空・eventsQuiet)を返す。
;; client は待ちをこの秒ごとの要求に分けて撃ち直す(眠らない)。間に立つ口の要求の時間切れより短く置く。
(val WATCH-MAX-SECONDS 25.0)
;; 書きの操作(行か出来事を書く — 計器の要求の種 write)。OPERATIONS の残りは読み(read)。
(val WRITE-OPERATIONS (frozenset #(OP-PUT-ROW OP-PUT-ROWS OP-APPEND-EVENT OP-APPEND-EVENTS)))

;; 要求の種(計器 /metrics の counter の名に畳む種 — #2709): WRITE = 書きの操作の route・READ = 残りの記録の操作の route・
;; OTHER = 記録の操作でない route(/healthz・/readyz・/metrics・知らない route)。
(defenum RequestKind WRITE READ OTHER)

;; 断りの語(契約 $defs.refusal の error の語彙のうち、この口が使う物)と HTTP の status。口は呼び手を断らないので、身元の断りの語と
;; 401 は持たない(#2988・#3007・#2986)。
(val ERROR-MALFORMED "malformed")
(val ERROR-NOT-FOUND "not-found")
(val ERROR-STORE-UNAVAILABLE "store-unavailable")
(val ERROR-INTERNAL "internal")
(val STATUS-OF-ERROR {ERROR-MALFORMED 400 ERROR-NOT-FOUND 404 ERROR-STORE-UNAVAILABLE 503 ERROR-INTERNAL 500})

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
;; 届かなかった — 接続できない・時間切れ。service の計器には出ない数)・other(それ以外の status — 間の proxy の 502 / 504・
;; 401 / 403 など)。頁送りの読み直しの合図(Reset)は 200 の本文の答えなので 200 に入る(Conflict・Refused と同じ)。系列は閉じていて、使い手は
;; 起動の時に全部を 0 で置ける(http_client.zero-client-metrics)。
(val CLIENT-ANSWER-METRIC "records_client_requests_{}_{}")
(val CLIENT-UNREACHABLE "unreachable")
(val CLIENT-OTHER-STATUS "other")
(val CLIENT-KINDS #(RequestKind.WRITE RequestKind.READ))
(val CLIENT-OUTCOMES (+ (tuple (gfor status ANSWER-STATUSES (str status))) #(CLIENT-UNREACHABLE CLIENT-OTHER-STATUS)))
(val CLIENT-ANSWER-METRICS (tuple (gfor kind CLIENT-KINDS outcome CLIENT-OUTCOMES (.format CLIENT-ANSWER-METRIC kind outcome))))

;; 呼び手が名乗る job の名の見出し(一覧の読みを呼び手ごとに数えるため — 書き手の名 X-Records-Writer とは別の名乗りで、許可には使わない)。
(val CALLER-HEADER "X-Records-Caller")
;; 行の書き(put-row・put-rows)の冪等キーの見出し(card ki-78c6d663491f): client は書きの要求ごとに鍵を 1 つ載せ、届かない書きの撃ち直しに
;; 同じ鍵を載せる。service は見出しの鍵を撃つ PutRow・PutRows の idempotency-key にする(keyed-request)。本文の鍵にしない訳: 要求の本文の
;; 知らない鍵は古い service が 400 で断る(頭の註)ので、鍵を本文に足すと service を先に新しい版で起こす順を求める — 見出しは古い service が
;; 読み捨てるので、client と service をどの順で入れ替えても書きは通る(古い service の間は今までどおり鍵の無い書き)。他の操作の見出しは読まない。
(val IDEMPOTENCY-HEADER "X-Records-Idempotency-Key")

;; 答えの区間の秒の見出し(ki-ca4b41612e2e): service は記録の操作の答えに、答えが決まった所までの区間の秒を W3C の Server-Timing の形
;; (<区間>;dur=<ミリ秒>, …)で載せ(request_timing.hy の server-timing-of)、client は往復 1 本のうち service の受けの列(queue)と service の
;; 中(残りの区間)の秒を要求ごとに名指す(http_client.hy の往復の計時の行)。区間の名の並びは request_timing.hy の区間の並びと同じで、
;; 答えを送った後に決まる区間(send・total・woke)は載せない。
(val SERVER-TIMING-HEADER "Server-Timing")
(val SERVER-TIMING-QUEUE "queue")
(val SERVER-TIMING-NAMES #(SERVER-TIMING-QUEUE "body" "decode" "handler" "wait" "encode"))


(defrecord ServerSpan
  "答えの見出し Server-Timing の区間 1 つ: name = 区間の名(SERVER-TIMING-NAMES のどれか)/ seconds = 秒。"
  {:tags {:context "records" :role "type"}}
  (#^ str name)
  (#^ float seconds))


(defk server-timing-spelled [spans]
  {:pre [(: spans (get tuple #(ServerSpan ...)))] :post [(: % str)] :tags {:context "records" :role "judgment"}}
  "答えの見出し Server-Timing の値を綴るため: 区間を <名>;dur=<ミリ秒(小数 3 桁)> の , 区切りにする(載せない名は呼び手が選ぶ)。"
  (.join ", " (gfor span spans (.format "{};dur={:.3f}" span.name (* span.seconds 1000)))))


(defk server-timing-spans [value]
  {:pre [(: value (| str None))] :post [(: % (get tuple #(ServerSpan ...)))] :tags {:context "records" :role "judgment"}}
  "答えの見出し Server-Timing の値を区間の列に読むため。見出しが無い(古い版の service)・SERVER-TIMING-NAMES に無い名・dur の無い項・数で
   ない dur は読み飛ばす(呼び手は載った区間だけを名指す — 見出しの読み違いで答えを落とさない)。"
  (when (is value None)
    (return #()))
  (var read #())
  (for [item (.split value ",")]
    (val parts (tuple (gfor part (.split item ";") (.strip part))))
    (val durations (tuple (gfor part (cut parts 1 None) :if (.startswith part "dur=") (cut part 4 None))))
    (when (and (in (get parts 0) SERVER-TIMING-NAMES) durations)
      (try
        (:= read (+ read #((ServerSpan :name (get parts 0) :seconds (/ (float (get durations 0)) 1000)))))
        (except [ValueError]
          None))))
  read)
;; 一覧の読み(list-rows)の計器の counter の名の綴り: 一覧の答え 1 つごとに、表ごとの呼びの数(calls)と返した行の数(rows)を数える
;; (描く名は末尾に _total)。表と呼び手の名は Prometheus の名の字(英数字と _)へ畳んだ語(metric-word)にする。
;;   records_list_rows_table_<表>_<calls|rows>                全部の呼び手の和。宣言の表の全部を起動の時に 0 で置く
;;   records_list_rows_caller_<呼び手>_table_<表>_<calls|rows>  呼び手ごと。最初の答えで現れる(宣言の表 × 呼び手の組は数千になるので
;;                                                           0 で置かない — 呼び手の和は表ごとの系列で読む)
;; 呼び手 = 宣言された呼び手の名(service の設定)・名乗りの無い要求 = unnamed・宣言に無い名乗り = undeclared(名乗りの綴りで系列を増やさない)。
;; 表は宣言の表だけ(宣言に無い表の要求は撃つ前に 404 で断る)。系列は宣言の表 × (宣言の呼び手 + 2)× 2 で閉じる。
(val LIST-ROWS-TABLE-METRIC "records_list_rows_table_{}_{}")
(val LIST-ROWS-CALLER-METRIC "records_list_rows_caller_{}_table_{}_{}")
(val LIST-ROWS-MEASURES #("calls" "rows"))
(val CALLER-UNNAMED "unnamed")
(val CALLER-UNDECLARED "undeclared")
;; 名を語に畳む時に _ へ替える字(Prometheus の名に使えない字 — ASCII の英数字と _ の外)。
(val METRIC-WORD-UNSAFE (re.compile "[^A-Za-z0-9_]"))

;; 操作ごとに答えてよい kind(契約の records.routes の 200 の答えと同じ)。
(setv ANSWER-KINDS {OP-READ-ROW #("row" "missing")
                    OP-LIST-ROWS #("page" "reset" "notIndexed")
                    OP-PUT-ROW #("written" "conflict" "refused")
                    OP-WATCH-CHANGES #("changes" "reset")
                    OP-APPEND-EVENT #("appended" "refused")
                    OP-READ-EVENTS #("events" "notIndexed")
                    OP-PUT-ROWS #("writtenRows" "rowsConflict" "rowsRefused")
                    OP-READ-STREAM-END #("streamEnd" "streamEmpty")
                    OP-WATCH-EVENTS #("eventsMoved" "eventsQuiet")
                    OP-READ-EVENT-BY-KEY #("event" "eventAbsent" "eventRetired")
                    OP-APPEND-EVENTS #("appendedEvents")})

;; 境界の値の型(公開 effect・wire の本文で運ぶ答え)。JSON の値の型 JsonValue は上の import(doeff_hy.json_value)。
(setv PublicEffect (| ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents PutRows ReadStreamEnd WatchEvents ReadEventByKey
                      AppendEvents))
(setv WireAnswer (| Row Missing Page Written Conflict Refused NotIndexed Reset Changes Appended Events
                    WrittenRows RowsConflict RowsRefused StreamEnd StreamEmpty EventsMoved EventsQuiet Event EventAbsent EventRetired
                    AppendedEvents))


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

(defk operation-of [path]
  {:pre [(: path str)] :post [(: % (| str None))]}
  "要求の path(query を除く)が名指す記録の操作の名(OPERATIONS の 1 つ)を読むため。記録の操作の route でなければ None(計器が要求の種を
   決め、操作ごとに区間の秒を積むため — #2709・#3688)。"
  (val named (if (.startswith path PATH-PREFIX) (cut path (len PATH-PREFIX) None) None))
  (if (in named OPERATIONS) named None))


(defk request-kind [path]
  {:pre [(: path str)] :post [(: % RequestKind)]}
  "要求の path(query を除く)を要求の種にする(計器が書きと読みを分けて数えるため)。method は問わない — POST でない記録の操作の
   400 も、その操作の種で数える。"
  (<- operation (| str None) (operation-of path))
  (match operation
    None RequestKind.OTHER
    name :if (in name WRITE-OPERATIONS) RequestKind.WRITE
    _ RequestKind.READ))


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


(defk metric-word [name]
  {:pre [(: name str)] :post [(: % str)]}
  "表や呼び手の名を counter の名に入れる語にするため(Prometheus の名に使えない字 — ASCII の英数字と _ の外 — を _ に畳む)。"
  (.sub METRIC-WORD-UNSAFE "_" name))


(defk list-rows-caller [named callers]
  {:pre [(: named (| str None)) (: callers tuple)] :post [(: % str)]}
  "要求が名乗った呼び手の名 named を、数える呼び手の名にするため: 宣言された名 callers に在ればその名・名乗りが無いか空なら unnamed・
   宣言に無ければ undeclared(名乗りの綴りで系列を増やさない)。"
  (val stripped (if (is named None) "" (.strip named)))
  (cond (= stripped "") CALLER-UNNAMED
        (in stripped callers) stripped
        True CALLER-UNDECLARED))


(defk list-rows-counts [caller table rows]
  {:pre [(: caller str) (: table str) (: rows int)] :post [(: % tuple)]}
  "一覧の答え 1 つ(呼び手 caller — list-rows-caller の答え・表 table・返した行の数 rows)で足す counter の名と量の組を作るため
   (表ごとの calls と rows・呼び手ごとの calls と rows の 4 つ)。"
  (val table-word (.sub METRIC-WORD-UNSAFE "_" table))
  (val caller-word (.sub METRIC-WORD-UNSAFE "_" caller))
  (tuple (gfor #(measure amount) (zip LIST-ROWS-MEASURES #(1.0 (float rows)) :strict True)
               pair #(#((.format LIST-ROWS-TABLE-METRIC table-word measure) amount)
                      #((.format LIST-ROWS-CALLER-METRIC caller-word table-word measure) amount))
               pair)))


(defk list-rows-metrics [tables callers]
  {:pre [(: tables tuple) (: callers tuple)] :post [(: % tuple)]}
  "宣言の表 tables と宣言の呼び手 callers の一覧の読みの系列の閉じた組を確かめ、表ごとの系列(起動の時に 0 で置く物)を返すため。
   語へ畳んだ名が重なる(別の表・呼び手が同じ counter に入る)か、宣言の呼び手が unnamed・undeclared を名乗るなら、数えを混ぜずに
   起動を止める(ValueError)。"
  (val reserved (tuple (gfor name callers :if (in name #(CALLER-UNNAMED CALLER-UNDECLARED)) name)))
  (when reserved
    (raise (ValueError (.format "宣言の呼び手の名に予約の語は使えない: {}" reserved))))
  (val table-words (tuple (gfor table tables (.sub METRIC-WORD-UNSAFE "_" table))))
  (val caller-words (tuple (gfor who (+ callers #(CALLER-UNNAMED CALLER-UNDECLARED)) (.sub METRIC-WORD-UNSAFE "_" who))))
  (val table-names (tuple (gfor word table-words measure LIST-ROWS-MEASURES (.format LIST-ROWS-TABLE-METRIC word measure))))
  (val every (+ table-names
                (tuple (gfor who caller-words word table-words measure LIST-ROWS-MEASURES
                             (.format LIST-ROWS-CALLER-METRIC who word measure)))))
  (val clashes (sorted (gfor #(name seen) (.items (Counter every)) :if (> seen 1) name)))
  (when clashes
    (raise (ValueError (.format "一覧の読みの系列の名が重なる(表か呼び手の名が同じ語に畳まれる — 表 {}・呼び手 {}): {}"
                                tables callers (cut clashes 0 5)))))
  table-names)


(defk list-rows-metric-helps [tables]
  {:pre [(: tables tuple)] :post [(: % (get FrozenMap str))]}
  "表ごとの一覧の読みの系列の # HELP の説明(名 → 説明)を作り、/metrics の読み手が系列の意味を読めるようにするため(呼び手ごとの
   系列は数が多いので説明を付けない — 表ごとの説明が綴りを名指す)。"
  (FrozenMap (gfor table tables
                   :setv word (.sub METRIC-WORD-UNSAFE "_" table)
                   measure LIST-ROWS-MEASURES
                   #((.format LIST-ROWS-TABLE-METRIC word measure)
                     (.format "記録の service が表 {} の一覧の読み(list-rows)に{}(起動からの累計・呼び手ごとは {})"
                              table (if (= measure "calls") "答えた数" "返した行の数")
                              (.format LIST-ROWS-CALLER-METRIC "<呼び手>" word measure))))))


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


(defk answer-object-of [value what required optional]
  {:pre [(: value JsonValue) (: what str) (: required tuple) (: optional tuple)] :post [(: % dict)]}
  "答えの JSON の object を読むため: 足りない鍵は断り、知らない鍵は読み捨てて、契約の鍵だけの object を返す(頭の註 — 新しい版の service が
   答えに足した鍵を、1 版前の client が WireMalformed にしない)。"
  (when (not (isinstance value dict))
    (raise (WireMalformed (.format "{} は JSON の object: {!r}" what value))))
  (val missing (sorted (gfor name required :if (not-in name value) name)))
  (when missing (raise (WireMalformed (.format "{} に鍵が足りない: {}" what missing))))
  (dfor #(name part) (.items value) :if (in name (+ required optional)) name part))


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


(defk boolean-of [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % bool)]}
  "真偽値であるべき JSON の値を検める(数を真偽値として通さない)。"
  (when (not (isinstance value bool))
    (raise (WireMalformed (.format "{} は真偽値: {!r}" what value))))
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


(defk shape-of [value what required optional answer]
  {:pre [(: value JsonValue) (: what str) (: required tuple) (: optional tuple) (: answer bool)] :post [(: % dict)]}
  "要求と答えの両方に載る object(位置)を、載った向きの決まりで読むため(頭の註 — answer = 答えの読みなら知らない鍵を読み捨てる)。"
  (if answer
      (! (answer-object-of value what required optional))
      (! (object-of value what required optional))))


(defk watch-cursor-from [value what answer]
  {:pre [(: value JsonValue) (: what str) (: answer bool)] :post [(: % WatchCursor)]}
  "wire の object から変更の列の位置を読む(answer = 答えの位置か — 要求の位置は知らない鍵を断る)。"
  (<- body (shape-of value what #("epoch" "sequence") #() answer))
  (WatchCursor (! (integer-of (get body "epoch") (+ what ".epoch"))) (! (integer-of (get body "sequence") (+ what ".sequence")))))


(defk list-cursor-json [cursor]
  {:pre [(: cursor (| ListCursor None))] :post [(: % (| dict None))]}
  "一覧の次の頁の位置を wire の値にする(None = 終わり / 最初から)。鍵 afterValue は上から読む読みの位置の時だけ載せる。"
  (cond (is cursor None) None
        (is cursor.after-value None) {"epoch" cursor.epoch "afterKey" cursor.after-key}
        True {"epoch" cursor.epoch "afterKey" cursor.after-key "afterValue" cursor.after-value}))


(defk list-cursor-from [value what answer]
  {:pre [(: value JsonValue) (: what str) (: answer bool)] :post [(: % (| ListCursor None))]}
  "wire の値から一覧の次の頁の位置を読む(null = 無し・鍵 afterValue は上から読む読みの位置の時だけ在る・answer = 答えの位置か — 要求の
   位置は知らない鍵を断る)。"
  (when (is value None) (return None))
  (<- body (shape-of value what #("epoch" "afterKey") #("afterValue") answer))
  (ListCursor (! (integer-of (get body "epoch") (+ what ".epoch")))
              (! (string-of (get body "afterKey") (+ what ".afterKey")))
              (if (in "afterValue" body) (! (integer-of (get body "afterValue") (+ what ".afterValue"))) None)))


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


(defk event-append-json [one]
  {:pre [(: one EventAppend)] :post [(: % dict)]}
  "AppendEvents の束の追記 1 つを wire の object(append-event の本文と同じ鍵 stream・idempotencyKey・body)にする。"
  {"stream" one.stream "idempotencyKey" one.idempotency-key "body" (thaw-json one.body)})


(defk event-append-from [value]
  {:pre [(: value JsonValue)] :post [(: % EventAppend)]}
  "wire の object から AppendEvents の束の追記 1 つを読む(鍵の組は append-event の本文と同じ)。"
  (<- body (object-of value "append-events の追記" #("stream" "idempotencyKey" "body") #()))
  (EventAppend (! (string-of (get body "stream") "appends[].stream")) (! (string-of (get body "idempotencyKey") "appends[].idempotencyKey"))
               (get body "body")))


(defk event-appends-from [body]
  {:pre [(: body JsonValue)] :post [(: % tuple)]}
  "append-events の本文 {appends: [...]} から束の追記(EventAppend の tuple)を読む(同じ追記が 2 度出る束・空の束は AppendEvents が作る時に
   断る)。"
  (<- (object-of body "append-events の本文" #("appends") #()))
  (<- items list (list-in (get body "appends") "appends"))
  (var appends #())
  (for [item items]
    (<- one EventAppend (event-append-from item))
    (:= appends (+ appends #(one))))
  appends)


(defk encode-request [ask]
  {:pre [(: ask PublicEffect)] :post [(: % WireRequest)]}
  "client の handler が撃つ公開 effect(ask)を、HTTP で送る操作の名と本文にする。"
  (match ask
    (ReadRow :table table :key key)
      (WireRequest OP-READ-ROW {"table" table "key" (list key)})
    (ListRows :table table :where where :fields fields :cursor cursor :limit limit :descending_by descending-by :where_any where-any)
      ;; 鍵 descendingBy は上から読む読みの時だけ・鍵 whereAny(欄 → 値の組の配列)は値の組で絞る時だけ送る(どちらも使わない読みは前の
      ;; 形のまま。古い記録の service は知らない鍵を 400 で断るので、使う client より先に service を新しい版で起こす)。
      (WireRequest OP-LIST-ROWS (| {"table" table "where" (thaw-json where) "fields" (if (is fields None) None (list fields))
                                    "cursor" (! (list-cursor-json cursor)) "limit" limit}
                                   (if (is descending-by None) {} {"descendingBy" descending-by})
                                   (if where-any {"whereAny" (thaw-json where-any)} {})))
    (PutRow :table table :key key :value value :expect expect)
      (WireRequest OP-PUT-ROW {"table" table "key" (list key) "value" (thaw-json value) "expect" (! (expect-json expect))})
    (WatchChanges :tables tables :cursor cursor :timeout timeout :limit limit :streams streams)
      ;; 鍵 streams は列を名指した時だけ送る(#3718 — 名指さない要求は前の形のまま。古い記録の service は知らない鍵を 400 で断るので、
      ;; 名指す client より先に service を新しい版で起こす)。
      (WireRequest OP-WATCH-CHANGES (| {"tables" (list tables) "cursor" (! (watch-cursor-json cursor)) "timeout" (float timeout)
                                        "limit" limit}
                                       (if streams {"streams" (list streams)} {})))
    (AppendEvent :stream stream :idempotency_key idempotency-key :body body)
      (WireRequest OP-APPEND-EVENT {"stream" stream "idempotencyKey" idempotency-key "body" (thaw-json body)})
    (ReadEvents :stream stream :after after :limit limit :key_head key-head)
      ;; 鍵 keyHead は頭で引く読みの時だけ送る(列の全部を読む読みは前の形のまま。古い記録の service は知らない鍵を 400 で断るので、
      ;; 頭で引く client より先に service を新しい版で起こす)。
      (WireRequest OP-READ-EVENTS (| {"stream" stream "after" after "limit" limit}
                                     (if (is key-head None) {} {"keyHead" key-head})))
    (ReadStreamEnd :stream stream)
      (WireRequest OP-READ-STREAM-END {"stream" stream})
    (WatchEvents :stream stream :after after :timeout timeout)
      (WireRequest OP-WATCH-EVENTS {"stream" stream "after" after "timeout" (float timeout)})
    (ReadEventByKey :stream stream :idempotency_key idempotency-key)
      (WireRequest OP-READ-EVENT-BY-KEY {"stream" stream "idempotencyKey" idempotency-key})
    (PutRows :writes writes)
      (do (val write-items [])
          (for [write writes] (.append write-items (! (row-write-json write))))
          (WireRequest OP-PUT-ROWS {"writes" write-items}))
    (AppendEvents :appends appends)
      (do (var append-items [])
          (for [one appends]
            (<- item dict (event-append-json one))
            (:= append-items (+ append-items [item])))
          (WireRequest OP-APPEND-EVENTS {"appends" append-items}))))


(defk request-key [ask]
  {:pre [(: ask PublicEffect)] :post [(: % (| str None))]}
  "client が要求 ask の見出し IDEMPOTENCY-HEADER に載せる冪等キーを読むため(行の書きの鍵 — 他の操作と鍵の無い書きは None)。"
  (match ask
    (PutRow :idempotency_key key) key
    (PutRows :idempotency_key key) key
    _ None))


(defk keyed-request [decoded key]
  {:pre [(: decoded DecodedRequest) (: key (| str None))] :post [(: % DecodedRequest)]}
  "service が読んだ要求 decoded に、見出し IDEMPOTENCY-HEADER の冪等キー key を付けるため(行の書きだけ — 他の操作と見出しの無い要求は
   decoded のまま)。鍵の形の外(空・長すぎる)は作る時の検め(values.checked-write-key)が ValueError で断る — service は malformed にする。"
  (match #(decoded.effect key)
    #(_ None) decoded
    #((PutRow) _) (DecodedRequest (replace decoded.effect :idempotency-key key))
    #((PutRows) _) (DecodedRequest (replace decoded.effect :idempotency-key key))
    _ decoded))


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
        (do (<- (object-of body "list-rows の本文" #("table") #("where" "fields" "cursor" "limit" "descendingBy" "whereAny")))
            (setv keywords {})
            (when (in "where" body) (setv (get keywords "where") (! (json-object-in (get body "where") "where"))))
            (when (in "fields" body)
              (setv (get keywords "fields") (if (is (get body "fields") None) None (! (strings-of (get body "fields") "fields")))))
            (when (in "cursor" body) (setv (get keywords "cursor") (! (list-cursor-from (get body "cursor") "cursor" False))))
            ;; limit の上限 LIST-LIMIT-MAX(effects.hy)は ListRows が作る時に断る — 越えた本文は malformed(400)に写る(ADR-DOE-RECORDS-002 R6)。
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            (when (in "descendingBy" body)
              (setv (get keywords "descending_by") (! (string-of (get body "descendingBy") "descendingBy"))))
            ;; 鍵 whereAny は値の組で絞る時だけ在る(encode-request は空の写像を送らない)— 空の object は綴りの外として断る。組の配列の
            ;; 形・空の組・上限・where との重なりは ListRows が作る時に断る(malformed に写る)。
            (when (in "whereAny" body)
              (<- any-of (json-object-in (get body "whereAny") "whereAny"))
              (when (not any-of) (raise (WireMalformed "list-rows の whereAny は空でない object(絞らない時は鍵ごと無い)")))
              (setv (get keywords "where_any") any-of))
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
        (do (<- (object-of body "watch-changes の本文" #("tables" "cursor") #("timeout" "limit" "streams")))
            (setv keywords {})
            ;; 待ちの秒は 1 回の要求の上限 WATCH-MAX-SECONDS で切る(long-poll の取り決め — #3074: service はこの秒まで待ち、変化が
            ;; 無ければ空の答えを返す。client は待ちをこの秒ごとの要求に分ける)。
            (when (in "timeout" body)
              (setv (get keywords "timeout") (min (! (seconds-of (get body "timeout") "timeout")) WATCH-MAX-SECONDS)))
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            ;; 鍵 streams は名指した時だけ在る(encode-request は空の列を送らない)— 空の配列は綴りの外として断る。
            (when (in "streams" body)
              (<- streams (strings-of (get body "streams") "streams"))
              (when (not streams) (raise (WireMalformed "watch-changes の streams は空でない配列(名指さない時は鍵ごと無い)")))
              (setv (get keywords "streams") streams))
            (WatchChanges (! (strings-of (get body "tables") "tables")) (! (watch-cursor-from (get body "cursor") "cursor" False))
                          #** keywords))
      "append-event"
        (do (<- (object-of body "append-event の本文" #("stream" "idempotencyKey" "body") #()))
            (AppendEvent (! (string-of (get body "stream") "stream")) (! (string-of (get body "idempotencyKey") "idempotencyKey"))
                         (get body "body")))
      "read-events"
        (do (<- (object-of body "read-events の本文" #("stream") #("after" "limit" "keyHead")))
            (setv keywords {})
            (when (in "after" body) (setv (get keywords "after") (! (integer-of (get body "after") "after"))))
            (when (in "limit" body) (setv (get keywords "limit") (! (integer-of (get body "limit") "limit"))))
            (when (in "keyHead" body) (setv (get keywords "key_head") (! (string-of (get body "keyHead") "keyHead"))))
            (ReadEvents (! (string-of (get body "stream") "stream")) #** keywords))
      "put-rows"
        (PutRows (! (row-writes-from body)))
      "read-stream-end"
        (do (<- (object-of body "read-stream-end の本文" #("stream") #()))
            (ReadStreamEnd (! (string-of (get body "stream") "stream"))))
      "watch-events"
        ;; 新しい操作なので欄は 3 つとも必須(既定へ倒す読みを置かない — encode-request は常に 3 つを送る)。待ちの秒は watch-changes と
        ;; 同じく WATCH-MAX-SECONDS で切る。
        (do (<- (object-of body "watch-events の本文" #("stream" "after" "timeout") #()))
            (WatchEvents (! (string-of (get body "stream") "stream")) :after (! (integer-of (get body "after") "after"))
                         :timeout (min (! (seconds-of (get body "timeout") "timeout")) WATCH-MAX-SECONDS)))
      "read-event-by-key"
        (do (<- (object-of body "read-event-by-key の本文" #("stream" "idempotencyKey") #()))
            (ReadEventByKey (! (string-of (get body "stream") "stream")) (! (string-of (get body "idempotencyKey") "idempotencyKey"))))
      "append-events"
        (AppendEvents (! (event-appends-from body)))
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
    (WatchChanges :tables tables :streams streams) (NamedStores tables streams)
    (AppendEvent :stream stream) (NamedStores #() #(stream))
    (ReadEvents :stream stream) (NamedStores #() #(stream))
    (ReadStreamEnd :stream stream) (NamedStores #() #(stream))
    (WatchEvents :stream stream) (NamedStores #() #(stream))
    (ReadEventByKey :stream stream) (NamedStores #() #(stream))
    (PutRows :writes writes) (NamedStores (tuple (sorted (sfor write writes write.table))) #())
    (AppendEvents :appends appends) (NamedStores #() (tuple (sorted (sfor one appends one.stream))))))


;; --- 宣言に無い名の断り(404 not-found)の理由の綴り---------------------------------------------------------
;; service が理由の文を組み、client がその文から UndeclaredTable の欄(宣言に無いと分かった名)を戻す — 綴りはこの 2 つの 1 か所。
;; 断りの本文(契約 $defs.refusal = error・reason)には欄を足さない: 答えの知らない鍵を読み捨てる前の版(頭の註)の client は本文の
;; 知らない鍵を WireMalformed にするので、
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


(defk tail-json [tail]
  {:pre [(: tail (| StreamTail StreamTailEmpty))] :post [(: % dict)]}
  "WatchChanges の答えの、名指した列 1 つの末尾を wire の object(kind = streamTail | streamTailEmpty)にするため(#3718)。"
  (match tail
    (StreamTail :stream stream :sequence sequence :at at) {"kind" "streamTail" "stream" stream "sequence" sequence "at" at}
    (StreamTailEmpty :stream stream) {"kind" "streamTailEmpty" "stream" stream}))


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
    ;; 鍵 position は書きの変更の位置を持つ答えの時だけ出す(省ける鍵 — 1 版前の読み手は知らない鍵を読み捨てる・test_wire_versions)。
    ;; 鍵 replayed は冪等キーの送り直しの答えの時だけ出す(省ける鍵 — 真の時だけ・card ki-78c6d663491f。1 版前の読み手は読み捨てる)。
    (Written :version version :value value :position position :replayed replayed)
      (| {"kind" "written" "version" version "value" (thaw-json value)}
         (if (is position None) {} {"position" (! (watch-cursor-json position))})
         (if replayed {"replayed" True} {}))
    (Conflict :current current) {"kind" "conflict" "current" (! (encode-answer current))}
    (Refused :reason reason) {"kind" "refused" "reason" reason}
    (NotIndexed :fields fields) {"kind" "notIndexed" "fields" (list fields)}
    (Reset :epoch epoch :floor floor) {"kind" "reset" "epoch" epoch "floor" floor}
    (Changes :items items :cursor cursor :tails tails)
      (do (setv encoded [])
          (for [item items] (.append encoded (! (change-json item))))
          (var tail-items [])
          (for [tail tails]
            (<- tail-item dict (tail-json tail))
            (:= tail-items (+ tail-items [tail-item])))
          ;; 鍵 tails は要求が列を名指した時だけ出す(tails は名指した列ごとに 1 つ — 名指さない要求の答えは前の形のまま。古い client は
          ;; 知らない鍵を WireMalformed にするので、名指さない client を壊さない・#3718)。
          (| {"kind" "changes" "items" encoded "cursor" (! (watch-cursor-json cursor))}
             (if tail-items {"tails" tail-items} {})))
    (Appended :sequence sequence :replayed replayed) {"kind" "appended" "sequence" sequence "replayed" replayed}
    (AppendedEvents :items items)
      (do (var appended-items [])
          (for [item items]
            (<- encoded dict (encode-answer item))
            (:= appended-items (+ appended-items [encoded])))
          {"kind" "appendedEvents" "items" appended-items})
    (Events :items items :last_sequence last-sequence)
      (do (setv encoded [])
          (for [event items] (.append encoded (! (event-json event))))
          {"kind" "events" "items" encoded "lastSequence" last-sequence})
    (WrittenRows :items items :replayed replayed)
      (do (val written-items [])
          (for [written items] (.append written-items (! (encode-answer written))))
          (| {"kind" "writtenRows" "items" written-items}
             (if replayed {"replayed" True} {})))
    (RowsConflict :index index :table table :key key :current current)
      {"kind" "rowsConflict" "index" index "table" table "key" (list key) "current" (! (encode-answer current))}
    (RowsRefused :index index :table table :key key :reason reason)
      {"kind" "rowsRefused" "index" index "table" table "key" (list key) "reason" reason}
    (StreamEnd :sequence sequence) {"kind" "streamEnd" "sequence" sequence}
    (StreamEmpty) {"kind" "streamEmpty"}
    (EventsMoved) {"kind" "eventsMoved"}
    (EventsQuiet) {"kind" "eventsQuiet"}
    ;; ReadEventByKey の答え: 出来事は events の items と同じ object を欄 event に入れ子で運ぶ(読みは同じ event-from)。
    (Event) {"kind" "event" "event" (! (event-json answer))}
    (EventAbsent) {"kind" "eventAbsent"}
    (EventRetired :sequence sequence) {"kind" "eventRetired" "sequence" sequence}))


(defk row-from [value]
  {:pre [(: value JsonValue)] :post [(: % Row)]}
  "wire の object から行 1 つを読む。"
  (<- body (answer-object-of value "row" #("kind" "key" "value" "version") #()))
  (<- key (strings-of (get body "key") "row.key"))
  (<- fields (json-object-in (get body "value") "row.value"))
  (<- version (integer-of (get body "version") "row.version"))
  (try (Row key fields version) (except [error #(TypeError ValueError)] (raise (! (malformed "row" error))))))


(defk change-from [value]
  {:pre [(: value JsonValue)] :post [(: % (| RowChanged RowRemoved))]}
  "wire の object から変更 1 つを読む。"
  (match value
    {"kind" "rowChanged"}
      (do (<- (answer-object-of value "rowChanged" #("kind" "table" "key" "version" "value" "sequence" "at") #()))
          (RowChanged (! (string-of (get value "table") "table")) (! (strings-of (get value "key") "key"))
                      (! (integer-of (get value "version") "version")) (! (json-object-in (get value "value") "value"))
                      (! (integer-of (get value "sequence") "sequence")) (! (integer-of (get value "at") "at"))))
    {"kind" "rowRemoved"}
      (do (<- (answer-object-of value "rowRemoved" #("kind" "table" "key" "sequence") #()))
          (RowRemoved (! (string-of (get value "table") "table")) (! (strings-of (get value "key") "key"))
                      (! (integer-of (get value "sequence") "sequence"))))
    _ (raise (WireMalformed (.format "変更は kind = rowChanged | rowRemoved: {!r}" value)))))


(defk event-from [value]
  {:pre [(: value JsonValue)] :post [(: % Event)]}
  "wire の object から追記の列の出来事 1 つを読む。"
  (<- body (answer-object-of value "event" #("stream" "sequence" "idempotencyKey" "body" "writer" "at") #()))
  (Event (! (string-of (get body "stream") "event.stream")) (! (integer-of (get body "sequence") "event.sequence"))
         (! (string-of (get body "idempotencyKey") "event.idempotencyKey")) (get body "body")
         (! (string-of (get body "writer") "event.writer")) (! (integer-of (get body "at") "event.at"))))


(defk list-in [value what]
  {:pre [(: value JsonValue) (: what str)] :post [(: % list)]}
  "配列であるべき JSON の値を検める(行・変更・出来事の列)。"
  (when (not (isinstance value list)) (raise (WireMalformed (.format "{} は配列: {!r}" what value))))
  value)


(defk tail-from [value]
  {:pre [(: value JsonValue)] :post [(: % (| StreamTail StreamTailEmpty))]}
  "wire の object から、WatchChanges の答えの名指した列 1 つの末尾を読むため(#3718)。"
  (match value
    {"kind" "streamTail"}
      (do (<- (answer-object-of value "streamTail" #("kind" "stream" "sequence" "at") #()))
          (StreamTail :stream (! (string-of (get value "stream") "streamTail.stream"))
                      :sequence (! (integer-of (get value "sequence") "streamTail.sequence"))
                      :at (! (integer-of (get value "at") "streamTail.at"))))
    {"kind" "streamTailEmpty"}
      (do (<- (answer-object-of value "streamTailEmpty" #("kind" "stream") #()))
          (StreamTailEmpty :stream (! (string-of (get value "stream") "streamTailEmpty.stream"))))
    _ (raise (WireMalformed (.format "列の末尾は kind = streamTail | streamTailEmpty: {!r}" value)))))


(defk tails-from [value]
  {:pre [(: value dict)] :post [(: % (get tuple #((| StreamTail StreamTailEmpty) ...)))]}
  "changes の本文の鍵 tails(要求が列を名指した時だけ在る — #3718)を、名指した列の末尾の tuple にするため。鍵が無ければ空。在るのに空の
   配列は綴りの外(encode-answer は空の tails を鍵ごと出さない)。名指した列と合うかは、要求を知る client が answer-for-request で照らす。"
  (when (not-in "tails" value)
    (return #()))
  (<- items list (list-in (get value "tails") "changes.tails"))
  (when (not items)
    (raise (WireMalformed "changes の tails は空でない配列(列を名指さない要求の答えには鍵ごと無い)")))
  (var tails #())
  (for [item items]
    (<- tail (| StreamTail StreamTailEmpty) (tail-from item))
    (:= tails (+ tails #(tail))))
  tails)


(defk written-rows-from [value]
  {:pre [(: value JsonValue)] :post [(: % WrittenRows)]}
  "wire の object から PutRows の確定(束の順の written の列)を読む。"
  (<- (answer-object-of value "writtenRows" #("kind" "items") #("replayed")))
  (val items [])
  (for [item (! (list-in (get value "items") "writtenRows.items"))] (.append items (! (answer-from item))))
  (when (not (all (gfor item items (isinstance item Written))))
    (raise (WireMalformed (.format "writtenRows.items は written の列: {!r}" value))))
  (WrittenRows (tuple items) :replayed (if (in "replayed" value) (! (boolean-of (get value "replayed") "writtenRows.replayed")) False)))


(defk appended-events-from [value]
  {:pre [(: value JsonValue)] :post [(: % AppendedEvents)]}
  "wire の object から AppendEvents の束の答え(束の順の、追記ごとの appended | refused の列)を読む。"
  (<- (answer-object-of value "appendedEvents" #("kind" "items") #()))
  (<- listed list (list-in (get value "items") "appendedEvents.items"))
  (var items #())
  (for [item listed]
    (<- one WireAnswer (answer-from item))
    (when (not (isinstance one #(Appended Refused)))
      (raise (WireMalformed (.format "appendedEvents.items は appended | refused の列: {!r}" value))))
    (:= items (+ items #(one))))
  (AppendedEvents items))


(defk rows-conflict-from [value]
  {:pre [(: value JsonValue)] :post [(: % RowsConflict)]}
  "wire の object から PutRows の衝突(束の中の位置・行・今の値)を読む。"
  (<- (answer-object-of value "rowsConflict" #("kind" "index" "table" "key" "current") #()))
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
      {"kind" "missing"} (do (<- (answer-object-of value "missing" #("kind") #())) (Missing))
      {"kind" "page"}
        (do (<- (answer-object-of value "page" #("kind" "rows" "nextCursor" "epoch" "sequence") #()))
            (setv rows [])
            (for [item (! (list-in (get value "rows") "page.rows"))] (.append rows (! (row-from item))))
            (Page (tuple rows) (! (list-cursor-from (get value "nextCursor") "page.nextCursor" True))
                  (! (integer-of (get value "epoch") "page.epoch")) (! (integer-of (get value "sequence") "page.sequence"))))
      {"kind" "written"}
        (do (<- (answer-object-of value "written" #("kind" "version" "value") #("position" "replayed")))
            (Written (! (integer-of (get value "version") "written.version")) (! (json-object-in (get value "value") "written.value"))
                     (if (in "position" value) (! (watch-cursor-from (get value "position") "written.position" True)) None)
                     :replayed (if (in "replayed" value) (! (boolean-of (get value "replayed") "written.replayed")) False)))
      {"kind" "conflict"}
        (do (<- (answer-object-of value "conflict" #("kind" "current") #()))
            (<- current (answer-from (get value "current")))
            (when (not (isinstance current #(Row Missing)))
              (raise (WireMalformed (.format "conflict.current は row | missing: {!r}" value))))
            (Conflict current))
      {"kind" "refused"}
        (do (<- (answer-object-of value "refused" #("kind" "reason") #())) (Refused (! (string-of (get value "reason") "refused.reason"))))
      {"kind" "notIndexed"}
        (do (<- (answer-object-of value "notIndexed" #("kind" "fields") #()))
            (NotIndexed (! (strings-of (get value "fields") "notIndexed.fields"))))
      {"kind" "reset"}
        (do (<- (answer-object-of value "reset" #("kind" "epoch" "floor") #()))
            (Reset (! (integer-of (get value "epoch") "reset.epoch")) (! (integer-of (get value "floor") "reset.floor"))))
      {"kind" "changes"}
        (do (<- (answer-object-of value "changes" #("kind" "items" "cursor") #("tails")))
            (setv items [])
            (for [item (! (list-in (get value "items") "changes.items"))] (.append items (! (change-from item))))
            (<- tails tuple (tails-from value))
            (Changes (tuple items) (! (watch-cursor-from (get value "cursor") "changes.cursor" True)) tails))
      {"kind" "appended"}
        (do (<- (answer-object-of value "appended" #("kind" "sequence" "replayed") #()))
            (Appended (! (integer-of (get value "sequence") "appended.sequence")) (! (boolean-of (get value "replayed") "appended.replayed"))))
      {"kind" "events"}
        (do (<- (answer-object-of value "events" #("kind" "items" "lastSequence") #()))
            (setv items [])
            (for [item (! (list-in (get value "items") "events.items"))] (.append items (! (event-from item))))
            (Events (tuple items) (! (integer-of (get value "lastSequence") "events.lastSequence"))))
      {"kind" "streamEnd"}
        (do (<- (answer-object-of value "streamEnd" #("kind" "sequence") #()))
            (StreamEnd (! (integer-of (get value "sequence") "streamEnd.sequence"))))
      {"kind" "streamEmpty"} (do (<- (answer-object-of value "streamEmpty" #("kind") #())) (StreamEmpty))
      {"kind" "eventsMoved"} (do (<- (answer-object-of value "eventsMoved" #("kind") #())) (EventsMoved))
      {"kind" "eventsQuiet"} (do (<- (answer-object-of value "eventsQuiet" #("kind") #())) (EventsQuiet))
      {"kind" "event"} (do (<- (answer-object-of value "event の答え" #("kind" "event") #())) (! (event-from (get value "event"))))
      {"kind" "eventAbsent"} (do (<- (answer-object-of value "eventAbsent" #("kind") #())) (EventAbsent))
      {"kind" "eventRetired"}
        (do (<- (answer-object-of value "eventRetired" #("kind" "sequence") #()))
            (EventRetired (! (integer-of (get value "sequence") "eventRetired.sequence"))))
      {"kind" "writtenRows"} (! (written-rows-from value))
      {"kind" "appendedEvents"} (! (appended-events-from value))
      {"kind" "rowsConflict"} (! (rows-conflict-from value))
      {"kind" "rowsRefused"}
        (do (<- (answer-object-of value "rowsRefused" #("kind" "index" "table" "key" "reason") #()))
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


(defk answer-for-request [ask answer]
  {:pre [(: ask PublicEffect) (: answer WireAnswer)] :post [(: % WireAnswer)]}
  "client が受けて読んだ答え answer が、撃った要求 ask に合うかを照らすため(#3718): WatchChanges の答えの tails は、名指した列 streams と
   同じ数・同じ順の列の末尾。名指したのに鍵 tails が無い・数や列が違う・名指さないのに在る答えは、黙って空や既定へ倒さず WireMalformed で
   名指して上げる(service の綴りの誤り)。他の答えはそのまま返す。"
  (match #(ask answer)
    #((WatchChanges :streams streams) (Changes :tails tails))
      (do (val named (tuple (gfor tail tails tail.stream)))
          (when (!= named streams)
            (raise (WireMalformed (.format "watch-changes の答えの tails の列 {} が、要求が名指した列 {} と合わない"
                                           (list named) (list streams)))))
          answer)
    _ answer))


;; --- 断り ------------------------------------------------------------------------------------------------

(defk refusal-json [refusal]
  {:pre [(: refusal WireRefusal)] :post [(: % dict)]}
  "HTTP の断りを本文(契約 $defs.refusal)にする。"
  {"error" refusal.error "reason" refusal.reason})


(defk refusal-from [value]
  {:pre [(: value JsonValue)] :post [(: % WireRefusal)]}
  "HTTP の断りの本文を読む(client の handler が status と合わせて答えへ写すため)。"
  (<- body (answer-object-of value "断り" #("error" "reason") #()))
  (<- error (string-of (get body "error") "error"))
  (<- reason (string-of (get body "reason") "reason"))
  (try (WireRefusal error reason) (except [problem ValueError] (raise (! (malformed "断り" problem))))))
