;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy)の、答え手の片方だけが持つ性質の検。両方に共通の性質(答えの届き方・HEAD と
;;; 本文の無い答え・中継の status と 502・ws の往復と閉じ・断り・送りの上限の切り・閉じた後・本文の読み)は test_http_server_contract.hy
;;; の契約テストが両方の答え手で回す。
;;;   - 台本の答え手(scripted-http-server): 中継先の最長の一致と ws を受けない先の 502・読めない file の範囲の理由・読まない相手の箱が
;;;     溜まって切られる勘定・走っている台本への後足し・読みの途中で相手が切った本文。
;;;   - 本物の答え手(aiohttp-http-server): HTTP の中継の本文と X-Forwarded-Proto・ws の中継(frame の往復と close の状態符)・port 0 で
;;;     結んだ port・出来事の received-at(aiohttp の無い venv では skip)。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import asyncio)
(import collections.abc [Callable])
(import socket)
(import threading)
(import pytest)
(import doeff [run with_handlers Program])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpRequestArrived HttpServerClosed HttpListen HttpNextRequest
                                                HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody HttpScript
                                                ScriptedUpstream ReadHttpServed HttpEvent WsAccept WsSendText
                                                HttpShutdown TakeWsSendReport WsSendReport WsTextArrived WsClosed
                                                WsCloseSent AppendHttpScript HttpReadBody HttpBodyFailed HttpBodyOutcome ScriptedBody
                                                WS-CUT-REASON])
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

(defn arrival [n path #** fields]
  (HttpRequestArrived :ticket (str n) :method (.get fields "method" "GET") :path path :target (.get fields "target" path)
                      :headers (.get fields "headers" #()) :upgrade (.get fields "upgrade" False)
                      :remote (.get fields "remote" None)))


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


(defn test-an-appended-script-is-served-after-the-script-ran-dry []
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

(defn free-port []
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defn test-the-aiohttp-server-relays-http-bodies-and-ws-frames [tmp-path]
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
  (defn :async upstream-app []
    (defn :async echo [request]
      (.append seen (.get request.headers "X-Forwarded-Proto"))
      (web.Response :status 201 :body (await (.read request))))
    (defn :async ws-echo [request]
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
  (defn :async scenario []
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


(defn test-the-aiohttp-server-binds-a-free-port-and-stamps-events []
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import queue)
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv result (queue.Queue) bound (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                       (ws-echo 64 bound.put))))))
                            :daemon True))
  (defn :async scenario [base]
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
