;;; 記録の service の HTTP の口に公開 effect 8 つで答える client の handler — 別の process の Hy / Python の Program が、
;;; memory や PostgreSQL の handler と同じ effect のまま記録の service を読み書きするため。
;;;
;;; 書き手の名は endpoint の writer を平文の見出し X-Records-Writer で送る(service は確かめずに書き手の名に使う — 自分の program
;;; どうしの呼び出しに token の認証を入れない・#2986・#2988・#3007)。client は Authorization を送らない(欄 token は #2986 で消した)。
;;; 綴りは wire.hy(service と同じ 1 か所)。
;;;
;;; 答えの写し方:
;;;   200                    wire の本文の答え(Row・Page・Written・Conflict・Refused・Changes・WrittenRows・RowsConflict・RowsRefused …)
;;;   503 / 届かない          Unreachable(読みは撃ち直してよい・書きは期待つきなら撃ち直してよい)
;;;   404(宣言に無い表)      UndeclaredTable を上げる(組み立ての誤り — memory の handler と同じ)。欄 tables・streams は、撃った要求が
;;;                          名指した名のうち断りの理由に載った物(wire.hy の undeclared-refusal — 本文の形は変えない)
;;;   400 / 500              WireError を上げる(client か service の実装の誤り)
;;;   ほかの status          400 / 500 と同じ一般の失敗で WireError(401 / 403・間の proxy の 502 など — status ごとの枝を持たない。
;;;                          本文が契約の断りの形でない JSON の時だけ WireMalformed)。記録の service は 401 / 403 を出さない
;;;                          (呼び手を断らない — #2988・#3007)ので、身元の断りの名の付いた例外も持たない(#2986)
;;;
;;; WatchChanges と WatchEvents の待ちは service の long-poll(#3074): 待ちの秒(timeout)つきで service へ撃ち、待つのは service の中の
;;; 置き場の待ち(memory の呼び鈴・PostgreSQL の待ち)。service は 1 回の要求で WATCH-MAX-SECONDS(wire)までしか待たないので、client は
;;; 待ちをその秒ごとの要求に分け、変化の無い答えなら残りの秒でもう一度撃つ — 眠らず、読み直しを繰り返さない(前は timeout 0 の問いを
;;; poll-seconds ごとに撃ち直し、列の待ちは ReadEvents を読み直していた)。待つ要求の時間切れの秒は、待ちの秒 + request-timeout。
;;;
;;; 要求の送り方は 1 つ: 要求を doeff-core-effects の HttpRequest の effect として出し、答えるのは呼び手の外側の
;;; handler(本番 = 塞がない http-production-handler と await-handler)。処理ループと同じ scheduler の task から読む呼び手が、記録の
;;; service に届かない間も処理ループを止めないため。届かない(HttpFailed)は Unreachable に読む。
;;;
;;; 置き場の止まり(#3557 — 記録の service の短い停止を越える 1 か所): 要求と答えの 7 つ(ReadRow・ListRows・PutRow・
;;; PutRows・AppendEvent・ReadEvents・ReadStreamEnd)は、届かない(503 store-unavailable を含む Unreachable)時に、止まりの上限まで置き場の
;;; 戻りを待って同じ要求を撃ち直す(answered-riding-stall)。待つ時間は client だけが問う ReadRequestPatience の答え(RequestPatience —
;;; 合図の源が止まりに耐える時間 ReadSourcePatience とは別の問い)、戻りは合図の源と同じ came-back-within(AwaitRecordsBack を見張りの task で
;;; 受ける — 時間で撃ち直さない)。待つ時間の問いは止まった時だけでなく要求のたびに撃つ — 答え手の無い組み立ては最初の要求で名指しで落ち、
;;; 止まりの日まで隠れない。待つかどうかは組み立てが値で名を選ぶ(待つ秒の request-patience-handler か、待たない 0 秒 = records-unwaited)—
;;; 待てない組み立てと呼び(処理ループ・coordinator の居ない process)は、client の外側に 0 秒の答え手を置く。合図の源の問いには答えない
;;; ので、同じ組の中で源は自分の時間だけ止まりに耐える。上限を越えたら、待った秒を名指した Unreachable を返す(呼び手の扱いは今までどおり)。撃ち直しの冪等: 書きは
;;; 期待つき(ExpectVersion・ExpectAbsent — 1 回目が実は書けていれば Conflict)か冪等キーつき(AppendEvent — 同じ鍵・同じ本文は前の
;;; 番号を返す・laws.hy の law-append-is-idempotent)。変化の待ち 2 つ(WatchChanges・WatchEvents)は待たない — 合図の源が自分で
;;; 止まりを越え、SourceStalled・SourceResumed を bus に出す(event_source.hy の ride-out-stall)。
;;;
;;; 計器(#2740): endpoint に計器の答え手(meter)を渡すと、送った要求 1 つごとに要求の種 × 結果(service の答えの status・届かない・
;;; それ以外の status)を doeff の CountMetric で 1 つ数える(名の綴りは wire の CLIENT-ANSWER-METRIC)。記録の service に届かなかった
;;; 要求は service の計器に出ないので、client の側でだけ数えられる。meter の無い endpoint は計器の effect を出さない(答え手を持たない
;;; 使い手が壊れない)。書き手の job は、拍ごとに断面を送る計器と同じ答え手を渡す。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import json)
(import datetime [datetime])
(import doeff [with-handlers])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff_core_effects.meter_effects [CountMetric])
(import doeff_time [GetMonotonic GetTime])
(import doeff_records.event_source [RECORDS-SIGNAL-SOURCE ReadSignalSource came-back-within first-seen])
(import doeff_records.values [Changes EventsMoved EventsQuiet Reset Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd
                               ReadRequestPatience])
(import doeff_records.wire [PATH-PREFIX PublicEffect WireAnswer JsonValue encode-request decode-answer refusal-from undeclared-refusal
                            CLIENT-ANSWER-METRICS CLIENT-UNREACHABLE client-answer-metric client-status-outcome WRITER-HEADER
                            WATCH-MAX-SECONDS])

(setv DEFAULT-REQUEST-TIMEOUT 30.0)


(defclass WireError [RuntimeError]
  "service が 400 / 500 で答えた(client か service の実装の誤り — 値の失敗ではない)。ほかの status(401 / 403・間の proxy の 502 など)の
   本文が JSON でない時も同じ(file の頭の表)。")


(defclass [(dataclass :frozen True)] RecordsEndpoint []
  "記録の service 1 つへの接続の組: base-url = http://host:port /
   request-timeout = 要求 1 つの上限の秒(変化の待ちの要求は、待ちの秒をこれに足す)。待ちの読み直しの間隔の欄は無い(待ちは
   long-poll — #3074。前の欄 poll-seconds は使い手が渡すのをやめた後に消した)。要求は常に HttpRequest の effect で出す(file の頭の註)。
   meter = 計器の答え手(doeff の CountMetric に答える handler — 送った要求を数える。None = 数えない・file の頭の註)/
   writer = 呼び手の名(在れば平文の見出し X-Records-Writer で送る — service は確かめずに書き手の名に使う・#2988)。"
  (#^ str base-url)
  (setv #^ float request-timeout DEFAULT-REQUEST-TIMEOUT)
  (setv #^ (| (get Callable #(... object)) None) meter None)
  (setv #^ (| str None) writer None))


(defclass [(dataclass :frozen True)] RawReply []
  "service の答え 1 つの生の形: status と本文の byte(JSON として読むのは計器に数えた後 — 間の proxy の本文は JSON とは限らない)。"
  (#^ int status)
  (#^ bytes payload))


(defk service-url [endpoint operation]
  {:pre [(: endpoint RecordsEndpoint) (: operation str)] :post [(: % str)]}
  "操作 1 つの口の URL。"
  (+ (.rstrip endpoint.base-url "/") PATH-PREFIX operation))


(defk request-headers [endpoint]
  {:pre [(: endpoint RecordsEndpoint)] :post [(: % (get dict #(str str)))]}
  "要求の header(本文の型と、在れば書き手の名)。"
  (| {"Content-Type" "application/json; charset=utf-8"}
     (if (is endpoint.writer None) {} {WRITER-HEADER endpoint.writer})))


(defk request-bytes [body]
  {:pre [(: body (get dict #(str object)))] :post [(: % bytes)]}
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


(defk exchange-by-effect [endpoint operation body waited]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body (get dict #(str object))) (: waited float)]
   :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを HttpRequest の effect として出す。撃ち直しは呼び手の読みが決めるので 0 回、届かない失敗は値で受けて
   Unreachable にする。時間切れの秒 = request-timeout + waited(service の中で待ち得る秒 — long-poll の待ちを届かないと読まない)。"
  (<- url str (service-url endpoint operation))
  (<- headers dict (request-headers endpoint))
  (<- data bytes (request-bytes body))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest "POST" url :headers headers :body data :timeout-seconds (+ endpoint.request-timeout waited) :max-retries 0
                   :follow-redirects False :failures-as-values True))
  (when (isinstance answer HttpFailed)
    (return (Unreachable (.format "記録の service に届かない: {}" answer.detail))))
  (RawReply answer.status answer.content))


(defk exchange [endpoint operation body waited]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: body (get dict #(str object))) (: waited float)]
   :post [(: % (| RawReply Unreachable))]}
  "要求 1 つを送り、status と JSON の本文を受ける(HTTP の境界の 1 か所 — 送り方は HttpRequest の effect 1 つ)。届かなければ Unreachable。"
  (! (exchange-by-effect endpoint operation body waited)))


(defk waited-seconds [ask]
  {:pre [(: ask PublicEffect)] :post [(: % float)] :tags {:context "records" :role "foundation"}}
  "要求 1 つが service の中で待ち得る秒を知るため(変化の待ちは timeout・他は 0)— 要求の時間切れの秒にこれを足し、long-poll の待ちを
   届かない失敗と読まない。"
  (match ask
    (WatchChanges :timeout timeout) (float timeout)
    (WatchEvents :timeout timeout) (float timeout)
    _ 0.0))


(defk zero-client-metrics [endpoint]
  {:pre [(: endpoint RecordsEndpoint)] :post [(: % None)]}
  "client の計器の閉じた系列(wire の CLIENT-ANSWER-METRICS)を全部 0 で置くため — 書き手の job が起動の時に 1 回呼び、読み手が「無い」と
   「0」を区別しなくて済むようにする(service の口の起動の 0 と同じ)。計器の無い endpoint では何もしない。"
  (when (is-not endpoint.meter None)
    (for [name CLIENT-ANSWER-METRICS]
      (<- (with-handlers [endpoint.meter] (CountMetric name 0.0)))))
  None)


(defk counted-reply [endpoint operation reply]
  {:pre [(: endpoint RecordsEndpoint) (: operation str) (: reply (| RawReply Unreachable))] :post [(: % None)]}
  "送った要求 1 つの結果(届かない・答えの status)を endpoint の計器に 1 つ数えるため(記録の service に届かなかった要求は service の
   計器に出ないので、client の側でだけ数えられる — file の頭の註)。数えるのは答えを値や例外にする前の 1 か所で、JSON で
   ない本文の断りも同じ所を通る。計器の無い endpoint では何もしない。"
  (when (is-not endpoint.meter None)
    (val outcome (if (isinstance reply Unreachable) CLIENT-UNREACHABLE (! (client-status-outcome reply.status))))
    (<- name str (client-answer-metric operation outcome))
    (<- (with-handlers [endpoint.meter] (CountMetric name))))
  None)


(defk call-service [endpoint ask]
  {:pre [(: endpoint RecordsEndpoint) (: ask PublicEffect)] :post [(: % (| WireAnswer Unreachable))]}
  "公開 effect(ask)1 つを service へ撃ち、答えの値にする(status の写し方は file の頭の表)。"
  (<- request (encode-request ask))
  (<- waited float (waited-seconds ask))
  (<- reply (exchange endpoint request.operation request.body waited))
  (<- (counted-reply endpoint request.operation reply))
  (when (isinstance reply Unreachable) (return reply))
  (<- body (reply-json request.operation reply))
  (when (= reply.status 200)
    (return (! (decode-answer request.operation body))))
  (<- refusal (refusal-from body))
  (match refusal.error
    "store-unavailable" (Unreachable refusal.reason)
    "not-found" (raise (! (undeclared-refusal ask refusal.reason)))
    _ (raise (WireError (.format "{} が {} で断られた: {} {}" request.operation reply.status refusal.error refusal.reason)))))


(defrecord RequestPatience
  "HTTP の client が、要求 1 つが置き場に届かない時に置き場の戻りを待つ時間(ReadRequestPatience の答え — #3557): seconds = 最初に届かなかった
   時から数えて待つ秒。過ぎても戻らなければ client は待った秒を名指した Unreachable を返す。0 = 待たない(最初の Unreachable をそのまま返す —
   名のある答え手 records-unwaited)。値は組み立てが 1 か所で選ぶ(この module は既定を持たない)。合図の源が止まりに耐える時間は別の値
   SignalSourcePatience。"
  {:tags {:context "records" :role "type"}
   :check [(and (isinstance seconds (| int float)) (not (isinstance seconds bool)) (>= seconds 0))]}
  (#^ float seconds))


(defhandler request-patience-handler [#^ RequestPatience patience]
  "組み立てが選んだ、client の要求を待つ時間を、問い ReadRequestPatience に答えるため(問うのは HTTP の client だけ — client の外側に置く)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: 待つ時間は土台の宣言の値で、組み立ての 1 か所が渡す(Ask で読むと組の内側の設定の読み手に横取りされうる — ReadRequestPatience の註)。
  (ReadRequestPatience []
    (resume patience)))


;; 待たない(0 秒)の名のある答え手 — 止まりを待てない組み立て(coordinator の居ない process)と、待てない呼び(処理ループの中の
;; 読み書き)が、client の外側に置いて名で選ぶ(#3557)。合図の源の耐える時間(ReadSourcePatience)には答えない。
(val records-unwaited (request-patience-handler (RequestPatience :seconds 0.0)))


(defk stall-names [ask]
  {:pre [(: ask (| ReadRow ListRows PutRow PutRows AppendEvent ReadEvents ReadStreamEnd))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "records" :role "foundation"}}
  "要求と答えの要求 ask が触る表と列の名前を知るため(戻りの問い AwaitRecordsBack に渡す — 置き場は名ごとに止まり得る)。"
  (match ask
    (ReadRow :table table) #(table)
    (ListRows :table table) #(table)
    (PutRow :table table) #(table)
    (PutRows :writes writes) (! (first-seen (tuple (gfor write writes write.table))))
    (AppendEvent :stream stream) #(stream)
    (ReadEvents :stream stream) #(stream)
    (ReadStreamEnd :stream stream) #(stream)))


(defk answered-riding-stall [endpoint ask]
  {:pre [(: endpoint RecordsEndpoint) (: ask (| ReadRow ListRows PutRow PutRows AppendEvent ReadEvents ReadStreamEnd))]
   :post [(: % (| WireAnswer Unreachable))] :tags {:context "records" :role "foundation"}}
  "要求と答えの要求 ask 1 つに、置き場の止まりを越えて答えるため(file の頭の註「置き場の止まり」— client の 1 か所)。待つ時間を要求のたびに
   問い、届けばその答え、届かなければ待つ時間(0 秒なら待たない)の残りまで置き場の戻りを待って同じ要求を撃ち直す。越えたら、待った秒と
   上限と最後の届かなさを名指した Unreachable を返す。"
  (<- patience RequestPatience (ReadRequestPatience))
  (<- first (| WireAnswer Unreachable) (call-service endpoint ask))
  (when (or (not (isinstance first Unreachable)) (<= patience.seconds 0))
    (return first))
  (<- names (get tuple #(str ...)) (stall-names ask))
  (<- since datetime (GetTime))
  (var answer first)
  (while (isinstance answer Unreachable)
    (<- now datetime (GetTime))
    (val waited (.total-seconds (- now since)))
    (<- back bool (came-back-within names (- patience.seconds waited)))
    (when (not back)
      (<- ended datetime (GetTime))
      (return (Unreachable (.format "記録の置き場({})が {:g} 秒 待っても戻らない(上限 {:g} 秒): {}"
                                    (.join ", " names) (.total-seconds (- ended since)) patience.seconds answer.detail))))
    (<- again (| WireAnswer Unreachable) (call-service endpoint ask))
    (:= answer again))
  answer)


(defk long-poll [endpoint ask]
  {:pre [(: endpoint RecordsEndpoint) (: ask (| WatchChanges WatchEvents))]
   :post [(: % (| Changes Reset EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "変化の待ち ask(WatchChanges・WatchEvents)に service の long-poll で答えるため(#3074 — file の頭の註): 待ちを WATCH-MAX-SECONDS ごとの
   要求に分けて撃ち、待つのは service の中の置き場の待ち。変化の無い答え(空の Changes・EventsQuiet)は、ask の timeout が残っていれば
   同じ位置(cursor・after)からもう一度撃つ — 眠らない。変化の在る答え・Reset・Unreachable はすぐ返す(届かない時の撃ち直しは呼び手の
   読みが決める — 呼び手が同じ位置から待ち直せば、接続が切れた間の変化も取りこぼさない)。"
  (<- start float (GetMonotonic))
  (while True
    (<- now float (GetMonotonic))
    (val left (max 0.0 (- ask.timeout (- now start))))
    (val wait (min left WATCH-MAX-SECONDS))
    (<- answer (call-service endpoint (replace ask :timeout wait)))
    (val quiet (or (and (isinstance answer Changes) (not answer.items)) (isinstance answer EventsQuiet)))
    ;; この要求が残りの秒を全部待った(wait = left)か、変化の在る答え・Reset・Unreachable なら返す。
    (when (or (not quiet) (= wait left))
      (return answer))))


(defhandler http-records-handler [#^ RecordsEndpoint endpoint]
  ;; 源の工場の問い(ReadSignalSource)には、記録の置き場の変化の待ちの long-poll で合図を発する本番の源 RECORDS-SIGNAL-SOURCE で答える
  ;; (#3127 — 源の WatchChanges・WatchEvents はこの client が答える)。
  (ReadSignalSource []
    (resume RECORDS-SIGNAL-SOURCE))
  (ReadRow [table key]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (ListRows [table where fields cursor limit]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (PutRow [table key value expect]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (PutRows [writes]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    ;; 待ちは service の long-poll(service の中の置き場が待つ — 読み直しを繰り返さない)。
    (<- answer (long-poll endpoint effect))
    (resume answer))
  (WatchEvents [stream after timeout]
    ;; 列の待ちも service の long-poll(wire の watch-events)。
    (<- answer (long-poll endpoint effect))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (ReadStreamEnd [stream]
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer)))


(defhandler http-table-records-handler [#^ RecordsEndpoint endpoint #^ (get frozenset str) served]
  ;; 引数に残す理由: 同じ handler を置き場ごとに別の口と表で 1 つの組に重ねる(Ask では区別できない)。
  ;; 表 served の読み書きだけに答える http-records-handler(他の表と追記の列は外側の handler へ渡す)— 記録が表ごとに別の service に
  ;; 在る時(例: 着地の列の台帳と業務の記録)、置き場ごとの handler を値の列を持たずに重ねるため(内側に表で絞った handler・外側に
  ;; 残りの表の handler)。handler の列を値で持って中で並べ直す振り分けは、組み立てを実行せずに読む道具(doeff-effect-analyzer)が
  ;; 読めない — この形は並びが呼び出しの字面に在るので読める。束の書き(PutRows)と待ち(WatchChanges)は、束の表が全部 served の中の時
  ;; だけ答える(置き場をまたぐ束は 1 つの置き場では書けない — 外側で断られる)。
  (ReadRow [table key]
    :when (in table served)
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (ListRows [table where fields cursor limit]
    :when (in table served)
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (PutRow [table key value expect]
    :when (in table served)
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (PutRows [writes]
    :when (and writes (all (gfor w writes (in w.table served))))
    (<- answer (answered-riding-stall endpoint effect))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    :when (and tables (all (gfor t tables (in t served))))
    (<- answer (long-poll endpoint effect))
    (resume answer)))
