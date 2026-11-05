;;; The form where the row of a failed Await promise keeps its own handle and is never swept (in the messaging process,
;;; for each request that ended in failure, httpcore AsyncHTTPConnection, Origin, ExternalPromise, and Await grew 1:1:1:1 at about 0.8 per second,
;;; reaching 9,971 in 3.5 hours and not shrinking after gc). The scheduler holds the exception of a failed promise in the promise table until the sweep (#502) reads
;;; the handle as all dead. The head of the traceback of the exception caught by the coroutine of the await-handler bridge is the bridge's own frame, and its closure holds the ExternalPromise and the Await
;;; (together with the awaited coroutine), so the row kept its own handle alive and the sweep did not remove it for a long time.
;;; The test uses no socket -- it hands a connection-refusing httpcore network backend to a real connection pool, and fails together with the AsyncHTTPConnection the pool creates.
(require doeff-hy.macros [deftest defk deff <- val with-handler])
;; Httpx has no public slot to install a connection-refusing network backend, so replace the transport's _pool (the answerer's _client-pools reads the same slot).
(import gc)
(import httpcore)
(import httpx)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpFailed HttpRequest])
(import doeff_core_effects [http-production-handler])

;; The number of failing requests issued in sequence. Before the fix one remained per request, so the remaining count equals this number.
(val REQUESTS 40)
;; The count that may remain after the fix -- the row of the last request may stay in the table until the next sweep (a gap in the wait).
(val LEFT-AT-MOST 2)


(defclass RefusingBackend [httpcore.AsyncNetworkBackend]
  "Test counterpart -- a network backend that refuses every connection (opens no socket)."
  (defn :async connect-tcp [self host port [timeout None] [local-address None] [socket-options None]]
    "To refuse the connection (raises the exception httpcore reads as a connection refusal)."
    (raise (httpcore.ConnectError "refused by the test backend"))))


(deff refusing-client []  ; cannot be a defk: it is a client maker the answerer calls as a plain function outside the Program at the start of the scope
  {:pre [] :post [(: % httpx.AsyncClient)]}
  "To build a real httpx client holding a connection pool with the refusing backend (for each request the pool creates an AsyncHTTPConnection and fails at the connect step)."
  (setv transport (httpx.AsyncHTTPTransport))
  (setv transport._pool (httpcore.AsyncConnectionPool :network-backend (RefusingBackend)))
  (httpx.AsyncClient :transport transport))


;; Names of the counted types (the two that remained, one per failed request, before the fix).
(val COUNTED #("AsyncHTTPConnection" "ExternalPromise"))


(defk live-counts []
  {:pre [] :post [(: % (get dict #(str int)))] :tags {:context "http" :role "program"}}
  "To read the number of live objects per COUNTED type after gc."
  (gc.collect)
  (val names (lfor o (gc.get-objects) (. (type o) __name__)))
  (dfor name COUNTED name (.count names name)))


(defk fail-many [n]
  {:pre [(: n int) (> n 0)] :post [(: % (get dict #(str int)))] :tags {:context "http" :role "program"}}
  "To issue n failing requests in sequence, and answer the number of live connections and promises after issuing."
  (for [_ (range n)]
    (<- answer HttpFailed (HttpRequest "GET" "http://refused.test/" :max-retries 0 :failures-as-values True))
    (assert (isinstance answer HttpFailed) answer))
  (<- counts (get dict #(str int)) (live-counts))
  counts)


(deftest test-failed-requests-leave-no-connection-or-promise-behind
  ;; Failure case: before the fix, for each failed request the bridge frame at the head of the traceback of the promise row's exception kept the ExternalPromise alive, the sweep did not remove the row,
  ;; and the httpcore AsyncHTTPConnection held by the exception traceback also remained -- after 40 requests both grew by 40.
  (<- before dict (live-counts))
  (<- after dict (with-handler [await-handler (http-production-handler :client-factory refusing-client)]
                   (fail-many REQUESTS)))
  (val grown (dfor #(name count) (.items after) name (- count (get before name))))
  (assert (all (gfor count (.values grown) (<= count LEFT-AT-MOST))) grown))
