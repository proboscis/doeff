;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の、答え手の片方だけが持つ性質の検。両方に共通の性質(答えの届き方・HEAD と
;;; 本文の無い答え・中継の status と 502・ws の往復と閉じ・断り・送りの上限の切り・閉じた後・本文の読み)は test_http_server_contract.hy
;;; の契約テストが両方の答え手で回す。
;;;   - 台本の答え手(scripted-http-server): 中継先の最長の一致と ws を受けない先の 502・読めない file の範囲の理由・読まない相手の箱が
;;;     溜まって切られる勘定・走っている台本への後足し・読みの途中で相手が切った本文。
;;;   - 本物の答え手(aiohttp-http-server): HTTP の中継の本文と X-Forwarded-Proto・ws の中継(frame の往復と close の状態符)・相手が先に
;;;     切った要求への答えの 1 行の名乗りと数え(traceback を出さない — #2757)・port 0 で
;;;     結んだ port・出来事の received-at・本体の流れ(共有の event loop か scheduler)を塞いでも probe の口が答える(#2776)・
;;;     答え(HttpRespond)・宣言の小さい本文の読み(HttpReadBody)・列に既に在る到着(HttpNextRequest)が共有の event loop へ入らない
;;;     (#3688 の子 (3) の 3b・3c と案 1)
;;;     (aiohttp の無い venv では skip)。
(require doeff-hy.macros [deftest defk deff defhandler <- val var with-handler])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import http.client)
(import pathlib [Path])
(import queue)
(import socket)
(import threading)
(import time)
(import pytest)
(import doeff [EffectBase run with_handlers Program])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])
(import doeff_core_effects.process_latest [process-latest-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpRequestArrived HttpServerClosed HttpListen HttpNextRequest
                                                HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody HttpScript
                                                ScriptedUpstream ReadHttpServed HttpEvent WsAccept WsSendText
                                                HttpShutdown TakeWsSendReport WsSendReport WsTextArrived WsClosed
                                                WsCloseSent AppendHttpScript HttpReadBody HttpBodyRead HttpBodyFailed HttpBodyOutcome ScriptedBody
                                                WS-CUT-REASON HttpProbe HttpProbeAnswer])
(import doeff_core_effects.scripted_http_server [scripted-http-server])


(defk serve-until-closed [site upstream]
  {:pre [(: site str) (: upstream str)] :post [(: % int)]}
  "検の Program: 待ち受けを開き、閉じるまで path ごとに 1 つの命令を撃つ(答え = 受けた要求の数)。/file は site の file の 2〜6 byte・
   /head は HEAD の答え・/relay は upstream へ HTTP・/ws は upstream へ ws・他は本文 1 行。"
  (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (var count 0)
  (while True
    (<- event (| HttpRequestArrived HttpServerClosed) (HttpNextRequest))
    (when (isinstance event HttpServerClosed)
      (return count))
    (:= count (+ count 1))
    (val t event.ticket)
    (cond
      (= event.path "/file") (<- (HttpRespond :ticket t :status 206 :headers #((HttpHeader :name "Content-Length" :value "5"))
                                              :body (HttpBodyFileRange :path site :start 2 :length 5)))
      (= event.path "/head") (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "Content-Length" :value "11"))
                                              :body (HttpNoBody)))
      (.startswith event.path "/relay") (<- (HttpForward :ticket t :url (+ upstream (cut event.target 6 None))))
      (= event.path "/ws") (<- (WsForward :ticket t :url upstream))
      True (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "X-Seen" :value (str (len event.headers))))
                            :body (HttpBodyBytes :data (.encode (+ "hello " event.method) "utf-8")))))))


;; --- 台本の答え手 --------------------------------------------------------------------------------------------------------------

(defn #^ HttpRequestArrived arrival [#^ (| int str) n #^ str path * #^ str [method "GET"] #^ (| str None) [target None]
                                     #^ (get tuple #(HttpHeader ...)) [headers #()] #^ bool [upgrade False]
                                     #^ (| str None) [remote None]]
  "台本の要求 1 つを作るため(ticket = n の文字・target を書かなければ path と同じ)。"
  (HttpRequestArrived :ticket (str n) :method method :path path :target (if (is target None) path target)
                      :headers headers :upgrade upgrade :remote remote))


(defk served-after [site upstream]
  {:pre [(: site str) (: upstream str)] :post [(: % tuple)]}
  "Program を閉じるまで回し、台本の記録を読むため。"
  (<- _count int (serve-until-closed site upstream))
  (<- served tuple (ReadHttpServed))
  served)


(deftest test-the-scripted-server-picks-the-longest-upstream-and-refuses-ws-to-a-plain-one
  (val script (HttpScript :arrivals #((arrival 1 "/relay/api/x" :target "/relay/api/x?q=1") (arrival 2 "/relay/other")
                                      (arrival 3 "/ws" :upgrade True))
                          :upstreams #((ScriptedUpstream :base "http://up" :http-status 201 :accepts-ws False)
                                       (ScriptedUpstream :base "http://up/api" :http-status 202 :accepts-ws True))))
  (<- served tuple (with-handler [(state) (memory-file-handler (MemoryFiles)) (scripted-http-server script)]
                     (served-after "/site/app.js" "http://up")))
  ;; 中継の url は台本の中継先の頭の最長の一致が答える。
  (assert (= (. (get served 0) command) (HttpForward :ticket "1" :url "http://up/api/x?q=1")) served)
  (assert (= (lfor s served s.status) [202 201 502]) served))


(deftest test-a-file-range-that-cannot-be-read-names-the-reason
  (val script (HttpScript :arrivals #((arrival 1 "/file"))))
  (<- served tuple (with-handler [(state) (memory-file-handler (MemoryFiles)) (scripted-http-server script)]
                     (served-after "/gone.js" "http://up")))
  (assert (.startswith (. (get served 0) body) "file を読めない") served))


(defk ws-echo [limit publish]
  {:pre [(: limit int) (: publish Callable)] :post [(: % tuple)]}
  "検の Program: ws に上げて 1 通ごとに答え、閉じるまで回すため(答え = 受けた出来事の列と、最後の送りの勘定)。text \"stop\" = 待ち受けを
   閉じる・他は echo。publish = 結んだ宛先を検へ渡す口。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :ws-send-max-bytes limit))
  (publish bound)
  (var seen #(bound))
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (:= seen (+ seen #(event)))
    (match event
      (HttpServerClosed) (do (<- report WsSendReport (TakeWsSendReport))
                             (return (+ seen #(report))))
      (HttpRequestArrived :ticket t) (<- (WsAccept :ticket t))
      (WsTextArrived :ticket t :text "stop") (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 2.0))
      (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ "echo:" text)))
      _ None)))


(defk do-served [program]
  {:pre [(: program Program)] :post [(: % tuple)]}
  "Program を回してから台本の記録を読むため。"
  (<- answer tuple program)
  (<- served tuple (ReadHttpServed))
  #(answer served))


(deftest test-a-stalled-peer-fills-its-box-until-the-limit-cuts-it
  ;; 読まない相手(b)の箱には 1 通 7 byte が溜まり、次の 1 通で上限 10 byte を超えるので切られる。溜まりは捨てた勘定へ。
  (val script (HttpScript :arrivals #((arrival "b" "/ws" :upgrade True) (WsTextArrived :ticket "b" :text "hi")
                                      (WsTextArrived :ticket "b" :text "hi") (WsTextArrived :ticket "b" :text "never")
                                      (arrival "d" "/ws" :upgrade True) (WsTextArrived :ticket "d" :text "stop"))
                          :stalled (frozenset #("b"))))
  (<- answer tuple (with-handler [(state) (scripted-http-server script)] (do-served (ws-echo 10 (fn [bound] None)))))
  (val events (cut (get answer 0) 1 -1))
  (val report (get (get answer 0) -1))
  (val served (get answer 1))
  ;; 台本は port を結ばない — 渡した宛先のまま。
  (assert (= (get (get answer 0) 0) (HttpAddress :host "127.0.0.1" :port 0)))
  (assert (= (lfor e events (. (type e) __name__))
             ["HttpRequestArrived" "WsOpened" "WsTextArrived" "WsTextArrived" "WsClosed" "WsTextArrived"
              "HttpRequestArrived" "WsOpened" "WsTextArrived" "HttpServerClosed"])
          events)
  (assert (= (lfor e events :if (isinstance e WsClosed) #(e.ticket e.code e.reason)) [#("b" 1006 WS-CUT-REASON)]) events)
  (assert (= [report.queued-frames report.queued-bytes report.flushed-bytes report.dropped-bytes report.cuts] [1 7 0 7 1]) report)
  ;; 閉じた後の b への送りは捨てられ、待ち受けを閉じると開いている d へ close 1000。
  (assert (= (lfor s served :if (isinstance s WsCloseSent) #(s.ticket s.code)) [#("d" 1000)]) served))


(defk append-then-drain []
  {:pre [] :post [(: % tuple)]}
  "走っている台本へ出来事を足すと、尽きた後でも続きが届くことを見るため(答え = 受けた出来事の型の名)。"
  (<- first HttpEvent (HttpNextRequest))
  (<- (AppendHttpScript :arrivals #((arrival "z" "/late"))))
  (<- second HttpRequestArrived (HttpNextRequest))
  (<- third HttpEvent (HttpNextRequest))
  #((. (type first) __name__) second.ticket (. (type third) __name__)))


(defn #^ None test-an-appended-script-is-served-after-the-script-ran-dry []
  (assert (= (run (scheduled (with_handlers [(state) (scripted-http-server (HttpScript :arrivals #()))] (append-then-drain))))
             #("HttpServerClosed" "z" "HttpServerClosed"))))


(defk read-broken-body []
  {:pre [] :post [(: % HttpBodyOutcome)] :tags {:context "http-server" :role "program"}}
  "台本で読みの途中に相手が切った本文を読むため。"
  (<- event HttpRequestArrived (HttpNextRequest))
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket event.ticket :max-bytes 8))
  outcome)


(deftest test-the-scripted-server-names-a-body-the-peer-cut-off
  (val script (HttpScript :arrivals #((arrival 1 "/broken" :method "POST"))
                          :bodies #((ScriptedBody :ticket "1" :data b"12" :failed "相手が途中で切った"))))
  (<- outcome HttpBodyOutcome (with-handler [(state) (scripted-http-server script)] (read-broken-body)))
  (assert (= outcome (HttpBodyFailed :reason "相手が途中で切った")) outcome))


(defk two-arrivals []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server" :role "program"}}
  "台本の要求を 2 つ受けるため(答え = 受けた 2 つの出来事)。"
  (<- first HttpRequestArrived (HttpNextRequest))
  (<- second HttpRequestArrived (HttpNextRequest))
  #(first second))


(deftest test-the-scripted-server-hands-over-the-remote-the-script-names
  ;; 送り元の address は台本の書き手が載せた値のまま届き、載せなければ None(名乗れない)。
  (val script (HttpScript :arrivals #((arrival 1 "/a" :remote "203.0.113.7") (arrival 2 "/b"))))
  (<- events tuple (with-handler [(state) (scripted-http-server script)] (two-arrivals)))
  (assert (= (lfor e events e.remote) ["203.0.113.7" None]) events))


;; --- 本物の答え手 --------------------------------------------------------------------------------------------------------------

(defn #^ int free-port []
  "空いている port を 1 つ OS に選ばせて、その番号を返すため。"
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defn #^ None test-the-aiohttp-server-relays-http-bodies-and-ws-frames [#^ Path tmp-path]
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import aiohttp [web WSMsgType])
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv port (free-port) seen [])
  (defk serve-on-port [upstream]
    {:pre [(: upstream str)] :post [(: % int)]}
    "検の Program を決まった port で開くため(HttpListen の port を差し替える)。"
    (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port port)))
    (while True
      (<- event HttpRequestArrived (HttpNextRequest))
      (val t event.ticket)
      (cond
        (.startswith event.path "/relay") (<- (HttpForward :ticket t :url (+ upstream (cut event.target 6 None))))
        (= event.path "/ws") (<- (WsForward :ticket t :url (+ upstream "/ws")))
        True (<- (HttpRespond :ticket t :status 200 :headers #() :body (HttpNoBody))))))
  (defn :async #^ (get tuple #(web.AppRunner int)) upstream-app []
    "中継先の相手役(HTTP の echo と ws の echo)を空いた port で開き、runner と port を返すため。"
    (defn :async #^ web.Response echo [#^ web.Request request]
      "受けた本文を 201 で返し、中継が付けた X-Forwarded-Proto を控えるため。"
      (.append seen (.get request.headers "X-Forwarded-Proto"))
      (web.Response :status 201 :body (await (.read request))))
    (defn :async #^ web.WebSocketResponse ws-echo [#^ web.Request request]
      "ws の text を echo し、bye で状態符 4002 の close を送るため。"
      (setv ws (web.WebSocketResponse))
      (await (.prepare ws request))
      (for [:async message ws]
        (if (= message.data "bye")
            (do (await (.close ws :code 4002)) (break))
            (await (.send-str ws (+ "echo:" message.data)))))
      ws)
    (setv app (web.Application))
    (.add-route app.router "GET" "/ws" ws-echo)
    (.add-route app.router "*" "/{tail:.*}" echo)
    (setv runner (web.AppRunner app))
    (await (.setup runner))
    (setv up (free-port))
    (await (.start (web.TCPSite runner "127.0.0.1" up)))
    #(runner up))
  (defn :async #^ None scenario []
    "相手役を開き、本物の答え手を別の thread で回して、HTTP の中継と ws の中継を確かめるため。"
    (setv #(runner up) (await (upstream-app)))
    (.start (threading.Thread :target (fn [] (try (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                 (serve-on-port (.format "http://127.0.0.1:{}" up)))))
                                                  (except [asyncio.CancelledError] None)))
                              :daemon True))
    (setv base (.format "http://127.0.0.1:{}" port))
    (with [:async session (aiohttp.ClientSession)]
      (for [_ (range 200)]
        (try (with [:async r (.get session (+ base "/"))] (break))
             (except [aiohttp.ClientError] (await (asyncio.sleep 0.05)))))
      (with [:async r (.post session (+ base "/relay/x") :data b"body")]
        (assert (= [r.status (await (.read r))] [201 b"body"])))
      (assert (= seen ["http"]))
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "hi"))
        (assert (= (. (await (.receive ws :timeout 5)) data) "echo:hi"))
        (await (.send-str ws "bye"))
        (setv closing (await (.receive ws :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 4002]))))
    (await (.cleanup runner)))
  (asyncio.run (scenario)))


(defn #^ None test-the-aiohttp-server-binds-a-free-port-and-stamps-events []
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import aiohttp [WSMessage])
  (import queue)
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv result (queue.Queue) bound (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                       (ws-echo 64 bound.put))))))
                            :daemon True))
  (defn :async #^ WSMessage scenario [#^ str base]
    "ws に上げて 1 往復し、stop で待ち受けを閉じさせて、最後に受けた frame を返すため。"
    (with [:async session (aiohttp.ClientSession)]
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "hi"))
        (assert (= (. (await (.receive ws :timeout 5)) data) "echo:hi"))
        (await (.send-str ws "stop"))
        (await (.receive ws :timeout 5)))))
  ;; port 0 で開き、結んだ port を HttpListen の答えで知る。
  (setv port (. (.get bound :timeout 30) port))
  (assert (> port 0))
  (asyncio.run (scenario (.format "http://127.0.0.1:{}" port)))
  (setv [_bound #* events report] (.get result :timeout 30))
  ;; 出来事は受けた拍の単調時計を持つ(台本の答え手は書き手が載せた値のまま)。
  (assert (all (gfor e events :if (not (isinstance e HttpServerClosed)) (isinstance e.received-at float))) events)
  ;; 要求は送り元の address(接続の相手 = 127.0.0.1)を持つ。
  (assert (= (lfor e events :if (isinstance e HttpRequestArrived) e.remote) ["127.0.0.1"]) events)
  (assert (= (. (get events -1) reason) "検が閉じた"))
  (assert (>= report.flushed-bytes (len "echo:hi"))))


(defn #^ None test-the-aiohttp-server-names-an-answer-the-peer-left-in-one-line-and-counts-it [#^ (get pytest.CaptureFixture str) capfd
                                                                                               #^ pytest.LogCaptureFixture caplog]
  ;; 相手が先に切った要求への答え(agora-redesign #2757): aiohttp の traceback(Error handling request)を出さず、1 行の名乗りと
  ;; 待ち受けの数え(閉じる時の合計)を残す。次の要求には今までどおり答える。
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import queue)
  (import struct)
  (import time)
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv bound (queue.Queue) left (threading.Event) result (queue.Queue))
  (defk answer-after-the-peer-left []
    {:pre [] :post [(: % int)]}
    "検の Program: 結んだ宛先を渡し、1 つ目の要求には相手が去った後に答え、2 つ目には普通に答えて閉じる(答え = 答えた数)。"
    (<- address HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
    (.put bound address)
    (<- late HttpRequestArrived (HttpNextRequest))
    (.wait left 10)
    (<- (HttpRespond :ticket late.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"too late")))
    (<- on-time HttpRequestArrived (HttpNextRequest))
    (<- (HttpRespond :ticket on-time.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"on time")))
    (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 1.0))
    (<- (HttpNextRequest))
    2)
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                       (answer-after-the-peer-left))))))
                            :daemon True))
  (setv port (. (.get bound :timeout 30) port))
  ;; 1 つ目: 送ってすぐ RST で切る(答えが遅れ、相手の上限が先に来た形 — 本番の curl -m 2)。aiohttp が切りを受け取ってから答えさせる。
  (with [peer (socket.create-connection #("127.0.0.1" port))]
    (.sendall peer b"GET /late HTTP/1.1\r\nHost: test\r\n\r\n")
    (.setsockopt peer socket.SOL-SOCKET socket.SO-LINGER (struct.pack "ii" 1 0)))
  (time.sleep 0.3)
  (.set left)
  ;; 2 つ目: 普通に答える。
  (setv reply b"")
  (with [peer (socket.create-connection #("127.0.0.1" port) :timeout 10)]
    (.sendall peer b"GET /on-time HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n")
    (while True
      (setv chunk (.recv peer 4096))
      (when (not chunk) (break))
      (setv reply (+ reply chunk))))
  (assert (= (.get result :timeout 30) 2))
  (assert (.endswith reply b"on time") reply)
  (setv err (. (.readouterr capfd) err))
  ;; aiohttp の traceback は logger aiohttp.server の記録に出る(pytest の logging の捕まえ口が受ける)。
  (assert (= (lfor record caplog.records :if (.startswith record.name "aiohttp") (.getMessage record)) []) caplog.text)
  (assert (not-in "Error handling request" err) err)
  (assert (not-in "Traceback" err) err)
  (assert (= (.count err "相手が先に切ったので届かなかった") 1) err)
  (assert (in "GET /late への答え(status 200)" err) err)
  (assert (in "この待ち受けで 1 件目" err) err)
  (assert (in "届かなかった答えは合わせて 1 件" err) err))


;; --- probe の口は本体の流れの止まりに巻き込まれない(agora-redesign #2776)-------------------------------------------------------------
;; 2026-10-02 の record の job: 待ち受け・timer・Await の全部が process に 1 つの共有の event loop に乗り、loop が 30 秒止まった間 /healthz も
;; 答えなかった(#2734)。probe の口は待ち受けの loop の上で、answer を別の thread の自分の run で答えるので、本体の流れ(共有の loop か
;; scheduler)を塞いでも /healthz は答え、/readyz は answer の判じ(拍の古さ)に従う。本体へ届く要求は塞がれている間は答えない(対照)。

;; probe の答えを待つ上限・/readyz が 503 と名乗る拍の古さ・相手が古さを越えるまで待つ秒・本体へ届く要求を待つ上限・塞ぐ上限(相手が放す
;; までの保険)— どれも秒。
(val PROBE-SECONDS 0.5)
(val READY-STALE-SECONDS 0.1)
(val STALE-WAIT-SECONDS 0.15)
(val WORK-SECONDS 0.2)
(val HOLD-SECONDS 2.0)


(defrecord Beat
  "本体の流れの最後の拍(単調時計の秒)— 最新の値の置き場に置き、/readyz の answer が別の run で読む。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ float at))


(defrecord Fetched
  "相手の要求 1 つの答え: status(時間の内に答えが無ければ None)と本文。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ (| int None) status)
  (#^ str body))


(defrecord HeldAnswers
  "本体の流れを塞いだ間に相手が受け取った物: health・ready = probe の口の答え・work = 本体へ届く要求の答え。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ Fetched health)
  (#^ Fetched ready)
  (#^ Fetched work))


(defk alive []
  {:pre [] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "/healthz の answer: process が居れば 200(本体の流れを見ない)。"
  (HttpProbeAnswer :status 200 :headers #() :body b"alive"))


(defk readiness [stale-seconds]
  {:pre [(: stale-seconds float)] :post [(: % HttpProbeAnswer)] :tags {:context "http-server-test" :role "program"}}
  "/readyz の answer: 置き場の最後の拍が stale-seconds より古い(まだ無い)なら 503 と名乗り、新しければ 200。"
  (<- beat (| Beat None) (ReadLatest Beat))
  (val stalled (if (is beat None) None (- (time.monotonic) beat.at)))
  (if (and (is-not stalled None) (<= stalled stale-seconds))
      (HttpProbeAnswer :status 200 :headers #() :body b"ready")
      (HttpProbeAnswer :status 503 :headers #() :body (.encode (.format "本体の流れが {} 秒 拍を刻んでいない" stalled) "utf-8"))))


(defk fetch [address path seconds]
  {:pre [(: address HttpAddress) (: path str) (: seconds float)] :post [(: % Fetched)] :tags {:context "http-server-test" :role "foundation"}}
  "相手の要求 1 つ: GET を送り、seconds 秒の内の答えを読む(来なければ status None)。"
  (val connection (http.client.HTTPConnection address.host address.port :timeout seconds))
  (try
    (.request connection "GET" path)
    (val response (.getresponse connection))
    (Fetched :status response.status :body (.decode (.read response) "utf-8"))
    (except [TimeoutError]
      (Fetched :status None :body ""))
    (finally
      (.close connection))))


(deff ask-while-held [address held release box]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: address HttpAddress) (: held threading.Event) (: release threading.Event) (: box queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "本体の流れが塞がれてから拍が古くなるまで待ち、probe の口 2 つと本体へ届く要求 1 つを送って答えを box へ置き、流れを放す(失敗なら例外を置く)。"
  (try
    (.wait held HOLD-SECONDS)
    (time.sleep STALE-WAIT-SECONDS)
    (.put box (HeldAnswers :health (run (fetch address "/healthz" PROBE-SECONDS)) :ready (run (fetch address "/readyz" PROBE-SECONDS))
                           :work (run (fetch address "/work" WORK-SECONDS))))
    (except [error Exception]
      (.put box error))
    (finally
      (.set release)))
  None)


(defk hold-the-loop [held release]
  {:pre [(: held threading.Event) (: release threading.Event)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "本体の流れを塞ぐ形 1: await-handler の共有の event loop の thread を、相手が放すまで止める(loop の上で同期に待つ coroutine)。"
  (<- (Await ((fn :async [] (.set held) (.wait release HOLD-SECONDS)))))
  None)


(defk hold-the-scheduler [held release]
  {:pre [(: held threading.Event) (: release threading.Event)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "本体の流れを塞ぐ形 2: 協調型の scheduler の thread(本体の run)を、相手が放すまで止める。"
  (.set held)
  (.wait release HOLD-SECONDS)
  None)


(defk probe-while-held [hold]
  {:pre [(: hold Callable)] :post [(: % HeldAnswers)] :tags {:context "http-server-test" :role "program"}}
  "probe の口つきで待ち受けを開き、拍を 1 つ置いてから hold で本体の流れを塞ぎ、その間に相手が受け取った物を返す。/readyz の answer は
   置き場と state を自分の中に被せた閉じた Program。"
  (val board (.format "test-http-probe-{}" (time.monotonic-ns)))
  (val held (threading.Event))
  (val release (threading.Event))
  (val box (queue.Queue))
  (val probes #((HttpProbe :path "/healthz" :answer (alive))
                (HttpProbe :path "/readyz" :answer (with_handlers [(state) (process-latest-handler board)] (readiness READY-STALE-SECONDS)))))
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :probes probes))
  (<- (with_handlers [(state) (process-latest-handler board)] (PublishLatest (Beat :at (time.monotonic)))))
  (.start (threading.Thread :target ask-while-held :args #(bound held release box) :daemon True))
  (<- (hold held release))
  ;; 塞いでいた間に本体へ届いた要求(相手はもう去った)に /work まで答えてから閉じる — 答えの無い要求が残ると aiohttp は閉じる時に待つ。
  ;; probe の口が列を通る形(直す前)では /healthz・/readyz もここへ届く。
  (var answering True)
  (while answering
    (<- arrived HttpRequestArrived (HttpNextRequest))
    (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"late")))
    (:= answering (!= arrived.path "/work")))
  (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.2))
  (val got (.get box :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  got)


(defk held-answers [hold]
  {:pre [(: hold Callable)] :post [(: % HeldAnswers)] :tags {:context "http-server-test" :role "program"}}
  "本物の答え手の下で probe-while-held を走らせるため(aiohttp の無い venv では skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (<- got HeldAnswers (with-handler [(await-handler) (state) aiohttp-http-server] (probe-while-held hold)))
  got)


(deftest test-a-probe-answers-while-the-shared-loop-is-held
  ;; 共有の event loop の thread を止めても、/healthz は 200・/readyz は拍の古さで 503 と名乗る。本体へ届く要求は答えない(対照)。
  (<- got HeldAnswers (held-answers hold-the-loop))
  (assert (= got.health (Fetched :status 200 :body "alive")) got)
  (assert (= got.ready.status 503) got)
  (assert (in "拍を刻んでいない" got.ready.body) got)
  (assert (is got.work.status None) got))


(deftest test-a-probe-answers-while-the-scheduler-is-held
  ;; 協調型の scheduler の thread(本体の run)を止めても同じ。
  (<- got HeldAnswers (held-answers hold-the-scheduler))
  (assert (= got.health (Fetched :status 200 :body "alive")) got)
  (assert (= got.ready.status 503) got)
  (assert (in "拍を刻んでいない" got.ready.body) got)
  (assert (is got.work.status None) got))


;; --- 答えは待ち受けの loop へ積むだけ(agora-redesign #3688 の子 (3) の 3b)-----------------------------------------------------

(defclass [(dataclass :frozen True)] ReadAwaits [EffectBase]
  "awaits-counted が数えた、答え手の節が共有の event loop へ入った回数を読む(答え = int)。")


(defhandler awaits-counted
  ;; 本物の答え手と await-handler の間に挟み、答え手の節が共有の event loop へ入る回数(Await)を数えてから、そのまま渡すため。
  (session var entered 0)
  (Await [coroutine]
    (:= entered (+ entered 1))
    (reperform effect))
  (ReadAwaits []
    (resume entered)))


(defk respond-awaits [box]
  {:pre [(: box queue.Queue)] :post [(: % int)] :tags {:context "http-server-test" :role "program"}}
  "port 0 で開いて結んだ宛先を box へ置き、要求 1 つに答え、その答え(HttpRespond 1 回)の間に共有の event loop へ入った回数を返すため。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- arrived HttpRequestArrived (HttpNextRequest))
  (<- before int (ReadAwaits))
  (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data b"sent")))
  (<- after int (ReadAwaits))
  (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.5))
  (- after before))


(deff ask-once [box answers]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: box queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "待ち受けが開いたら GET を 1 つ送り、答えを answers へ置くため(失敗なら例外を置く)。"
  (try
    (.put answers (run (fetch (.get box :timeout HOLD-SECONDS) "/one" WORK-SECONDS)))
    (except [error Exception]
      (.put answers error)))
  None)


(defk responded-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "本物の答え手の下で respond-awaits を走らせ、#(共有の loop へ入った回数 相手が受けた答え) を返すため(aiohttp の無い venv では skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target ask-once :args #(box answers) :daemon True))
  (<- entered int (with-handler [(await-handler) (state) awaits-counted aiohttp-http-server] (respond-awaits box)))
  (val got (.get answers :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  #(entered got))


(deftest test-the-aiohttp-server-hands-an-answer-to-the-edge-without-a-round-trip
  ;; 答え(HttpRespond)は待ち受けの loop へ積むだけで、共有の event loop へ入らない(直す前は Await 1 回 — 共有の loop → 待ち受けの loop →
  ;; 戻りの往復)。相手は送った答えをそのまま受ける。
  (<- got tuple (responded-awaits))
  (assert (= got #(0 (Fetched :status 200 :body "sent"))) got))


(defk listen-answer-a-gone-ticket-and-close []
  {:pre [] :post [(: % str)] :tags {:context "http-server-test" :role "program"}}
  "待ち受けを開き、待ち受けに無い札へ答えを撃ってから閉じるため(答えは積むだけで戻るので、撃った側へは何も上がらない)。"
  (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (<- (HttpRespond :ticket "no-such" :status 200 :headers #() :body (HttpNoBody)))
  (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.2))
  "returned")


(defk answer-a-ticket-the-edge-does-not-hold []
  {:pre [] :post [(: % str)] :tags {:context "http-server-test" :role "program"}}
  "本物の答え手の下で listen-answer-a-gone-ticket-and-close を走らせるため(aiohttp の無い venv では skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (<- done str (with-handler [(await-handler) (state) aiohttp-http-server] (listen-answer-a-gone-ticket-and-close)))
  done)


(deftest test-an-answer-to-a-ticket-the-edge-does-not-hold-is-named-in-one-line [capfd]
  ;; 積んだ答えの札が待ち受けに無ければ(2 度目・知らない札)、撃った側へは上げず、待ち受けの loop が traceback 無しの 1 行で名乗る。
  (<- done str (answer-a-ticket-the-edge-does-not-hold))
  (val err (. (.readouterr capfd) err))
  (assert (= done "returned"))
  (assert (in "札 no-such への命令 HttpRespond" err) err)
  (assert (not-in "Traceback" err) err))


;; --- 宣言の小さい本文は待ち受けの loop が先に読む(agora-redesign #3688 の子 (3) の 3c)-----------------------------------------

;; 相手が送る本文(宣言の長さつき)と、読みの上限。
(val SMALL-BODY b"small body")
(val READ-LIMIT 64)


(defk read-awaits [box]
  {:pre [(: box queue.Queue)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "port 0 で開いて結んだ宛先を box へ置き、要求 1 つの本文を読み(HttpReadBody 1 回)、読めた本文で答えるため。答え = #(読みの間に
   共有の event loop へ入った回数 読みの答え)。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- arrived HttpRequestArrived (HttpNextRequest))
  (<- before int (ReadAwaits))
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket arrived.ticket :max-bytes READ-LIMIT))
  (<- after int (ReadAwaits))
  (val data (match outcome
              (HttpBodyRead :data read) read
              _ b"unread"))
  (<- (HttpRespond :ticket arrived.ticket :status 200 :headers #() :body (HttpBodyBytes :data data)))
  (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.5))
  #((- after before) outcome))


(defk post-body [address path body seconds]
  {:pre [(: address HttpAddress) (: path str) (: body bytes) (: seconds float)] :post [(: % Fetched)]
   :tags {:context "http-server-test" :role "foundation"}}
  "相手の要求 1 つ: 宣言の長さ(Content-Length)つきの本文で POST を送り、seconds 秒の内の答えを読む(来なければ status None)。"
  (val connection (http.client.HTTPConnection address.host address.port :timeout seconds))
  (try
    (.request connection "POST" path :body body)
    (val response (.getresponse connection))
    (Fetched :status response.status :body (.decode (.read response) "utf-8"))
    (except [TimeoutError]
      (Fetched :status None :body ""))
    (finally
      (.close connection))))


(deff send-once [box answers]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: box queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "待ち受けが開いたら本文つきの POST を 1 つ送り、答えを answers へ置くため(失敗なら例外を置く)。"
  (try
    (.put answers (run (post-body (.get box :timeout HOLD-SECONDS) "/one" SMALL-BODY WORK-SECONDS)))
    (except [error Exception]
      (.put answers error)))
  None)


(defk read-body-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "本物の答え手の下で read-awaits を走らせ、#(共有の loop へ入った回数 読みの答え 相手が受けた答え) を返すため(aiohttp の無い venv では
   skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (val box (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target send-once :args #(box answers) :daemon True))
  (<- read tuple (with-handler [(await-handler) (state) awaits-counted aiohttp-http-server] (read-awaits box)))
  (val got (.get answers :timeout HOLD-SECONDS))
  (when (isinstance got Exception)
    (raise got))
  (+ read #(got)))


(deftest test-the-aiohttp-server-reads-a-small-declared-body-without-a-round-trip
  ;; 宣言の長さが小さい本文は、待ち受けの loop が到着を並べる前に読み切り、HttpReadBody は共有の event loop へ入らずに答える(直す前は
  ;; Await 1 回 — 共有の loop → 待ち受けの loop → 戻りの往復)。読めた本文はそのまま相手へ戻る。
  (<- got tuple (read-body-awaits))
  (assert (= got #(0 (HttpBodyRead :data SMALL-BODY) (Fetched :status 200 :body "small body"))) got))


;; --- 列に既に在る到着は往復せずに取る(agora-redesign #3688 の案 1)----------------------------------------------------------------
;; 書き 1 回で保留の待ちが一斉に起きると、続く要求が待ち受けの列に溜まる。受けの loop が 1 つ取るたびに共有の loop → 待ち受けの loop →
;; 戻りの往復をしていたので、波が 1 本ずつに並んだ(手元の測り: 待ち 20 本で読みの queue 68.5 ms)。

;; 一度に送る要求の数・相手が送り終えてから待ち受けが列へ並べ終えるまで待つ秒・相手の答えを待つ上限の秒。
(val WAVE-SIZE 8)
(val SETTLE-SECONDS 0.5)
(val WAVE-SECONDS 10.0)


(defk take-arrivals [n]
  {:pre [(: n int)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "受け口の列から n 個の到着を順に取るため(答え = 取った順の tuple)。"
  (if (= n 0)
      #()
      (do (<- head HttpRequestArrived (HttpNextRequest))
          (<- tail tuple (take-arrivals (- n 1)))
          (+ #(head) tail))))


(defk answer-each [arrivals]
  {:pre [(: arrivals tuple)] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "到着のそれぞれに、その要求の path を本文にして答えるため。"
  (for [arrival arrivals]
    (<- (HttpRespond :ticket arrival.ticket :status 200 :headers #() :body (HttpBodyBytes :data (.encode arrival.path "utf-8")))))
  None)


(defk take-a-wave [box sent]
  {:pre [(: box queue.Queue) (: sent queue.Queue)] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "port 0 で開いて結んだ宛先を box へ置き、1 つ目の到着を受けてから、相手が残りを全部送り終えて列に並ぶのを待ち、残りを取って全部に
   答えるため。答え = #(残りを取る間に共有の event loop へ入った回数 取った順の到着)。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (.put box bound)
  (<- first HttpRequestArrived (HttpNextRequest))
  (for [_ (range WAVE-SIZE)]
    (.get sent :timeout WAVE-SECONDS))
  (time.sleep SETTLE-SECONDS)
  (<- before int (ReadAwaits))
  (<- rest tuple (take-arrivals (- WAVE-SIZE 1)))
  (<- after int (ReadAwaits))
  (val taken (+ #(first) rest))
  (<- (answer-each taken))
  (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.5))
  #((- after before) taken))


(deff ask-in-wave [address path sent answers]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: address HttpAddress) (: path str) (: sent queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "GET を 1 つ送り終えたら sent へ印を置き、答えの #(path 本文) を answers へ置くため(失敗なら例外を置く)。"
  (setv connection (http.client.HTTPConnection address.host address.port :timeout WAVE-SECONDS))
  (try
    (.request connection "GET" path)
    (.put sent path)
    (setv response (.getresponse connection))
    (.put answers #(path (.decode (.read response) "utf-8")))
    (except [error Exception]
      (.put sent error)
      (.put answers error))
    (finally
      (.close connection)))
  None)


(deff fire-wave [box sent answers]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: box queue.Queue) (: sent queue.Queue) (: answers queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "待ち受けが開いたら WAVE-SIZE 本の GET を別々の接続から一度に送るため(/w0 〜)。"
  (setv address (.get box :timeout WAVE-SECONDS))
  (for [n (range WAVE-SIZE)]
    (.start (threading.Thread :target ask-in-wave :args #(address (.format "/w{}" n) sent answers) :daemon True)))
  None)


(defk wave-awaits []
  {:pre [] :post [(: % tuple)] :tags {:context "http-server-test" :role "program"}}
  "本物の答え手の下で take-a-wave を走らせ、#(共有の loop へ入った回数 取った順の到着 相手が受けた答えの集合) を返すため(aiohttp の
   無い venv では skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (val box (queue.Queue))
  (val sent (queue.Queue))
  (val answers (queue.Queue))
  (.start (threading.Thread :target fire-wave :args #(box sent answers) :daemon True))
  (<- got tuple (with-handler [(await-handler) (state) awaits-counted aiohttp-http-server] (take-a-wave box sent)))
  (val replies (frozenset (gfor _ (range WAVE-SIZE) (.get answers :timeout WAVE-SECONDS))))
  (+ got #(replies)))


(deftest test-the-aiohttp-server-takes-queued-arrivals-without-a-round-trip
  ;; 列に既に在る到着は、HttpNextRequest が共有の event loop へ入らずに取る(直す前は 1 つ取るたびに Await 1 回 — 共有の loop →
  ;; 待ち受けの loop → 戻りの往復)。取りこぼしも順の入れ替わりも無い: 取った順は札の順のまま、相手は全員が自分の path の答えを受ける。
  (<- got tuple (wave-awaits))
  (val entered (get got 0))
  (val taken (get got 1))
  (val replies (get got 2))
  (val paths (frozenset (gfor n (range WAVE-SIZE) (.format "/w{}" n))))
  (assert (= entered 0) got)
  (assert (= (lfor a taken (int a.ticket)) (sorted (gfor a taken (int a.ticket)))) taken)
  (assert (= (frozenset (gfor a taken a.path)) paths) taken)
  (assert (= replies (frozenset (gfor p paths #(p p)))) replies))
