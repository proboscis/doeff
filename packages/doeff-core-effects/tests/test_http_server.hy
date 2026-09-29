;;; 汎用の HTTP の待ち受けの effect(http_server_effects.hy — agora-redesign #802 便 2)の検。
;;;   - 台本の答え手(scripted-http-server)は、要求の列・閉じる・命令の記録・file の範囲の本文(外側の memory-file-handler から読む)・中継先の
;;;     台本の答え(最長の一致・ws を受けない先は 502)を確かめる。
;;;   - 本物の答え手(aiohttp-http-server)は、同じ Program を本物の socket で回し、byte 列と file の範囲の本文・頭・HEAD・HTTP の中継
;;;     (本文と X-Forwarded-Proto・届かない先の 502)・ws の中継(frame の往復・close の状態符)を確かめる(aiohttp の無い venv では skip)。
;;;   - 要求の本文の読み(HttpReadBody — #880 U1)は、同じ Program を両方の答え手で回し、上限の境目で同じ答えになることを確かめる。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import asyncio)
(import importlib.util)
(import collections.abc [Callable])
(import socket)
(import threading)
(import pytest)
(import doeff [run with_handlers Program])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpRequestArrived HttpServerClosed HttpListen HttpNextRequest
                                                HttpRespond HttpForward WsForward HttpBodyBytes HttpBodyFileRange HttpNoBody HttpScript
                                                ScriptedUpstream HttpServed ReadHttpServed HttpEvent WsAccept WsSendText WsClose
                                                HttpShutdown TakeWsSendReport WsSendReport WsTextArrived WsBinaryArrived WsClosed
                                                WsTextSent WsCloseSent AppendHttpScript HttpReadBody HttpBodyRead HttpBodyTooLarge
                                                HttpBodyFailed HttpBodyOutcome ScriptedBody])
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


;; --- ws の終端(agora-redesign #811 変更 3a)-----------------------------------------------------------------------------------

(defk ws-echo [limit publish]
  {:pre [(: limit int) (: publish Callable)] :post [(: % tuple)]}
  "検の Program: ws に上げて 1 通ごとに答え、閉じるまで回すため(答え = 受けた出来事の列と、最後の送りの勘定)。text \"bye\" = 4001 で閉じる・
   \"big\" = 送りの上限の 2 倍の 1 通を送る(読まない相手と同じく溜まりが上限を超えて切られる)・\"stop\" = 待ち受けを閉じる・他は echo。
   byte の 1 通は 1003 で閉じる。/plain の要求も ws に上げようとする(Upgrade の無い要求の断り)。publish = 結んだ宛先を検へ渡す口。"
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
      (WsTextArrived :ticket t :text "bye") (<- (WsClose :ticket t :code 4001 :reason "さようなら"))
      (WsTextArrived :ticket t :text "big") (<- (WsSendText :ticket t :text (* "x" (* 2 limit))))
      (WsTextArrived :ticket t :text "stop") (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 2.0))
      (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ "echo:" text)))
      (WsBinaryArrived :ticket t) (<- (WsClose :ticket t :code 1003 :reason "text だけ"))
      _ None)))


(defn test-the-scripted-server-terminates-ws-and-records-what-it-sent []
  (setv script (HttpScript :arrivals #((arrival "a" "/ws" :upgrade True) (WsTextArrived :ticket "a" :text "hi")
                                      (arrival "b" "/ws" :upgrade True) (WsTextArrived :ticket "b" :text "big")
                                      (WsBinaryArrived :ticket "a" :data b"\x00")
                                      (arrival "c" "/ws" :upgrade True) (WsTextArrived :ticket "c" :text "bye")
                                      (arrival "d" "/ws" :upgrade True) (WsTextArrived :ticket "d" :text "stop")
                                      (WsTextArrived :ticket "d" :text "never"))
                           :stalled (frozenset #("b")))
        [answer served] (run (scheduled (with_handlers [(state) (scripted-http-server script)]
                                                       (do-served (ws-echo 10 (fn [bound] None))))))
        [bound #* events report] answer)
  ;; 台本は port を結ばない — 渡した宛先のまま。
  (assert (= bound (HttpAddress :host "127.0.0.1" :port 0)))
  ;; WsAccept の拍に WsOpened が列の頭へ差さる・閉じと切りの WsClosed も。
  (assert (= (lfor e events (. (type e) __name__))
             ["HttpRequestArrived" "WsOpened" "WsTextArrived"
              "HttpRequestArrived" "WsOpened" "WsTextArrived" "WsClosed"
              "WsBinaryArrived" "WsClosed"
              "HttpRequestArrived" "WsOpened" "WsTextArrived" "WsClosed"
              "HttpRequestArrived" "WsOpened" "WsTextArrived" "HttpServerClosed"]))
  (setv closes (lfor e events :if (isinstance e WsClosed) #(e.ticket e.code e.reason)))
  (assert (= (lfor c closes (cut c 0 2)) [#("b" 1006) #("a" 1003) #("c" 4001)]))
  (assert (= (. (get events -1) reason) "検が閉じた"))
  ;; 読まない相手(b)は 20 byte を溜めて切られ、捨てた勘定に載る。a は直ぐに読む。
  (assert (= [report.queued-frames report.queued-bytes report.flushed-bytes report.dropped-bytes report.cuts] [2 27 7 20 1]))
  (assert (= (lfor s served :if (isinstance s WsTextSent) #(s.ticket s.text)) [#("a" "echo:hi")]))
  (assert (= (lfor s served :if (isinstance s WsCloseSent) #(s.ticket s.code)) [#("a" 1003) #("c" 4001) #("d" 1000)]))
  (assert (= (lfor s served :if (isinstance s HttpServed) s.status) [101 101 101 101])))


(defk do-served [program]
  {:pre [(: program Program)] :post [(: % tuple)]}
  "Program を回してから台本の記録を読むため。"
  (<- answer tuple program)
  (<- served tuple (ReadHttpServed))
  #(answer served))


(defk append-then-drain []
  {:pre [] :post [(: % tuple)]}
  "走っている台本へ出来事を足すと、尽きた後でも続きが届くことを見るため(答え = 受けた出来事の型の名)。"
  (<- first HttpEvent (HttpNextRequest))
  (<- (AppendHttpScript :arrivals #((arrival "z" "/late"))))
  (<- second HttpEvent (HttpNextRequest))
  (<- third HttpEvent (HttpNextRequest))
  #((. (type first) __name__) second.ticket (. (type third) __name__)))


(defn test-an-appended-script-is-served-after-the-script-ran-dry []
  (assert (= (run (scheduled (with_handlers [(state) (scripted-http-server (HttpScript :arrivals #()))] (append-then-drain))))
             #("HttpServerClosed" "z" "HttpServerClosed"))))


(defn test-the-aiohttp-server-terminates-ws-cuts-a-stuffed-outbox-and-shuts-down []
  (setv aiohttp (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存"))
  (import aiohttp [WSMsgType])
  (import queue)
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (setv result (queue.Queue) bound (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                       (ws-echo 64 bound.put))))))
                            :daemon True))
  (defn :async scenario [base]
    (with [:async session (aiohttp.ClientSession)]
      ;; Upgrade の無い要求を ws に上げようとすると 426。
      (with [:async r (.get session (+ base "/plain"))]
        (assert (= r.status 426)))
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "hi"))
        (assert (= (. (await (.receive ws :timeout 5)) data) "echo:hi"))
        (await (.send-bytes ws b"\x01"))
        (setv closing (await (.receive ws :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 1003])))
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "bye"))
        (setv closing (await (.receive ws :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 4001])))
      (with [:async ws (.ws-connect session (+ base "/ws"))]
        ;; 上限(64 byte)の 2 倍の 1 通 — 箱へ積む前に切られ、相手は close を受けずに切れる。
        (await (.send-str ws "big"))
        (setv cut (await (.receive ws :timeout 5)))
        (assert (in cut.type #(WSMsgType.CLOSED WSMsgType.ERROR WSMsgType.CLOSE))))
      (with [:async ws (.ws-connect session (+ base "/ws"))
             :async other (.ws-connect session (+ base "/ws"))]
        (await (.send-str ws "stop"))
        ;; 待ち受けを閉じると、開いている接続の全部に close 1000。
        (setv closing (await (.receive other :timeout 5)))
        (assert (= [closing.type closing.data] [WSMsgType.CLOSE 1000])))))
  ;; port 0 で開き、結んだ port を HttpListen の答えで知る。
  (setv port (. (.get bound :timeout 30) port))
  (assert (> port 0))
  (asyncio.run (scenario (.format "http://127.0.0.1:{}" port)))
  (setv [_bound #* events report] (.get result :timeout 30))
  (assert (all (gfor e events :if (not (isinstance e HttpServerClosed)) (isinstance e.received-at float))))
  (setv closes (lfor e events :if (isinstance e WsClosed) #(e.code e.reason)))
  (assert (in #(1006 "送りの箱が上限を超えた(読まない相手)") closes) closes)
  (assert (= (. (get events -1) reason) "検が閉じた"))
  (assert (= report.cuts 1))
  (assert (>= report.flushed-bytes (len "echo:hi"))))


;; --- 要求の本文の読み(agora-redesign #880 U1)------------------------------------------------------------------------------------
;; 同じ検の Program を台本の答え手と本物の答え手で回し、境目(上限ちょうど・1 byte 超え・chunked の上限内と超え・本文なし・上限を大きく
;; 超える宣言)で同じ答えになることを確かめる。

(val BODY-LIMIT 8)
;; 上限を大きく超える宣言(本物の検では本文を 1 byte も送らない — 読まずに断らなければ答えが来ない)。
(val HUGE-DECLARED 100000000)


(defk body-echo [limit publish]
  {:pre [(: limit int) (: publish Callable)] :post [(: % tuple)]
   :tags {:context "http-server" :role "program"}}
  "検の Program: 要求ごとに本文を limit まで読み(2 度目の読みも撃つ)、読めた本文は 200・上限超えは 413(宣言の長さ)・読めなければ 400 で
   答え、/stop で待ち受けを閉じるため。答え = 要求ごとの #(path 1 度目の答え 2 度目の答えの型の名) の列。publish = 結んだ宛先を検へ渡す口。"
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0)))
  (publish bound)
  (var seen #())
  (while True
    (<- event HttpEvent (HttpNextRequest))
    (match event
      (HttpServerClosed) (return seen)
      (HttpRequestArrived :ticket t :path "/stop")
        (do (<- (HttpRespond :ticket t :status 200 :headers #() :body (HttpNoBody)))
            (<- (HttpShutdown :reason "検が閉じた" :drain-seconds 0.5)))
      (HttpRequestArrived :ticket t :path path)
        (do (<- outcome HttpBodyOutcome (HttpReadBody :ticket t :max-bytes limit))
            (<- again HttpBodyOutcome (HttpReadBody :ticket t :max-bytes limit))
            (:= seen (+ seen #(#(path outcome (. (type again) __name__)))))
            (match outcome
              (HttpBodyRead :data data) (<- (HttpRespond :ticket t :status 200 :headers #() :body (HttpBodyBytes :data data)))
              (HttpBodyTooLarge :declared declared)
                (<- (HttpRespond :ticket t :status 413 :headers #() :body (HttpBodyBytes :data (.encode (str declared) "utf-8"))))
              (HttpBodyFailed :reason reason)
                (<- (HttpRespond :ticket t :status 400 :headers #() :body (HttpBodyBytes :data (.encode reason "utf-8"))))))
      _ None)))


;; 境目の要求の期待: path → 1 度目の答え(2 度目はどれも HttpBodyFailed)。
(val BODY-EXPECTED #(#("/exact" (HttpBodyRead :data b"12345678"))
                     #("/over" (HttpBodyTooLarge :declared 9))
                     #("/chunked-fit" (HttpBodyRead :data b"abcdefgh"))
                     #("/chunked-over" (HttpBodyTooLarge :declared None))
                     #("/empty" (HttpBodyRead :data b""))
                     #("/huge-declared" (HttpBodyTooLarge :declared HUGE-DECLARED))))


(deftest test-the-scripted-server-reads-bodies-up-to-the-limit
  (val length (fn [n] #((HttpHeader :name "Content-Length" :value (str n)))))
  (val script (HttpScript :arrivals #((arrival 1 "/exact" :method "POST" :headers (length 8))
                                      (arrival 2 "/over" :method "POST" :headers (length 9))
                                      (arrival 3 "/chunked-fit" :method "POST" :headers #((HttpHeader :name "Transfer-Encoding" :value "chunked")))
                                      (arrival 4 "/chunked-over" :method "POST" :headers #((HttpHeader :name "Transfer-Encoding" :value "chunked")))
                                      (arrival 5 "/empty")
                                      (arrival 6 "/huge-declared" :method "POST" :headers (length HUGE-DECLARED))
                                      (arrival 7 "/broken" :method "POST")
                                      (arrival 8 "/stop"))
                          :bodies #((ScriptedBody :ticket "1" :data b"12345678") (ScriptedBody :ticket "2" :data b"123456789")
                                    (ScriptedBody :ticket "3" :data b"abcdefgh") (ScriptedBody :ticket "4" :data b"abcdefghijkl")
                                    (ScriptedBody :ticket "7" :data b"12" :failed "相手が途中で切った"))))
  (<- answer tuple (with-handler [(state) (scripted-http-server script)] (do-served (body-echo BODY-LIMIT (fn [bound] None)))))
  (val seen (get answer 0))
  (val served (get answer 1))
  (assert (= (tuple (gfor [path outcome _again] seen #(path outcome)))
             (+ BODY-EXPECTED #(#("/broken" (HttpBodyFailed :reason "相手が途中で切った")))))
          seen)
  ;; 札ごとに 1 度だけ — 2 度目は読めない。
  (assert (= (set (gfor [_path _outcome again] seen again)) #{"HttpBodyFailed"}))
  (assert (= (lfor s served :if (isinstance s HttpServed) s.status) [200 413 200 413 200 413 400 200])))


(defk read-after-respond []
  {:pre [] :post [(: % HttpBodyOutcome)]
   :tags {:context "http-server" :role "program"}}
  "命令を撃った後の札の本文は読めないことを見るため。"
  (<- event HttpEvent (HttpNextRequest))
  (<- (HttpRespond :ticket event.ticket :status 204 :headers #() :body (HttpNoBody)))
  (<- late HttpBodyOutcome (HttpReadBody :ticket event.ticket :max-bytes BODY-LIMIT))
  late)


(deftest test-the-scripted-server-refuses-to-read-a-body-after-the-command
  (val script (HttpScript :arrivals #((arrival 1 "/x" :method "POST")) :bodies #((ScriptedBody :ticket "1" :data b"abc"))))
  (<- late (with-handler [(state) (scripted-http-server script)] (read-after-respond)))
  (assert (isinstance late HttpBodyFailed) late))


(defk ask-over-http [port method path body]
  {:pre [(: port int) (: method str) (: path str) (: body (| bytes list None))] :post [(: % tuple)]
   :tags {:context "http-server" :role "program"}}
  "本物の待ち受けへ要求を 1 つ送り、#(status 本文) を読むため(1 要求 = 1 接続 — 上限で断った接続は答え手が閉じる)。body が list なら
   Content-Length を付けずに chunked で送る。"
  (import http.client [HTTPConnection])
  (val connection (HTTPConnection "127.0.0.1" port :timeout 10))
  (if (isinstance body list)
      (.request connection method path :body (iter body) :encode-chunked True :headers {"Transfer-Encoding" "chunked"})
      (.request connection method path :body body))
  (val response (.getresponse connection))
  (val answer #(response.status (.read response)))
  (.close connection)
  answer)


(deftest test-the-aiohttp-server-reads-bodies-up-to-the-limit-with-or-without-content-length
  {:skip-if (is (importlib.util.find-spec "aiohttp") None)
   :skip-reason "aiohttp は extra http-server の依存"}
  (import queue)
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (val result (queue.Queue))
  (val bound (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put result (run (scheduled (with_handlers [(await-handler) (state) aiohttp-http-server]
                                                                                    (body-echo BODY-LIMIT bound.put))))))
                            :daemon True))
  (val port (. (.get bound :timeout 30) port))
  (assert (= (! (ask-over-http port "POST" "/exact" b"12345678")) #(200 b"12345678")))
  (assert (= (! (ask-over-http port "POST" "/over" b"123456789")) #(413 b"9")))
  (assert (= (! (ask-over-http port "POST" "/chunked-fit" [b"abcd" b"efgh"])) #(200 b"abcdefgh")))
  (assert (= (! (ask-over-http port "POST" "/chunked-over" [b"abcd" b"efgh" b"ijkl"])) #(413 b"None")))
  (assert (= (! (ask-over-http port "GET" "/empty" None)) #(200 b"")))
  ;; 上限を大きく超える宣言: 頭だけ送って本文を送らない — 読まずに断るので答えが来て、答えの後に接続が閉じる。
  (with [raw (socket.create-connection #("127.0.0.1" port) :timeout 10)]
    (.sendall raw (.encode (.format "POST /huge-declared HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n" HUGE-DECLARED) "ascii"))
    (var received b"")
    (while True
      (val chunk (.recv raw 65536))
      (when (not chunk) (break))
      (:= received (+ received chunk)))
    (assert (.startswith received b"HTTP/1.1 413") received)
    (assert (.endswith received (.encode (str HUGE-DECLARED) "ascii")) received))
  (assert (= (! (ask-over-http port "GET" "/stop" None)) #(200 b"")))
  (val seen (.get result :timeout 30))
  (assert (= (tuple (gfor [path outcome _again] seen #(path outcome))) BODY-EXPECTED) seen)
  (assert (= (set (gfor [_path _outcome again] seen again)) #{"HttpBodyFailed"})))
