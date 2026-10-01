(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_vm [EffectBase])


(setv _HTTP-METHODS (frozenset ["GET" "POST" "PUT" "PATCH" "DELETE" "HEAD" "OPTIONS"]))


(defclass HttpRequest [EffectBase]
  "HTTP request effect: dispatch a generic HTTP call.

   yield HttpRequest(method=\"GET\", url=\"https://...\") -> HttpResponse

   failures-as-values = True asks the handler to answer HttpFailed (instead of raising the transport's exception) when no
   response ever arrived — so a caller can read the failure as a value without importing the transport library.

   log-each-request = True asks the handler to slog one \"http_request\" line per attempt (method, url, status, elapsed).
   Off by default: a caller that polls (a node agent that PATCHes every minute) should not add a log line per request.
   Added for agora-redesign #823 (item 2).

   connect-timeout-seconds = a shorter limit for making the connection only (None = timeout-seconds covers every phase). A caller that
   can send the request elsewhere when no connection was made (CONNECT-FAILED) sets it short, so an address that drops the handshake
   is given up quickly while a slow answer still has timeout-seconds (#2337)."

  (defn __init__ [self method url * [headers None] [params None] [body None]
                  [timeout-seconds 30.0] [max-retries 3] [follow-redirects True] [failures-as-values False]
                  [log-each-request False] [connect-timeout-seconds None]]
    (.__init__ (super))
    (setv normalized-method (.upper method))
    (when (not-in normalized-method _HTTP-METHODS)
      (raise (ValueError (+ "Unsupported HTTP method: " (repr method)))))
    (when (< max-retries 0)
      (raise (ValueError (+ "max_retries must be non-negative: "
                            (repr max-retries)))))
    (setv self.method normalized-method
          self.url url
          self.headers headers
          self.params params
          self.body body
          self.timeout-seconds timeout-seconds
          self.max-retries max-retries
          self.follow-redirects follow-redirects
          self.failures-as-values failures-as-values
          self.log-each-request log-each-request
          self.connect-timeout-seconds connect-timeout-seconds))

  (defn __repr__ [self]
    (+ "HttpRequest(" self.method " " (repr self.url) ")")))


(defclass HttpResponse []
  "Result of HttpRequest. Plain data -- not an effect."

  (defn __init__ [self status headers content text url elapsed-seconds]
    (setv self.status status
          self.headers headers
          self.content content
          self.text text
          self.url url
          self.elapsed-seconds elapsed-seconds))

  (defn raise-for-status [self]
    (when (>= self.status 400)
      (raise (HttpError self.status self.url (cut self.text 0 500))))))


(defclass HttpError [Exception]
  "Raised by HttpResponse.raise_for_status for HTTP error statuses."

  (defn __init__ [self status url body-snippet]
    (.__init__ (super) (+ "HTTP " (str status) " " url ": " body-snippet))
    (setv self.status status
          self.url url
          self.body-snippet body-snippet)))


;; Why no response ever arrived, as a closed set (agora-redesign #850). The HTTP handler maps the transport error's class — never
;; its name or text — to one of these: TIMED-OUT = a time limit ran out after the connection was made, or while waiting for a
;; pooled connection (reading, writing — the request may have reached the server) · CONNECT-FAILED = no connection was made
;; (refused, DNS, TLS handshake, or the connect time limit ran out — the request never reached the server, so a caller may resend
;; it elsewhere; agora-redesign #2337) · OTHER = the rest (the connection dropped
;; mid-exchange, a protocol error …).
(defenum HttpFailureKind TIMED-OUT CONNECT-FAILED OTHER)


(defrecord HttpFailed
  "No response ever arrived (connection refused, timeout, TLS failure …) — answered instead of raising when the request
   set failures-as-values. Plain data -- not an effect. url = the request's URL; kind = why, as HttpFailureKind (branch on
   this); detail = the transport error's class and text, for people and logs (do not branch on it). kind has no default:
   a maker that forgot it must fail, not read as \"not a timeout\"."
  (#^ str url)
  (#^ str detail)
  (#^ HttpFailureKind kind))
