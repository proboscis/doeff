(require doeff-hy.macros [deftest defk <- with-handler])

;;; Tests of http-production-handler, the answerer of ResolveHost (name lookup only -- no connection):
;;;   * A name that resolves is answered with HostResolved, with the resolver's addresses in the same order, without duplicates
;;;   * A name that does not resolve is answered as a value (not an exception): a resolver error (socket.gaierror) is NOT-RESOLVED; no answer within the limit seconds is TIMED-OUT
;;;   * Counterexample: a resolver error whose text contains "timed out" is not read as a timeout (kind is decided from the exception class only)
;;;   * The default resolver is getaddrinfo of the running loop (same resolver as the connection of the HTTP client) -- it can resolve localhost
;;;   * A request without a host and a request without a limit in seconds are refused at construction time
;;; The resolver is a per-test stand-in (an awaitable that answers a list of getaddrinfo rows for host), and never leaves for the real name lookup (except the one for localhost).

(import asyncio)
(import socket)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HostNotResolved HostResolved ResolveFailureKind ResolveHost])
(import doeff_core_effects [http-production-handler])


(defn :async rows-of-two [host]
  "A resolver stand-in that answers getaddrinfo rows containing the same address twice (two IPv4 and one IPv6)."
  [#(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.7" 0))
   #(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.8" 0))
   #(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.7" 0))
   #(socket.AF-INET6 socket.SOCK-STREAM 6 "" #("2001:db8::7" 0 0 0))])


(defn :async no-such-name [host]
  "A resolver stand-in that answers that it does not know the name (same error class as glibc EAI_NONAME)."
  (raise (socket.gaierror socket.EAI-NONAME "Name or service not known")))


(defn :async failure-saying-timed-out [host]
  "A stand-in for a resolver error whose text contains timed out (not the timeout class -- a counterexample that gets mixed up if one reads the text)."
  (raise (socket.gaierror socket.EAI-AGAIN "lookup timed out at the server")))


(defn :async never-answers [host]
  "A resolver stand-in that returns no answer (waits on a future nobody fulfills -- until cut off by the limit in seconds)."
  (await (.create-future (asyncio.get-running-loop)))
  [])


(defk lookup-with [resolver host timeout-seconds]
  {:pre [(: resolver "stand-in resolver that resolves host (an awaitable from host to a list of getaddrinfo rows)") (: host str) (: timeout-seconds float)] :post [(: % (| HostResolved HostNotResolved))] :tags {:context "http" :role "foundation"}}
  "To resolve host once under the production answerer given the resolver stand-in, and read the answer."
  (<- answer (| HostResolved HostNotResolved)
      (with-handler [await-handler (http-production-handler :resolver resolver)]
        (ResolveHost :host host :timeout-seconds timeout-seconds)))
  answer)


(deftest test-a-resolved-name-answers-its-addresses-in-order-without-repeats
  (<- found (| HostResolved HostNotResolved) (lookup-with rows-of-two "notes.test" 5.0))
  (assert (= found (HostResolved :addresses #("192.0.2.7" "192.0.2.8" "2001:db8::7"))) found))


(deftest test-a-name-the-resolver-refuses-is-not-resolved
  (<- refused (| HostResolved HostNotResolved) (lookup-with no-such-name "notes.test" 5.0))
  (assert (= refused.kind ResolveFailureKind.NOT-RESOLVED) refused)
  (assert (.startswith refused.detail "gaierror: ") refused.detail))


(deftest test-the-kind-comes-from-the-error-class-not-its-text
  ;; Counterexample: even if the resolver error text says timed out, the class is the resolver error (gaierror), so NOT-RESOLVED.
  (<- said (| HostResolved HostNotResolved) (lookup-with failure-saying-timed-out "notes.test" 5.0))
  (assert (= said.kind ResolveFailureKind.NOT-RESOLVED) said))


(deftest test-a-lookup-past-its-limit-is-timed-out
  (<- late (| HostResolved HostNotResolved) (lookup-with never-answers "notes.test" 0.05))
  (assert (= late.kind ResolveFailureKind.TIMED-OUT) late)
  (assert (in "notes.test" late.detail) late.detail))


(deftest test-the-default-resolver-is-the-loops-getaddrinfo
  ;; The default resolver (an answerer given no argument) is getaddrinfo of the running loop -- it can resolve localhost in /etc/hosts.
  (<- local (| HostResolved HostNotResolved)
      (with-handler [await-handler (http-production-handler)]
        (ResolveHost :host "localhost" :timeout-seconds 5.0)))
  (assert (isinstance local HostResolved) local)
  (assert (& (set local.addresses) #{"127.0.0.1" "::1"}) local))


(deftest test-a-lookup-without-a-host-or-a-limit-is-refused-when-made
  (for [#(host seconds) [#("" 5.0) #("  " 5.0) #("notes.test" 0.0) #("notes.test" -1.0)]]
    (try
      (ResolveHost :host host :timeout-seconds seconds)
      (assert False #(host seconds))
      (except [ValueError]))))
