;;; 記録の service の待ち受けの Program と、検の殻(待ち受けを doeff の汎用の HTTP の effect へ移した)。
;;;
;;; process は 1 つの run・1 つの scheduler で動く。要求ごとに run を撃つ形(標準の http.server・要求ごとの thread と 1 本の thread の
;;; 切り替え・時計と scheduler を被せて run する runner・要求ごとに handler の組を借りる lease)は退役した。
;;;
;;;   serve-records        入口の Program(本体): 待ち受けを port の共有で開き(HttpListen の share-port)、結んだ宛先を名乗り(RecordsListening)、表の用意・受けの loop・
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
;;;     Task を自分の台帳(RequestLedger)に持ち、HttpServerClosed の後に Gather で待ってから返る(答え途中の要求を捨てない)
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
;;;     身元を問わず(/readyz と同じく身元を引く前に答える)、ReadMeter の断面に計算の pool の負荷(Compute の答え手の ReadComputeLoad —
;;;     並び・走り・待ちの秒の累計の系列 records_compute_* — ADR-DOE-RECORDS-002 R6)を足し、doeff の render-prometheus で Prometheus の text に描く
;;   - GET /served(#2742)は、動いている process が走っている木の commit と世代(serving.served — 使い手の入口が渡す・無ければ null)と、
;;     配っている表の宣言の要約(schema_digest.schema-digests)を答える。身元も表の用意も置き場も問わない(置き場に届かない間も使い手が
;;     動いている版を読める)。計器では種 other に数える
;;;   - 要求ごとの計時(#3688): 要求の task は要求 1 つの刻の帳面(MarkBook)を作り、task の始まり・本文の読み終わり・答えの決まり・
;;;     送り終わりの刻を、自分で土台の時計(GetMonotonic)から読んで帳面に打つ(札ごとの刻を session の値に控えない — card ki-1841b724d061・
;;;     ki-5c3e73834e73: 控えの Get と Put と控えから外す effect が要求ごとに土台まで往復し、1 要求の scheduler の thread の CPU の大きな
;;;     割合が計時だった)。要求の task は要求ごとの外側の handler(serving.request-handlers — 検の仮想の時計)の外で走るので、読むのは土台の
;;;     時計。記録の操作の handler は包み(stamped-handling)で被せ、handler の入りと出と、handler の中の待ち(doeff-time の WaitWithin —
;;;     変化の待ちの long-poll)の入りと起きの刻を、同じ帳面に打たせる(StampRequest)。包みは serving.request-handlers の内側で走るので、
;;;     刻は受けの loop に被せた刻の答え手(request-stamps)が、自分の外側の時計(土台の GetMonotonic)で読んで帳面に
;;;     打ち、受けた刻(received-at)と同じ物差しにそろう。待ちの刻の答え手と止めの切り手(wait-stamps・closing-cuts-waits)は、待つ操作
;;;     (WatchChanges・WatchEvents — 記録の handler が待ちを撃つのはこの 2 つだけ)にだけ被せる。答えが決まったら、記録の操作の要求だけ、
;;;     帳面の刻を 1 度割って答えの見出しに載せ、送り終わりの刻で決まる区間(sent-stages)を足した区間ごとの秒
;;;     (request_timing.hy — queue・body・decode・handler・wait・encode・send・total・woke)を records_stage_<操作>_<区間> の観測の列にし、
;;;     doeff の計器の効果 ObserveSecondsBatch 1 回で積む(区間ごとに ObserveSeconds を撃つと 1 要求で 7〜9 回 — 書きのたびに起きた待ちの
;;;     要求が波で並ぶ時、その 1 本ずつの後ろに観測の往復が乗っていた・#3688)。GET /metrics が区間ごとの秒の和と数を描く。log には書かない
;;;     (本番の書きと待ちの要求の数で行が積もり、log の file を切り替える仕組みが無いため)
;;;   - 一覧の読みの数(重い読み手を数で名指すため): 記録の handler の包み(stamped-handling)が、ListRows に置き場が答えるたびに(届かない
;;;     Unreachable は 503 の系列が数える)、表ごとと呼び手 × 表ごとの呼びの数と返した行の数を CountMetric で足す(綴りと閉じた系列は wire の
;;;     LIST-ROWS-*)。呼び手は要求の見出し X-Records-Caller の名乗りを宣言の名(serving.callers)に照らした名(宣言に無い名乗りは
;;;     undeclared・名乗りの無い要求は unnamed)。表ごとの系列は起動の時に 0 で置き、呼び手 × 表の系列は最初の答えで現れる(宣言の表 ×
;;;     呼び手の組は数千 — 全部を 0 で置くと /metrics の本文と、数えるたびの断面の写しが重くなる)。区間の秒の系列(records_stage_*)に表の
;;;     名は付けない(区間 9 × 表 80 の観測の列になる — 遅い表は呼びの数と行の数から絞り、区間は操作ごとに読む)
;;;   - scheduler の thread の仕事の分け(ADR-DOE-RECORDS-002 — loop_work.hy の頭の註): 受けの loop・要求の task・書きの門・変化の待ちは
;;;     全部 1 本の scheduler の thread の上で回るので、その thread は要求の受けと順の制御と、大きさが要求の本文か 1 行で決まる計算だけを持つ。
;;;     束の読みの行の読み解き(pg.hy の decoded-batch)と束の読みの答えの綴り(service.hy の answer-reply)は Compute を出す。入口の
;;;     Program は Compute に答えない — 答え手は土台が置く(本番の土台の口 = main.hy の records-connected の thread の pool・検の殻 =
;;;     start-records-server の thread の pool・台本の検と模擬 = inline-compute-handler)
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
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.thread_pool_compute [thread-pool-compute-handler])
(import doeff_core_effects.compute_effects [ComputeLoad ReadComputeLoad])
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
(import doeff_records.values [NotIndexed Page RecordsSchema Reset Unreachable WaitsClosed])
(import doeff_records.schema_digest [schema-digests])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent AppendEvents ReadEvents ReadStreamEnd
                               ReadEventByKey])
(import doeff_records.event_source [BodyWrapper])
(import doeff_records.maintenance [maintenance-loop])
(import doeff_records.request_timing [RequestMark MarkAt StageSeconds STAGE-METRIC-HELPS STAGE-METRIC-NAMES request-stages sent-stages waited server-timing-spans-of
                                      lock-metric-helps])
(import doeff_records.service [HttpRequest HttpAnswer RecordsService records-service respond refusal-answer json-answer])
(import doeff_records.wire [ERROR-INTERNAL ERROR-MALFORMED ERROR-STORE-UNAVAILABLE ANSWER-METRICS ANSWER-METRIC-HELPS answer-metric operation-kind
                            operation-of server-timing-spelled])
(import doeff_records.wire [WRITER-HEADER CALLER-HEADER IDEMPOTENCY-HEADER SERVER-TIMING-HEADER list-rows-caller list-rows-counts list-rows-metrics
                            list-rows-metric-helps])
(import doeff_records.store_choice [StorePressure PressureUnread])
(import doeff_core_effects.meter_effects [CountMetric MeterSettings MeterSnapshot ObserveSecondsBatch ReadMeter SecondsObservation
                                         SecondsTotal])
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
;; 計算の pool の負荷の系列(ADR-DOE-RECORDS-002 R6 — 束の読みの行の読み解きと答えの綴りを回す pool の詰まり)。GET /metrics の描きの時に
;; 答え手の負荷(ReadComputeLoad)を読んで断面に足す(計器へは積まない — with-compute-load): 並びと走りと pool の thread の数は今の値(gauge)、
;; 待ちは回り始めた仕事が pool の thread を待った秒の合計と回数、回りは回り終えた仕事が回った秒の合計と回数(<名>_seconds_sum・
;; _seconds_count — 答え手を組んだ時からの累計)。拍ごとに読んだ 2 つの断面の差で、その間の待ちの平均(待ちの合計の差 ÷ 回数の差)と、列に
;; 並んでいた仕事の数の時間の平均(待ちの合計の差 ÷ 拍の秒)と、pool の利用率(回りの合計の差 ÷ 拍の秒 ÷ thread の数)が出る — 要求の遅れが
;; 跳ねた刻と同じ拍で並べて読む。
(val COMPUTE-QUEUED-METRIC "records_compute_queued")
(val COMPUTE-RUNNING-METRIC "records_compute_running")
(val COMPUTE-WORKERS-METRIC "records_compute_workers")
(val COMPUTE-WAIT-METRIC "records_compute_wait")
(val COMPUTE-RUN-METRIC "records_compute_run")
(val COMPUTE-METRIC-HELPS
  (FrozenMap {COMPUTE-QUEUED-METRIC "束の読みの読み解きと綴りの計算のうち、計算の pool の thread を待って列に並んでいる数(読んだ時の今の値)"
              COMPUTE-RUNNING-METRIC "計算の pool の thread で回っている束の読みの読み解きと綴りの計算の数(読んだ時の今の値)"
              COMPUTE-WORKERS-METRIC "計算の pool の thread の数(組み立てが開いた数 — 利用率の割る数)"
              COMPUTE-WAIT-METRIC (+ "回り始めた計算が、出されてから計算の pool の thread で回り始めるまで待った秒(合計と回数 — 起動からの累計。"
                                     "2 回読んだ差の合計 ÷ 回数 = 待ちの平均・合計 ÷ 読みの間の秒 = 列に並んだ数の時間の平均)")
              COMPUTE-RUN-METRIC (+ "回り終えた計算が計算の pool の thread で回った秒(合計と回数 — 起動からの累計。2 回読んだ差の合計 ÷ 読みの間の秒 ÷ "
                                    "records_compute_workers = pool の利用率)")}))
;; GET /metrics の # HELP の説明(要求の数の系列 + 区間の秒の系列 — #3688 + 計算の pool の負荷の系列)。表ごとの一覧の読みの系列の説明は
;; 宣言の表から足す(metrics-answer)。
(val METRIC-HELPS (FrozenMap (+ (tuple (.items ANSWER-METRIC-HELPS)) (tuple (.items STAGE-METRIC-HELPS))
                                (tuple (.items COMPUTE-METRIC-HELPS)))))

(val MODULE-TAGS {:context "records" :role "entry"})

(val JSON-CONTENT-TYPE "application/json; charset=utf-8")
;; 要求の本文の上限(行の値の上限は表の宣言の size-budget が決める — これは口の読みの上限)。
(val REQUEST-MAX-BYTES (* 16 1024 1024))
;; 本文を持つ要求の method(他の method の本文は読まない — 記録の操作は POST だけ)。
(val BODY-METHODS #("POST" "PUT"))
;; 用意の前の要求への答えの理由(503 store-unavailable の reason)。
(val NOT-PREPARED-REASON "表の用意が済んでいない(起動の途中)")
;; 記録の handler が中で待ち(doeff-time の WaitWithin — 変化の待ちの呼び鈴の待ち)を撃つ公開 effect(頭の註の止めの印と要求ごとの計時 —
;; 待ちの刻の答え手と止めの切り手はこの型の要求にだけ被せる)。
(val WAITING-EFFECTS #(WatchChanges WatchEvents))
;; 手入れの係の書き手の名(手入れの effect は書き手の許可を通らない — 行を書かない)。
(val MAINTAINER "records-maintenance")
;; 検の殻: 止めの合図を問い直す間隔・待ち受けが開いて表の用意を告げるのを待つ上限・閉じて run が終わるのを待つ上限の秒と、止めの理由。
(val SHELL-STOP-POLL-SECONDS 0.02)
(val SHELL-OPEN-SECONDS 30.0)
(val SHELL-CLOSE-SECONDS 10.0)
(val SHELL-STOP-REASON "検の殻の close")
;; 検の殻の計算の pool の thread の数(束の読みの読み解きと綴り — 本番の土台の口は main.hy の COMPUTE-WORKERS)。
(val SHELL-COMPUTE-WORKERS 2)


;; --- 入口の設定 -------------------------------------------------------------------------------------------------------------------

(defrecord MaintenancePlan
  "手入れの周期の設定: interval-seconds = 拍の秒・keep-seconds = 変更の列に残す秒(maintenance.hy の PruneChanges)・archive = 刈る前に
   古い変更を送る関数(keep-seconds → 答えが ArchivedThrough | Unreachable の Program — 刈りはその番号まで・None = 送らない置き場。
   maintenance.hy の頭の註の「消す前に送る」)・archive-events = 印(StreamDecl.archive-before-retire)の列の期限を過ぎた出来事を送る
   関数(出来事の頁 → 答えが EventsArchived | Unreachable の Program — 確かめた出来事だけを外す・None = 送らない。印の列を宣言した
   置き場では要る — unsent-streams。maintenance.hy の頭の註の「出来事を送ってから外す」)。"
  (#^ float interval-seconds)
  (#^ float keep-seconds)
  (setv #^ (| Callable None) archive None)
  (setv #^ (| Callable None) archive-events None))


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
   served = 走っている木と世代(GET /served が答える・None = 知らない — 答えの commits と instance は null・#2742)・
   callers = 一覧の読みを呼び手ごとに数える宣言の呼び手の名(X-Records-Caller の名乗りをこの名に限って数える — 空 = 名乗りは全部 undeclared)・
   compute-workers = 組み立てが開いた計算の pool の thread の数(GET /metrics の records_compute_workers — 利用率の割る数・None = 名乗らない)。"
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
  (setv #^ (| ServedBuild None) served None)
  (setv #^ (get tuple #(str ...)) callers #())
  (setv #^ (| int None) compute-workers None))


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

(defeffect KeepPreparedHandlers
  "表の用意が済んだ — 書き手の名 → 記録の handler の関数を置く。答えは None。"
  {:fields [(: handler-for Callable)] :answer None :tags {:context "records" :role "entry"}})

(defeffect PreparedHandlers
  "用意で置いた 書き手の名 → 記録の handler の関数を読む(用意の前は None)。"
  {:fields [] :answer (| Callable None) :tags {:context "records" :role "entry"}})

(defclass MarkBook []
  "要求 1 つの刻の帳面(頭の註の要求ごとの計時): 要求の task が自分で読んだ刻と、記録の handler の包みが StampRequest で打たせた刻を、
   打った順に持つ。作るのも読むのも要求の task(answer-arrival)で、包みと待ちの刻も同じ task の中で StampRequest の答え手が打つ —
   持ち主が 1 つの task なので値の型ではなく帳面。札ごとの刻を session の値に控えて読み書きしない(要求ごとに控えの Get と Put と
   TakeMarks が土台まで往復していた — card ki-5c3e73834e73)。"

  (deff __init__ [self]  ; defk にできない: 帳面の class の初期化
    {:pre [(: self MarkBook)] :post [(: % None)]}
    "刻の無い帳面にするため。"
    (setv self.marks #()))

  (deff stamp [self mark at]  ; defk にできない: 要求の task と StampRequest の答え手の節が刻を打つ帳面の口
    {:pre [(: self MarkBook) (: mark RequestMark) (: at float)] :post [(: % None)]}
    "刻 mark を単調時計の秒 at と組にして帳面の末尾に足すため。"
    (setv self.marks (+ self.marks #((MarkAt :mark mark :at at))))))


(defeffect StampRequest
  "要求の刻の帳面 book に刻 mark を打つ(刻の答え手 request-stamps が、土台の単調時計の秒と組にして打つ — 頭の註の要求ごとの計時)。答えは None。"
  {:fields [(: book MarkBook) (: mark RequestMark)] :answer None :tags {:context "records" :role "entry"}})

(defeffect CloseWaits
  "入口が止まる — 要求の task の中の置き場の待ちを止めの印(理由 reason)で起こし、以後の待ちを待たせない(#3713)。答えは None。"
  {:fields [(: reason str)] :answer None :tags {:context "records" :role "entry"}})

(defeffect ClosingWaits
  "止めの印を読む: 止めた後なら印(WaitsClosed)・止めの前なら印で完了する門(promise)。"
  {:fields [] :answer (| WaitsClosed Promise) :tags {:context "records" :role "entry"}})


(defclass RequestLedger []
  "走り中の要求の Task の台帳(受けの loop が待ち受けの閉じの後に残りを待てるようにするため)。作るのは受けの loop(receive-requests)で、
   受けの loop が Spawn した task を載せ(track)、要求の task が答え終えたら外す(settle)— どちらも同じ scheduler の thread の上の
   task なので値の型ではなく帳面。session の値に控えて読み書きしない(要求ごとに Track と Settle の effect と控えの Get と Put が土台まで
   往復していた — card ki-5c3e73834e73)。"

  (deff __init__ [self]  ; defk にできない: 帳面の class の初期化
    {:pre [(: self RequestLedger)] :post [(: % None)]}
    "空の台帳にするため。tickets = 札 → 走り中の要求の Task か、track より先に答え終えた札の None(Spawn の直後に子が先に走っても台帳に
     終わった task を残さない)。"
    (setv self.tickets {}))

  (deff track [self ticket task]  ; defk にできない: 受けの loop が要求の task を載せる帳面の口
    {:pre [(: self RequestLedger) (: ticket str) (: task Task)] :post [(: % None)]}
    "受けの loop が Spawn した要求の task を台帳に載せるため(先に答え終えた札なら外すだけ)。"
    (setv self.tickets (if (and (in ticket self.tickets) (is (get self.tickets ticket) None))
                           (dfor [k t] (.items self.tickets) :if (!= k ticket) k t)
                           (| self.tickets {ticket task}))))

  (deff settle [self ticket]  ; defk にできない: 要求の task が答え終えた事を書く帳面の口
    {:pre [(: self RequestLedger) (: ticket str)] :post [(: % None)]}
    "要求の task が答え終えた札を台帳から外すため(まだ載っていなければ、答え終えた印を置く)。"
    (setv self.tickets (if (in ticket self.tickets)
                           (dfor [k t] (.items self.tickets) :if (!= k ticket) k t)
                           (| self.tickets {ticket None}))))

  (deff pending [self]  ; defk にできない: 受けの loop が閉じの後に残りを読む帳面の口
    {:pre [(: self RequestLedger)] :post [(: % tuple)]}
    "台帳に残る走り中の要求の Task の列を読むため。"
    (tuple (gfor task (.values self.tickets) :if (is-not task None) task))))


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
  (AppendEvents [appends] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadEvents [stream after limit] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadStreamEnd [stream] (resume (Unreachable NOT-PREPARED-REASON)))
  (ReadEventByKey [stream idempotency-key] (resume (Unreachable NOT-PREPARED-REASON))))


(defhandler request-stamps
  "記録の handler の包みが打つ要求の刻(StampRequest — handler の入りと出と待ち)を、要求ごとの外側の handler の時計ではなく土台の時計
   (GetMonotonic — 受けた刻と同じ物差し)の秒と組にして要求の刻の帳面に打ち、答えが決まった後に区間の秒を割れるようにするため(頭の註の
   要求ごとの計時)。受けの loop に被せる。"
  {:tags {:context "records" :role "entry"}}
  (StampRequest [book mark]
    (<- at float (GetMonotonic))
    (.stamp book mark at)
    (resume None)))


(defhandler wait-stamps [#^ MarkBook book]
  "記録の handler の中の待ち(doeff-time の WaitWithin — 変化の待ちの long-poll の置き場の待ち)の入りと起きに刻を打ち、待った秒と
   起きてからの秒を分けて測れるようにするため(待ちはそのまま外側の時計へ渡す)。"
  {:tags {:context "records" :role "entry"}}
  ;; 引数に残す理由: book は包んだ要求の刻の帳面(要求ごとに stamped-handling が被せる — Ask で読む設定ではない)。
  (WaitWithinEffect []
    (<- (StampRequest :book book :mark RequestMark.WAIT-IN))
    (<- woke effect)
    (<- (StampRequest :book book :mark RequestMark.WOKE))
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


(defk send-answer [ticket answer timing]
  {:pre [(: ticket str) (: answer (| HttpAnswer TextAnswer)) (: timing (get tuple #(HttpHeader ...)))] :post [(: % None)]
   :tags {:context "records" :role "entry"}}
  "答え(status と本文)を札の要求へ送るため。本文の種は答えの型が決める(記録の操作と断り = JSON・/metrics = 答えが名乗る text)。
   timing = 足して送る区間の見出し(記録の操作の答えの Server-Timing — timing-headers・他の答えは空)。"
  (val content-type (match answer
                      (TextAnswer :content-type declared) declared
                      (HttpAnswer) JSON-CONTENT-TYPE))
  ;; 綴りと同じ所で作った byte 列が在れば綴り直さない(束の読みの答えは scheduler の thread の外で作る — loop_work.hy の頭の註)。
  (val data (if (and (isinstance answer HttpAnswer) (is-not answer.data None))
                answer.data
                (.encode answer.body "utf-8")))
  (<- (HttpRespond :ticket ticket :status answer.status
                   :headers (+ #((HttpHeader :name "Content-Type" :value content-type)
                                 (HttpHeader :name "Content-Length" :value (str (len data))))
                               timing)
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


(defk with-compute-load [snapshot load workers]
  {:pre [(: snapshot MeterSnapshot) (: load ComputeLoad) (: workers (| int None))] :post [(: % MeterSnapshot)]
   :tags {:context "records" :role "entry"}}
  "計器の断面に計算の pool の負荷の系列(頭の註の COMPUTE-*-METRIC)を足すため(GET /metrics の描きの時だけ — 計器へは積まない。並びと
   走りと thread の数 workers は今の値〔workers = None は組み立てが名乗らない — 系列を描かない〕・待ちと回りは答え手を組んだ時からの累計。
   回りの回数は回り終えた仕事の数 = started − running)。"
  (MeterSnapshot :counters snapshot.counters
                 :gauges (.updated snapshot.gauges (| {COMPUTE-QUEUED-METRIC (float load.queued)
                                                       COMPUTE-RUNNING-METRIC (float load.running)}
                                                      (if (is workers None) {} {COMPUTE-WORKERS-METRIC (float workers)})))
                 :durations (.updated snapshot.durations {COMPUTE-WAIT-METRIC (SecondsTotal :total load.waited-seconds
                                                                                            :count load.started)
                                                          COMPUTE-RUN-METRIC (SecondsTotal :total load.ran-seconds
                                                                                           :count (- load.started load.running))})))


(defk metrics-answer [schema workers]
  {:pre [(: schema RecordsSchema) (: workers (| int None))] :post [(: % TextAnswer)] :tags {:context "records" :role "entry"}}
  "GET /metrics に答えるため: 計器の断面(ReadMeter)に計算の pool の負荷(ReadComputeLoad と pool の thread の数 workers — with-compute-load)を
   足し、Prometheus の text に描く(身元を問わない — 数だけで、行の中身を載せない)。説明は固定の系列と、宣言の表 schema の一覧の読みの
   系列と、宣言の表と列の書きの錠の系列の分(request_timing の lock-metric-helps)。"
  (<- meter MeterSnapshot (ReadMeter))
  (<- load ComputeLoad (ReadComputeLoad))
  (<- snapshot MeterSnapshot (with-compute-load meter load workers))
  (<- list-rows-helps (get FrozenMap str) (list-rows-metric-helps (tuple schema.tables)))
  (<- lock-helps (get FrozenMap str) (lock-metric-helps (tuple schema.tables) (tuple schema.streams)))
  (<- text str (render-prometheus snapshot (.updated (.updated METRIC-HELPS list-rows-helps) lock-helps)))
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


(defk count-list-rows [caller ask answer]
  {:pre [(: caller str) (: ask ListRows) (: answer (| Page Reset NotIndexed Unreachable))] :post [(: % None)]
   :tags {:context "records" :role "entry"}}
  "置き場が一覧の読み ask に答えた時に、表ごとと呼び手 caller × 表ごとの呼びの数と返した行の数を計器に足し、どの呼び手がどの表を
   読み通しているかを /metrics の数で言えるようにするため(頭の註の一覧の読みの数 — 届かない答え Unreachable は 503 の系列が数える)。"
  (when (isinstance answer Unreachable)
    (return None))
  (<- counts tuple (list-rows-counts caller ask.table (if (isinstance answer Page) (len answer.rows) 0)))
  (for [#(name amount) counts]
    (<- (CountMetric :name name :amount amount)))
  None)


(defk stamped-handling [book caller record-handler body]
  {:pre [(: book MarkBook) (: caller str) (: record-handler Callable) (: body (| Program EffectBase))] :post [(: % "body の答え")]
   :tags {:context "records" :role "entry"}}
  "記録の handler record-handler の下で body(要求の公開 effect)を撃ち、要求の刻の帳面 book に handler の入りと出の刻を打つため(handler の中の
   待ちの刻は wait-stamps — 頭の註の要求ごとの計時)。待ちの刻の答え手と止めの切り手は、待つ操作(WAITING-EFFECTS)にだけ被せる(ほかの
   操作に要求ごとの handler を組まない)。一覧の読みなら答えを呼び手 caller の名で数える(count-list-rows)。answer-with が
   書き手の名ごとの handler をこの包み(BodyWrapper)にして service へ渡す。"
  (<- (StampRequest :book book :mark RequestMark.HANDLER-IN))
  ;; 待ちの切り手は待ちの刻の答え手の外側(待ちの刻の答え手が待ちの入りと起きを、切り手の Race ごと測る)。
  (val answer (if (isinstance body WAITING-EFFECTS)
                  (! (with-handlers [closing-cuts-waits (wait-stamps book) record-handler] body))
                  (! (with-handlers [record-handler] body))))
  (<- (StampRequest :book book :mark RequestMark.HANDLER-OUT))
  (when (isinstance body ListRows)
    (<- (count-list-rows caller body answer)))
  answer)


(defk answer-with [serving book request]
  {:pre [(: serving RecordsServing) (: book MarkBook) (: request HttpRequest)] :post [(: % (| HttpAnswer TextAnswer))]
   :tags {:context "records" :role "entry"}}
  "要求 1 つを service.respond で答えるため: 用意が済んでいれば置き場の handler、済んでいなければ store-not-prepared の下で撃つ。
   記録の handler は、要求の handler の入りと出と待ちの刻を要求の刻の帳面 book に打つ包み(stamped-handling)で被せる。
   要求ごとの外側の handler(serving.request-handlers — 検の仮想の時計)を被せる。GET /readyz は置き場を問い(readiness-answer)、
   GET /metrics は計器を描く(metrics-answer — どちらも身元を引く前に答える)。GET /served は走っている木と表の要約を答える
   (served-answer — 身元も表の用意も問わない)。"
  (when (and (= request.method "GET") (= request.path PATH-METRICS))
    (return (! (metrics-answer serving.schema serving.compute-workers))))
  (when (and (= request.method "GET") (= request.path PATH-SERVED))
    (return (! (served-answer serving))))
  (<- prepared (| Callable None) (PreparedHandlers))
  (when (and (= request.method "GET") (= request.path PATH-READYZ))
    (return (! (with-handlers [#* serving.request-handlers] (readiness-answer serving prepared)))))
  (val handler-for (if (is prepared None) (fn [writer] store-not-prepared) prepared))
  (<- caller str (list-rows-caller request.caller serving.callers))
  (<- service (records-service serving.schema (fn [writer] (BodyWrapper stamped-handling book caller (handler-for writer)))))
  (<- answer (with-handlers [#* serving.request-handlers]
                            (respond service request)))
  answer)


(defk place-answer-metrics [serving]
  {:pre [(: serving RecordsServing)] :post [(: % None)] :tags {:context "records" :role "entry"}}
  "要求の数の系列(wire の ANSWER-METRICS)と表ごとの一覧の読みの系列(宣言の表と呼び手の閉じた組 — 名が重なる宣言は起動を止める)を
   全部 0 で置くため(起動の時 1 回 — 読み手が「無い」と「0」を区別しなくて済み、区間の差が最初の 1 つ目の増えを取りこぼさない)。"
  (<- list-rows tuple (list-rows-metrics (tuple serving.schema.tables) serving.callers))
  (for [metric (+ ANSWER-METRICS list-rows)]
    (<- (CountMetric :name metric :amount 0.0)))
  None)


(defk timing-headers [value]
  {:pre [(: value str)] :post [(: % (get tuple #(HttpHeader ...)))] :tags {:context "records" :role "entry"}}
  "記録の操作の答えに、答えが決まった所までの区間の秒を見出し Server-Timing で載せるため(value = 載せる区間の綴り — request_timing の
   server-timing-spans-of で選び wire の server-timing-spelled で綴った値。呼び手が往復 1 本のうち service の受けの列と中の分を要求ごとに
   名指せる)。載せる区間が 1 つも無い答え(空の綴り)は見出しを足さない。記録の操作でない route(/metrics など)は deliver が呼ばない。"
  (if value #((HttpHeader :name SERVER-TIMING-HEADER :value value)) #()))


(defk sent-answer [arrival answer timing]
  {:pre [(: arrival HttpRequestArrived) (: answer (| HttpAnswer TextAnswer)) (: timing (get tuple #(HttpHeader ...)))]
   :post [(: % (| HttpAnswer TextAnswer))] :tags {:context "records" :role "entry"}}
  "答えを札へ送り、実際に送った答えを返すため(timing = 足して送る区間の見出し — timing-headers)。送りが例外になれば(答えを byte に
   する所・送りの effect の失敗)、標準の誤りへ 1 行名指して 500 internal を 1 度だけ送り直す(答えの無い札を残さない — 送り直しは見出しを
   足さない)。"
  (try
    (<- (send-answer arrival.ticket answer timing))
    (return answer)
    (except [e Exception]
      (print (.format "記録の service: {} {} の答えを送れなかった — 500 internal で送り直す: {}: {}"
                      arrival.method arrival.target (. (type e) __name__) e)
             :file sys.stderr :flush True)))
  (<- internal HttpAnswer (refusal-answer ERROR-INTERNAL "答えを送る途中で落ちた"))
  (<- (send-answer arrival.ticket internal #()))
  internal)


(defk stage-observations [operation stages]
  {:pre [(: operation str) (: stages (get tuple #(StageSeconds ...)))] :post [(: % (get tuple #(SecondsObservation ...)))]
   :tags {:context "records" :role "entry"}}
  "記録の操作 operation の要求 1 つの区間の秒 stages(request_timing の request-stages と sent-stages)を、操作ごと・区間ごとの秒の観測の
   列にするため(deliver が計器の効果 1 回 — ObserveSecondsBatch — で積み、GET /metrics でどの区間が遅いかを読めるようにする — 頭の註の
   要求ごとの計時)。計器の名は STAGE-METRIC-NAMES から引く(区間ごとに綴らない)。"
  (val names (get STAGE-METRIC-NAMES operation))
  (tuple (gfor stage stages (SecondsObservation :name (get names stage.stage) :seconds stage.seconds))))


(defk deliver [arrival path answer marks ledger]
  {:pre [(: arrival HttpRequestArrived) (: path str) (: answer (| HttpAnswer TextAnswer)) (: marks (get tuple #(MarkAt ...)))
         (: ledger RequestLedger)]
   :post [(: % None)] :tags {:context "records" :role "entry"}}
  "答え 1 つを札へ送り(sent-answer)、待ち受けへ渡した答えの status を計器に 1 つ数え、台帳から外すため。数えるのはこの 1 か所で、
   送り直した 500 も、ここで 500 として数える(渡せなかった答えの status は数えない)。待ち受けの側で後から落ちた送り(相手が先に切った・
   札が待ち受けに無い — 本物の待ち受けは答えを loop へ積むだけで戻る・#3688 の子 (3) の 3b)は、待ち受けが標準の誤りへ 1 行名乗るだけで、
   ここの数えは直さない(渡した答えの status のまま)。送りの effect は答えを待ち受けへ渡すだけで、
   この task は同じ scheduler の上で続けて数えるので、答えを受け取った呼び手が次に送る要求より先に数えが済む。計器が落ちても
   答えは送ってあり、台帳 ledger からは外す(誤りはそのまま上げる)。marks = 要求の刻の帳面の答えが決まるまでの刻(始まり・本文の読み終わり・
   handler の入りと出と待ち・答えの決まり — 打った順)。送る前に区間の秒を 1 度割って答えの見出しに載せ、送り終えた刻を読んで、記録の
   操作の要求なら、送り終えた刻で決まる区間(sent-stages)を足して計器に積む(stage-observations — 頭の註の要求ごとの計時・刻の列を送った
   後に割り直さない)。区間と見出しと数えの名の計算は、どれも他の defk を呼ばない関数をここから 1 段ずつ呼ぶ(要求ごとに Program を回さない
   — card ki-5c3e73834e73)。"
  (val operation (! (operation-of path)))
  (val wait (! (waited marks)))
  (var stages #())
  (try
    (:= stages (if (is operation None) #() (! (request-stages arrival.received-at marks wait))))
    (val timing (if (is operation None)
                    #()
                    (! (timing-headers (! (server-timing-spelled (! (server-timing-spans-of stages))))))))
    (<- sent (| HttpAnswer TextAnswer) (sent-answer arrival answer timing))
    (<- sent-at float (GetMonotonic))
    (val metric (! (answer-metric (! (operation-kind operation)) sent.status)))
    (<- (CountMetric :name metric :amount 1.0))
    (finally
      (.settle ledger arrival.ticket)))
  (when (is-not operation None)
    (<- (ObserveSecondsBatch :observations (! (stage-observations operation (+ stages (! (sent-stages arrival.received-at marks wait sent-at))))))))
  None)


(defk answer-arrival [arrival serving ledger]
  {:pre [(: arrival HttpRequestArrived) (: serving RecordsServing) (: ledger RequestLedger)] :post [(: % None)]
   :tags {:context "records" :role "entry"}}
  "要求 1 つに答える task の本体: 本文を読み → 入口が本文を断った答え(400)はそのまま、読めれば answer-with で答えを決め → 送って計器に
   数える(deliver)。例外でも必ず答える(決まる前に落ちれば 500 internal・送りが落ちれば 500 internal で送り直す — 答えの無い札を残さない)。
   数えるのは実際に送った答えの 1 か所だけ(本文の断りの 400 も、落ちた時の 500 も同じ所)。実装の誤りの追跡は標準の誤りへ残す(黙って
   捨てない)。終われば台帳 ledger から外す。
   task の始まり・本文の読み終わり・答えの決まりの刻を土台の時計から読んで、要求の刻の帳面に打つ(handler の入りと出と待ちの刻は
   包みが同じ帳面に打つ — 頭の註の要求ごとの計時)。"
  (<- started float (GetMonotonic))
  (val book (MarkBook))
  (.stamp book RequestMark.STARTED started)
  (val path (get (.split arrival.target "?" 1) 0))
  (var decided None)
  (try
    (<- body (| bytes HttpAnswer) (request-body arrival serving.max-bytes))
    (<- read-at float (GetMonotonic))
    (.stamp book RequestMark.READ read-at)
    (:= decided (if (isinstance body HttpAnswer)
                    body
                    (! (answer-with serving book (HttpRequest arrival.method path body
                                                              (! (header-value arrival.headers WRITER-HEADER))
                                                              (! (header-value arrival.headers CALLER-HEADER))
                                                              (! (header-value arrival.headers IDEMPOTENCY-HEADER)))))))
    (except [e Exception]
      (print (.format "記録の service: {} {} の答えの途中で落ちた: {}: {}" arrival.method arrival.target (. (type e) __name__) e)
             :file sys.stderr :flush True)
      (traceback.print-exc))
    (finally
      (<- decided-at float (GetMonotonic))
      (.stamp book RequestMark.DECIDED decided-at)
      (val final (if (is decided None) (! (refusal-answer ERROR-INTERNAL "要求の task が答える前に落ちた")) decided))
      (<- (deliver arrival path final book.marks ledger))))
  None)


;; --- 受けの loop・止めの見張り・用意と手入れの task ----------------------------------------------------------------------------------

(defk receive-requests [serving]
  {:pre [(: serving RecordsServing)] :post [(: % ServingEnded)] :tags {:context "records" :role "entry"}}
  "待ち受けの出来事を受け、要求ごとに task を立てて台帳(RequestLedger — 受けの loop が持つ)に載せる。待ち受けが閉じたら、走り中の
   要求を Gather で待ってから返るため。"
  (val ledger (RequestLedger))
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (match event
      (HttpServerClosed :reason reason)
        (do (val pending (.pending ledger))
            (when pending
              (<- (Gather #* pending)))
            (return (ServingEnded :reason reason)))
      (HttpRequestArrived :ticket ticket)
        (do (<- task Task (Spawn (answer-arrival event serving ledger)))
            (.track ledger ticket task))
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
  (<- (with-handlers [(handler-for MAINTAINER)] (maintenance-loop plan.interval-seconds plan.keep-seconds plan.archive None
                                                                  plan.archive-events)))
  None)


(defk unsent-streams [schema plan]
  {:pre [(: schema RecordsSchema) (: plan (| MaintenancePlan None))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "records" :role "entry"}}
  "印(StreamDecl.archive-before-retire)の列を宣言したのに、手入れの計画が出来事の送りの関数を持たない列の名(名の順)を挙げるため —
   印の列の期限を過ぎた出来事は送って確かめた物しか外れないので、送りの無い手入れでは置き場に溜まり続ける(serve-records が起動の前に
   断る)。手入れを立てない組み立て(plan = None — 検の殻・模擬)は回収もしないので挙げない。"
  (if (or (is plan None) (is-not plan.archive-events None))
      #()
      (tuple (sorted (gfor #(name decl) (.items schema.streams) :if decl.archive-before-retire name)))))


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
  (<- unsent (unsent-streams serving.schema serving.maintenance))
  (when unsent
    (raise (ValueError (.format "手入れの計画が出来事の送りの関数(MaintenancePlan.archive-events)を持たないのに、送ってから外す列が在る: {}"
                                (.join "・" unsent)))))
  (<- (place-answer-metrics serving))
  ;; 待ち受けは port の共有(share-port — SO_REUSEPORT)で開く: 同じ置き場を答える口が同じ port に何本並んでも答えは同じなので、
  ;; 2 本で受けて 1 本が止まっても、kernel は生きている待ち受けへ接続を渡す(ki-a4f46b3d98c5 — 2026-10-09 23:08 の
  ;; 17 分の止まりで、1 本の口が閉じた間の書きが全部 503 になった)。入れ替えで新旧を同じ port に並べる形も同じ共有で開ける。
  (<- bound HttpAddress (HttpListen :address serving.address :share-port True))
  (<- (RecordsListening :address bound))
  ;; 用意を受けの loop より先に立てる(同じ拍に並んだ時に用意が先に走る)。用意の告知は用意の task の終わりが出す(prepare-store)。
  (<- preparing Task (Spawn (prepare-store serving.prepare bound)))
  (<- serving-task Task (Spawn (with-handlers [request-stamps] (receive-requests serving))))
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
   served = 走っている木と世代(RecordsServing の served へそのまま渡す・None = 知らない)/ callers = 一覧の読みを呼び手ごとに数える
   宣言の呼び手の名(RecordsServing の callers へそのまま渡す)。"
  (#^ RecordsSchema schema)
  (#^ Callable handler-for)
  (setv #^ tuple request-handlers #())
  (setv #^ str host "127.0.0.1")
  (setv #^ int port 0)
  (setv #^ (| (get Callable #(... object)) None) meter None)
  (setv #^ (| ServedBuild None) served None)
  (setv #^ tuple callers #()))


(defk records-server-config [schema handler-for [request-handlers #()] [host "127.0.0.1"] [port 0] [meter None] [served None]
                             [callers #()]]
  {:pre [(: schema RecordsSchema) (: handler-for Callable) (: request-handlers tuple) (: host str) (: port int)
         (: meter (| (get Callable #(... object)) None)) (: served (| ServedBuild None)) (: callers tuple)] :post [(: % RecordsServerConfig)]}
  "名簿を取らずに RecordsServerConfig を組む(引数を並べて組む・#3008)。
   引数は RecordsServerConfig の欄と同じ順。"
  (RecordsServerConfig schema handler-for request-handlers host port meter served callers))


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
   待ち受け・時計・Await の橋・計算の pool(aiohttp-http-server・async-time-handler・await-handler・thread-pool-compute-handler)で、
   止めの合図と名乗りと告知だけを shell-control が答える。開けない・用意が落ちたなら例外を上げる。"
  (setv opened (queue.Queue)
        ended (threading.Event)
        ;; 束の読みの読み解きと綴り(Compute)を回す pool — 殻が開いて、殻の止めで閉じる(頭の註の scheduler の thread の仕事の分け)。
        compute-pool (ThreadPoolExecutor :max-workers SHELL-COMPUTE-WORKERS :thread-name-prefix "doeff-records-compute"))
  (setv serving (RecordsServing :address (HttpAddress :host config.host :port config.port) :schema config.schema
                                :prepare (ready-handlers config.handler-for) :request-handlers (tuple config.request-handlers)
                                :max-bytes REQUEST-MAX-BYTES :maintenance None :stop-poll-seconds SHELL-STOP-POLL-SECONDS
                                :drain-seconds 0.0 :meter config.meter :served config.served :callers (tuple config.callers)
                                :compute-workers SHELL-COMPUTE-WORKERS))
  (setv program (with-handlers [(await-handler) (async-time-handler) (state) aiohttp-http-server (thread-pool-compute-handler compute-pool)
                                (shell-control opened.put (fn [] (if (.is-set ended) SHELL-STOP-REASON None)))]
                               (serve-records serving)))
  (setv thread (threading.Thread :target (fn [] (shell-run program opened)) :daemon True :name "doeff-records-http"))
  (.start thread)
  (setv bound (.get opened :timeout SHELL-OPEN-SECONDS))
  (when (isinstance bound BaseException)
    (.shutdown compute-pool :wait False :cancel-futures True)
    (raise bound))
  ;; 殻を閉じる同期の口(検の finally と atexit が呼ぶ — Program の外)。入口の run が終わってから計算の pool を閉じる。
  (setv stop (fn []
               (.set ended)
               (.join thread :timeout SHELL-CLOSE-SECONDS)
               (.shutdown compute-pool :wait False :cancel-futures True)))
  ;; 登録は待ち受けが開いた後: atexit は後に登録した物から走るので、doeff の Await の橋の閉じより先に入口の run を終えられる。
  (atexit.register stop)
  (RunningServer (.format "http://{}:{}" bound.host bound.port) stop))
