;;; HTTP の待ち受けの契約テストの解釈器(composition root)— 同じ契約の Program を、待ち受けの handler だけ替えて走らせる。
;;;
;;;   aiohttp-http-server   本物: aiohttp-http-server(127.0.0.1 の空いた port で本当に聞く)。相手(PeerSend の要求を送る端末)は
;;;                         別の thread の aiohttp の client で、その thread だけの event loop で要求を 1 つずつ送り、答えを読んでから次へ進む
;;;   scripted-http-server  fake: scripted-http-server(I/O なし — 台本)。相手の要求は同じ PeerRequest から台本の出来事(AppendHttpScript)に
;;;                         し、相手が受け取る答えは台本の記録(ReadHttpServed)から読む
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する(ContractWorld の答え — World):
;;;   * site     file の範囲の本文を読む file(中身 SITE-CONTENT): 本物 = 一時 dir の実の file・fake = memory の file
;;;   * upstream 中継先(どの path にも UPSTREAM-STATUS で答える): 本物 = 127.0.0.1 の wsgiref の聞き手・fake = 台本の ScriptedUpstream
;;;   * dead     届かない中継先: 本物 = 誰も聞いていない port・fake = 台本に無い宛先
;;; 相手の要求の形は PeerRequest 1 つで、本物の端末の送り方(visit)と台本の出来事(scripted-arrivals)の両方がそこから作る。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS・REQUIRES)。
(require doeff-hy.macros [defk deff defhandler <- val var])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import os)
(import queue)
(import socket)
(import tempfile)
(import threading)
(import wsgiref.simple-server [make-server])
(import doeff [EffectBase Program run with_handlers])
(import doeff_core_effects.handlers [state await-handler])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.http_server_effects [AppendHttpScript HttpAddress HttpHeader HttpRequestArrived HttpRespond HttpScript HttpServed
                                                ReadHttpServed ScriptedBody ScriptedUpstream WsBinaryArrived WsCloseSent WsClosed
                                                WsTextArrived WsTextSent])
(import doeff_core_effects.scripted_http_server [scripted-http-server])

(val AIOHTTP "aiohttp-http-server")
(val SCRIPTED "scripted-http-server")
;; 解釈器が要る外の module(無い環境では conftest.py がその解釈器の検を skip する)。
(val REQUIRES {AIOHTTP "aiohttp"})

(val SITE-CONTENT b"console.log(1)")
(val MEMORY-SITE "/contract-site/app.js")
(val UPSTREAM-STATUS 201)
(val UPSTREAM-BODY b"upstream")
(val SCRIPTED-UPSTREAM "http://upstream.test")
(val SCRIPTED-DEAD "http://dead.test")
;; 相手が 1 つの答え・1 通を待つ上限(秒)。
(val PEER-SECONDS 10)
(val WS-OPEN-STATUS 101)


(defrecord World
  "契約の世界: site = file の範囲の本文を読む file の path・upstream = 答える中継先の base・dead = 届かない中継先の base。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str site)
  (#^ str upstream)
  (#^ str dead))


(defrecord PeerRequest
  "相手が送る要求 1 つ: method・target(path と query)・頭・本文(None = 送らない)・chunked(本文を長さの宣言なしで送る)・
   declared(本文を送らず Content-Length だけ宣言する)・raw(本物は client を通さず素の socket で頭だけ送り、接続が閉じるまでの byte を
   そのまま読む — declared も素の socket で送る)・ws(ws で繋ぐ)・sends(ws で送る 1 通の列 —
   str は文字・bytes は byte)・replies と leave(leave が在れば replies 通を受けてから相手が状態符と理由で閉じる。無ければ待ち受けが
   閉じるまで受ける)。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str method)
  (#^ str target)
  (setv #^ (get tuple #(HttpHeader ...)) headers #()
        #^ (| bytes None) body None
        #^ bool chunked False
        #^ (| int None) declared None
        #^ bool raw False
        #^ bool ws False
        #^ (get tuple #((| str bytes) ...)) sends #()
        #^ int replies 0
        #^ (| tuple None) leave None))


(defrecord PeerAnswer
  "相手が受け取った物: status・頭・本文(ws は b\"\")・ws で受けた文字の 1 通の列・待ち受けから受けた close(#(状態符 理由) — 受けなければ None)。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ int status)
  (#^ (get tuple #(HttpHeader ...)) headers)
  (#^ bytes body)
  (#^ (get tuple #(str ...)) texts)
  (#^ (| tuple None) closed))


(defclass [(dataclass :frozen True)] ContractWorld [EffectBase]
  "契約の世界(World)を求める効果。")


(defclass [(dataclass :frozen True)] PeerSend [EffectBase]
  "相手に requests(PeerRequest の列)を address へ順に送らせる効果(撃つだけで待たない)。答え = None。"
  (#^ HttpAddress address)
  (#^ tuple requests))


(defclass [(dataclass :frozen True)] PeerAnswers [EffectBase]
  "相手が送り終えるのを待ち、要求ごとに受け取った物(PeerAnswer の列 — 送った順)を求める効果。")


(defhandler contract-world [world]
  ;; 引数に残す理由: 世界は解釈器ごとに組み立ての側で決まる値(本物は走るたびに作る一時 dir と port)。
  (ContractWorld []
    (resume world)))


;; --- 両方が使う: 相手の要求の形 ---------------------------------------------------------------------------------------------------

(defk path-of [target]
  {:pre [(: target str)] :post [(: % str)] :tags {:context "http-server-test" :role "judgment"}}
  "target の path(query を除く)。"
  (get (.partition target "?") 0))


(defk sent-headers [request]
  {:pre [(: request PeerRequest)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "相手が送る頭のうち契約が見る物: 書いた頭と、本文の長さの宣言(本文を送るなら Content-Length・chunked なら Transfer-Encoding・
   declared なら宣言だけ)。"
  (val length (cond
                (is-not request.declared None) #((HttpHeader :name "Content-Length" :value (str request.declared)))
                request.chunked #((HttpHeader :name "Transfer-Encoding" :value "chunked"))
                (is-not request.body None) #((HttpHeader :name "Content-Length" :value (str (len request.body))))
                True #()))
  (+ request.headers length))


;; --- 本物の相手(別の thread の aiohttp の client)------------------------------------------------------------------------------------

(defk visit-http [loop session base request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: session "aiohttp.ClientSession") (: base str) (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "HTTP の要求を 1 つ送り、答えを読み切る。"
  (val response (.run-until-complete loop (.request session request.method (+ base request.target) :data request.body
                                                    :chunked (if request.chunked True None)
                                                    :headers (dfor h request.headers h.name h.value))))
  (val body (.run-until-complete loop (.read response)))
  (.release response)
  (PeerAnswer :status response.status :headers (tuple (gfor [name value] (.items response.headers) (HttpHeader :name name :value value)))
              :body body :texts #() :closed None))


(defk read-to-end [raw]
  {:pre [(: raw socket.socket)] :post [(: % bytes)] :tags {:context "http-server-test" :role "foundation"}}
  "socket を相手が閉じるまで読む。"
  (var received b"")
  (var chunk (.recv raw 65536))
  (while chunk
    (:= received (+ received chunk))
    (:= chunk (.recv raw 65536)))
  received)


(defk visit-raw [address request]
  {:pre [(: address HttpAddress) (: request PeerRequest)] :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "頭だけ(declared が在れば Content-Length の宣言も — 本文は送らない)を素の socket で送り、答えを接続が閉じるまで読む。答えの本文 =
   頭の後に届いた byte の全部(本文を運ばない答えの後に byte が来れば、それも本文として見える)。宣言が上限を超える要求は、読まずに断る
   答え手でなければ答えが来ない。"
  (var received b"")
  (val length (if (is request.declared None) "" (.format "Content-Length: {}\r\n" request.declared)))
  (with [raw (socket.create-connection #(address.host address.port) :timeout PEER-SECONDS)]
    (.sendall raw (.encode (.format "{} {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n{}\r\n"
                                    request.method request.target address.host length)
                           "ascii"))
    (<- all-bytes bytes (read-to-end raw))
    (:= received all-bytes))
  (val parts (.partition received b"\r\n\r\n"))
  (val lines (.split (.decode (get parts 0) "latin-1") "\r\n"))
  (PeerAnswer :status (int (get (.split (get lines 0)) 1))
              :headers (tuple (gfor line (cut lines 1 None) :setv [name _colon value] (.partition line ":")
                                    (HttpHeader :name name :value (.strip value))))
              :body (get parts 2) :texts #() :closed None))


(defk talk-ws [loop ws request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: ws "aiohttp.ClientWebSocketResponse") (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "繋いだ ws へ sends を順に送り、受ける。leave が在れば replies 通を受けた所で状態符と理由で閉じ、無ければ待ち受けが閉じるまで受ける。"
  (import aiohttp [WSMsgType])
  (for [item request.sends]
    (.run-until-complete loop (if (isinstance item bytes) (.send-bytes ws item) (.send-str ws item))))
  (var texts #())
  (var closed None)
  (var reading True)
  (var message None)
  (while (and reading (not (and (is-not request.leave None) (>= (len texts) request.replies))))
    (:= message (.run-until-complete loop (.receive ws :timeout PEER-SECONDS)))
    (match message.type
      WSMsgType.TEXT (:= texts (+ texts #(message.data)))
      WSMsgType.CLOSE (do (:= closed #(message.data message.extra))
                          (:= reading False))
      _ (:= reading False)))
  (match request.leave
    #(code reason) :if reading (.run-until-complete loop (.close ws :code code :message (.encode reason "utf-8")))
    _ None)
  (.run-until-complete loop (.close ws))
  (PeerAnswer :status WS-OPEN-STATUS :headers #() :body b"" :texts texts :closed closed))


(defk visit-ws [loop session base request]
  {:pre [(: loop asyncio.AbstractEventLoop) (: session "aiohttp.ClientSession") (: base str) (: request PeerRequest)]
   :post [(: % PeerAnswer)] :tags {:context "http-server-test" :role "foundation"}}
  "ws で繋いで話す。handshake を断られたら断りの status だけを答えにする。"
  (import aiohttp)
  (var ws None)
  (var refused None)
  (try
    (:= ws (.run-until-complete loop (.ws-connect session (+ base request.target))))
    (except [error aiohttp.WSServerHandshakeError]
      (:= refused error.status)))
  (if (is refused None)
      (do (<- talked PeerAnswer (talk-ws loop ws request))
          talked)
      (PeerAnswer :status refused :headers #() :body b"" :texts #() :closed None)))


(defk visit [address requests]
  {:pre [(: address HttpAddress) (: requests tuple)] :post [(: % tuple)] :tags {:context "http-server-test" :role "foundation"}}
  "本物の相手: requests を 1 つずつ送り、答えを読んでから次へ進む(この thread だけの event loop と aiohttp の client)。"
  (import aiohttp)
  (val base (.format "http://{}:{}" address.host address.port))
  (val loop (asyncio.new-event-loop))
  ;; 要求ごとに新しい接続で送る(上限で断った答えの後に待ち受けが閉じた接続を、次の要求が使い回さないように)。
  (val session (aiohttp.ClientSession :loop loop :connector (aiohttp.TCPConnector :loop loop :force-close True)))
  (var answers #())
  (try
    (for [request requests]
      (<- answer PeerAnswer (cond
                              request.ws (visit-ws loop session base request)
                              (or request.raw (is-not request.declared None)) (visit-raw address request)
                              True (visit-http loop session base request)))
      (:= answers (+ answers #(answer))))
    (finally
      (.run-until-complete loop (.close session))
      (.close loop)))
  answers)


(deff visit-in-thread [address requests outbox]  ; defk にできない: threading.Thread が別の thread で呼ぶ callback
  {:pre [(: address HttpAddress) (: requests tuple) (: outbox queue.Queue)] :post [(: % None)]
   :tags {:context "http-server-test" :role "foundation"}}
  "本物の相手を走らせ、答えの列(失敗なら例外)を outbox へ置く。"
  (try
    (.put outbox (run (visit address requests)))
    (except [error Exception]
      (.put outbox error)))
  None)


(defhandler live-peer
  ;; 本物の相手(頭の註)。outbox = 走っている相手が答えの列を置く箱(session の値 — PeerSend で作る)。
  (session var outbox None)
  (PeerSend [address requests]
    (val box (queue.Queue))
    (.start (threading.Thread :target visit-in-thread :args #(address requests box) :daemon True))
    (:= outbox box)
    (resume None))
  (PeerAnswers []
    (match outbox
      None (raise (RuntimeError "PeerSend の前に PeerAnswers を撃った"))
      box (do (val got (.get box :timeout (* 3 PEER-SECONDS)))
              (if (isinstance got Exception)
                  (raise got)
                  (resume got))))))


(deff upstream-app [environ start-response]  ; defk にできない: wsgiref が要求ごとに呼ぶ WSGI の callback
  {:pre [(: environ dict) (: start-response Callable)] :post [(: % list)]
   :tags {:context "http-server-test" :role "foundation"}}
  "本物の中継先: どの要求にも UPSTREAM-STATUS と UPSTREAM-BODY で答える。"
  (start-response (.format "{} Upstream" UPSTREAM-STATUS) [#("Content-Type" "text/plain")
                                                          #("Content-Length" (str (len UPSTREAM-BODY)))])
  [UPSTREAM-BODY])


(defk vacant-port []
  {:pre [] :post [(: % int)] :tags {:context "http-server-test" :role "foundation"}}
  "誰も聞いていない 127.0.0.1 の port(結んで直ぐに閉じる)。"
  (with [probe (socket.socket)]
    (.bind probe #("127.0.0.1" 0))
    (val port (get (.getsockname probe) 1)))
  port)


(defk under-aiohttp [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "http-server-test" :role "foundation"}}
  "本物の aiohttp-http-server の下で program を走らせる。site は一時 dir の実の file、upstream は走る間だけ聞く wsgiref の聞き手。"
  (import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val site (os.path.join (os.path.realpath directory) "app.js"))
    (with [handle (open site "wb")]
      (.write handle SITE-CONTENT))
    (val upstream (make-server "127.0.0.1" 0 upstream-app))
    (.start (threading.Thread :target upstream.serve-forever :daemon True))
    (<- dead int (vacant-port))
    (val world (World :site site :upstream (.format "http://127.0.0.1:{}" upstream.server-port) :dead (.format "http://127.0.0.1:{}" dead)))
    (try
      (<- ran (with_handlers [(await-handler) (state) (contract-world world) aiohttp-http-server live-peer] program))
      (:= answer ran)
      (finally
        (.shutdown upstream)
        (.server-close upstream))))
  answer)


;; --- fake の相手(台本)---------------------------------------------------------------------------------------------------------

(defk scripted-arrivals [requests first]
  {:pre [(: requests tuple) (: first int)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "相手の要求の列を台本の出来事と本文にする(札は first から順に振る — 本物の待ち受けが届いた順に振るのと同じ)。答え = #(出来事 本文)。"
  (var arrivals #())
  (var bodies #())
  (for [[index request] (enumerate requests)]
    (val ticket (str (+ first index)))
    (<- path str (path-of request.target))
    (<- headers tuple (sent-headers request))
    (:= arrivals (+ arrivals #((HttpRequestArrived :ticket ticket :method request.method :path path :target request.target
                                                   :headers headers :upgrade request.ws))))
    (when (is-not request.body None)
      (:= bodies (+ bodies #((ScriptedBody :ticket ticket :data request.body)))))
    (when request.ws
      (:= arrivals (+ arrivals (tuple (gfor item request.sends
                                            (if (isinstance item bytes)
                                                (WsBinaryArrived :ticket ticket :data item)
                                                (WsTextArrived :ticket ticket :text item))))))
      (match request.leave
        #(code reason) (:= arrivals (+ arrivals #((WsClosed :ticket ticket :code code :reason reason))))
        None None)))
  #(arrivals bodies))


(defk scripted-answer [served ticket request]
  {:pre [(: served tuple) (: ticket str) (: request PeerRequest)] :post [(: % PeerAnswer)]
   :tags {:context "http-server-test" :role "judgment"}}
  "台本の記録から、札の要求の相手が受け取った物を読む(台本の HttpServed が「端末が受け取る答え」)。"
  (val told (lfor s served :if (and (isinstance s HttpServed) (= s.ticket ticket)) s))
  (when (!= (len told) 1)
    (raise (AssertionError (.format "札 {} への命令はちょうど 1 つのはず: {!r}" ticket told))))
  (val answer (get told 0))
  (cond
    (and request.ws (= answer.status WS-OPEN-STATUS))
      (PeerAnswer :status answer.status :headers #() :body b""
                  :texts (tuple (gfor s served :if (and (isinstance s WsTextSent) (= s.ticket ticket)) s.text))
                  :closed (next (gfor s served :if (and (isinstance s WsCloseSent) (= s.ticket ticket)) #(s.code s.reason)) None))
    request.ws (PeerAnswer :status answer.status :headers #() :body b"" :texts #() :closed None)
    True (PeerAnswer :status answer.status
                     :headers (if (isinstance answer.command HttpRespond) answer.command.headers #())
                     :body (.encode answer.body "utf-8") :texts #() :closed None)))


(defhandler scripted-peer
  ;; fake の相手(頭の註)。asked = これまでに送らせた要求の列(札 = 位置 + 1)。
  (session var asked #())
  (PeerSend [address requests]
    (<- plan tuple (scripted-arrivals requests (+ (len asked) 1)))
    (<- (AppendHttpScript :arrivals (get plan 0) :bodies (get plan 1)))
    (:= asked (+ asked requests))
    (resume None))
  (PeerAnswers []
    (<- served tuple (ReadHttpServed))
    (var answers #())
    (for [[index request] (enumerate asked)]
      (<- answer PeerAnswer (scripted-answer served (str (+ index 1)) request))
      (:= answers (+ answers #(answer))))
    (resume answers)))


(val SCRIPTED-WORLD (World :site MEMORY-SITE :upstream SCRIPTED-UPSTREAM :dead SCRIPTED-DEAD))


(defk under-scripted [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "http-server-test" :role "foundation"}}
  "fake の scripted-http-server の下で program を走らせる。外から順に: state・memory の file(site)・世界・台本(出来事は空 — 相手が
   PeerSend で足す・中継先は SCRIPTED-UPSTREAM だけ)・相手。"
  (val files (MemoryFiles :files #((MemoryFile :path MEMORY-SITE :content SITE-CONTENT)) :dirs #((os.path.dirname MEMORY-SITE))))
  (val script (HttpScript :arrivals #()
                          :upstreams #((ScriptedUpstream :base SCRIPTED-UPSTREAM :http-status UPSTREAM-STATUS :accepts-ws False))))
  (<- answer (with_handlers [(state) (memory-file-handler files) (contract-world SCRIPTED-WORLD) (scripted-http-server script) scripted-peer]
               program))
  answer)


(val INTERPRETERS {AIOHTTP under-aiohttp
                   SCRIPTED under-scripted})
