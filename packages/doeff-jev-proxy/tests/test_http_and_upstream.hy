;; 本物の Jev への翻訳(jev-upstream-handler)と HTTP の口の反例: キーは本物の Jev への見出しにだけ載り、届かなかった理由の文にも
;; 答えにも出ない・再試行は代理でしない(呼び手が撃ち直す)・実の socket の上で 2 回目が当たり、計器に出る。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import json)
(import urllib.request [Request urlopen])
(import urllib.error [HTTPError])
(import doeff [run with_handlers])
(import doeff_core_effects [await_handler try_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff_jev.target [JevTarget])
(import doeff_jev_proxy.values [UpstreamReply UpstreamUnreachable])
(import doeff_jev_proxy.effects [AskJev])
(import doeff_jev_proxy.handlers [jev-upstream-handler])
(import doeff_jev_proxy.http_server [ProxyServerConfig start-proxy-server stop-server])
(import tests.world [OPERATOR-TOKEN open-world jev-answers question])

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
  (<- failed (ask-through (HttpFailed :url TARGET.base-url :detail "ConnectTimeout: timed out") []))
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
