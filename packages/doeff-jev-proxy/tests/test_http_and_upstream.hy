;; 本物の Jev への翻訳(jev-upstream-handler)と HTTP の口の反例: キーは本物の Jev への見出しにだけ載り、届かなかった理由の文にも
;; 答えにも出ない・再試行は代理でしない(呼び手が撃ち直す)・実の socket の上で 2 回目が当たり、計器に出る。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import collections.abc [Callable])
(import hashlib)
(import json)
(import os)
(import socket)
(import tempfile)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import urllib.request [Request urlopen])
(import urllib.error [HTTPError])
(import doeff [run with_handlers])
(import doeff_core_effects [await_handler try_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_jev.target [JevTarget])
(import doeff_jev_proxy.values [UpstreamReply UpstreamUnreachable])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.principals [Roster])
(import doeff_jev_proxy.effects [AskJev PrepareStore])
(import doeff_jev_proxy.handlers [Flights jev-upstream-handler])
(import doeff_jev_proxy.main [production-handlers proxy-runner])
(import doeff_jev_proxy.http_server [ProxyServerConfig start-proxy-server stop-server])
(import tests.world [OPERATOR-TOKEN open-world jev-answers question request-of header])

(val SECRET "apikey_for_test_only")
(val TARGET (JevTarget :base-url "https://api.typesafe.ai/v1/systemone" :model "jev-latest" :wire "direct" :api-key SECRET
                       :source "env"))


(defhandler scripted-http [seen answer]
  ;; 引数に残す理由: 検の本体が届いた HttpRequest を読むため、外から渡した列に積む
  (HttpRequest []
    (.append seen effect)
    (resume answer)))


(defk ask-through [answer seen]
  {:pre [(: answer (| HttpResponse HttpFailed)) (: seen list)] :post [(: % (| UpstreamReply UpstreamUnreachable))]}
  "台本の HTTP の上で AskJev を 1 回答えさせるため。"
  (run (scheduled (with_handlers [(await_handler) try_handler (scripted-http seen answer) (jev-upstream-handler TARGET 5.0)]
                                 (AskJev b"{\"questions\":{}}")))))


(deftest test-upstream-carries-the-key-only-in-the-authorization-header
  (val seen [])
  (<- ok (ask-through (HttpResponse :status 200 :headers {} :content b"{\"answers\":{}}" :text "{\"answers\":{}}"
                                    :url TARGET.base-url :elapsed-seconds 0.1)
                      seen))
  (assert (= ok (UpstreamReply :status 200 :body b"{\"answers\":{}}")))
  (val request (get seen 0))
  (assert (= #(request.method request.url) #("POST" TARGET.base-url)))
  (assert (= (get request.headers "authorization") (+ "Bearer " SECRET)))
  (assert (= request.body b"{\"questions\":{}}") "本文はそのまま渡す")
  (assert (= request.max-retries 0) "撃ち直しは呼び手の持ち物(代理で重ねない)")
  (<- failed (ask-through (HttpFailed :url TARGET.base-url :detail "ConnectTimeout: timed out" :kind HttpFailureKind.TIMED-OUT) []))
  (assert (isinstance failed UpstreamUnreachable))
  (assert (not-in SECRET failed.detail) "届かなかった理由の文にキーを載せない"))


(defk http-call [url method body token]
  {:pre [(: url str) (: method str) (: body (| bytes None)) (: token (| str None))] :post [(: % tuple)]}
  "実の socket で要求を 1 つ撃ち、#(status 見出しの写像 本文) を返すため。"
  (val headers (| {"content-type" "application/json"} (if token {"authorization" (+ "Bearer " token)} {})))
  (try
    (with [response (urlopen (Request url :data body :method method :headers headers) :timeout 10)]
      #(response.status (dict (.items response.headers)) (.read response)))
    (except [error HTTPError]
      #(error.code (dict (.items error.headers)) (.read error)))))


(deftest test-http-socket-second-ask-is-a-hit-and-metrics-count-it
  (<- world (open-world (! (jev-answers 0.8)) 0.0))
  (val running (start-proxy-server (ProxyServerConfig :runner world.run)))
  (try
    (<- body (question "(defk q [] 6)" "jev-latest"))
    (<- first (http-call (+ running.url "/v1/systemone") "POST" body OPERATOR-TOKEN))
    (<- second (http-call (+ running.url "/v1/systemone") "POST" body OPERATOR-TOKEN))
    (assert (= #((get first 0) (.get (get first 1) "x-jev-proxy")) #(200 "miss")) first)
    (assert (= #((get second 0) (.get (get second 1) "x-jev-proxy")) #(200 "hit")) second)
    (assert (= (get first 2) (get second 2)))
    (<- health (http-call (+ running.url "/healthz") "GET" None None))
    (assert (= (get health 0) 200))
    (<- metrics (http-call (+ running.url "/metrics") "GET" None None))
    (val text (.decode (get metrics 2) "utf-8"))
    (assert (in "jev_proxy_saved_calls_total 1" text) text)
    (assert (in "jev_proxy_upstream_calls_total 1" text) text)
    (assert (in "jev_proxy_requests_total 2" text) text)
    (assert (= (len world.script.calls) 1))
    (finally
      (stop-server running))))


(defn fake-jev-server [#^ list seen]  ; defk にできない: http.server の要求ごとの callback の class を持つ偽の Jev を立てる検の部品
  "偽の本物の Jev(127.0.0.1 の空き port)を立てる。届いた Authorization を seen に積み、いつも同じ答えを返す。"
  (defclass FakeJev [BaseHTTPRequestHandler]
    (setv protocol-version "HTTP/1.1")
    (defn do-POST [self]
      (.read self.rfile (int (.get self.headers "Content-Length" "0")))
      (.append seen (.get self.headers "Authorization"))
      (setv payload b"{\"answers\":{\"q\":{\"noul\":0.5}},\"model\":\"jev-fake\"}")
      (.send-response self 200)
      (.send-header self "Content-Type" "application/json")
      (.send-header self "Content-Length" (str (len payload)))
      (.end-headers self)
      (.write self.wfile payload))
    (defn log-message [self #* args] None))
  (setv server (ThreadingHTTPServer #("127.0.0.1" 0) FakeJev))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(defk production-world [base-url]
  {:pre [(: base-url str)] :post [(: % Callable)]}
  "本番と同じ答え手の組(HTTP の実体 = httpx)で、本物の Jev の宛先だけを base-url にした runner を作るため。"
  (val path (os.path.join (tempfile.mkdtemp :prefix "jev-proxy-test-") "answers.sqlite"))
  (val target (JevTarget :base-url base-url :model "jev-latest" :wire "direct" :api-key SECRET :source "env"))
  (val digest (.hexdigest (hashlib.sha256 (.encode OPERATOR-TOKEN "utf-8"))))
  (val handlers-for (production-handlers path target 5.0 (Roster (FrozenMap {"operator" digest})) (frozenset ["operator"])
                                         (Flights :table {} :lock (threading.Lock))))
  (run (scheduled (with_handlers (+ [(await_handler) try_handler] (handlers-for)) (PrepareStore))))
  (proxy-runner handlers-for))


(deftest test-production-stack-serves-many-requests-and-names-an-unreachable-jev
  ;; 本番の組は要求ごとに作り直す — 使い回すと 2 つ目の要求で httpx の client が閉じている(実弾 2026-09-28 の配備で 500)。
  ;; 本物の Jev の port が閉じていれば 502(upstream-error)で答え、覚えない。
  (val seen [])
  (val fake (fake-jev-server seen))
  (try
    (<- runner (production-world (.format "http://127.0.0.1:{}/v1/systemone" (get fake.server-address 1))))
    (<- one (request-of "POST" "/v1/systemone" (! (question "(defk one [] 1)" "jev-latest")) OPERATOR-TOKEN None))
    (<- two (request-of "POST" "/v1/systemone" (! (question "(defk two [] 2)" "jev-latest")) OPERATOR-TOKEN None))
    (<- first (header (runner one) "x-jev-proxy"))
    (<- second (header (runner two) "x-jev-proxy"))
    (<- again (header (runner one) "x-jev-proxy"))
    (assert (= [first second again] ["miss" "miss" "hit"]))
    (assert (= seen [(+ "Bearer " SECRET) (+ "Bearer " SECRET)]) "本物の Jev には代理のキーだけが届く(呼び手の token ではない)")
    (finally
      (.shutdown fake)
      (.server-close fake)))
  (val probe (socket.socket))
  (.bind probe #("127.0.0.1" 0))
  (val closed-port (get (.getsockname probe) 1))
  (.close probe)
  (<- dead (production-world (.format "http://127.0.0.1:{}/v1/systemone" closed-port)))
  (val reply (dead one))
  (assert (= reply.status 502) reply)
  (<- outcome (header reply "x-jev-proxy"))
  (assert (= outcome "upstream-error"))
  (assert (not-in SECRET (.decode reply.body "utf-8")) "届かなかった理由の文にキーを載せない"))
