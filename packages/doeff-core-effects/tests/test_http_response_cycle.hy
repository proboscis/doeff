;;; After the HTTP handler answers, the httpx response disappears without waiting for the cyclic GC.
;;; An httpx response and its body stream point at each other (Response.stream = BoundAsyncStream(..., response=self) - _client.py in httpx 0.28.1), so
;;; as it is, the body bytes and str are not freed by reference counting and stay until a GC run. A free-threading CPython widens the GC interval according to the number of live objects
;;; (40 to 160 thousand objects apart in local measurement) and does not return the memory freed by GC to the machine, so a job that receives a 96 KB response every 60 seconds
;;; (node-boot-facts in agora-controllers) saw its resident size rise to 380-540 MiB over several days.
;;; The test uses no socket (httpx MockTransport) - with GC stopped it issues one request and, after the answer, checks that the weakref to the httpx response is gone.
(require doeff-hy.macros [deftest defk deff <- val with-handler])
(import collections.abc [Callable])
(import functools)
(import gc)
(import weakref)
(import httpx)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_core_effects [http-production-handler])

;; The body to answer with (96 KB, close to the size of the production Node list).
(val BODY (* b"x" (* 96 1024)))
(val URL "http://api.test/api/v1/nodes")


(deff answer-body [seen request]  ; defk にできない: a callback called by httpx's MockTransport (seen is bound with functools.partial)
  {:pre [(: seen dict) (: request httpx.Request)] :post [(: % httpx.Response)]}
  "The test's counterpart (the MockTransport answer) - answer every request with 200 and BODY, and record a weakref to the first response in seen (httpx returns the response object
   the transport returned after reattaching the body stream, so the recorded one is the response the client returns itself)."
  (setv response (httpx.Response 200 :stream (httpx.ByteStream BODY)))
  (.setdefault seen "first" (weakref.ref response))
  response)


(defk watching-factory [seen]
  {:pre [(: seen dict)] :post [(: % Callable)] :tags {:context "http" :role "foundation"}}
  "The test's client builder - build a client that uses no socket and records weakrefs to the httpx responses it receives in seen."
  (fn [] (httpx.AsyncClient :transport (httpx.MockTransport (functools.partial answer-body seen)))))


;; Number of requests issued in one run - whether the first response is gone is checked inside the run after later requests were issued (after the run ends, the scheduler's own state
;; waits for GC). The Await handler does not keep awaited values in the promise table (_AwaitedValue in doeff_core_effects._handlers), so there is no need to wait for the table's sweep
;; (every HANDLE_SWEEP_INTERVAL in scheduler.py).
(val REQUESTS 3)


(defk fetch-many [n]
  {:pre [(: n int) (> n 0)] :post [(: % HttpResponse)] :tags {:context "http" :role "program"}}
  "Issue n requests and return the last answer."
  (var answer None)
  (for [_ (range n)]
    (<- got HttpResponse (HttpRequest "GET" URL :max-retries 0))
    (:= answer got))
  answer)


(deftest test-the-httpx-response-is-freed-without-the-cyclic-gc
  ;; Failure case: before the fix, the httpx response stayed through a cycle with the body stream, and while GC was stopped the weakref to the first response did not go away even after later requests were issued
  ;; (the 96 KB body stayed too - the shape in which the resident size of a job receiving large responses every round kept rising). After the fix, the cycle is broken at the moment the handler answers,
  ;; and it disappears by reference counting. It also goes red if reverted to keeping awaited responses in the promise table. The answer body is not lost.
  (val seen {})
  (<- factory Callable (watching-factory seen))
  (gc.collect)
  (gc.disable)
  (try
    (<- answer HttpResponse (with-handler [await-handler (http-production-handler :client-factory factory)]
                              (fetch-many REQUESTS)))
    (assert (= #(answer.status (len answer.content)) #(200 (len BODY))) answer.status)
    (assert (in "first" seen) seen)
    (assert (is ((get seen "first")) None) "The first httpx response remains (a cycle with the body stream, or the Await promise table)")
    (finally
      (gc.enable))))
