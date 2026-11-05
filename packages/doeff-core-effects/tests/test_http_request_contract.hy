;;; Contract test for HttpRequest -- the real one (http-production-handler) and the fake (replay of http-fixture-handler) that answer the same effect HttpRequest
;;; pass through the same deftest. The interpreter assembly and the contract world are in http_contract_handlers.hy.
;;;
;;;   * the same request (method, url, headers, body) returns the same status, headers, body, and final url. A request that differs only in header or body
;;;     gets a different answer
;;;   * 4xx is an answer (not retried, and not turned into a failure)
;;;   * 5xx is retried up to max-retries times and the last answer is returned (requests with different retry counts get different answers)
;;;   * redirects are followed by default, and with follow-redirects = False a 3xx is returned as is
;;;   * when it cannot be sent: with failures-as-values it answers HttpFailed (url, detail, kind), otherwise a transport exception is raised
;;; The elapsed seconds of the answer (elapsed-seconds) are a time value, so they are outside the contract. The real request shape (fields passed to the client, slog) is in
;;; tests/effects/http_request_deftest_cases.hy, and how failure kinds are mapped is in test_http_failures.hy.
(require doeff-hy.macros [defk deftest <-])
(import httpx)
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import http_contract_handlers [REFUSED WORLD])


(defk seen [response]
  {:pre [(: response HttpResponse)] :post [(: % tuple)] :tags {:context "http-test" :role "judgment"}}
  "Answer fields compared by the contract: status, the header the world added (x-world / location), body, and final url."
  #(response.status
    (.get response.headers "x-world")
    (.get response.headers "location")
    response.content
    response.text
    response.url))


(defk ask [method path #** options]
  {:pre [(: method str) (: path str) (: options dict)] :post [(: % tuple)] :tags {:context "http-test" :role "program"}}
  "Issue one HttpRequest to a path of the contract world (options are the fields of HttpRequest), and return it as the fields to compare."
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
          (.format "Answer to the request differing only in header {}" other-header))
  (assert (= posted #(200 "echo" None b"POST a payload" "POST a payload" (+ WORLD "/echo"))) posted)
  (assert (= other-body #(200 "echo" None b"POST a another" "POST a another" (+ WORLD "/echo")))
          (.format "Answer to the request differing only in body {}" other-body)))


(deftest test-a-client-error-status-is-an-answer
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- missing tuple (ask "GET" "/missing" :max-retries 2))
  (assert (= missing #(404 None None b"no such thing" "no such thing" (+ WORLD "/missing"))) missing))


(deftest test-a-server-error-is-retried-up-to-max-retries
  {:interpreters ["http-production" "http-fixture-replay"]}
  ;; The world's /flaky gives 503 until the 2nd attempt. The 1st request (no retries) gets it once, and the 2nd (retries up to 2 times) gets the 2nd 503 and
  ;; the 3rd 200.
  (<- once tuple (ask "GET" "/flaky" :max-retries 0))
  (<- retried tuple (ask "GET" "/flaky" :max-retries 2))
  (assert (= once #(503 None None b"not yet" "not yet" (+ WORLD "/flaky")))
          (.format "Answer to the request without retries {}" once))
  (assert (= retried #(200 None None b"recovered" "recovered" (+ WORLD "/flaky")))
          (.format "Answer to the request retried up to 2 times {}" retried)))


(deftest test-redirects-are-followed-unless-asked-not-to
  {:interpreters ["http-production" "http-fixture-replay"]}
  (<- followed tuple (ask "GET" "/moved"))
  (<- kept tuple (ask "GET" "/moved" :follow-redirects False))
  (assert (= followed #(200 None None b"landed" "landed" (+ WORLD "/landing")))
          (.format "Answer when redirects are followed by default {}" followed))
  (assert (= kept #(302 None "/landing" b"" "" (+ WORLD "/moved")))
          (.format "Answer with follow-redirects = False {}" kept)))


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
  (assert (= raised REFUSED) (.format "Text of the transport exception raised for the unsendable request {!r}" raised)))
