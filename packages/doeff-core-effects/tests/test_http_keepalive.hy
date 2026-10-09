;;; HTTP の client が使われない接続を持つ秒と、相手の server が閉じる秒の組(agora-redesign #3688 の子 — 記録の service への書きが前の
;;; 書きから 5 秒を越えて空くと、書きのたびに TCP を張り直して名前を引き直していた)。本物の socket で、検の中の最小の待ち受け(標準の
;;; http.server — 受けた接続の数と要求の本文を数える)へ当てる。
;;;   (1) 既定の client は、旧い既定(httpx の keepalive_expiry 5 秒)を越える実時間の間(6 秒)を空けても張り直さない。本番の書きの間
;;;       (30 秒)を待つと遅いので、30 秒の側は、公開の作り方で作った client が実際に持つ接続の期限(Limits の値そのもの)と、doeff の
;;;       待ち受けが使われない接続を閉じる秒がそれより長いことの断言で補う
;;;   (2) server が先に閉じた接続へ書く形でも誤りにならず、1 度だけ張り直して書く(書きの重複 0)— httpcore が送る前に閉じた接続を捨てる。
;;;       送った後に閉じられた要求は httpx も httpcore も送り直さない(送り直すかは HttpRequest の max-retries だけが決める)
;;; 新しい公開の名(http-client-factory・CLIENT-KEEPALIVE-SECONDS・IDLE-CONNECTION-SECONDS)は module の欄として読む — 直す前の版では
;;; 欄が無い検だけが落ち、(1) の実時間の検は振る舞いで落ちる。
(require doeff-hy.macros [deftest defk <- val with-handler])
(require doeff-hy.record [defrecord])
(import asyncio)
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import http.server)
(import socket)
(import ssl)
(import threading)
(import time)
(import httpx)
(import pytest)
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_handlers :as http-handlers)
(import doeff_core_effects.http_server_effects [HttpAddress WS-CLOSE-NORMAL])

;; 旧い既定の期限(httpx の keepalive_expiry 5 秒)を越える実時間の間(秒)。
(val OLD-EXPIRY-GAP-SECONDS 6.0)
;; 本番の書きの間(画面の job が記録の service へ書く間の実測 26〜30 秒)。
(val CALL-GAP-SECONDS 30.0)
;; server が先に閉じる形: 待ち受けが次の要求を待つ上限(秒)と、client がそれより長く空ける間(秒 — 相手の FIN が届いてから書く)。
(val SERVER-FIRST-IDLE-SECONDS 0.3)
(val AFTER-SERVER-CLOSED-GAP-SECONDS 1.0)
;; 待ち受けが読んでから答えずに閉じる本文(送った後に閉じられる形)。
(val CUT b"cut")
;; doeff の待ち受けを開く・接続を受けるまでを待つ上限(秒)。
(val EDGE-SECONDS 10.0)


(defclass Tally []
  "待ち受けが受けた接続の数と、要求の本文の列(接続ごとの thread が lock の内で足す)。"
  (defn #^ None __init__ [self]
    "数えを 0 と空で始めるため。"
    (setv self.lock (threading.Lock))
    (setv self.connections 0)
    (setv #^ (get tuple #(bytes ...)) self.arrivals #())
    None)
  (defn #^ None connected [self]
    "接続を 1 本受けた事を数えるため。"
    (with [self.lock]
      (setv self.connections (+ self.connections 1)))
    None)
  (defn #^ None arrived [self #^ bytes body]
    "要求の本文を 1 つ控えるため(受けた順)。"
    (with [self.lock]
      (setv self.arrivals (+ self.arrivals #(body))))
    None))


(defclass CountingHandler [http.server.BaseHTTPRequestHandler]
  "接続ごとの受け手(HTTP/1.1 — 接続を使い回す): 接続を数え、POST の本文を控えて 200 で答える。本文 CUT は読んでから答えずに閉じる。
   待ち受けの idle-seconds が在れば、次の要求をその秒だけ待って来なければ閉じる(server が先に閉じる形 — 標準の socket の上限)。"
  (setv protocol-version "HTTP/1.1")
  (defn #^ None setup [self]
    "次の要求を待つ上限を待ち受けから受けてから接続を開き、接続を数えるため。"
    (setv self.timeout self.server.idle-seconds)
    (.setup (super))
    (.connected self.server.tally)
    None)
  (defn #^ None do-POST [self]
    "本文を控え、CUT なら答えずに閉じ、他は 200 で答えるため。"
    (setv body (.read self.rfile (int (.get self.headers "Content-Length" "0"))))
    (.arrived self.server.tally body)
    (if (= body CUT)
        (setv self.close-connection True)
        (do (.send-response self 200)
            (.send-header self "Content-Length" "2")
            (.end-headers self)
            (.write self.wfile b"ok")))
    None)
  (defn #^ None log-message [self #^ str format #^ (| str int) #* args]
    "要求ごとの行を書かないため。"
    None))


(defclass CountingServer [http.server.ThreadingHTTPServer]
  "受けた接続と要求を数える待ち受け(127.0.0.1 の空きの port)。idle-seconds = 接続ごとに次の要求を待つ上限(None = 待ち続ける)。"
  (setv daemon-threads True)
  (defn #^ None __init__ [self #^ (| float None) idle-seconds]
    "空きの port で開き、数えを空で始めるため。"
    (.__init__ (super) #("127.0.0.1" 0) CountingHandler)
    (setv self.idle-seconds idle-seconds)
    (setv self.tally (Tally))
    None))


(defrecord Observed
  "client の Program の答えと、待ち受けが受けた接続の数と要求の本文の列。"
  (#^ tuple answers)
  (#^ int connections)
  (#^ tuple arrivals))


(defk observed-through [handler idle-seconds program-of]
  {:pre [(: handler Callable) (: idle-seconds (| float None)) (: program-of Callable)] :post [(: % Observed)]
   :tags {:context "http" :role "foundation"}}
  "数える待ち受けを立て、その URL への client の Program(program-of の答え)を HTTP の答え手 handler と await-handler の下で走らせ、
   待ち受けを止めてから数えを読むため(client は範囲の終わりで閉じる — 答え手が範囲ごとに作って閉じる)。"
  (val server (CountingServer idle-seconds))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (val url (.format "http://127.0.0.1:{}/write" (get server.server-address 1)))
  (try
    (<- answers tuple (with-handler [(await-handler) handler] (program-of url)))
    (Observed :answers answers :connections server.tally.connections :arrivals server.tally.arrivals)
    (finally
      (.shutdown server)
      (.server-close server))))


(defk write-twice [gap url]
  {:pre [(: gap float) (: url str)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "本文 1 と 2 の POST を、gap 秒の実時間を空けて 1 回ずつ出すため。撃ち直しは 0 回で、届かない失敗は値で受ける — 答え手の送り直しで
   張り直しや送った後の失敗を隠さない。答え = 2 つの答え。"
  (<- first (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"1" :max-retries 0 :failures-as-values True))
  (<- (Await (asyncio.sleep gap)))
  (<- second (| HttpResponse HttpFailed) (HttpRequest "POST" url :body b"2" :max-retries 0 :failures-as-values True))
  #(first second))


(defk write-cut [url]
  {:pre [(: url str)] :post [(: % tuple)] :tags {:context "http" :role "program"}}
  "本文 CUT の POST を 1 回出すため(撃ち直し 0 回・届かない失敗は値で受ける)。答え = 1 つの答え。"
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "POST" url :body CUT :max-retries 0 :failures-as-values True))
  #(answer))


(defk all-written [observed]
  {:pre [(: observed Observed)] :post [(: % bool)] :tags {:context "http" :role "program"}}
  "client の答えが全部 200 の返事かを判じるため(届かない失敗が混ざれば偽)。"
  (all (gfor answer observed.answers (and (isinstance answer HttpResponse) (= answer.status 200)))))


(deftest test-the-default-client-keeps-its-connection-across-a-gap-longer-than-the-old-expiry
  ;; 失敗ケース (1): 既定の client(client-factory を渡さない http-production-handler)で、旧い既定の期限 5 秒を越える 6 秒の実時間を
  ;; 空けて 2 回書く。直す前は 2 回目の前に期限の切れた接続を閉じて張り直した(接続 2)。直した後は同じ接続で書く(接続 1)。
  (<- seen Observed (observed-through (http-production-handler) None (fn [url] (write-twice OLD-EXPIRY-GAP-SECONDS url))))
  (assert (! (all-written seen)) seen)
  (assert (= seen.arrivals #(b"1" b"2")) seen)
  (assert (= seen.connections 1) seen))


(defrecord MadeClient
  "client が実際に持つ接続の持ち方: 接続の数の上限・使われない接続を持つ数の上限・使われない接続を持つ秒・proxy の env を読むか・
   相手の証明書を検める SSLContext。"
  (#^ int max-connections)
  (#^ int max-keepalive)
  (#^ float keepalive-seconds)
  (#^ bool trust-env)
  (#^ ssl.SSLContext ssl-context))


(defk made-client [factory]
  {:pre [(: factory Callable)] :post [(: % MadeClient)] :tags {:context "http" :role "foundation"}}
  "factory で client を 1 つ作り、実際に持つ接続の持ち方を読んで閉じるため。httpx は Limits を client に残さず httpcore の pool にだけ
   渡すので、pool の欄を読む(版は uv.lock の httpx 0.28.1・httpcore 1.0.9)。"
  (val client (factory))
  (val pool client._transport._pool)
  (val made (MadeClient :max-connections pool._max-connections :max-keepalive pool._max-keepalive-connections
                        :keepalive-seconds pool._keepalive-expiry :trust-env client.trust-env :ssl-context pool._ssl-context))
  (<- (Await (.aclose client)))
  made)


(deftest test-the-public-client-factory-keeps-idle-connections-past-the-call-gap
  ;; 失敗ケース (1) の 30 秒の側: 公開の作り方で作った client(既定の形と、agora の foundation の 3 か所の引数の形)が実際に持つ使われない
  ;; 接続の期限は、名前つきの 1 か所の CLIENT-KEEPALIVE-SECONDS で、本番の書きの間 30 秒より長い。接続の数の 2 つの上限は httpx の既定の
  ;; まま(素の httpx.AsyncClient と同じ)で、trust-env と verify は渡したとおり。直す前は公開の作り方が無く、既定の client の期限は
  ;; httpx の既定 5 秒だった(30 秒空いた書きは毎回張り直した)。
  (val make http-handlers.http-client-factory)
  (val context (ssl.create-default-context))
  (<- plain MadeClient (with-handler [(await-handler)] (made-client httpx.AsyncClient)))
  (for [#(factory trust-env given) [#(make True None)
                                    #((fn [] (make :trust-env False)) False None)
                                    #((fn [] (make :verify context :trust-env False)) False context)]]
    (<- made MadeClient (with-handler [(await-handler)] (made-client factory)))
    (assert (= #(made.max-connections made.max-keepalive) #(plain.max-connections plain.max-keepalive)) made)
    (assert (= made.keepalive-seconds http-handlers.CLIENT-KEEPALIVE-SECONDS) made)
    (assert (> made.keepalive-seconds CALL-GAP-SECONDS) made)
    (assert (= made.trust-env trust-env) made)
    (assert (or (is given None) (is made.ssl-context given)) made)))


(deftest test-a-connection-the-server-closed-first-is-replaced-before-the-write-is-sent
  ;; 失敗ケース (2): 相手の server が先に閉じた接続(使われない接続を SERVER-FIRST-IDLE-SECONDS で閉じる待ち受け)へ、公開の作り方の
  ;; client(agora の形 trust-env False)で次を書く。httpcore は送る前に、使われていない接続の socket が読める(相手の FIN が届いた)のを
  ;; 見てその接続を捨て、新しい接続で送る: 誤りにならず(撃ち直し 0 回でも 200)、書きは 1 回ずつ(重複 0)、張り直しは 1 回(接続 2)。
  (val handler (http-production-handler :client-factory (fn [] (http-handlers.http-client-factory :trust-env False))))
  (<- seen Observed (observed-through handler SERVER-FIRST-IDLE-SECONDS
                                      (fn [url] (write-twice AFTER-SERVER-CLOSED-GAP-SECONDS url))))
  (assert (! (all-written seen)) seen)
  (assert (= seen.arrivals #(b"1" b"2")) seen)
  (assert (= seen.connections 2) seen))


(deftest test-a-write-the-server-closed-after-reading-is-not-sent-again
  ;; (2) の境: 相手が要求を読んでから答えずに閉じた(送った後に閉じられた)要求を、httpx も httpcore も送り直さない — 届かない失敗
  ;; (OTHER・RemoteProtocolError)で答え、相手が受けた書きは 1 回。送り直すかは HttpRequest の max-retries だけが決める(冪等でない書きは
  ;; 0 回で出す)。
  (val handler (http-production-handler :client-factory (fn [] (http-handlers.http-client-factory :trust-env False))))
  (<- seen Observed (observed-through handler None write-cut))
  (val failed (get seen.answers 0))
  (assert (and (isinstance failed HttpFailed) (= failed.kind HttpFailureKind.OTHER)) failed)
  (assert (in "RemoteProtocolError" failed.detail) failed)
  (assert (= seen.arrivals #(CUT)) seen)
  (assert (= seen.connections 1) seen))


(defrecord ServerIdle
  "doeff の待ち受けが使われない接続を閉じるまでの秒: 受けた接続の aiohttp の受け手が持つ値と、名前つきの宣言の値。"
  (#^ float live)
  (#^ float declared))


(defk doeff-server-idle []
  {:pre [] :post [(: % ServerIdle)] :tags {:context "http" :role "foundation"}}
  "doeff の待ち受け(aiohttp-http-server の WebEdge)を空きの port で開いて接続を 1 本張り、待ち受けがその接続を受けるのを待って、
   使われない接続を閉じるまでの秒を aiohttp の受け手から読み、宣言の値と並べて閉じるため(aiohttp の無い venv では skip)。"
  (pytest.importorskip "aiohttp" :reason "aiohttp は extra http-server の依存")
  (import doeff_core_effects.aiohttp_http_server :as edge-module)
  (val edge (edge-module.WebEdge))
  (val loop (.edge-loop edge))
  (val bound (.result (asyncio.run-coroutine-threadsafe (.start edge (HttpAddress :host "127.0.0.1" :port 0) 1024 1024 #() False) loop)
                      EDGE-SECONDS))
  (val peer (socket.create-connection #("127.0.0.1" bound.port) :timeout EDGE-SECONDS))
  (val deadline (+ (time.monotonic) EDGE-SECONDS))
  (try
    (while (and (not edge.runner.server.connections) (< (time.monotonic) deadline))
      (time.sleep 0.02))
    (ServerIdle :live (float (. (get edge.runner.server.connections 0) keepalive-timeout))
                :declared edge-module.IDLE-CONNECTION-SECONDS)
    (finally
      (.close peer)
      (.result (asyncio.run-coroutine-threadsafe (.shutdown edge "検が閉じた" 0.2 WS-CLOSE-NORMAL) loop) EDGE-SECONDS))))


(deftest test-the-doeff-server-keeps-an-idle-connection-longer-than-the-client
  ;; 組の他方: doeff の待ち受け(記録の表の service 8875 と会話の記録の service 8874 の待ち受け)が使われない接続を閉じるまでの秒は、
  ;; 名前つきの IDLE-CONNECTION-SECONDS(aiohttp の既定に頼らない)で、client が使われない接続を持つ秒 CLIENT-KEEPALIVE-SECONDS より
  ;; 長い — server が先に閉じた接続へ client が書く形(失敗ケース (2))は、server の再起動などの時だけ起きる。
  (<- idle ServerIdle (doeff-server-idle))
  (assert (= idle.live idle.declared) idle)
  (assert (> idle.declared http-handlers.CLIENT-KEEPALIVE-SECONDS) idle))
