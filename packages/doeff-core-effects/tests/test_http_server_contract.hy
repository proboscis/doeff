;;; HTTP の待ち受けの契約テスト — 同じ効果(HttpListen・HttpNextRequest・HttpRespond・HttpReadBody・HttpForward・WsAccept・WsSendText・
;;; WsClose・HttpShutdown・TakeWsSendReport)に答える本物(aiohttp-http-server)と fake(scripted-http-server)が、同じ deftest を通る。
;;; 解釈器の組み立てと契約の世界は http_server_contract_handlers.hy。どの deftest も同じ Program(serve — 受け口を開き、相手に要求を
;;; 送らせ、要求に答え、全部に決着が付いたら閉じる)を回し、相手が受け取った物・出来事の並び・閉じた後の答えを見る。
;;;
;;;   * HttpRespond の status・頭(同じ名の頭も落とさない)・本文(byte 列・file の範囲)がそのまま相手に届く。本文なしの答えは本文が空
;;;   * HEAD と 204 の答えは本文を送らない(HttpBodyBytes を渡しても)
;;;   * HttpForward は中継先の status を相手に届け、届かない中継先は 502
;;;   * WsAccept で上げた札は WsOpened・相手の 1 通が WsTextArrived / WsBinaryArrived・WsSendText が相手に届き、WsClose の状態符と理由が
;;;     相手の受ける close と出来事 WsClosed の両方に載る。相手が閉じれば WsClosed は相手の状態符と理由
;;;   * Upgrade の無い要求への WsAccept は 426 で断り、ws の出来事は出ない
;;;   * 送りの上限を超える 1 通は接続をその場で切る(WsClosed 1006 と切りの理由・切りの数)
;;;   * HttpShutdown は開いている ws へ close 1000 と理由を送り、以後の HttpNextRequest は何度でも HttpServerClosed(その理由)
;;;   * HttpReadBody は上限ちょうどまで読み、超えれば HttpBodyTooLarge(宣言の長さ・宣言なしは None)・札ごとに 1 度だけ・命令の後は読めない
;;; 答え手だけの性質は test_http_server.hy(本物: 結んだ port・HTTP の中継の本文と X-Forwarded-Proto・ws の中継・received-at。fake: 中継先の
;;; 最長の一致・読まない相手の箱・台本の後足し・読みの途中の失敗)。
(require doeff-hy.macros [defk deftest <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import doeff_core_effects.http_server_effects [HttpAddress HttpBodyBytes HttpBodyFailed HttpBodyFileRange HttpBodyOutcome HttpBodyRead
                                                HttpBodyTooLarge HttpEvent HttpForward HttpHeader HttpListen HttpNextRequest HttpNoBody
                                                HttpReadBody HttpRequestArrived HttpRespond HttpServerClosed HttpShutdown TakeWsSendReport
                                                WsAccept WsBinaryArrived WsClose WsClosed WsOpened WsSendReport WsSendText WsTextArrived
                                                WS-CUT-REASON WS-REFUSAL-TEXT])
(import http_server_contract_handlers [ContractWorld PeerAnswer PeerAnswers PeerRequest PeerSend UPSTREAM-STATUS World])

(val SHUTDOWN-REASON "検が閉じた")
(val SEND-LIMIT 64)
(val BODY-LIMIT 8)
(val HUGE-DECLARED 100000000)
(val GREETING (.encode "こんにちは" "utf-8"))


(defrecord ReadNote
  "本文の読みの記録: path・1 度目の答え・同じ札への 2 度目の答え。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ str path)
  (#^ HttpBodyOutcome first)
  (#^ HttpBodyOutcome again))


(defrecord Answered
  "要求 1 つへ命令を撃った結果: settled = この要求がここで決着したか(ws に上げた札は WsClosed で決着する)・note = 本文の読みの記録。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ bool settled)
  (setv #^ (| ReadNote None) note None))


(defrecord Served
  "serve の答え: events = 閉じるまでに受けた出来事(比べる形 — event-shape)・again = 閉じた後にもう 1 度受けた答え・answers = 相手が
   受け取った物(送った順)・reads = 本文の読みの記録・report = 最後の送りの勘定。"
  {:tags {:context "http-server-test" :role "type"}}
  (#^ tuple events)
  (#^ tuple again)
  (#^ tuple answers)
  (#^ (get tuple #(ReadNote ...)) reads)
  (#^ WsSendReport report))


(defk event-shape [event]
  {:pre [(: event HttpEvent)] :post [(: % tuple)] :tags {:context "http-server-test" :role "judgment"}}
  "出来事の比べる欄(頭と受けた拍は答え手ごとに違うので外す — 頭は相手の client が足す物を含む)。"
  (match event
    (HttpRequestArrived :ticket t :method m :path p :target target :upgrade u) #("request" t m p target u)
    (WsOpened :ticket t) #("opened" t)
    (WsTextArrived :ticket t :text text) #("text" t text)
    (WsBinaryArrived :ticket t :data data) #("binary" t data)
    (WsClosed :ticket t :code code :reason reason) #("closed" t code reason)
    (HttpServerClosed :reason reason) #("server-closed" reason)))


(defk respond [ticket status headers body]
  {:pre [(: ticket str) (: status int) (: headers tuple) (: body (| HttpBodyBytes HttpBodyFileRange HttpNoBody))] :post [(: % None)]
   :tags {:context "http-server-test" :role "program"}}
  "札へ答えを送る。"
  (<- (HttpRespond :ticket ticket :status status :headers headers :body body))
  None)


(defk read-and-answer [ticket path]
  {:pre [(: ticket str) (: path str)] :post [(: % ReadNote)] :tags {:context "http-server-test" :role "program"}}
  "本文を BODY-LIMIT まで読み(2 度目の読みも撃つ)、読めた本文は 200・上限超えは 413(宣言の長さ)・読めなければ 400 で答える。
   答え = 読みの記録。"
  (<- outcome HttpBodyOutcome (HttpReadBody :ticket ticket :max-bytes BODY-LIMIT))
  (<- again HttpBodyOutcome (HttpReadBody :ticket ticket :max-bytes BODY-LIMIT))
  (<- (match outcome
        (HttpBodyRead :data data) (respond ticket 200 #() (HttpBodyBytes :data data))
        (HttpBodyTooLarge :declared declared) (respond ticket 413 #() (HttpBodyBytes :data (.encode (str declared) "utf-8")))
        (HttpBodyFailed :reason reason) (respond ticket 400 #() (HttpBodyBytes :data (.encode reason "utf-8")))))
  (ReadNote :path path :first outcome :again again))


(defk answer-request [event world]
  {:pre [(: event HttpRequestArrived) (: world World)] :post [(: % Answered)] :tags {:context "http-server-test" :role "program"}}
  "要求 1 つへ path ごとの命令を撃つ。"
  (val t event.ticket)
  (match event.path
    "/text" (do (<- (respond t 200 #((HttpHeader :name "X-Contract" :value "a") (HttpHeader :name "X-Contract" :value "b")
                                     (HttpHeader :name "Content-Type" :value "text/plain; charset=utf-8"))
                             (HttpBodyBytes :data GREETING)))
                (Answered :settled True))
    "/file" (do (<- (respond t 206 #((HttpHeader :name "Content-Length" :value "5")) (HttpBodyFileRange :path world.site :start 2 :length 5)))
                (Answered :settled True))
    "/nothing" (do (<- (respond t 204 #() (HttpNoBody)))
                   (Answered :settled True))
    "/nothing-but-bytes" (do (<- (respond t 204 #() (HttpBodyBytes :data b"dropped")))
                             (Answered :settled True))
    "/head" (do (<- (respond t 200 #((HttpHeader :name "Content-Length" :value "11")) (HttpNoBody)))
                (Answered :settled True))
    "/relay" (do (<- (HttpForward :ticket t :url (+ world.upstream "/x")))
                 (Answered :settled True))
    "/dead" (do (<- (HttpForward :ticket t :url (+ world.dead "/x")))
                (Answered :settled True))
    "/ws" (do (<- (WsAccept :ticket t))
              ;; Upgrade の無い要求は断られ、ws の出来事は出ない — ここで決着する。
              (Answered :settled (not event.upgrade)))
    "/late-read" (do (<- (respond t 204 #() (HttpNoBody)))
                     (<- late HttpBodyOutcome (HttpReadBody :ticket t :max-bytes BODY-LIMIT))
                     (Answered :settled True :note (ReadNote :path event.path :first late :again late)))
    path :if (.startswith path "/body") (do (<- note ReadNote (read-and-answer t path))
                                            (Answered :settled True :note note))
    other (raise (ValueError (+ "契約の Program に無い path: " other)))))


(defk answer-ws [event]
  {:pre [(: event (| WsOpened WsTextArrived WsBinaryArrived))] :post [(: % None)] :tags {:context "http-server-test" :role "program"}}
  "ws の 1 通へ答える: \"bye\" = 4001 で閉じる・\"big\" = 送りの上限の 2 倍の 1 通を送る・\"stop\" = 待ち受けを閉じる・他の文字は echo・
   byte の 1 通は 1003 で閉じる。"
  (match event
    (WsTextArrived :ticket t :text "bye") (<- (WsClose :ticket t :code 4001 :reason "さようなら"))
    (WsTextArrived :ticket t :text "big") (<- (WsSendText :ticket t :text (* "x" (* 2 SEND-LIMIT))))
    (WsTextArrived :text "stop") (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 2.0))
    (WsTextArrived :ticket t :text text) (<- (WsSendText :ticket t :text (+ "echo:" text)))
    (WsBinaryArrived :ticket t) (<- (WsClose :ticket t :code 1003 :reason "text だけ"))
    (WsOpened) None)
  None)


(defk serve [requests]
  {:pre [(: requests tuple)] :post [(: % Served)] :tags {:context "http-server-test" :role "program"}}
  "契約の Program: 待ち受けを開き、相手に requests を送らせ、要求に答え、全部の要求が決着したら閉じる。閉じた後にもう 1 度受け、相手が
   受け取った物と送りの勘定を読む。"
  (<- world World (ContractWorld))
  (<- bound HttpAddress (HttpListen :address (HttpAddress :host "127.0.0.1" :port 0) :ws-send-max-bytes SEND-LIMIT))
  (<- (PeerSend :address bound :requests requests))
  (var events #())
  (var reads #())
  (var settled 0)
  (var shut False)
  (var open True)
  (while open
    (<- event HttpEvent (HttpNextRequest))
    (<- shape tuple (event-shape event))
    (:= events (+ events #(shape)))
    (match event
      (HttpServerClosed) (:= open False)
      (HttpRequestArrived) (do (<- answered Answered (answer-request event world))
                               (:= settled (+ settled (if answered.settled 1 0)))
                               (:= reads (+ reads (if (is answered.note None) #() #(answered.note)))))
      (WsClosed) (:= settled (+ settled 1))
      _ (<- (answer-ws event)))
    (when (and open (not shut) (= settled (len requests)))
      (:= shut True)
      (<- (HttpShutdown :reason SHUTDOWN-REASON :drain-seconds 0.5))))
  (<- again HttpEvent (HttpNextRequest))
  (<- again-shape tuple (event-shape again))
  (<- answers tuple (PeerAnswers))
  (<- report WsSendReport (TakeWsSendReport))
  (Served :events events :again again-shape :answers answers :reads reads :report report))


(defk header-values [answer name]
  {:pre [(: answer PeerAnswer) (: name str)] :post [(: % list)] :tags {:context "http-server-test" :role "judgment"}}
  "相手が受け取った頭のうち name の値の列(名の大小を問わない・届いた順)。"
  (lfor h answer.headers :if (= (.lower h.name) (.lower name)) h.value))


(defk request-shapes [requests]
  {:pre [(: requests tuple)] :post [(: % list)] :tags {:context "http-server-test" :role "judgment"}}
  "要求の列が届いた時の出来事の形(札は 1 から順)。"
  (lfor [index r] (enumerate requests)
        #("request" (str (+ index 1)) r.method (get (.partition r.target "?") 0) r.target r.ws)))


(val CLOSED #(#("server-closed" SHUTDOWN-REASON)))


(deftest test-an-answer-reaches-the-peer-as-sent
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/text?q=1") (PeerRequest :method "GET" :target "/file")
                  (PeerRequest :method "GET" :target "/nothing")))
  (<- served Served (serve requests))
  (<- shapes list (request-shapes requests))
  (assert (= served.events (+ (tuple shapes) CLOSED)) served.events)
  (val text (get served.answers 0))
  (val file (get served.answers 1))
  (val nothing (get served.answers 2))
  (assert (= [text.status text.body] [200 GREETING]) text)
  ;; 同じ名の頭は 2 つとも届く。
  (assert (= (! (header-values text "X-Contract")) ["a" "b"]) text.headers)
  (assert (= (! (header-values text "Content-Type")) ["text/plain; charset=utf-8"]) text.headers)
  ;; file の範囲は site の 2 byte 目から 5 byte。
  (assert (= [file.status file.body (! (header-values file "Content-Length"))] [206 b"nsole" ["5"]]) file)
  (assert (= [nothing.status nothing.body] [204 b""]) nothing))


(deftest test-head-and-no-content-answers-carry-no-body
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  ;; 素の socket で送る — 答えの頭の後に届いた byte は全部本文として見える。
  (val requests #((PeerRequest :method "HEAD" :target "/head" :raw True) (PeerRequest :method "HEAD" :target "/text" :raw True)
                  (PeerRequest :method "GET" :target "/nothing-but-bytes" :raw True) (PeerRequest :method "GET" :target "/text")))
  (<- served Served (serve requests))
  (val head (get served.answers 0))
  (val head-with-bytes (get served.answers 1))
  (val no-content (get served.answers 2))
  (val after (get served.answers 3))
  (assert (= [head.status head.body (! (header-values head "Content-Length"))] [200 b"" ["11"]]) head)
  ;; HttpBodyBytes を渡しても HEAD と 204 は本文を送らない。
  (assert (= [head-with-bytes.status head-with-bytes.body] [200 b""]) head-with-bytes)
  (assert (= [no-content.status no-content.body] [204 b""]) no-content)
  (assert (= [after.status after.body] [200 GREETING]) after))


(deftest test-a-forward-answers-with-the-upstream-status-or-502
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "POST" :target "/relay" :body b"payload") (PeerRequest :method "GET" :target "/dead")))
  (<- served Served (serve requests))
  (assert (= (lfor a served.answers a.status) [UPSTREAM-STATUS 502]) served.answers)
  (assert (= served.events (+ (tuple (! (request-shapes requests))) CLOSED)) served.events))


(deftest test-a-ws-exchange-reaches-both-sides-and-our-close-is-named
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("hi" "bye")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "hi") #("text" "1" "bye")
                                #("closed" "1" 4001 "さようなら"))
                              CLOSED))
          served.events)
  (val answer (get served.answers 0))
  (assert (= [answer.status answer.texts answer.closed] [101 #("echo:hi") #(4001 "さようなら")]) answer)
  (assert (= [served.report.queued-frames served.report.queued-bytes served.report.flushed-bytes served.report.dropped-bytes
              served.report.cuts (len served.report.flush-seconds)]
             [1 7 7 0 0 1])
          served.report))


(deftest test-a-peer-close-and-a-binary-close-name-their-code
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("hi") :replies 1 :leave #(4002 "相手が去る"))
                  (PeerRequest :method "GET" :target "/ws" :ws True :sends #(b"\x01"))))
  (<- served Served (serve requests))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "hi") #("closed" "1" 4002 "相手が去る")
                                #("request" "2" "GET" "/ws" "/ws" True) #("opened" "2") #("binary" "2" b"\x01")
                                #("closed" "2" 1003 "text だけ"))
                              CLOSED))
          served.events)
  (val left (get served.answers 0))
  (val binary (get served.answers 1))
  ;; 相手が閉じた接続は、待ち受けからの close を相手の受けとして数えない。
  (assert (= [left.texts left.closed] [#("echo:hi") None]) left)
  (assert (= [binary.texts binary.closed] [#() #(1003 "text だけ")]) binary))


(deftest test-a-ws-accept-without-upgrade-is-refused
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (val requests #((PeerRequest :method "GET" :target "/ws")))
  (<- served Served (serve requests))
  ;; 断った札には ws の出来事が出ない。
  (assert (= served.events (+ (tuple (! (request-shapes requests))) CLOSED)) served.events)
  (val refused (get served.answers 0))
  (assert (= [refused.status refused.body] [426 (.encode WS-REFUSAL-TEXT "utf-8")]) refused))


(deftest test-an-oversized-send-cuts-the-connection
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("big")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "big") #("closed" "1" 1006 WS-CUT-REASON))
                              CLOSED))
          served.events)
  (val cut (get served.answers 0))
  (assert (= [cut.texts cut.closed] [#() None]) cut)
  ;; 上限を超える 1 通は箱へ積まずに切る — 積んだ・流した・捨てた byte は 0、切りは 1。
  (assert (= [served.report.queued-frames served.report.queued-bytes served.report.flushed-bytes served.report.dropped-bytes
              served.report.cuts]
             [0 0 0 0 1])
          served.report))


(deftest test-a-shutdown-closes-open-ws-and-stays-closed
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "GET" :target "/ws" :ws True :sends #("stop")))))
  (assert (= served.events (+ #(#("request" "1" "GET" "/ws" "/ws" True) #("opened" "1") #("text" "1" "stop")) CLOSED)) served.events)
  ;; 閉じた後の HttpNextRequest も同じ理由の HttpServerClosed。
  (assert (= served.again (get CLOSED 0)) served.again)
  (val answer (get served.answers 0))
  (assert (= answer.closed #(1000 SHUTDOWN-REASON)) answer))


;; 境目の要求と 1 度目の答え(2 度目はどれも HttpBodyFailed)。
(val BODY-CASES #(#((PeerRequest :method "POST" :target "/body/exact" :body b"12345678") (HttpBodyRead :data b"12345678") #(200 b"12345678"))
                  #((PeerRequest :method "POST" :target "/body/over" :body b"123456789") (HttpBodyTooLarge :declared 9) #(413 b"9"))
                  #((PeerRequest :method "POST" :target "/body/chunked-fit" :body b"abcdefgh" :chunked True)
                    (HttpBodyRead :data b"abcdefgh") #(200 b"abcdefgh"))
                  #((PeerRequest :method "POST" :target "/body/chunked-over" :body b"abcdefghijkl" :chunked True)
                    (HttpBodyTooLarge :declared None) #(413 b"None"))
                  #((PeerRequest :method "GET" :target "/body/empty") (HttpBodyRead :data b"") #(200 b""))
                  ;; 上限を大きく超える宣言: 本物の相手は頭だけ送る — 読まずに断らなければ答えが来ない。
                  #((PeerRequest :method "POST" :target "/body/huge-declared" :declared HUGE-DECLARED)
                    (HttpBodyTooLarge :declared HUGE-DECLARED) #(413 (.encode (str HUGE-DECLARED) "ascii")))))


(deftest test-a-body-is-read-once-up-to-the-limit
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve (tuple (gfor c BODY-CASES (get c 0)))))
  (assert (= (tuple (gfor note served.reads #(note.path note.first)))
             (tuple (gfor [request outcome _answer] BODY-CASES #(request.target outcome))))
          served.reads)
  ;; 札ごとに 1 度だけ — 2 度目は読めない。
  (assert (all (gfor note served.reads (isinstance note.again HttpBodyFailed))) served.reads)
  (assert (= (lfor a served.answers #(a.status a.body)) (lfor c BODY-CASES (get c 2))) served.answers))


(deftest test-a-body-cannot-be-read-after-the-command
  {:interpreters ["aiohttp-http-server" "scripted-http-server"]}
  (<- served Served (serve #((PeerRequest :method "POST" :target "/late-read" :body b"abc"))))
  (val late (. (get served.reads 0) first))
  (assert (isinstance late HttpBodyFailed) late)
  (assert (= (. (get served.answers 0) status) 204) served.answers))
