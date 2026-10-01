;;; 壁の時計の sim-cluster(doeff_cluster.sim.local の wall-sim-cluster)の検(test_local_wall_clock.hy)の service と task の Program の見本。
;;;
;;; どの Program も sim の土台で本体を包む(scheduler と時計は sim-cluster の外側が答える)。本物の待ち受けを持つ service は、土台に
;;; Await の答え手(await-handler)と aiohttp の待ち受けを並べる(wall-io-foundation)— 柵は Await を通さない(host_contract.SIM-PASSABLE)
;;; ので、本番の土台と同じく job の中の await-handler が Await を外部の promise(CreateExternalPromise・Wait — 柵を通る)に変える。
;;; 外の thread の客(talk-over-ws)は Program の外の本物の socket の客で、筋書きが Await で待つ(壁の時計の入口の await-handler が答える)。
(require doeff-hy.macros [defk deff defsystem <- val var])
(require doeff-hy.record [defrecord])
(import asyncio)
(import json)
(import time)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff [with-handlers EffectBase Program])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.http_server_effects [HttpAddress HttpListen HttpNextRequest HttpRequestArrived HttpServerClosed WsAccept
                                                WsTextArrived WsSendText HttpShutdown])
(import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
(import doeff_time [Delay GetMonotonic])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [SharedRows])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached DetachedSubmitted DetachedSucceeded])
(import tests.fixtures.sim_programs [sim-task-foundation])

(val NET (frozenset ["cluster-net"]))
(val POLL-SECONDS 0.1)          ; 筋書きが盤を読み直す間隔
(val TOOL-KEY "wall/tool")      ; ws の service が道具の始まりを書いて読み直す盤の行


;; --- 土台 ----------------------------------------------------------------------------------------------

(defk wall-io-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の待ち受けを持つ sim の土台: session の値の置き場(state)・Await の答え手・aiohttp の待ち受け。scheduler と時計は sim-cluster の
   外側が答える。"
  (<- answer (with-handlers [(state) (await-handler) aiohttp-http-server] body))
  answer)


;; --- 切り離した task の往復 ----------------------------------------------------------------------------------

(defk slow-task [foundation seconds n]
  {:pre [(: foundation Callable) (: seconds float) (: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "task: seconds 秒待ってから 100 + n を返す(時間のかかる task の見本)。"
  (<- total int (foundation (slow-sum seconds n)))
  total)


(defk slow-sum [seconds n]
  {:pre [(: seconds float) (: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 秒待ってから 100 + n を返す。"
  (<- (Delay seconds))
  (+ 100 n))


(defk submitter-body [seconds n key]
  {:pre [(: seconds float) (: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "時間のかかる task を 1 本 SubmitDetached で出し、AwaitDetached で答えを待ち、答えと送った・受けた時刻(系の中の時計の epoch ms)を盤の key に
   書いてから、準備できたと報告し続ける。"
  (<- sent int (now-epoch-ms))
  (<- submitted DetachedSubmitted (SubmitDetached (slow-task sim-task-foundation seconds n) :key "wall-task" :needs NET :name "slow"))
  (<- outcome (AwaitDetached submitted.key))
  (<- answered int (now-epoch-ms))
  (<- (WriteShared key {"created" submitted.created
                        "value" (if (isinstance outcome DetachedSucceeded) outcome.value None)
                        "outcome" (. (type outcome) __name__)
                        "sentMs" sent
                        "answeredMs" answered}))
  (while True
    (<- (ReportReady True "受けた"))
    (<- (Delay 1.0)))
  0)


(defk submitter-program [foundation seconds n key]
  {:pre [(: foundation Callable) (: seconds float) (: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: submitter-body を土台で包む。"
  (<- r int (foundation (submitter-body seconds n key)))
  r)


;; --- 外の thread の客と話す service ----------------------------------------------------------------------

(defk say [ticket kind]
  {:pre [(: ticket str) (: kind str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "ws の客へ 1 通 {kind atMs board} を送る。atMs = 系の中の時計の epoch ms・board = 盤の TOOL-KEY の行の startedMs(無ければ None)。
   答え = atMs。"
  (<- at int (now-epoch-ms))
  (<- rows dict (ReadShared TOOL-KEY))
  (val board (.get (.get rows TOOL-KEY {}) "startedMs"))
  (<- (WsSendText :ticket ticket :text (json.dumps {"kind" kind "atMs" at "board" board})))
  at)


(defk use-the-tool [ticket seconds]
  {:pre [(: ticket str) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "道具の 1 回(時間のかかる外の道具の見本): すぐ started を返し、始まりの時刻を盤に書いて、seconds 秒の後に done を返す
   (done の board = 盤から読み直した始まりの時刻 — 話の途中で coordinator と往復する)。"
  (<- started int (say ticket "started"))
  (<- (WriteShared TOOL-KEY {"startedMs" started}))
  (<- (Delay seconds))
  (<- (say ticket "done"))
  None)


(defk listener-body [key seconds]
  {:pre [(: key str) (: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の待ち受けを開いて結んだ port を盤の key に書き、ws の客と話す(tool = 道具の 1 回・bye = 待ち受けを閉じる)。閉じた後は準備
   できたと報告し続ける。答え = 受けた 1 通の数。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (<- (WriteShared key {"port" bound.port}))
  (var heard 0)
  (var serving True)
  (while serving
    (<- event (HttpNextRequest))
    (match event
      (HttpRequestArrived :ticket ticket) (<- (WsAccept :ticket ticket))
      (WsTextArrived :ticket ticket :text "tool") (do (:= heard (+ heard 1))
                                                      (<- (use-the-tool ticket seconds)))
      (WsTextArrived :text "bye") (do (:= heard (+ heard 1))
                                      (<- (HttpShutdown :reason "客が bye を送った")))
      (HttpServerClosed) (:= serving False)
      _ None))
  (while True
    (<- (ReportReady True "閉じた"))
    (<- (Delay 1.0)))
  heard)


(defk listener-program [foundation key seconds]
  {:pre [(: foundation Callable) (: key str) (: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: listener-body を土台で包む。"
  (<- r int (foundation (listener-body key seconds)))
  r)


;; --- 筋書きと外の thread の客 ------------------------------------------------------------------------------

(defk rows-when-present [prefix key deadline-seconds]
  {:pre [(: prefix str) (: key str) (: deadline-seconds float)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 盤の key が現れるまで POLL-SECONDS おきに prefix の行を読む(deadline-seconds を過ぎたら、その時の行を返す)。"
  (<- started float (GetMonotonic))
  (var rows {})
  (var waiting True)
  (while waiting
    (<- read dict (SharedRows prefix))
    (:= rows read)
    (<- now float (GetMonotonic))
    (if (or (in key rows) (> (- now started) deadline-seconds))
        (:= waiting False)
        (<- (Delay POLL-SECONDS))))
  rows)


(defrecord Heard
  "外の thread の客が受けた 1 通: body = 本文(JSON)・after-seconds = tool を送ってから届くまでの秒(客の単調時計)・client-ms = 届いた時の
   客の時計の epoch ms。"
  (#^ dict body)
  (#^ float after-seconds)
  (#^ int client-ms))


(defn :async #^ tuple talk [#^ int port]
  "外の thread の本物の socket の客: ws で繋ぎ、tool を送って 2 通(started・done)を受け、bye を送る。"
  (import aiohttp)
  (with [:async session (aiohttp.ClientSession)]
    (with [:async ws (.ws-connect session (.format "http://127.0.0.1:{}/ws" port))]
      (setv sent (time.monotonic))
      (await (.send-str ws "tool"))
      (setv heard [])
      (for [_ (range 2)]
        (setv message (await (.receive ws :timeout 10)))
        (.append heard (Heard :body (json.loads message.data) :after-seconds (- (time.monotonic) sent)
                              :client-ms (// (time.time-ns) 1000000))))
      (await (.send-str ws "bye"))
      (tuple heard))))


(deff talk-over-ws [#^ int port]  ; defk にできない: 外の library(asyncio.to_thread)が外の thread で呼ぶ callback
  {:pre [(: port int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "protocol"}}
  "外の thread の客: 自分の event loop で talk を回す(答え = Heard の tuple)。"
  (asyncio.run (talk port)))


(defk talk-to-the-listener [key deadline-seconds]
  {:pre [(: key str) (: deadline-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 盤に port が載るのを待ち、外の thread の客(talk-over-ws)を回して、客が受けた 1 通の列を返す。"
  (<- rows dict (rows-when-present "wall/" key deadline-seconds))
  (<- heard tuple (Await (asyncio.to-thread talk-over-ws (get rows key "port"))))
  heard)


;; --- 系 ------------------------------------------------------------------------------------------------

(defsystem submitters [foundation]
  "見本の系: 0.5 秒かかる task を切り離して出し、答えを待つ service 1 つ"
  (submitter (submitter-program foundation 0.5 3 "wall/task") :needs #{"cluster-net"}))


(defsystem listeners [foundation]
  "見本の系: 本物の待ち受けで外の客と話す service 1 つ(道具は 1 秒)"
  (listener (listener-program foundation "wall/address" 1.0) :needs #{"cluster-net"}))
