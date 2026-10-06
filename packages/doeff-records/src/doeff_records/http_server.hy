;;; 記録の service の待ち受けの Program と、検の殻(待ち受けを doeff の汎用の HTTP の effect へ移した)。
;;;
;;; process は 1 つの run・1 つの scheduler で動く。要求ごとに run を撃つ形(標準の http.server・要求ごとの thread と 1 本の thread の
;;; 切り替え・時計と scheduler を被せて run する runner・要求ごとに handler の組を借りる lease)は退役した。
;;;
;;;   serve-records        入口の Program(本体): 待ち受けを開き(HttpListen)、結んだ宛先を名乗り(RecordsListening)、表の用意・受けの loop・
;;;                        止めの見張りの task を立てて待つ。表の用意が済んだ拍に告知する(RecordsPrepared)。形は下の「入口の形」
;;;   answer-arrival       要求 1 つに答える task の本体: 本文を読み(HttpReadBody)→ service.respond → 答えを送る(HttpRespond)
;;;   store-reach          置き場に届くかの公開の判断(/readyz と同じ判断 — 届く StoreReachable・届かない StoreUnreachable・上限の秒の内に
;;;                        答えない StoreSilent の閉じた型 StoreReach を返す)。使い手の土台が準備の報告に同じ判断を撃つ口
;;;   start-records-server 検の殻: 入口の Program を別の thread の run で回し、表の用意の告知を待って、結んだ宛先の url と止める close を持つ
;;;                        RunningServer を返す(使い手の検と模擬が使う口 — 置き場は呼び手が渡す)。止めの合図は殻の合図(threading.Event)を
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
;;;   - 用意の告知(#3733): 用意の task は 書き手の名 → handler の関数を prepared-slot に置いた後に RecordsPrepared(結んだ宛先・所要の秒)を
;;;     1 度だけ出す — 告知が出たなら記録の操作に答えられる。告知は用意の task の終わりで、止めの道(watch-stop → CloseWaits → HttpShutdown)の
;;;     外(止めの合図で受けの loop が先に終われば、告知の前でも途中でも用意の task ごと取り消す)。答え手: 本番の土台は 1 行を印字し
;;;     (main.hy の printed-listening)、使い手の土台は準備の報告を立て、検の殻は開いた口を返す。答え手が落ちれば用意の失敗と同じく run を
;;;     例外で終える
;;;   - 止めの見張り(watch-stop)は StopRequested を問い、合図で保留中の置き場の待ちを止めの印で起こし(CloseWaits)、HttpShutdown を撃つ。
;;;     止めの印(#3713): 入口の session(waits-closing)が印を持ち、要求ごとの記録の handler の中の待ち(WaitWithin — 変化の待ちの long-poll の
;;;     呼び鈴の待ち)を切り手(closing-cuts-waits)が呼び鈴・期限・止めの門の早い方で起こす。印を受けた待ち手は読み直さずに静かな答え
;;;     (空の changes・eventsQuiet — 位置は頼んだ位置のまま)で返り、client は今の取り決めのまま次の置き場へ撃ち直す。止めた後に来た待ちは
;;;     待たずに印で答える。置き場(memory・PostgreSQL)の呼び鈴そのものは鳴らさない — 待ち手が外す(置き場は他の入口と共有してよい)
;;;   - GET /readyz(#1479)は置き場を問う: 用意の前 = 503・置き場に届くかの判断 store-reach(serving.readiness の問いを READINESS-SECONDS の
;;;     上限で撃つ)が届く = 200・届かないか上限の内に答えない = 503 store-unavailable。/healthz は process の生存だけ(liveness が置き場の
;;;     不調で再起動を繰り返さない)
;;;   - 手入れ(serving.maintenance — 無ければ立てない)は用意の後に :daemon True の task で、Delay で拍を刻む
;;;   - 計器(#2709): 要求の task は答えを送った直後に、要求の種(wire の request-kind)と実際に送った答えの status の counter
;;;     records_requests_<種>_<status> を doeff の計器の effect CountMetric で 1 つ数える(本文の断りの 400・落ちた時の 500・送りが落ちて
;;;     送り直した 500 も同じ 1 か所 — 答えを受け取った呼び手が次に読む /metrics には、その答えがもう入っている)。答え手は serving.meter(None = doeff の
;;;     memory-meter-handler — 検は壊した計器を差す)。名の綴りと閉じた系列は wire の ANSWER-METRICS で、起動の時に全部を 0 で置く。GET /metrics は
;;;     身元を問わず(/readyz と同じく身元を引く前に答える)、ReadMeter の断面を doeff の render-prometheus で Prometheus の text に描く
;;   - GET /served(#2742)は、動いている process が走っている木の commit と世代(serving.served — 使い手の入口が渡す・無ければ null)と、
;;     配っている表の宣言の要約(schema_digest.schema-digests)を答える。身元も表の用意も置き場も問わない(置き場に届かない間も使い手が
;;     動いている版を読める)。計器では種 other に数える
;;;   - 要求ごとの計時(#3688): 要求の task は、task の始まり・本文の読み終わり・答えの決まり・送り終わりに札ごとの刻(StampRequest)を
;;;     打つ。記録の操作の handler は包み(stamped-handling)で被せ、handler の入りと出と、handler の中の待ち(doeff-time の WaitWithin —
;;;     変化の待ちの long-poll)の入りと起きにも刻を打つ。刻は受けの loop に request-ledger と並べて被せた控え(request-stamps)が、自分の
;;;     外側の時計(土台の GetMonotonic — 要求ごとの外側の handler serving.request-handlers の時計ではない)で読んで札ごとに控えるので、受けた
;;;     刻(received-at)と同じ物差しにそろう。送った後に札の刻を控えから外し(TakeMarks)、記録の操作の要求だけ、区間ごとの秒
;;;     (request_timing.hy — queue・body・decode・handler・wait・encode・send・total・woke)を records_stage_<操作>_<区間> の観測の列にし、
;;;     doeff の計器の効果 ObserveSecondsBatch 1 回で積む(区間ごとに ObserveSeconds を撃つと 1 要求で 7〜9 回 — 書きのたびに起きた待ちの
;;;     要求が波で並ぶ時、その 1 本ずつの後ろに観測の往復が乗っていた・#3688)。GET /metrics が区間ごとの秒の和と数を描く。log には書かない
;;;     (本番の書きと待ちの要求の数で行が積もり、log の file を切り替える仕組みが無いため)
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
(import doeff_core_effects.scheduler [Cancel CompletePromise CreatePromise Gather PRIORITY-IDLE Promise Race Spawn Task TaskCancelledError
                                      Wait scheduled])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress HttpBodyBytes HttpBodyFailed HttpBodyRead HttpBodyTooLarge HttpEvent HttpHeader
                                                HttpListen HttpNextRequest HttpReadBody HttpRequestArrived HttpRespond HttpServerClosed
                                                HttpShutdown])
(import doeff_time [Delay GetMonotonic WaitWithin async-time-handler])
(import doeff_time.effects.time [WaitWithinEffect])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [RecordsSchema Unreachable WaitsClosed])
(import doeff_records.schema_digest [schema-digests])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd
                               ReadEventByKey])
(import doeff_records.event_source [BodyWrapper])
(import doeff_records.maintenance [maintenance-loop])
(import doeff_records.request_timing [RequestMark MarkAt StageSeconds STAGE-METRIC-HELPS request-stages stage-metric])
(import doeff_records.service [HttpRequest HttpAnswer RecordsService records-service respond refusal-answer json-answer])
(import doeff_records.wire [ERROR-INTERNAL ERROR-MALFORMED ERROR-STORE-UNAVAILABLE ANSWER-METRICS ANSWER-METRIC-HELPS answer-metric
                            operation-of])
(import doeff_records.wire [WRITER-HEADER])
(import doeff_records.store_choice [StorePressure PressureUnread])
(import doeff_core_effects.meter_effects [CountMetric MeterSettings MeterSnapshot ObserveSecondsBatch ReadMeter SecondsObservation])
(import doeff_core_effects.memory_meter [memory-meter-handler])
(import doeff_core_effects.meter_prometheus [render-prometheus CONTENT-TYPE :as METRICS-CONTENT-TYPE])

;; 置き場に届くかを問う口。/healthz は process の生存だけを答え(liveness — 置き場が落ちている間に再起動を
;; 繰り返さない)、/readyz は置き場を問う(readiness)。問いの答えを待つ上限の秒 — 超えたら 503(固まった置き場で口を固めない)。
(val PATH-READYZ "/readyz")
(val READINESS-SECONDS 1.0)
;; 計器の口(身元を問わない — 数だけを答え、行の中身を載せない・#2709)。
(val PATH-METRICS "/metrics")
;; 動いている process の木と配っている表の要約の口(身元を問わず、表の用意と置き場に頼らない — #2742)。
(val PATH-SERVED "/served")
;; GET /metrics の # HELP の説明(要求の数の系列 + 区間の秒の系列 — #3688)。
(val METRIC-HELPS (FrozenMap (+ (tuple (.items ANSWER-METRIC-HELPS)) (tuple (.items STAGE-METRIC-HELPS)))))

(val MODULE-TAGS {:context "records" :role "entry"})

(val JSON-CONTENT-TYPE "application/json; charset=utf-8")
;; 要求の本文の上限(行の値の上限は表の宣言の size-budget が決める — これは口の読みの上限)。
(val REQUEST-MAX-BYTES (* 16 1024 1024))
;; 本文を持つ要求の method(他の method の本文は読まない — 記録の操作は POST だけ)。
(val BODY-METHODS #("POST" "PUT"))
;; 用意の前の要求への答えの理由(503 store-unavailable の reason)。
(val NOT-PREPARED-REASON "表の用意が済んでいない(起動の途中)")
;; 手入れの係の書き手の名(手入れの effect は書き手の許可を通らない — 行を書かない)。
(val MAINTAINER "records-maintenance")
;; 検の殻: 止めの合図を問い直す間隔・待ち受けが開いて表の用意を告げるのを待つ上限・閉じて run が終わるのを待つ上限の秒と、止めの理由。
(val SHELL-STOP-POLL-SECONDS 0.02)
(val SHELL-OPEN-SECONDS 30.0)
(val SHELL-CLOSE-SECONDS 10.0)
(val SHELL-STOP-REASON "検の殻の close")


;; --- 入口の設定 -------------------------------------------------------------------------------------------------------------------

(defrecord MaintenancePlan
  "手入れの周期の設定: interval-seconds = 拍の秒・keep-seconds = 変更の列に残す秒(maintenance.hy の PruneChanges)。"
  (#^ float interval-seconds)
  (#^ float keep-seconds))


(defrecord RepoCommit
  "走っている木の repo 1 つ: repo = repo の名(使い手の repo と doeff など)・commit = commit の sha。"
  (#^ str repo)
  (#^ str commit))


(defrecord ServedBuild
  "記録の service の process が走っている木と世代(GET /served が答える・#2742): commits = 走っている木の repo ごとの commit(知らなければ
   None)・instance = process の世代(入れ替えの間に旧と新が交互に答えるのを読み手が見分けるため・知らなければ None)。使い手の入口が、
   土台の実行の文脈(doeff-cluster の job なら宣言の revision・実行環境の宣言・世代)から読んで渡す。"
  (setv #^ (| (get tuple #(RepoCommit ...)) None) commits None)
  (setv #^ (| str None) instance None))


(defrecord RecordsServing
  "入口の Program(serve-records)の設定: address = 待ち受けの宛先・schema = 置き場の宣言・prepare = 表を用意して
   書き手の名 → 記録の handler の関数を返す Program(1 度だけ走る)・request-handlers = 要求ごとの答えの外側に被せる handler の列(本番は空・
   検は呼び手の仮想の時計)・max-bytes = 要求の本文の上限・maintenance = 手入れの設定(None = 立てない)・stop-poll-seconds /
   drain-seconds = 止めの見張りの間隔と待ち受けの閉じの流し切りの上限・readiness = () → 置き場に届けば True の Program(置き場に届くかの
   判断 store-reach が READINESS-SECONDS の上限で撃つ・None = 問わない — 用意が済めば ready)・pressure = () → 置き場の詰まりの読み
   (StorePressure | PressureUnread)の Program(store-reach が届いた後に同じ上限の内で撃つ・None = 詰まりの無い置き場 = 0・#1858)・
   meter = 計器の handler(doeff の CountMetric と ReadMeter に答える・None = memory-meter-handler — 検が壊した計器を差す口・#2709)・
   served = 走っている木と世代(GET /served が答える・None = 知らない — 答えの commits と instance は null・#2742)。"
  (#^ HttpAddress address)
  (#^ RecordsSchema schema)
  (#^ (| Program EffectBase) prepare)
  (#^ tuple request-handlers)
  (#^ int max-bytes)
  (#^ (| MaintenancePlan None) maintenance)
  (#^ float stop-poll-seconds)
  (#^ float drain-seconds)
  (setv #^ (| Callable None) readiness None)
  (setv #^ (| Callable None) pressure None)
  (setv #^ (| (get Callable #(... object)) None) meter None)
  (setv #^ (| ServedBuild None) served None))


(defrecord TextAnswer
  "JSON でない本文の答え(/metrics の Prometheus の text): status・content-type = Content-Type の値・body = 本文。記録の操作と断りの
   答えは service の HttpAnswer(JSON)。"
  (#^ int status)
  (#^ str content-type)
  (#^ str body))


;; 置き場に届くかの判断(store-reach)の答えの閉じた型 StoreReach = 届く | 届かない | 上限の秒の内に答えない。
(defrecord StoreReachable
  "置き場に届いた(store-reach の答え): pressure = 詰まりの読み(錠を待つ本数と idle in transaction の最長の秒・読めなければ理由 — #1858)。"
  (#^ (| StorePressure PressureUnread) pressure))


(defrecord StoreUnreachable
  "置き場の問い(serving.readiness)が届かないと答えた(store-reach の答え): reason = 理由の 1 行。"
  (#^ str reason))


(defrecord StoreSilent
  "置き場の問いが上限の秒の内に答えなかった(store-reach の答え — 上限の時計 readiness-timer の答えでもあり、Race で問いの答えと
   見分ける): seconds = 待った上限の秒。"
  (#^ float seconds))


(val StoreReach (| StoreReachable StoreUnreachable StoreSilent))


(defrecord StorePrepared
  "表の用意の task の答え(所要の秒)— 本体の Race が用意の終わりと受けの loop の終わりを見分けるため。"
  (#^ float seconds))


(defrecord ServingEnded
  "受けの loop の答え(待ち受けが閉じた理由)— 本体の Race が用意の終わりと見分けるため。"
  (#^ str reason))


;; --- 入口の Program が問う effect -------------------------------------------------------------------------------------------------

(defeffect RecordsListening
  "待ち受けが address を結んだ拍の名乗り(本番の土台は 1 行を印字する — 表の用意はまだ済んでいない)。答えは None。"
  {:fields [(: address HttpAddress)] :answer None :tags {:context "records" :role "entry"}})

(defeffect RecordsPrepared
  "表の用意が済んだ拍の告知(書き手の名 → 記録の handler の関数を置いた後 — 告知が出たなら記録の操作に答えられる): address = 結んだ宛先・
   seconds = 用意の所要の秒。本番の土台は 1 行を印字し、使い手の土台は準備の報告を立て、検の殻は開いた口を返す。答えは None。"
  {:fields [(: address HttpAddress) (: seconds float)] :answer None :tags {:context "records" :role "entry"}})

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

(defeffect StampRequest
  "札 ticket の要求に刻 mark を打つ(刻の控え request-stamps が、今の単調時計の秒と組にして控える — 頭の註の要求ごとの計時)。答えは None。"
  {:fields [(: ticket str) (: mark RequestMark)] :answer None :tags {:context "records" :role "entry"}})

(defeffect CloseWaits
  "入口が止まる — 要求の task の中の置き場の待ちを止めの印(理由 reason)で起こし、以後の待ちを待たせない(#3713)。答えは None。"
  {:fields [(: reason str)] :answer None :tags {:context "records" :role "entry"}})

(defeffect ClosingWaits
  "止めの印を読む: 止めた後なら印(WaitsClosed)・止めの前なら印で完了する門(promise)。"
  {:fields [] :answer (| WaitsClosed Promise) :tags {:context "records" :role "entry"}})

(defeffect TakeMarks
  "札 ticket の要求に打った刻の列(打った順の MarkAt)を読み、控えから外す(答え終えた要求の刻を残さない)。"
  {:fields [(: ticket str)] :answer (get tuple #(MarkAt ...)) :tags {:context "records" :role "entry"}})



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


(defhandler waits-closing
  "止めの合図を受けた入口が、要求の task の中の置き場の待ち(変化の待ちの long-poll の呼び鈴の待ち)を直ぐに終わらせられるよう、止めの印
   (WaitsClosed)を入口の session に持つため(頭の註の止めの形・#3713)。CloseWaits が印を置いて門(promise)を印で完了し、待ちの切り手
   (closing-cuts-waits)は ClosingWaits で印か門を読む。serve-records が用意・受けの loop・止めの見張りの全部の外側に 1 つ被せる。"
  {:tags {:context "records" :role "entry"}}
  ;; closed = 置いた止めの印(None = まだ止めていない)・gate = 止めの前の待ちが印を待つ門(最初の ClosingWaits が作る)。
  (session var closed None)
  (session var gate None)
  (ClosingWaits []
    (when (is-not closed None)
      (return (resume closed)))
    (when (is gate None)
      (<- made (CreatePromise))
      (:= gate made))
    (resume gate))
  (CloseWaits [reason]
    (when (is closed None)
      (val mark (WaitsClosed :reason reason))
      (:= closed mark)
      (when (is-not gate None)
        (<- (CompletePromise gate mark))))
    (resume None)))


(defhandler closing-cuts-waits
  "要求の記録の handler の中の待ち(doeff-time の WaitWithin — 記録の handler が撃つ待ちは変化の待ちの呼び鈴の待ちだけ: memory の
   bell-or-timer・PostgreSQL の wait-for-signal)を、入口の止めの印でも起こすため(#3713)。止めた後の待ちは待たずに印で答え、止めの前の
   待ちは呼び鈴・期限・止めの門の早い方で起きる。印を受けた待ち手は読み直さずに静かな答えで返る(watching.closing-wake?)。"
  {:tags {:context "records" :role "entry"}}
  (WaitWithinEffect []
    (<- closing (| WaitsClosed Promise) (ClosingWaits))
    (match closing
      (WaitsClosed) (resume closing)
      _ (do ;; 期限と止めの門を 1 本の待ちにまとめ(門の値 = 印・期限 = None)、呼び鈴と早い方を取る。呼び鈴の park はそのまま Race の
            ;; 待ち方にする(仮想の時計を止めない — doeff-time の WaitWithin と同じ)。
            (<- deadline Task (Spawn (WaitWithin closing.future effect.seconds)))
            (<- first (Race effect.future deadline :priority (if effect.park PRIORITY-IDLE None)))
            (<- (Cancel deadline))
            (resume first)))))


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
  (WatchEvents [stream after timeout] (resume (Unreachable NOT-PREPARED-REASON)))
  (AppendEvent [stream idempotency-key body] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadEvents [stream after limit] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadStreamEnd [stream] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadEventByKey [stream idempotency-key] (resume (Unreachable NOT-PREPARED-REASON))))


(defhandler request-stamps
  "答えている要求の刻(StampRequest)を札ごとに、要求ごとの外側の handler の時計ではなく土台の時計(GetMonotonic — 受けた刻と同じ物差し)の
   秒と組にして控え、送った後に区間の秒を割れるようにするため(頭の註の要求ごとの計時)。受けの loop に request-ledger と並べて被せる。"
  {:tags {:context "records" :role "entry"}}
  ;; marks = 札 → 打った順の刻の列(session の値)。答え終えた札は TakeMarks が外す。
  (session var marks {})
  (StampRequest [ticket mark]
    (<- at float (GetMonotonic))
    (:= marks (| marks {ticket (+ (.get marks ticket #()) #((MarkAt :mark mark :at at)))}))
    (resume None))
  (TakeMarks [ticket]
    (val taken (.get marks ticket #()))
    (:= marks (dfor [k m] (.items marks) :if (!= k ticket) k m))
    (resume taken)))


(defhandler wait-stamps [#^ str ticket]
  "記録の handler の中の待ち(doeff-time の WaitWithin — 変化の待ちの long-poll の置き場の待ち)の入りと起きに刻を打ち、待った秒と
   起きてからの秒を分けて測れるようにするため(待ちはそのまま外側の時計へ渡す)。"
  {:tags {:context "records" :role "entry"}}
  ;; 引数に残す理由: ticket は包んだ要求の札(要求ごとに stamped-handling が被せる — Ask で読む設定ではない)。
  (WaitWithinEffect []
    (<- (StampRequest :ticket ticket :mark RequestMark.WAIT-IN))
    (<- woke effect)
    (<- (StampRequest :ticket ticket :mark RequestMark.WOKE))
    (resume woke)))


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
  {:pre [(: ticket str) (: answer (| HttpAnswer TextAnswer))] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "答え(status と本文)を札の要求へ送るため。本文の種は答えの型が決める(記録の操作と断り = JSON・/metrics = 答えが名乗る text)。"
  (val content-type (match answer
                      (TextAnswer :content-type declared) declared
                      (HttpAnswer) JSON-CONTENT-TYPE))
  (val data (.encode answer.body "utf-8"))
  (<- (HttpRespond :ticket ticket :status answer.status
                   :headers #((HttpHeader :name "Content-Type" :value content-type)
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
  {:pre [(: seconds float)] :post [(: % StoreSilent)] :tags {:context "records" :role "entry"}}
  "置き場の問いの上限を刻むため(seconds 秒眠って「答えない」を返す — store-reach の Race の片方)。"
  (<- (Delay seconds))
  (StoreSilent :seconds seconds))


(defk store-pressure [serving]
  {:pre [(: serving RecordsServing)] :post [(: % (| StorePressure PressureUnread))] :tags {:context "records" :role "entry"}}
  "置き場の詰まりを読むため(#1858): 問いの無い置き場(memory)は錠も transaction も持たないので 0・問いが在れば撃った答え。"
  (if (is serving.pressure None)
      (StorePressure :lock-waiters 0 :idle-in-transaction-max-seconds 0.0)
      (! (serving.pressure))))


(defk store-probe [serving]
  {:pre [(: serving RecordsServing)] :post [(: % (| StoreReachable StoreUnreachable))] :tags {:context "records" :role "entry"}}
  "置き場の問いの本体: 置き場に届くかを問い(届かなければ StoreUnreachable)、届けば詰まりを読む。1 つの task で撃つので、
   READINESS-SECONDS の上限は届くかの問いと詰まりの読みの両方に掛かる。"
  (<- reachable bool (serving.readiness))
  (if reachable
      (StoreReachable :pressure (! (store-pressure serving)))
      (StoreUnreachable :reason "置き場に届かない")))


(defk cancelled-and-settled [task]
  {:pre [(: task Task)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "Race で答えの決まった問いの task を取り消し、解け終わるまで待つため(終わっていた task の取り消しは何もしない — 答えは捨てる)。"
  (<- (Cancel task))
  (try
    (<- _ended (Wait task))
    (except [TaskCancelledError] None))
  None)


(defk store-reach [serving]
  {:pre [(: serving RecordsServing)] :post [(: % StoreReach)] :tags {:context "records" :role "entry"}}
  "置き場に届くかを /readyz と同じ判断で決めるため(公開 — 使い手の土台が準備の報告に同じ判断を撃つ・#3733): 問いが無ければ届く
   (詰まりは store-pressure の読み)・問いを READINESS-SECONDS の上限で撃ち、届けば StoreReachable(詰まりの読みを持つ)・届かなければ
   StoreUnreachable・上限の内に答えなければ StoreSilent(問いの task は取り消す — 固まった置き場の問いを待ち続けない)。取り消した task は
   解け終わるまで待つ(走りかけの問いと時計を置き去りにして返らない — 判断を root で撃つ呼び手でも #501 の置き去りにならない)。表の用意の
   済みは問わない(用意の告知 RecordsPrepared と /readyz の用意の前の 503 が持つ)。"
  (when (is serving.readiness None)
    (return (StoreReachable :pressure (! (store-pressure serving)))))
  (<- probe Task (Spawn (store-probe serving)))
  (<- timer Task (Spawn (readiness-timer READINESS-SECONDS)))
  (<- first StoreReach (Race probe timer))
  (<- (cancelled-and-settled probe))
  (<- (cancelled-and-settled timer))
  first)


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
  "/readyz に答えるため: 用意の前は 503・置き場に届くかの判断(store-reach)が届くなら 200(錠を待つ本数と idle in transaction の最長の
   秒を載せる・#1858)・届かないか上限の内に答えなければ 503 store-unavailable。"
  (when (is prepared None)
    (return (! (refusal-answer ERROR-STORE-UNAVAILABLE "表の用意が済んでいない"))))
  (<- reach StoreReach (store-reach serving))
  (match reach
    (StoreReachable :pressure pressure) (! (ready-answer pressure))
    (StoreUnreachable :reason reason) (! (refusal-answer ERROR-STORE-UNAVAILABLE reason))
    (StoreSilent :seconds seconds)
      (! (refusal-answer ERROR-STORE-UNAVAILABLE (.format "置き場が {} 秒の内に答えない" seconds)))))


(defk metrics-answer []
  {:pre [] :post [(: % TextAnswer)] :tags {:context "records" :role "entry"}}
  "GET /metrics に答えるため: 計器の断面(ReadMeter)を Prometheus の text に描く(身元を問わない — 数だけで、行の中身を載せない)。"
  (<- snapshot MeterSnapshot (ReadMeter))
  (<- text str (render-prometheus snapshot METRIC-HELPS))
  (TextAnswer :status 200 :content-type METRICS-CONTENT-TYPE :body text))


(defk served-answer [serving]
  {:pre [(: serving RecordsServing)] :post [(: % HttpAnswer)] :tags {:context "records" :role "entry"}}
  "GET /served に答えるため: 動いている process が走っている木の commit・世代と、配っている表の宣言の要約(schema-digests)を返す。
   使い手が動いている版をそろえるために読む(#2742)。身元を問わず、表の用意と置き場に頼らない — 置き場に届かない間も読める。
   知らない欄は null(commits・instance)。"
  (<- digests FrozenMap (schema-digests serving.schema))
  (val build (or serving.served (ServedBuild)))
  (! (json-answer 200 {"commits" (if (is build.commits None) None (dfor c build.commits c.repo c.commit))
                       "instance" build.instance
                       "schemaDigests" (dict digests)})))


(defk stamped-handling [ticket record-handler body]
  {:pre [(: ticket str) (: record-handler Callable) (: body (| Program EffectBase))] :post [(: % "body の答え")]
   :tags {:context "records" :role "entry"}}
  "記録の handler record-handler の下で body(札 ticket の要求の公開 effect)を撃ち、handler の入りと出に刻を打つため(handler の中の
   待ちの刻は wait-stamps — 頭の註の要求ごとの計時)。answer-with が書き手の名ごとの handler をこの包み(BodyWrapper)にして service へ渡す。"
  (<- (StampRequest :ticket ticket :mark RequestMark.HANDLER-IN))
  ;; 待ちの切り手は刻の控えの外側(刻の控えが待ちの入りと起きを、切り手の Race ごと測る)。
  (<- answer (with-handlers [closing-cuts-waits (wait-stamps ticket) record-handler] body))
  (<- (StampRequest :ticket ticket :mark RequestMark.HANDLER-OUT))
  answer)


(defk answer-with [serving ticket request]
  {:pre [(: serving RecordsServing) (: ticket str) (: request HttpRequest)] :post [(: % (| HttpAnswer TextAnswer))]
   :tags {:context "records" :role "entry"}}
  "要求 1 つを service.respond で答えるため: 用意が済んでいれば置き場の handler、済んでいなければ store-not-prepared の下で撃つ。
   記録の handler は、札 ticket の要求の handler の入りと出と待ちに刻を打つ包み(stamped-handling)で被せる。
   要求ごとの外側の handler(serving.request-handlers — 検の仮想の時計)を被せる。GET /readyz は置き場を問い(readiness-answer)、
   GET /metrics は計器を描く(metrics-answer — どちらも身元を引く前に答える)。GET /served は走っている木と表の要約を答える
   (served-answer — 身元も表の用意も問わない)。"
  (when (and (= request.method "GET") (= request.path PATH-METRICS))
    (return (! (metrics-answer))))
  (when (and (= request.method "GET") (= request.path PATH-SERVED))
    (return (! (served-answer serving))))
  (<- prepared (| Callable None) (PreparedHandlers))
  (when (and (= request.method "GET") (= request.path PATH-READYZ))
    (return (! (with-handlers [#* serving.request-handlers] (readiness-answer serving prepared)))))
  (val handler-for (if (is prepared None) (fn [writer] store-not-prepared) prepared))
  (<- service (records-service serving.schema (fn [writer] (BodyWrapper stamped-handling ticket (handler-for writer)))))
  (<- answer (with-handlers [#* serving.request-handlers]
                            (respond service request)))
  answer)


(defk body-answer [serving arrival path body]
  {:pre [(: serving RecordsServing) (: arrival HttpRequestArrived) (: path str) (: body (| bytes HttpAnswer))]
   :post [(: % (| HttpAnswer TextAnswer))] :tags {:context "records" :role "entry"}}
  "本文の読みの答えから要求の答えを決めるため: 入口が本文を断った答え(400)はそのまま、本文が読めれば answer-with で答える。"
  (match body
    (HttpAnswer) body
    _ (! (answer-with serving arrival.ticket (HttpRequest arrival.method path body
                                                          (! (header-value arrival.headers WRITER-HEADER)))))))


(defk final-answer [decided]
  {:pre [(: decided (| HttpAnswer TextAnswer None))] :post [(: % (| HttpAnswer TextAnswer))] :tags {:context "records" :role "entry"}}
  "札に送る答えを決めるため: 決まった答えか、決まる前に落ちた札の 500 internal(答えの無い札を残さない)。"
  (match decided
    None (! (refusal-answer ERROR-INTERNAL "要求の task が答える前に落ちた"))
    answer answer))


(defk place-answer-metrics []
  {:pre [] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "要求の数の系列(wire の ANSWER-METRICS)を全部 0 で置くため(起動の時 1 回 — 読み手が「無い」と「0」を区別しなくて済み、区間の差が最初の
   1 つ目の増えを取りこぼさない)。"
  (for [metric ANSWER-METRICS]
    (<- (CountMetric :name metric :amount 0.0)))
  None)


(defk sent-answer [arrival answer]
  {:pre [(: arrival HttpRequestArrived) (: answer (| HttpAnswer TextAnswer))] :post [(: % (| HttpAnswer TextAnswer))]
   :tags {:context "records" :role "entry"}}
  "答えを札へ送り、実際に送った答えを返すため。送りが例外になれば(答えを byte にする所・送りの effect の失敗)、標準の誤りへ 1 行
   名指して 500 internal を 1 度だけ送り直す(答えの無い札を残さない)。"
  (try
    (<- (send-answer arrival.ticket answer))
    (return answer)
    (except [e Exception]
      (print (.format "記録の service: {} {} の答えを送れなかった — 500 internal で送り直す: {}: {}"
                      arrival.method arrival.target (. (type e) __name__) e)
             :file sys.stderr :flush True)))
  (<- internal HttpAnswer (refusal-answer ERROR-INTERNAL "答えを送る途中で落ちた"))
  (<- (send-answer arrival.ticket internal))
  internal)


(defk observe-stages [path received-at marks]
  {:pre [(: path str) (: received-at (| float None)) (: marks (get tuple #(MarkAt ...)))] :post [(: % None)]
   :tags {:context "records" :role "entry"}}
  "記録の操作の要求 1 つの区間の秒(request_timing の request-stages)を、操作ごと・区間ごとの秒の観測の列にして計器の効果 1 回
   (ObserveSecondsBatch)で積み、GET /metrics でどの区間が遅いかを読めるようにするため(頭の註の要求ごとの計時 — 記録の操作でない route は
   積まない)。"
  (<- operation (| str None) (operation-of path))
  (when (is operation None)
    (return None))
  (<- stages (get tuple #(StageSeconds ...)) (request-stages received-at marks))
  (var observations #())
  (for [stage stages]
    (<- name str (stage-metric operation stage.stage))
    (:= observations (+ observations #((SecondsObservation :name name :seconds stage.seconds)))))
  (<- (ObserveSecondsBatch :observations observations))
  None)


(defk deliver [arrival path answer]
  {:pre [(: arrival HttpRequestArrived) (: path str) (: answer (| HttpAnswer TextAnswer))] :post [(: % None)]
   :tags {:context "records" :role "entry"}}
  "答え 1 つを札へ送り(sent-answer)、待ち受けへ渡した答えの status を計器に 1 つ数え、台帳から外すため。数えるのはこの 1 か所で、
   送り直した 500 も、ここで 500 として数える(渡せなかった答えの status は数えない)。待ち受けの側で後から落ちた送り(相手が先に切った・
   札が待ち受けに無い — 本物の待ち受けは答えを loop へ積むだけで戻る・#3688 の子 (3) の 3b)は、待ち受けが標準の誤りへ 1 行名乗るだけで、
   ここの数えは直さない(渡した答えの status のまま)。送りの effect は答えを待ち受けへ渡すだけで、
   この task は同じ scheduler の上で続けて数えるので、答えを受け取った呼び手が次に送る要求より先に数えが済む。計器が落ちても
   答えは送ってあり、台帳からは外す(誤りはそのまま上げる)。送り終えた刻を打ち、札の刻を控えから外して、記録の操作の要求なら区間の
   秒を計器に積む(observe-stages — 頭の註の要求ごとの計時)。"
  (try
    (<- sent (| HttpAnswer TextAnswer) (sent-answer arrival answer))
    (<- (StampRequest :ticket arrival.ticket :mark RequestMark.SENT))
    (<- metric str (answer-metric path sent.status))
    (<- (CountMetric :name metric :amount 1.0))
    (finally
      (<- marks (get tuple #(MarkAt ...)) (TakeMarks arrival.ticket))
      (<- (SettleRequest arrival.ticket))))
  (<- (observe-stages path arrival.received-at marks))
  None)


(defk answer-arrival [arrival serving]
  {:pre [(: arrival HttpRequestArrived) (: serving RecordsServing)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "要求 1 つに答える task の本体: 本文を読み → answer-with で答えを決め → 送って計器に数える(deliver)。例外でも必ず答える(決まる前に
   落ちれば 500 internal・送りが落ちれば 500 internal で送り直す — 答えの無い札を残さない)。数えるのは実際に送った答えの 1 か所だけ
   (本文の断りの 400 も、落ちた時の 500 も同じ所)。実装の誤りの追跡は標準の誤りへ残す(黙って捨てない)。終われば台帳から外す。
   task の始まり・本文の読み終わり・答えの決まりに刻を打つ(頭の註の要求ごとの計時)。"
  (<- (StampRequest :ticket arrival.ticket :mark RequestMark.STARTED))
  (val path (get (.split arrival.target "?" 1) 0))
  (var decided None)
  (try
    (<- body (| bytes HttpAnswer) (request-body arrival serving.max-bytes))
    (<- (StampRequest :ticket arrival.ticket :mark RequestMark.READ))
    (<- answer (| HttpAnswer TextAnswer) (body-answer serving arrival path body))
    (:= decided answer)
    (except [e Exception]
      (print (.format "記録の service: {} {} の答えの途中で落ちた: {}: {}" arrival.method arrival.target (. (type e) __name__) e)
             :file sys.stderr :flush True)
      (traceback.print-exc))
    (finally
      (<- (StampRequest :ticket arrival.ticket :mark RequestMark.DECIDED))
      (<- final (| HttpAnswer TextAnswer) (final-answer decided))
      (<- (deliver arrival path final))))
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
  "止めの合図を poll-seconds ごとに問い、合図を見たら保留中の置き場の待ちを止めの印で起こしてから(CloseWaits — 変化の待ちの long-poll が
   上限の秒を待たずに空の答えを返す・#3713)待ち受けを閉じるため(受けの loop が HttpServerClosed を受けて終わる)。印を閉じより先に置くのは、
   待ち受けの閉じ(aiohttp の後始末)が答え途中の要求の答えを待つため — 先に閉じると long-poll の答えまで閉じが終わらない。"
  (while True
    (<- reason (| str None) (StopRequested))
    (when (is-not reason None)
      (<- (CloseWaits :reason reason))
      (<- (HttpShutdown :reason reason :drain-seconds drain-seconds))
      (return None))
    (<- (Delay poll-seconds))))


(defk prepare-store [prepare bound]
  {:pre [(: prepare (| Program EffectBase)) (: bound HttpAddress)] :post [(: % StorePrepared)] :tags {:context "records" :role "entry"}}
  ;; 表の用意(起動時の 1 点・冪等)を 1 回撃ち、書き手の名 → handler の関数を prepared-slot に置いてから、用意の告知(RecordsPrepared —
  ;; 結んだ宛先 bound と所要の秒・頭の註の用意の告知)を出す。告知は handler の関数を置いた後なので、告知が出たなら記録の操作に答えられる。
  ;; 口は先に開いていて、/healthz は process の生存だけを答え、記録の操作は用意が済むまで 503。失敗は名乗って例外のまま上げる(本体の Race
  ;; が受けて run を 0 以外で終える — 半端に立ったまま答え続けない・再起動が繋ぎ直す)。
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
  (val seconds (- done-at started))
  (<- (RecordsPrepared :address bound :seconds seconds))
  (StorePrepared :seconds seconds))


(defk maintain [handler-for plan]
  {:pre [(: handler-for Callable) (: plan MaintenancePlan)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "手入れの係: 止めるまで maintenance-loop を手入れの係の書き手の handler の下で回すため(置き場に届かない回は Unreachable の値で次の回へ)。"
  (<- (with-handlers [(handler-for MAINTAINER)] (maintenance-loop plan.interval-seconds plan.keep-seconds None)))
  None)


(defk serve-records [serving]
  {:pre [(: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "入口の Program(頭の註の「入口の形」): 待ち受けを開いて名乗り、用意・受けの loop・止めの見張りの task を立て、用意の失敗か待ち受けの
   閉じまで見張って、process の終わりの code(0)を返すため。用意の失敗は例外のまま上げる。計器は doeff の memory-meter-handler(この
   run の中の断面・桁の表なし)を常に被せ、serving.meter が在ればその内側に被せる(metered-body — 差し替えの計器が先に答える)。
   既定の計器を名で書くのは、答えの無い effect を実行せずに読む閉じの検(doeff-effect-analyzer)が、計器の effect の答え手を読めるように。"
  (<- code int (with-handlers [prepared-slot waits-closing (memory-meter-handler (MeterSettings))] (metered-body serving)))
  code)


(defk metered-body [serving]
  {:pre [(: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "差し替えの計器(serving.meter — 検が壊した計器を差す口)が在れば、既定の計器の内側に被せて本体を走らせるため(無ければそのまま)。"
  (if (is serving.meter None)
      (! (serving-body serving))
      (! (with-handlers [serving.meter] (serving-body serving)))))


(defk serving-body [serving]
  {:pre [(: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "serve-records の本体(prepared-slot と計器の内側 — 用意の task と要求の task が同じ session の値と計器を読む)。"
  (<- (place-answer-metrics))
  (<- bound HttpAddress (HttpListen :address serving.address))
  (<- (RecordsListening :address bound))
  ;; 用意を受けの loop より先に立てる(同じ拍に並んだ時に用意が先に走る)。用意の告知は用意の task の終わりが出す(prepare-store)。
  (<- preparing Task (Spawn (prepare-store serving.prepare bound)))
  (<- serving-task Task (Spawn (with-handlers [request-ledger request-stamps] (receive-requests serving))))
  (<- watcher Task (Spawn (watch-stop serving.stop-poll-seconds serving.drain-seconds) :daemon True))
  (<- first (| StorePrepared ServingEnded) (Race preparing serving-task))
  (match first
    (StorePrepared)
      (do (when (is-not serving.maintenance None)
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
  "検の殻の口 1 つの組み立て: schema = 置き場の宣言 / handler-for = 書き手の名 → 記録の handler(用意し終えた置き場の上) /
   request-handlers = 要求ごとの答えの外側に被せる handler の列(呼び手の仮想の時計・SQL の答え手)/ host・port(0 = 空いている port)/
   meter = 計器の handler(None = memory-meter-handler — 検が壊した計器を差す口・RecordsServing の meter へそのまま渡す)/
   served = 走っている木と世代(RecordsServing の served へそのまま渡す・None = 知らない)。"
  (#^ RecordsSchema schema)
  (#^ Callable handler-for)
  (setv #^ tuple request-handlers #())
  (setv #^ str host "127.0.0.1")
  (setv #^ int port 0)
  (setv #^ (| (get Callable #(... object)) None) meter None)
  (setv #^ (| ServedBuild None) served None))


(defk records-server-config [schema handler-for [request-handlers #()] [host "127.0.0.1"] [port 0] [meter None] [served None]]
  {:pre [(: schema RecordsSchema) (: handler-for Callable) (: request-handlers tuple) (: host str) (: port int)
         (: meter (| (get Callable #(... object)) None)) (: served (| ServedBuild None))] :post [(: % RecordsServerConfig)]}
  "名簿を取らずに RecordsServerConfig を組む(引数を並べて組む・#3008)。
   引数は RecordsServerConfig の欄と同じ順。"
  (RecordsServerConfig schema handler-for request-handlers host port meter served))


(defclass [(dataclass :frozen True)] RunningServer []
  "開いた HTTP の口: url = 基の URL(http://host:port)/ stop = 殻の止めの口 / close() = 止めて port を返す(受けている要求を答え終えてから)。"
  (#^ str url)
  (#^ Callable stop)
  (deff close [self]  ; defk にできない: 検の殻を閉じる同期の口(Program の外の検の try / finally が呼ぶ)
    {:pre [(: self RunningServer)] :post [(: % None)] :tags {:context "records" :role "entry"}}
    "口を止める(止めの合図を立て、入口の run が待ち受けを閉じて終わるのを待つ)。2 度目は何もしない。"
    (self.stop)
    None))


(defhandler shell-control [#^ Callable prepared #^ Callable stopping]
  "検の殻の外から、入口の Program の名乗り(RecordsListening)・表の用意の告知(RecordsPrepared)・止めの合図(StopRequested)に答えるため
   (#880 F3)。殻は告知の宛先を受け取ってから口を返す — 返った口は記録の操作に答えられる(#3733)。"
  {:tags {:context "records" :role "entry"}}
  ;; 引数に残す理由: prepared は殻が告知の宛先を受け取る口(thread の外の queue の put)、stopping は殻の止めの合図を読む口(合図が
  ;; 立っていれば理由・無ければ None)。どちらも thread をまたぐ殻の物で、Ask で読む設定ではない。
  (RecordsListening [address]
    (resume None))
  (RecordsPrepared [address seconds]
    (prepared address)
    (resume None))
  (StopRequested []
    (resume (stopping))))


(defk ready-handlers [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % Callable)] :tags {:context "records" :role "entry"}}
  "検の殻の用意: 呼び手が用意し終えた置き場の 書き手の名 → handler をそのまま返すため(用意の I/O は無い)。"
  handler-for)


(deff shell-run [program failures]  ; defk にできない: 検の殻の thread の target(framework の入口 — 別の run は Program の中から立てられない)
  {:pre [(: program (| Program EffectBase)) (: failures queue.Queue)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "入口の Program を scheduler の下で 1 回走らせ、落ちたら殻の待ち手へ例外を渡すため(表の用意の告知の前に落ちても殻が 30 秒待たない)。"
  (try
    (run (scheduled program))
    (except [e BaseException]
      (.put failures e)))
  None)


(deff start-records-server [config]  ; defk にできない: 入口の run を別の thread で立てる検の殻(framework の入口 — 返す物が開いた口)
  {:pre [(: config RecordsServerConfig)] :post [(: % RunningServer)] :tags {:context "records" :role "entry"}}
  "入口の Program(serve-records)を別の thread の run で回して口を開き、表の用意の告知を待って RunningServer を返す。土台は本番と同じ
   待ち受け・時計・Await の橋(aiohttp-http-server・async-time-handler・await-handler)で、止めの合図と名乗りと告知だけを shell-control が
   答える。開けない・用意が落ちたなら例外を上げる。"
  (setv opened (queue.Queue)
        ended (threading.Event))
  (setv serving (RecordsServing :address (HttpAddress :host config.host :port config.port) :schema config.schema
                                :prepare (ready-handlers config.handler-for) :request-handlers (tuple config.request-handlers)
                                :max-bytes REQUEST-MAX-BYTES :maintenance None :stop-poll-seconds SHELL-STOP-POLL-SECONDS
                                :drain-seconds 0.0 :meter config.meter :served config.served))
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
