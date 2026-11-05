(require doeff-hy.macros [deftest defhandler defk <- val with-handler])

;;; http-host-gate(doeff_core_effects/_http_host_gate.hy):
;;;   * a request to a host in the allowed set is not answered and flows straight to the outer answerer (the port is not checked)
;;;   * a request to a host not in the allowed set never leaves: a request that takes failures as values gets HttpFailed(CONNECT-FAILED), any other gets HostNotAllowed
;;;   * the host is matched exactly against the URL's hostname (an outside name that merely has a local name prefixed does not pass)
;;;   * if the allowed set is empty, no request leaves
;;; The outer counterpart answers a received request with 200 (body = the received url). A request that left receives the counterpart's HttpResponse, and a blocked request receives HttpFailed or
;;; HostNotAllowed -- only the counterpart builds an HttpResponse, so the type of the answer names directly whether it "left".

(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [HostNotAllowed http-host-gate])

;; Allowed set of the local configuration (inside this machine only).
(val LOOPBACK (frozenset ["127.0.0.1" "localhost" "::1"]))


(defhandler answering-peer
  "Outer counterpart: answers a received request with 200 (body = the received url) (only a request that left receives this answer)."
  {:tags {:context "http" :role "foundation"}}
  (HttpRequest [url]
    (resume (HttpResponse 200 {} (.encode url "utf-8") url url 0.0))))


(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "To issue the same request once, changing only the failures-as-values flag (no retries)."
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "GET" url :max-retries 0 :failures-as-values values))
  answer)


(defk gated [hosts url values]
  {:pre [(: hosts frozenset) (: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "foundation"}}
  "To issue a request once with http-host-gate (allowed set hosts) wrapped inside the counterpart."
  (<- answer (| HttpResponse HttpFailed) (with-handler [answering-peer (http-host-gate hosts)] (ask url values)))
  answer)


(deftest test-a-request-to-an-allowed-host-flows-to-the-outer-answerer
  (for [url ["http://127.0.0.1:8320/worker/heartbeat" "http://localhost/x" "http://[::1]:9/y"]]
    (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK url True))
    (assert (isinstance answer HttpResponse) #(url answer))
    (assert (= answer.text url) #(url answer.text))))


(deftest test-a-request-to-an-outside-host-is-answered-as-not-connected-and-never-leaves
  (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK "https://api.anthropic.com/api/oauth/usage" True))
  (assert (isinstance answer HttpFailed) answer)
  (assert (= answer.kind HttpFailureKind.CONNECT-FAILED) answer))


(deftest test-a-request-to-an-outside-host-without-failures-as-values-raises-the-named-error
  (try
    (<- (gated LOOPBACK "https://api.github.com/app/installations" False))
    (assert False "A request outside the allowed set was not stopped")
    (except [e HostNotAllowed]
      (assert (= e.url "https://api.github.com/app/installations") e.url))))


(deftest test-a-host-is-matched-exactly-not-by-prefix
  (for [url ["http://127.0.0.1.attacker.test/x" "http://localhost.example/x"]]
    (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK url True))
    (assert (isinstance answer HttpFailed) #(url answer))))


(deftest test-an-empty-allowed-set-lets-no-request-out
  (for [url ["http://127.0.0.1:8320/worker/heartbeat" "https://api.anthropic.com/api/oauth/usage"]]
    (<- answer (| HttpResponse HttpFailed) (gated (frozenset) url True))
    (assert (isinstance answer HttpFailed) #(url answer))))
