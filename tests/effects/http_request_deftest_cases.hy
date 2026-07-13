(require doeff-hy.macros [deftest <- do!])
(require doeff-hy.handle [defhandler])

(import pathlib [Path])
(import pytest)
(import doeff_core_effects [HttpError HttpRequest HttpResponse Listen SlogEffect])
(import doeff_core_effects.handlers [await-handler listen-handler slog-handler state])
(import doeff_core_effects.http_handlers [http-production-handler http-fixture-handler])
(import doeff_hy.http [http-get http-post http-put http-delete http-head])
(import tests.effects.http_request_support
  [FakeAsyncClient
   handler-name
   is-doeff-handler
   make-response
   noop-sleep
   record-sleep
   timeout-error])


(defhandler capture-http-request [captured response]
  "Capture HttpRequest values emitted by doeff-hy HTTP wrappers."
  (HttpRequest []
    (.append captured effect)
    (resume response)))


(deftest test-http-request-effect-shape-and-raise-for-status
  (setv request (HttpRequest "get" "https://example.test/data"))
  (assert (= request.method "GET"))
  (assert (= (repr request) "HttpRequest(GET 'https://example.test/data')"))

  (setv response (HttpResponse :status 404
                               :headers {"Content-Type" "text/plain"}
                               :content b"not found"
                               :text "not found"
                               :url "https://example.test/data"
                               :elapsed-seconds 0.5))
  (with [(pytest.raises HttpError
                        :match "HTTP 404 https://example\\.test/data: not found")]
    (.raise-for-status response)))


(deftest test-http-handlers-are-defhandler-functions [tmp-path]
  (setv production-handler (http-production-handler
                             :client-factory (fn [] (FakeAsyncClient []))
                             :sleep noop-sleep))
  (setv record-handler (http-fixture-handler (/ tmp-path "http-fixture.pickle")
                                             :mode "record"
                                             :client-factory (fn [] (FakeAsyncClient []))
                                             :sleep noop-sleep))
  (setv replay-handler (http-fixture-handler (/ tmp-path "http-fixture.pickle")
                                             :mode "replay"))

  (assert (is-doeff-handler production-handler))
  (assert (= (handler-name production-handler) "_http-production-handler"))
  (assert (is-doeff-handler record-handler))
  (assert (= (handler-name record-handler) "_http-fixture-record-handler"))
  (assert (is-doeff-handler replay-handler))
  (assert (= (handler-name replay-handler) "_http-fixture-replay-handler")))


(deftest test-http-production-handler-get-slog-and-close-client
  (setv client (FakeAsyncClient
                 [(make-response 200 {"X-Test" "yes"} b"ok" "ok"
                                 "https://example.test/final" 0.2)]))
  ;; ADR-DOE-CORE-EFFECTS-001 R3: slog の収集は Listen(types=(SlogEffect,)) の値フロー。
  (<- pair
      (slog-handler
        (listen-handler
          ((await-handler)
            ((http-production-handler :client-factory (fn [] client)
                                      :sleep noop-sleep)
              (do!
                (<- inner-pair
                    (Listen (do!
                              (<- resp (HttpRequest "GET" "https://example.test/start"
                                         :params {"a" "1"}))
                              resp)
                            :types #(SlogEffect)))
                inner-pair))))))
  (setv #(response entries) pair)
  (assert (= (. response status) 200))
  (assert (= (. response headers) {"X-Test" "yes"}))
  (assert (= (. response content) b"ok"))
  (assert (= (. response text) "ok"))
  (assert (= (. response url) "https://example.test/final"))
  (assert (= client.calls
             [{"method" "GET"
               "url" "https://example.test/start"
               "headers" None
               "params" {"a" "1"}
               "content" None
               "timeout" 30.0
               "follow_redirects" True}]))
  (assert (= (len entries) 1))
  (setv entry (get entries 0))
  (assert (= entry.msg "http_request"))
  (assert (= entry.kwargs
             {"method" "GET"
              "url" "https://example.test/start"
              "status" 200
              "final_url" "https://example.test/final"
              "elapsed_seconds" 0.2
              "attempt" 1}))
  (assert (= client.close-calls 1)))


(deftest test-http-production-handler-post-json-body
  (setv client (FakeAsyncClient
                 [(make-response 201 {"Content-Type" "application/json"} b"{}" "{}"
                                 "https://example.test/api" 0.1)]))
  (<- response
      ((state) (slog-handler ((await-handler)
          ((http-production-handler :client-factory (fn [] client)
                                    :sleep noop-sleep)
            (do!
              (<- resp (HttpRequest "POST" "https://example.test/api"
                                     :headers {"X-Trace" "abc"}
                                     :body {"b" 2 "a" 1}))
              resp))))))
  (assert (= (. response status) 201))
  (assert (= (get (get client.calls 0) "headers")
             {"X-Trace" "abc" "Content-Type" "application/json"}))
  (assert (= (get (get client.calls 0) "content") b"{\"a\":1,\"b\":2}"))
  (assert (= client.close-calls 1)))


(deftest test-http-production-handler-redirect-flag-and-timeout
  (setv client (FakeAsyncClient
                 [(make-response 302 {"Location" "/next"} b"" ""
                                 "https://example.test/start" 0.1)]))
  (<- response
      ((state) (slog-handler ((await-handler)
          ((http-production-handler :client-factory (fn [] client)
                                    :sleep noop-sleep)
            (do!
              (<- resp (HttpRequest "HEAD" "https://example.test/start"
                                     :timeout-seconds 1.25
                                     :follow-redirects False))
              resp))))))
  (assert (= (. response status) 302))
  (assert (= (get (get client.calls 0) "timeout") 1.25))
  (assert (is (get (get client.calls 0) "follow_redirects") False))
  (assert (= client.close-calls 1)))


(deftest test-http-production-handler-retries-5xx-statuses
  (setv client (FakeAsyncClient
                 [(make-response 500 {} b"error" "error" "https://example.test/api" 0.1)
                  (make-response 502 {} b"bad" "bad" "https://example.test/api" 0.1)
                  (make-response 200 {} b"ok" "ok" "https://example.test/api" 0.1)]))
  (setv sleeps [])
  (<- response
      ((state) (slog-handler ((await-handler)
          ((http-production-handler :client-factory (fn [] client)
                                    :sleep (record-sleep sleeps))
            (do!
              (<- resp (HttpRequest "GET" "https://example.test/api"
                                     :max-retries 2))
              resp))))))
  (assert (= (. response status) 200))
  (assert (= (len client.calls) 3))
  (assert (= sleeps [0.25 0.5]))
  (assert (= client.close-calls 1)))


(deftest test-http-production-handler-retries-request-exceptions-with-timeout
  (setv client (FakeAsyncClient
                 [(timeout-error "slow response")
                  (make-response 200 {} b"ok" "ok" "https://example.test/api" 0.1)]))
  (setv sleeps [])
  (<- response
      ((state) (slog-handler ((await-handler)
          ((http-production-handler :client-factory (fn [] client)
                                    :sleep (record-sleep sleeps))
            (do!
              (<- resp (HttpRequest "GET" "https://example.test/api"
                                     :timeout-seconds 0.01
                                     :max-retries 1))
              resp))))))
  (assert (= (. response status) 200))
  (assert (= (len client.calls) 2))
  (assert (= (lfor call client.calls (get call "timeout")) [0.01 0.01]))
  (assert (= sleeps [0.25]))
  (assert (= client.close-calls 1)))


(deftest test-http-fixture-record-forwards-to-production-handler [tmp-path]
  (setv fixture-path (/ tmp-path "http-fixture.pickle"))
  (setv client (FakeAsyncClient
                 [(make-response 200 {"X-Fixture" "yes"} b"fixture" "fixture"
                                 "https://example.test/resource" 0.3)]))
  (<- recorded
      ((state) (slog-handler ((await-handler)
          ((http-fixture-handler fixture-path :mode "record"
                                 :client-factory (fn [] client)
                                 :sleep noop-sleep)
            (do!
              (<- resp (HttpRequest "GET" "https://example.test/resource"))
              resp))))))
  (<- replayed
      ((http-fixture-handler fixture-path :mode "replay")
        (do!
          (<- resp (HttpRequest "GET" "https://example.test/resource"))
          resp)))
  (assert (= (. recorded status) 200))
  (assert (= (. replayed status) 200))
  (assert (= (. replayed text) "fixture"))
  (assert (= (len client.calls) 1))
  (assert (= client.close-calls 1)))


(deftest test-http-fixture-replay-errors-on-unknown-request [tmp-path]
  (with [(pytest.raises KeyError :match "No recorded HTTP fixture")]
    (<- _
        ((http-fixture-handler (/ tmp-path "http-fixture.pickle") :mode "replay")
          (do!
            (<- resp (HttpRequest "GET" "https://example.test/missing"))
            resp)))))


(deftest test-http-wrapper-methods-build-requests
  (setv captured [])
  (setv response (HttpResponse :status 200
                               :headers {}
                               :content b"ok"
                               :text "ok"
                               :url "https://example.test/final"
                               :elapsed-seconds 0.0))
  (setv capture (capture-http-request captured response))
  (<- get-response (capture (http-get "https://example.test/data"
                                      :params {"a" "1"})))
  (<- post-response (capture (http-post "https://example.test/data"
                                        :body {"a" 1})))
  (<- put-response (capture (http-put "https://example.test/data"
                                      :body "payload")))
  (<- delete-response (capture (http-delete "https://example.test/data")))
  (<- head-response (capture (http-head "https://example.test/data")))
  (assert (= (. get-response status) 200))
  (assert (= (. post-response status) 200))
  (assert (= (. put-response status) 200))
  (assert (= (. delete-response status) 200))
  (assert (= (. head-response status) 200))
  (assert (= (len captured) 5))
  (assert (= (. (get captured 0) method) "GET"))
  (assert (= (. (get captured 0) params) {"a" "1"}))
  (assert (= (. (get captured 1) method) "POST"))
  (assert (= (. (get captured 1) body) {"a" 1}))
  (assert (= (. (get captured 2) method) "PUT"))
  (assert (= (. (get captured 3) method) "DELETE"))
  (assert (= (. (get captured 4) method) "HEAD")))


(deftest test-http-request-implementation-surface-is-hy
  (assert (= HttpRequest.__module__ "doeff_core_effects.http_effects"))
  (assert (= HttpResponse.__module__ "doeff_core_effects.http_effects"))
  (assert (= http-production-handler.__module__
             "doeff_core_effects._http_handlers_impl"))
  (assert (= http-fixture-handler.__module__
             "doeff_core_effects._http_handlers_impl")))
