;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy — agora-redesign #802 便 2)の検。
;;;   - 台本の答え手(scripted-http-server)は、要求の列・閉じる・命令の記録・file の範囲の本文(外側の memory-file-handler から読む)・中継先の
;;;     台本の答え(最長の一致・ws を受けない先は 502)を確かめる。
;;;   - 本物の答え手(aiohttp-http-server)は、同じ Program を本物の socket で回し、byte 列と file の範囲の本文・頭・HEAD・HTTP の中継
;;;     (本文と X-Forwarded-Proto・届かない先の 502)・ws の中継(frame の往復・close の状態符)を確かめる(aiohttp の無い venv では skip)。
(require doeff-hy.macros [defk <- val var])
(import asyncio)
(import socket)
(import threading)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpRequestArrived HttpServerClosed HttpListen HttpNextRequest
                                                HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody HttpScript
                                                ScriptedUpstream HttpServed ReadHttpServed])
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
                      :headers (.get fields "headers" #()) :upgrade (.get fields "upgrade" False)))


(defk served-after [site upstream]
  {:pre [(: site str) (: upstream str)] :post [(: % tuple)]}
  "Program を閉じるまで回し、台本の記録を読むため。"
  (<- _count int (serve-until-closed site upstream))
  (<- served tuple (ReadHttpServed))
  served)


(defn test-the-scripted-server-serves-the-script-and-records-what-it-was-told []
  (setv script (HttpScript :arrivals #((arrival 1 "/") (arrival 2 "/file") (arrival 3 "/relay/x?q=1") (arrival 4 "/ws" :upgrade True)
                                      (arrival 5 "/relay/y"))
                           :upstreams #((ScriptedUpstream :base "http://up" :http-status 201 :accepts-ws False)))
        files (MemoryFiles :files #((MemoryFile :path "/site/app.js" :content (.encode "console.log(1)" "utf-8")))
                           :dirs #("/site"))
        served (run (scheduled (with_handlers [(state) (memory-file-handler files) (scripted-http-server script)]
                                              (served-after "/site/app.js" "http://up")))))
  (assert (= (lfor s served s.ticket) ["1" "2" "3" "4" "5"]))
  (assert (= [(. (get served 0) status) (. (get served 0) body)] [200 "hello GET"]))
  ;; file の範囲は外側の file の答え手から読んで切り出す。
  (assert (= [(. (get served 1) status) (. (get served 1) body)] [206 "nsole"]))
  (assert (= (. (get served 2) command) (HttpForward :ticket "3" :url "http://up/x?q=1")))
  (assert (= (. (get served 2) status) 201))
  ;; ws を受けない中継先は 502。
  (assert (= (. (get served 3) status) 502)))


(defn test-a-file-range-that-cannot-be-read-names-the-reason []
  (setv script (HttpScript :arrivals #((arrival 1 "/file")))
        served (run (scheduled (with_handlers [(state) (memory-file-handler (MemoryFiles)) (scripted-http-server script)]
                                              (served-after "/gone.js" "http://up")))))
  (assert (.startswith (. (get served 0) body) "file を読めない")))


;; --- 本物の答え手 --------------------------------------------------------------------------------------------------------------

(defn free-port []
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defn test-the-aiohttp-server-serves-bodies-and-relays-http-and-ws [tmp-path]
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import aiohttp [web WSMsgType])
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv site (/ tmp-path "app.js"))
  (.write-text site "console.log(1)")
  (setv port (free-port) seen [])
  (defk serve-on-port [upstream]
    {:pre [(: upstream str)] :post [(: % int)]}
    "検の Program を決まった port で開くため(HttpListen の port を差し替える)。"
    (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port port)))
    (while True
      (<- event (| HttpRequestArrived HttpServerClosed) (HttpNextRequest))
      (val t event.ticket)
      (cond
        (= event.path "/file") (<- (HttpRespond :ticket t :status 206 :headers #((HttpHeader :name "Content-Length" :value "5"))
                                                :body (HttpBodyFileRange :path (str site) :start 2 :length 5)))
        (= event.path "/head") (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "Content-Length" :value "11"))
                                                :body (HttpNoBody)))
        (.startswith event.path "/relay") (<- (HttpForward :ticket t :url (+ upstream (cut event.target 6 None))))
        (= event.path "/ws") (<- (WsForward :ticket t :url (+ upstream "/ws")))
        True (<- (HttpRespond :ticket t :status 200 :headers #((HttpHeader :name "X-Seen" :value "yes"))
                              :body (HttpBodyBytes :data (.encode (+ "hello " event.method) "utf-8")))))))
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
      (with [:async r (.get session (+ base "/"))]
        (assert (= [r.status (get r.headers "X-Seen") (await (.text r))] [200 "yes" "hello GET"])))
      (with [:async r (.get session (+ base "/file"))]
        (assert (= [r.status (await (.text r))] [206 "nsole"])))
      (with [:async r (.head session (+ base "/head"))]
        (assert (= [r.status (get r.headers "Content-Length")] [200 "11"])))
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


(defn test-an-unreachable-relay-is-a-502 []
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv port (free-port) dead (free-port))
  (defk relay-all [target]
    {:pre [(: target int)] :post [(: % int)]}
    "全部の要求を届かない先へ中継するため。"
    (<- (HttpListen :address (HttpAddress :host "127.0.0.1" :port port)))
    (while True
      (<- event (| HttpRequestArrived HttpServerClosed) (HttpNextRequest))
      (<- (HttpForward :ticket event.ticket :url (.format "http://127.0.0.1:{}/x" target)))))
  (.start (threading.Thread :target (fn [] (try (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server] (relay-all dead))))
                                                (except [asyncio.CancelledError] None)))
                            :daemon True))
  (defn :async scenario []
    (with [:async session (aiohttp.ClientSession)]
      (for [_ (range 200)]
        (try (with [:async r (.get session (.format "http://127.0.0.1:{}/" port))]
               (assert (= r.status 502))
               (return None))
             (except [aiohttp.ClientConnectionError] (await (asyncio.sleep 0.05))))))
    (raise (RuntimeError "待ち受けが開かない")))
  (asyncio.run (scenario)))
