;;; 記録の service の待ち受けの Program と、検の殻(待ち受けを doeff の汎用の HTTP の effect へ移した)。
;;;
;;; process は 1 つの run・1 つの scheduler で動く。要求ごとに run を撃つ形(標準の http.server・要求ごとの thread と 1 本の thread の
;;; 切り替え・時計と scheduler を被せて run する runner・要求ごとに handler の組を借りる lease)は退役した。
;;;
;;;   serve-records        入口の Program(本体): 待ち受けを開き(HttpListen)、結んだ宛先を名乗り(RecordsListening)、表の用意・受けの loop・
;;;                        止めの見張りの task を立てて待つ。形は下の「入口の形」
;;;   answer-arrival       要求 1 つに答える task の本体: 本文を読み(HttpReadBody)→ service.respond → 答えを送る(HttpRespond)
;;;   start-records-server 検の殻: 入口の Program を別の thread の run で回し、結んだ宛先の url と止める close を持つ RunningServer を返す
;;;                        (使い手の検と模擬が使う口 — 置き場は呼び手が渡す)。止めの合図は殻の合図(threading.Event)を
;;;                        shell-control が StopRequested の答えにする。
;;; 本番の土台と env の読みは main.hy。
;;;
;;; 入口の形(他の記録の service の入口と同じ形):
;;;   - 受けの loop(receive-requests)は HttpNextRequest で受け、要求ごとに Spawn した task(answer-arrival)が答える。loop は走り中の要求の
;;;     Task を持ち(request-ledger)、HttpServerClosed の後に Gather で待ってから返る(答え途中の要求を捨てない)
;;;   - 要求の task は例外でも必ず HttpRespond で終わる(try / finally — 答えていなければ 500 internal)。台本の検
;;;     (tests/test_http_entry_program.hy)が「答えの無い札が残らない」を固定する
;;;   - 表の用意は task(prepare-store — serving.prepare の Program が 書き手の名 → handler の関数を返す)。本体が Race(用意 / 受けの loop の
;;;     終わり)で見張る: 用意が落ちれば例外が run を 0 以外で終える(再起動が繋ぎ直す)。止めの合図で loop が先に終われば用意を取り消して
;;;     0 で終わる。用意の間も口は開いていて、/healthz = 200・記録の操作 = 503 store-unavailable(用意の済みは prepared-slot の session の値)
;;;   - 止めの見張り(watch-stop)は StopRequested を問い、合図で HttpShutdown を撃つ
;;;   - GET /readyz(#1479)は置き場を問う: 用意の前 = 503・serving.readiness の問いを READINESS-SECONDS の上限で撃ち、True = 200・
;;;     False か時間切れ = 503 store-unavailable。/healthz は process の生存だけ(liveness が置き場の不調で再起動を繰り返さない)
;;;   - 手入れ(serving.maintenance — 無ければ立てない)は用意の後に :daemon True の task で、Delay で拍を刻む
;;; 表を用意せずに書けない約束(PreparedStore)は、handler の関数を用意の task だけが作ることで守る(用意の前の要求は prepared-slot が
;;; 空なので store-not-prepared が Unreachable で答える)。
;;; 要求の本文の上限は HttpReadBody の max-bytes(読む前に宣言の長さで、宣言の無い本文は流しながら判じる)だけが持つ。
(require doeff-hy.macros [defeffect defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(import sys)
(import atexit)
(import queue)
(import threading)
(import traceback)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [Cancel Gather Race Spawn Task TaskCancelledError Wait scheduled])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress HttpBodyBytes HttpBodyFailed HttpBodyRead HttpBodyTooLarge HttpEvent HttpHeader
                                                HttpListen HttpNextRequest HttpReadBody HttpRequestArrived HttpRespond HttpServerClosed
                                                HttpShutdown])
(import doeff_time [Delay GetMonotonic async-time-handler])
(import doeff_records.values [RecordsSchema Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges AppendEvent ReadEvents ReadStreamEnd])
(import doeff_records.principals [Roster])
(import doeff_records.maintenance [maintenance-loop])
(import doeff_records.service [HttpRequest HttpAnswer RecordsService respond refusal-answer json-answer])
(import doeff_records.wire [ERROR-INTERNAL ERROR-MALFORMED ERROR-STORE-UNAVAILABLE])
(import doeff_records.store_choice [StorePressure PressureUnread])

;; 置き場に届くかを問う口。/healthz は process の生存だけを答え(liveness — 置き場が落ちている間に再起動を
;; 繰り返さない)、/readyz は置き場を問う(readiness)。問いの答えを待つ上限の秒 — 超えたら 503(固まった置き場で口を固めない)。
(val PATH-READYZ "/readyz")
(val READINESS-SECONDS 1.0)

(val MODULE-TAGS {:context "records" :role "entry"})

(val AUTH-HEADER "Authorization")
(val JSON-CONTENT-TYPE "application/json; charset=utf-8")
;; 要求の本文の上限(行の値の上限は表の宣言の size-budget が決める — これは口の読みの上限)。
(val REQUEST-MAX-BYTES (* 16 1024 1024))
;; 本文を持つ要求の method(他の method の本文は読まない — 記録の操作は POST だけ)。
(val BODY-METHODS #("POST" "PUT"))
;; 用意の前の要求への答えの理由(503 store-unavailable の reason)。
(val NOT-PREPARED-REASON "表の用意が済んでいない(起動の途中)")
;; 手入れの係の書き手の名(手入れの effect は書き手の許可を通らない — 行を書かない)。
(val MAINTAINER "records-maintenance")
;; 検の殻: 止めの合図を問い直す間隔・待ち受けが開くのを待つ上限・閉じて run が終わるのを待つ上限の秒と、止めの理由。
(val SHELL-STOP-POLL-SECONDS 0.02)
(val SHELL-OPEN-SECONDS 30.0)
(val SHELL-CLOSE-SECONDS 10.0)
(val SHELL-STOP-REASON "検の殻の close")


;; --- 入口の設定 -------------------------------------------------------------------------------------------------------------------

(defrecord MaintenancePlan
  "手入れの周期の設定: interval-seconds = 拍の秒・keep-seconds = 変更の列に残す秒(maintenance.hy の PruneChanges)。"
  (#^ float interval-seconds)
  (#^ float keep-seconds))


(defrecord RecordsServing
  "入口の Program(serve-records)の設定: address = 待ち受けの宛先・schema = 置き場の宣言・roster = 身元の名簿・prepare = 表を用意して
   書き手の名 → 記録の handler の関数を返す Program(1 度だけ走る)・request-handlers = 要求ごとの答えの外側に被せる handler の列(本番は空・
   検は呼び手の仮想の時計)・max-bytes = 要求の本文の上限・maintenance = 手入れの設定(None = 立てない)・stop-poll-seconds /
   drain-seconds = 止めの見張りの間隔と待ち受けの閉じの流し切りの上限・readiness = () → 置き場に届けば True の Program(/readyz が
   READINESS-SECONDS の上限で撃つ・None = 用意が済めば ready — memory の置き場)・pressure = () → 置き場の詰まりの読み
   (StorePressure | PressureUnread)の Program(/readyz が届いた後に同じ上限の内で撃つ・None = 詰まりの無い置き場 = 0・#1858)。"
  (#^ HttpAddress address)
  (#^ RecordsSchema schema)
  (#^ Roster roster)
  (#^ (| Program EffectBase) prepare)
  (#^ tuple request-handlers)
  (#^ int max-bytes)
  (#^ (| MaintenancePlan None) maintenance)
  (#^ float stop-poll-seconds)
  (#^ float drain-seconds)
  (setv #^ (| Callable None) readiness None)
  (setv #^ (| Callable None) pressure None))


(defrecord ReadinessTimedOut
  "/readyz の問いの時計の答え — 置き場の問いが上限の秒の内に答えなかった印(Race で問いの答えと見分けるため)。"
  (#^ float seconds))


(defrecord StorePrepared
  "表の用意の task の答え(所要の秒)— 本体の Race が用意の終わりと受けの loop の終わりを見分けるため。"
  (#^ float seconds))


(defrecord ServingEnded
  "受けの loop の答え(待ち受けが閉じた理由)— 本体の Race が用意の終わりと見分けるため。"
  (#^ str reason))


;; --- 入口の Program が問う effect -------------------------------------------------------------------------------------------------

(defeffect RecordsListening
  "待ち受けが address を結んだ拍の名乗り(本番の土台は 1 行を印字し、検の殻は結んだ port を受け取る)。答えは None。"
  {:fields [(: address HttpAddress)] :answer None :tags {:context "records" :role "entry"}})

(defeffect TrackRequest
  "受けの loop が Spawn した要求の task を台帳に載せる。答えは None。"
  {:fields [(: ticket str) (: task Task)] :answer None :tags {:context "records" :role "entry"}})

(defeffect SettleRequest
  "要求の task が答え終えた(台帳から外す)。答えは None。"
  {:fields [(: ticket str)] :answer None :tags {:context "records" :role "entry"}})

(defeffect PendingRequests
  "台帳に残る走り中の要求の Task の列を読む。"
  {:fields [] :answer tuple :tags {:context "records" :role "entry"}})

(defeffect KeepPreparedHandlers
  "表の用意が済んだ — 書き手の名 → 記録の handler の関数を置く。答えは None。"
  {:fields [(: handler-for Callable)] :answer None :tags {:context "records" :role "entry"}})

(defeffect PreparedHandlers
  "用意で置いた 書き手の名 → 記録の handler の関数を読む(用意の前は None)。"
  {:fields [] :answer (| Callable None) :tags {:context "records" :role "entry"}})


(defhandler request-ledger
  "走り中の要求の Task を台帳に持ち、受けの loop が待ち受けの閉じの後に待てるようにするため。"
  {:tags {:context "records" :role "entry"}}
  ;; held = 札 → 走り中の要求の Task(session の値)。early = Track より先に答え終えた札(Spawn の直後に子が先に走っても台帳に終わった
  ;; task を残さない)。受けの loop は閉じた後に残りを Gather で待つ。
  (session var held {})
  (session var early (frozenset))
  (TrackRequest [ticket task]
    (if (in ticket early)
        (:= early (- early (frozenset [ticket])))
        (:= held (| held {ticket task})))
    (resume None))
  (SettleRequest [ticket]
    (if (in ticket held)
        (:= held (dfor [k t] (.items held) :if (!= k ticket) k t))
        (:= early (| early (frozenset [ticket]))))
    (resume None))
  (PendingRequests []
    (resume (tuple (.values held)))))


(defhandler prepared-slot
  "表の用意の task が置いた 書き手の名 → 記録の handler の関数を、要求の task が読めるようにするため(用意の前は None)。"
  {:tags {:context "records" :role "entry"}}
  (session var kept None)
  (KeepPreparedHandlers [handler-for]
    (:= kept handler-for)
    (resume None))
  (PreparedHandlers []
    (resume kept)))


(defhandler store-not-prepared
  "用意の前の要求の公開 effect に、置き場に届かない(Unreachable)で答えるため(口は 503 store-unavailable にする)。"
  {:tags {:context "records" :role "entry"}}
  (ReadRow [table key] (resume (Unreachable NOT-PREPARED-REASON)))
  (ListRows [table where fields cursor limit] (resume (Unreachable NOT-PREPARED-REASON)))
  (PutRow [table key value expect] (resume (Unreachable NOT-PREPARED-REASON)))
  (PutRows [writes] (resume (Unreachable NOT-PREPARED-REASON)))
  (WatchChanges [tables cursor timeout limit] (resume (Unreachable NOT-PREPARED-REASON)))
  (AppendEvent [stream idempotency-key body] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadEvents [stream after limit] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadStreamEnd [stream] (resume (Unreachable NOT-PREPARED-REASON))))


;; --- 1 要求の答え -------------------------------------------------------------------------------------------------------------------

(defk header-value [headers name]
  {:pre [(: headers tuple) (: name str)] :post [(: % (| str None))] :tags {:context "records" :role "entry"}}
  "要求の頭から名 name の最初の値を読むため(名の大小を問わない — HTTP の頭の名は大小を区別しない)。"
  (val wanted (.lower name))
  (for [header headers]
    (when (= (.lower header.name) wanted)
      (return header.value)))
  None)


(defk send-answer [ticket answer]
  {:pre [(: ticket str) (: answer HttpAnswer)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "答え(status と JSON の本文)を札の要求へ送るため。"
  (val data (.encode answer.body "utf-8"))
  (<- (HttpRespond :ticket ticket :status answer.status
                   :headers #((HttpHeader :name "Content-Type" :value JSON-CONTENT-TYPE)
                              (HttpHeader :name "Content-Length" :value (str (len data))))
                   :body (HttpBodyBytes :data data)))
  None)


(defk request-body [arrival max-bytes]
  {:pre [(: arrival HttpRequestArrived) (: max-bytes int)] :post [(: % (| bytes HttpAnswer))] :tags {:context "records" :role "entry"}}
  "要求の本文を読むため(本文を持つ method だけ・上限を超えれば読まずに 400 malformed・読めなければ 400 malformed)。答え = 本文か、
   入口が断る答え。上限を超えた札は、答え手が答えを送った後に接続を閉じる(残りの本文を次の要求として読まない)。"
  (when (not-in arrival.method BODY-METHODS)
    (return b""))
  (<- outcome (| HttpBodyRead HttpBodyTooLarge HttpBodyFailed) (HttpReadBody :ticket arrival.ticket :max-bytes max-bytes))
  (match outcome
    (HttpBodyRead :data data) data
    (HttpBodyTooLarge) (! (refusal-answer ERROR-MALFORMED (.format "本文が {} byte を超える" max-bytes)))
    (HttpBodyFailed :reason reason) (! (refusal-answer ERROR-MALFORMED (.format "本文を読めない: {}" reason)))))


(defk readiness-timer [seconds]
  {:pre [(: seconds float)] :post [(: % ReadinessTimedOut)] :tags {:context "records" :role "entry"}}
  "/readyz の問いの上限を刻むため(seconds 秒眠って印を返す — Race の片方)。"
  (<- (Delay seconds))
  (ReadinessTimedOut :seconds seconds))


(defk store-pressure [serving]
  {:pre [(: serving RecordsServing)] :post [(: % (| StorePressure PressureUnread))] :tags {:context "records" :role "entry"}}
  "置き場の詰まりを読むため(#1858): 問いの無い置き場(memory)は錠も transaction も持たないので 0・問いが在れば撃った答え。"
  (if (is serving.pressure None)
      (StorePressure :lock-waiters 0 :idle-in-transaction-max-seconds 0.0)
      (! (serving.pressure))))


(defk store-probe [serving]
  {:pre [(: serving RecordsServing)] :post [(: % (| StorePressure PressureUnread bool))] :tags {:context "records" :role "entry"}}
  "/readyz の問いの本体: 置き場に届くかを問い(届かなければ False)、届けば詰まりを読む。1 つの task で撃つので、READINESS-SECONDS の
   上限は届くかの問いと詰まりの読みの両方に掛かる。"
  (<- reachable bool (serving.readiness))
  (if reachable (! (store-pressure serving)) False))


(defk ready-answer [pressure]
  {:pre [(: pressure (| StorePressure PressureUnread))] :post [(: % HttpAnswer)] :tags {:context "records" :role "entry"}}
  "置き場に届いた時の 200 の本文を組むため: 錠を待つ本数と idle in transaction の最長の秒を載せる(#1858 — 錠の詰まりで 503 になる前に
   見張りが数で気づくため)。読めなかった時は数を名乗らず理由を載せる(0 と読み違えない)。"
  (match pressure
    (StorePressure :lock-waiters waiters :idle-in-transaction-max-seconds idle)
      (! (json-answer 200 {"status" "ready" "lockWaiters" waiters "idleInTransactionMaxSeconds" idle}))
    (PressureUnread :reason reason)
      (! (json-answer 200 {"status" "ready" "pressureUnread" reason}))))


(defk readiness-answer [serving prepared]
  {:pre [(: serving RecordsServing) (: prepared (| Callable None))] :post [(: % HttpAnswer)] :tags {:context "records" :role "entry"}}
  "/readyz に答えるため: 用意の前は 503・問いが無ければ 200(詰まりは store-pressure の読み)・問いを READINESS-SECONDS の上限で撃ち、
   届けば 200(錠を待つ本数と idle in transaction の最長の秒を載せる・#1858)・False か時間切れなら 503 store-unavailable(問いの task は
   取り消す — 固まった置き場の問いを待ち続けない)。"
  (when (is prepared None)
    (return (! (refusal-answer ERROR-STORE-UNAVAILABLE "表の用意が済んでいない"))))
  (when (is serving.readiness None)
    (return (! (ready-answer (! (store-pressure serving))))))
  (<- probe Task (Spawn (store-probe serving)))
  (<- timer Task (Spawn (readiness-timer READINESS-SECONDS)))
  (<- first (| StorePressure PressureUnread bool ReadinessTimedOut) (Race probe timer))
  (<- (Cancel probe))
  (<- (Cancel timer))
  (match first
    (ReadinessTimedOut :seconds seconds)
      (! (refusal-answer ERROR-STORE-UNAVAILABLE (.format "置き場が {} 秒の内に答えない" seconds)))
    (StorePressure) (! (ready-answer first))
    (PressureUnread) (! (ready-answer first))
    _ (! (refusal-answer ERROR-STORE-UNAVAILABLE "置き場に届かない"))))


(defk answer-with [serving request]
  {:pre [(: serving RecordsServing) (: request HttpRequest)] :post [(: % HttpAnswer)] :tags {:context "records" :role "entry"}}
  "要求 1 つを service.respond で答えるため: 用意が済んでいれば置き場の handler、済んでいなければ store-not-prepared の下で撃つ。
   要求ごとの外側の handler(serving.request-handlers — 検の仮想の時計)を被せる。GET /readyz は置き場を問う(readiness-answer)。"
  (<- prepared (| Callable None) (PreparedHandlers))
  (when (and (= request.method "GET") (= request.path PATH-READYZ))
    (return (! (with-handlers [#* serving.request-handlers] (readiness-answer serving prepared)))))
  (val handler-for (if (is prepared None) (fn [writer] store-not-prepared) prepared))
  (<- answer (with-handlers [#* serving.request-handlers]
                            (respond (RecordsService serving.schema serving.roster handler-for) request)))
  answer)


(defk answer-arrival [arrival serving]
  {:pre [(: arrival HttpRequestArrived) (: serving RecordsServing)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "要求 1 つに答える task の本体: 本文を読み → answer-with → 答えを送る。例外でも必ず答える(答えていなければ 500 internal — 答えの無い
   札を残さない)。実装の誤りの追跡は標準の誤りへ残す(黙って捨てない)。終われば台帳から外す。"
  (var answered False)
  (try
    (<- body (| bytes HttpAnswer) (request-body arrival serving.max-bytes))
    (if (isinstance body HttpAnswer)
        (<- (send-answer arrival.ticket body))
        (do (<- answer HttpAnswer (answer-with serving (HttpRequest arrival.method (get (.split arrival.target "?" 1) 0)
                                                                    (! (header-value arrival.headers AUTH-HEADER)) body)))
            (<- (send-answer arrival.ticket answer))))
    (:= answered True)
    (except [e Exception]
      (print (.format "記録の service: {} {} の答えの途中で落ちた: {}: {}" arrival.method arrival.target (. (type e) __name__) e)
             :file sys.stderr :flush True)
      (traceback.print-exc))
    (finally
      (when (not answered)
        (<- internal (refusal-answer ERROR-INTERNAL "要求の task が答える前に落ちた"))
        (<- (send-answer arrival.ticket internal)))
      (<- (SettleRequest arrival.ticket))))
  None)


;; --- 受けの loop・止めの見張り・用意と手入れの task ----------------------------------------------------------------------------------

(defk receive-requests [serving]
  {:pre [(: serving RecordsServing)] :post [(: % ServingEnded)] :tags {:context "records" :role "entry"}}
  "待ち受けの出来事を受け、要求ごとに task を立てて台帳に載せる。待ち受けが閉じたら、走り中の要求を Gather で待ってから返るため。"
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (match event
      (HttpServerClosed :reason reason)
        (do (<- pending tuple (PendingRequests))
            (when pending
              (<- (Gather #* pending)))
            (return (ServingEnded :reason reason)))
      (HttpRequestArrived :ticket ticket)
        (do (<- task Task (Spawn (answer-arrival event serving)))
            (<- (TrackRequest :ticket ticket :task task)))
      _ (raise (TypeError (+ "記録の service の待ち受けに ws の出来事が来た: " (repr event)))))))


(defk watch-stop [poll-seconds drain-seconds]
  {:pre [(: poll-seconds float) (: drain-seconds float)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "止めの合図を poll-seconds ごとに問い、合図を見たら待ち受けを閉じるため(受けの loop が HttpServerClosed を受けて終わる)。"
  (while True
    (<- reason (| str None) (StopRequested))
    (when (is-not reason None)
      (<- (HttpShutdown :reason reason :drain-seconds drain-seconds))
      (return None))
    (<- (Delay poll-seconds))))


(defk prepare-store [prepare]
  {:pre [(: prepare (| Program EffectBase))] :post [(: % StorePrepared)] :tags {:context "records" :role "entry"}}
  ;; 表の用意(起動時の 1 点・冪等)を 1 回撃ち、書き手の名 → handler の関数を prepared-slot に置いて所要を名乗る。口は先に開いていて、
  ;; /healthz は process の生存だけを答え、記録の操作は用意が済むまで 503。失敗は名乗って例外のまま上げる(本体の Race が受けて run を
  ;; 0 以外で終える — 半端に立ったまま答え続けない・再起動が繋ぎ直す)。
  (<- started float (GetMonotonic))
  (try
    (<- handler-for Callable prepare)
    (except [e Exception]
      (<- failed-at float (GetMonotonic))
      (print (.format "記録の service: 表の用意が {} 秒で落ちた: {}: {}" (round (- failed-at started) 1) (. (type e) __name__) e)
             :file sys.stderr :flush True)
      (raise)))
  (<- (KeepPreparedHandlers :handler-for handler-for))
  (<- done-at float (GetMonotonic))
  (StorePrepared :seconds (- done-at started)))


(defk maintain [handler-for plan]
  {:pre [(: handler-for Callable) (: plan MaintenancePlan)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "手入れの係: 止めるまで maintenance-loop を手入れの係の書き手の handler の下で回すため(置き場に届かない回は Unreachable の値で次の回へ)。"
  (<- (with-handlers [(handler-for MAINTAINER)] (maintenance-loop plan.interval-seconds plan.keep-seconds None)))
  None)


(defk serve-records [serving]
  {:pre [(: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "入口の Program(頭の註の「入口の形」): 待ち受けを開いて名乗り、用意・受けの loop・止めの見張りの task を立て、用意の失敗か待ち受けの
   閉じまで見張って、process の終わりの code(0)を返すため。用意の失敗は例外のまま上げる。"
  (<- code int (with-handlers [prepared-slot] (serving-body serving)))
  code)


(defk serving-body [serving]
  {:pre [(: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "serve-records の本体(prepared-slot の内側 — 用意の task と要求の task が同じ session の値を読む)。"
  (<- bound HttpAddress (HttpListen :address serving.address))
  (<- (RecordsListening :address bound))
  ;; 用意を受けの loop より先に立てる(同じ拍に並んだ時に用意が先に走る)。
  (<- preparing Task (Spawn (prepare-store serving.prepare)))
  (<- serving-task Task (Spawn (with-handlers [request-ledger] (receive-requests serving))))
  (<- watcher Task (Spawn (watch-stop serving.stop-poll-seconds serving.drain-seconds) :daemon True))
  (<- first (| StorePrepared ServingEnded) (Race preparing serving-task))
  (match first
    (StorePrepared :seconds seconds)
      (do (print (.format "記録の service: 表の用意が {} 秒で済んだ — {}:{} で答える" (round seconds 1) bound.host bound.port)
                 :file sys.stderr :flush True)
          (when (is-not serving.maintenance None)
            (<- handler-for Callable (PreparedHandlers))
            (<- (Spawn (maintain handler-for serving.maintenance) :daemon True)))
          (<- (Wait serving-task)))
    (ServingEnded) (do (<- (Cancel preparing))
                       ;; 取り消した用意の終わりを待つ(走りかけの仕事を置き去りにして root が返らない — #501)。
                       (try
                         (<- (Wait preparing))
                         (except [TaskCancelledError] None))))
  (<- (Cancel watcher))
  0)


;; --- 検の殻 --------------------------------------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] RecordsServerConfig []
  "検の殻の口 1 つの組み立て: schema = 置き場の宣言 / roster = 身元の名簿 / handler-for = 書き手の名 → 記録の handler(用意し終えた置き場の上) /
   request-handlers = 要求ごとの答えの外側に被せる handler の列(呼び手の仮想の時計・SQL の答え手)/ host・port(0 = 空いている port)。"
  (#^ RecordsSchema schema)
  (#^ Roster roster)
  (#^ Callable handler-for)
  (setv #^ tuple request-handlers #())
  (setv #^ str host "127.0.0.1")
  (setv #^ int port 0))


(defclass [(dataclass :frozen True)] RunningServer []
  "開いた HTTP の口: url = 基の URL(http://host:port)/ stop = 殻の止めの口 / close() = 止めて port を返す(受けている要求を答え終えてから)。"
  (#^ str url)
  (#^ Callable stop)
  (deff close [self]  ; defk にできない: 検の殻を閉じる同期の口(Program の外の検の try / finally が呼ぶ)
    {:pre [(: self RunningServer)] :post [(: % None)] :tags {:context "records" :role "entry"}}
    "口を止める(止めの合図を立て、入口の run が待ち受けを閉じて終わるのを待つ)。2 度目は何もしない。"
    (self.stop)
    None))


(defhandler shell-control [#^ Callable listening #^ Callable stopping]
  "検の殻の外から、入口の Program の名乗り(RecordsListening)と止めの合図(StopRequested)に答えるため(#880 F3)。"
  {:tags {:context "records" :role "entry"}}
  ;; 引数に残す理由: listening は殻が結んだ宛先を受け取る口(thread の外の queue の put)、stopping は殻の止めの合図を読む口(合図が
  ;; 立っていれば理由・無ければ None)。どちらも thread をまたぐ殻の物で、Ask で読む設定ではない。
  (RecordsListening [address]
    (listening address)
    (resume None))
  (StopRequested []
    (resume (stopping))))


(defk ready-handlers [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % Callable)] :tags {:context "records" :role "entry"}}
  "検の殻の用意: 呼び手が用意し終えた置き場の 書き手の名 → handler をそのまま返すため(用意の I/O は無い)。"
  handler-for)


(deff shell-run [program failures]  ; defk にできない: 検の殻の thread の target(framework の入口 — 別の run は Program の中から立てられない)
  {:pre [(: program (| Program EffectBase)) (: failures queue.Queue)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "入口の Program を scheduler の下で 1 回走らせ、落ちたら殻の待ち手へ例外を渡すため(待ち受けが開く前に落ちても殻が 30 秒待たない)。"
  (try
    (run (scheduled program))
    (except [e BaseException]
      (.put failures e)))
  None)


(deff start-records-server [config]  ; defk にできない: 入口の run を別の thread で立てる検の殻(framework の入口 — 返す物が開いた口)
  {:pre [(: config RecordsServerConfig)] :post [(: % RunningServer)] :tags {:context "records" :role "entry"}}
  "入口の Program(serve-records)を別の thread の run で回して口を開き、RunningServer を返す。土台は本番と同じ待ち受け・時計・Await の橋
   (aiohttp-http-server・async-time-handler・await-handler)で、止めの合図と名乗りだけを shell-control が答える。開けなければ例外を上げる。"
  (setv opened (queue.Queue)
        ended (threading.Event))
  (setv serving (RecordsServing :address (HttpAddress :host config.host :port config.port) :schema config.schema :roster config.roster
                                :prepare (ready-handlers config.handler-for) :request-handlers (tuple config.request-handlers)
                                :max-bytes REQUEST-MAX-BYTES :maintenance None :stop-poll-seconds SHELL-STOP-POLL-SECONDS
                                :drain-seconds 0.0))
  (setv program (with-handlers [(await-handler) (async-time-handler) (state) aiohttp-http-server
                                (shell-control opened.put (fn [] (if (.is-set ended) SHELL-STOP-REASON None)))]
                               (serve-records serving)))
  (setv thread (threading.Thread :target (fn [] (shell-run program opened)) :daemon True :name "doeff-records-http"))
  (.start thread)
  (setv bound (.get opened :timeout SHELL-OPEN-SECONDS))
  (when (isinstance bound BaseException)
    (raise bound))
  ;; 殻を閉じる同期の口(検の finally と atexit が呼ぶ — Program の外)。
  (setv stop (fn [] (.set ended) (.join thread :timeout SHELL-CLOSE-SECONDS)))
  ;; 登録は待ち受けが開いた後: atexit は後に登録した物から走るので、doeff の Await の橋の閉じより先に入口の run を終えられる。
  (atexit.register stop)
  (RunningServer (.format "http://{}:{}" bound.host bound.port) stop))
