;;; HttpRequest の契約テスト — 同じ効果 HttpRequest に答える本物(http-production-handler)と fake(http-fixture-handler の replay)
;;; が、同じ deftest を通る(agora-redesign #1159)。解釈器の組み立てと契約の世界は http_contract_handlers.hy。
;;;
;;;   * 同じ要求(method・url・headers・body)に同じ status・headers・本文・最後の url が返る。header や本文だけが違う要求は
;;;     別の答えになる
;;;   * 4xx は答え(再試行せず、失敗にもしない)
;;;   * 5xx は max-retries の回数まで再試行、最後の答えを返す(再試行の回数が違う要求は別の答えになる)
;;;   * redirect は既定で辿り、follow-redirects = False なら 3xx をそのまま返す
;;;   * 送れない時: failures-as-values なら HttpFailed(url・detail・kind)で答え、そうでなければ transport の例外が上がる
;;; 答えの経過の秒(elapsed-seconds)は時刻の値なので契約の外。本物の要求の形(client へ渡す欄・slog)は
;;; tests/effects/http_request_deftest_cases.hy、失敗の類の写し方は test_http_failures.hy。
(require doeff-hy.macros [defk deftest <-])
(import httpx)
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import http_contract_handlers [REFUSED WORLD])


(defk seen [response]
  {:pre [(: response HttpResponse)] :post [(: % tuple)] :tags {:context "http-test" :role "judgment"}}
  "契約で比べる答えの欄: status・世界が付けた header(x-world / location)・本文・最後の url。"
  #(response.status
    (.get response.headers "x-world")
    (.get response.headers "location")
    response.content
    response.text
    response.url))


(defk ask [method path #** options]
  {:pre [(: method str) (: path str) (: options dict)] :post [(: % tuple)] :tags {:context "http-test" :role "program"}}
  "契約の世界の path へ HttpRequest を 1 つ出し(options は HttpRequest の欄)、比べる欄にして返す。"
  (<- response HttpResponse (HttpRequest method (+ WORLD path) #** options))
  (<- answer tuple (seen response))
  answer)


(deftest test-the-same-request-gets-the-same-answer
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- plain tuple (ask "GET" "/echo" :headers {"x-mode" "a"}))
  (<- other-header tuple (ask "GET" "/echo" :headers {"x-mode" "b"}))
  (<- posted tuple (ask "POST" "/echo" :headers {"x-mode" "a"} :body "payload"))
  (<- other-body tuple (ask "POST" "/echo" :headers {"x-mode" "a"} :body "another"))
  (assert (= plain #(200 "echo" None b"GET a " "GET a " (+ WORLD "/echo"))) plain)
  (assert (= other-header #(200 "echo" None b"GET b " "GET b " (+ WORLD "/echo")))
          (.format "header だけが違う要求の答え {}" other-header))
  (assert (= posted #(200 "echo" None b"POST a payload" "POST a payload" (+ WORLD "/echo"))) posted)
  (assert (= other-body #(200 "echo" None b"POST a another" "POST a another" (+ WORLD "/echo")))
          (.format "本文だけが違う要求の答え {}" other-body)))


(deftest test-a-client-error-status-is-an-answer
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- missing tuple (ask "GET" "/missing" :max-retries 2))
  (assert (= missing #(404 None None b"no such thing" "no such thing" (+ WORLD "/missing"))) missing))


(deftest test-a-server-error-is-retried-up-to-max-retries
  {:interpreters ["http-production" "http-fixture-replay"]}
  ;; 世界の /flaky は 2 回目の試行まで 503。1 つ目の要求(再試行なし)で 1 回、2 つ目(2 回まで再試行する)で 2 回目の 503 と
  ;; 3 回目の 200。
  (<- once tuple (ask "GET" "/flaky" :max-retries 0))
  (<- retried tuple (ask "GET" "/flaky" :max-retries 2))
  (assert (= once #(503 None None b"not yet" "not yet" (+ WORLD "/flaky")))
          (.format "再試行しない要求の答え {}" once))
  (assert (= retried #(200 None None b"recovered" "recovered" (+ WORLD "/flaky")))
          (.format "2 回まで再試行する要求の答え {}" retried)))


(deftest test-redirects-are-followed-unless-asked-not-to
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- followed tuple (ask "GET" "/moved"))
  (<- kept tuple (ask "GET" "/moved" :follow-redirects False))
  (assert (= followed #(200 None None b"landed" "landed" (+ WORLD "/landing")))
          (.format "既定で redirect を辿った答え {}" followed))
  (assert (= kept #(302 None "/landing" b"" "" (+ WORLD "/moved")))
          (.format "follow-redirects = False の答え {}" kept)))


(deftest test-an-unreachable-server-is-answered-as-a-value-when-asked
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- failed (HttpRequest "GET" (+ WORLD "/down") :max-retries 1 :failures-as-values True))
  (assert (= failed (HttpFailed :url (+ WORLD "/down") :detail (+ "ConnectError: " REFUSED)
                                :kind HttpFailureKind.CONNECT-FAILED))
          failed))


(deftest test-an-unreachable-server-raises-the-transport-error-otherwise
  {:interpreters ["http-production" "http-fixture-replay"]}
  (var raised None)
  (try
    (<- (HttpRequest "GET" (+ WORLD "/down") :max-retries 1))
    (except [error httpx.ConnectError]
      (:= raised (str error))))
  (assert (= raised REFUSED) (.format "送れない要求で上がった transport の例外の文 {!r}" raised)))
