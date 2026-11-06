;;; Interpreters (composition root) for the HttpRequest contract tests -- run the same contract Program, swapping only the HttpRequest handler.
;;;
;;;   http-production      Real: http-production-handler. The client is an httpx MockTransport, and the contract world (WORLD) answers without going outside
;;;   http-fixture-replay  Fake: replay of http-fixture-handler. First run the same Program once in record mode and record the same world's answers
;;;                        into the fixture, then run it once more under the replay handler with no transport (the answer returned is this one)
;;;
;;; The contract world (world-answer) is an httpx counterpart that returns an answer fixed per URL path. Only /flaky changes its answer by the count of attempts
;;; made in the world (to exercise 5xx retries) -- the world is built fresh for each single run of an interpreter.
;;; The wait between retries is 0 seconds (no-wait). Used by doeff_interpreter in conftest.py (names in :interpreters of deftest -> INTERPRETERS).
(require doeff-hy.macros [defk deff <- val])
(import asyncio)
(import collections [Counter])
(import datetime [timedelta])
(import functools [partial])
(import pathlib [Path])
(import tempfile)
(import httpx)
(import doeff [Program with_handlers])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [http-fixture-handler http-production-handler])

(val PRODUCTION "http-production")
(val FIXTURE-REPLAY "http-fixture-replay")
(val WORLD "https://world.test")
;; Number of attempts for which /flaky returns 5xx (200 from the 3rd).
(val FLAKY-FAILURES 2)
(val REFUSED "[Errno 111] Connection refused")
(val NO-TIME (timedelta 0))


(deff world-answer [attempts request]  ; defk にできない: a callback that httpx MockTransport calls synchronously per request
  {:pre [(: attempts Counter) (: request httpx.Request)] :post [(: % httpx.Response)]
   :tags {:context "http-test" :role "foundation"}}
  "The contract world that httpx MockTransport calls per request. attempts is the attempt count per path (only within one run of the world).
   /echo      200, body = method, request header x-mode, request body (header and body change the answer)
   /moved     302, location = /landing      /landing  200 \"landed\"
   /flaky     503 up to attempt FLAKY-FAILURES, then 200 \"recovered\"
   /missing   404 \"no such thing\"           /down     cannot connect (httpx.ConnectError)"
  (.update attempts [request.url.path])
  ;; Why elapsed is set to 0: a Response holding a body returned by MockTransport is neither re-read nor closed by the client, so httpx
  ;; does not attach elapsed (with a real transport it does). The real handler reads it, so attach it on the world side.
  (let [response (match request.url.path
    "/echo" (httpx.Response 200 :headers {"x-world" "echo"}
                            :content (.encode (.join " " [request.method (.get request.headers "x-mode" "-") (.decode request.content "utf-8")])
                                              "utf-8"))
    "/moved" (httpx.Response 302 :headers {"location" "/landing"})
    "/landing" (httpx.Response 200 :content b"landed")
    "/flaky" (if (<= (get attempts "/flaky") FLAKY-FAILURES)
                 (httpx.Response 503 :content b"not yet")
                 (httpx.Response 200 :content b"recovered"))
    "/missing" (httpx.Response 404 :content b"no such thing")
    "/down" (raise (httpx.ConnectError REFUSED :request request))
    other (raise (ValueError (+ "path not in the contract world: " other))))]
    (setattr response "elapsed" NO-TIME)
    response))


(deff world-client []  ; defk にできない: a callback called synchronously as the client-factory of http-production-handler
  {:pre [] :post [(: % httpx.AsyncClient)] :tags {:context "http-test" :role "foundation"}}
  "An httpx client connected to a fresh contract world (client-factory of http-production-handler -- the mouth to an outside library, hence deff)."
  (httpx.AsyncClient :transport (httpx.MockTransport (partial world-answer (Counter)))))


(deff no-wait [delay]  ; defk にできない: a callback called as the sleep of http-production-handler that returns an awaitable handed to Await
  {:pre [(: delay float)] :post [(: % "awaitable that finishes without waiting")] :tags {:context "http-test" :role "foundation"}}
  "The wait between retries (the sleep of http-production-handler -- a callback returning an awaitable handed to Await, hence deff)."
  (asyncio.sleep 0))


(defk under-production [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type depends on the Program)")]
   :tags {:context "http-test" :role "foundation"}}
  "Run program under the real http-production-handler (the client is a MockTransport connected to the contract world)."
  (<- answer (with_handlers [await-handler (http-production-handler :client-factory world-client :sleep no-wait)] program))
  answer)


(defk under-fixture-replay [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type depends on the Program)")]
   :tags {:context "http-test" :role "foundation"}}
  "Run program once in record mode to record the contract world's answers into the fixture, then run it once more under the replay handler with no transport.
   The replay handler reads the fixture when built, so build it after the record run."
  (with [directory (tempfile.TemporaryDirectory)]
    (val path (/ (Path directory) "contract.pickle"))
    (<- (with_handlers [await-handler (http-fixture-handler path :mode "record" :client-factory world-client :sleep no-wait)]
          program))
    (<- answer (with_handlers [(http-fixture-handler path :mode "replay")] program)))
  answer)


(val INTERPRETERS {PRODUCTION under-production
                   FIXTURE-REPLAY under-fixture-replay})
