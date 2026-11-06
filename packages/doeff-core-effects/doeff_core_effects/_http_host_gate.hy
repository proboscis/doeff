;;; An answerer that narrows outgoing HTTP requests (HttpRequest) to a set of allowed destination hosts (so a local setup does not hit upstream with real credentials.
;;; Decision of cisco-c8 2026-10-05 05:53: the HTTP of jobs in the local custody answers only to 127.0.0.1).
;;;
;;;   http-host-gate [hosts]  Stops a request whose destination host (the hostname of the URL -- the port is not looked at) is not in hosts. If the request is in the form that receives
;;;                           unreachable failures as values (failures-as-values), it answers HttpFailed (kind = CONNECT-FAILED -- could not connect); otherwise it fails with the named exception
;;;                           HostNotAllowed (same direction as a transport exception rising -- never silently returns a value). A request whose host is in hosts is not answered and
;;;                           flows unchanged to the outer answerer (the real HTTP) (:when).
;;;
;;; It makes no answer of any kind -- it only keeps stopped requests from going out. The destination decision is passed in by the caller as an argument (this module does not know any name).
(require doeff-hy.macros [defhandler val])
(import urllib.parse [urlsplit])
(import doeff_core_effects._http_effects [HttpFailed HttpFailureKind HttpRequest])

(val MODULE-TAGS {:context "http" :role "foundation"})


(defclass HostNotAllowed [ConnectionError]
  "A request whose destination host is not in the allowed set was stopped for a caller that does not receive failures as values (no connection was made) -- names the url and the allowed set."
  (defn #^ None __init__ [self #^ str url #^ (get frozenset str) hosts]  ; cannot be a defk: it is the entry Python calls when building the exception type (__init__)
    (.__init__ (super) (.format "host of destination {} is not in the allowed set {} -- the request was not sent" url (sorted hosts)))
    (setv self.url url
          self.hosts hosts)))


(defhandler http-host-gate [#^ (get frozenset str) hosts]
  "Stops an HttpRequest whose destination host is not in hosts (HttpFailed for a request that receives failures as values, HostNotAllowed otherwise). A request whose host is in hosts flows to the outside."
  {:tags {:context "http" :role "foundation"}}
  ;; Why it stays an argument: the allowed destinations differ per assembly of the outside world (local setup = only inside this machine), and are not an Ask setting.
  (HttpRequest [url failures-as-values]
    :when (not (in (. (urlsplit url) hostname) hosts))
    (if failures-as-values
        (resume (HttpFailed :url url :detail (.format "destination host is not in the allowed set {} -- the request was not sent" (sorted hosts))
                            :kind HttpFailureKind.CONNECT-FAILED))
        (raise (HostNotAllowed url hosts)))))
