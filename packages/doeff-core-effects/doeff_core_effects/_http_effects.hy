(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord defenum])
(import collections.abc [Mapping])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])


(val _HTTP-METHODS (frozenset ["GET" "POST" "PUT" "PATCH" "DELETE" "HEAD" "OPTIONS"]))


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
   is given up quickly while a slow answer still has timeout-seconds (#2337).

   trace-phases = True asks the handler to time the exchange's phases (HttpPhases on the response's phases — whether a new
   connection was made, and the seconds to connect, send, wait for the answer's head and read its body). Off by default: a
   caller that does not read them pays nothing (agora card ki-ca4b41612e2e).

   params and body are handed to the outside HTTP as they are (a query mapping; bytes / str sent as they are; a mapping sent as JSON),
   so their values are any value the outside accepts: the boundary type is `Mapping[str, object]` — covariant in the value, so
   a caller's `dict[str, str]` or `dict[str, Any]` is accepted (`dict[str, object]` is invariant and was not; `Any` is a type escape).
   A mapping that is not a dict is kept as a dict (the handlers and the fixture key read a dict; a dict is kept as it is) —
   agora-redesign #2939."

  (defn #^ None __init__ [self #^ str method #^ str url *
                          #^ (| (get dict #(str str)) None) [headers None]
                          #^ (| (get Mapping #(str object)) None) [params None]
                          #^ (| bytes str (get Mapping #(str object)) None) [body None]
                          #^ float [timeout-seconds 30.0] #^ int [max-retries 3] #^ bool [follow-redirects True]
                          #^ bool [failures-as-values False] #^ bool [log-each-request False]
                          #^ (| float None) [connect-timeout-seconds None] #^ bool [trace-phases False]]
    (.__init__ (super))
    (setv normalized-method (.upper method))
    (when (not-in normalized-method _HTTP-METHODS)
      (raise (ValueError (+ "Unsupported HTTP method: " (repr method)))))
    (when (< max-retries 0)
      (raise (ValueError (+ "max_retries must be non-negative: "
                            (repr max-retries)))))
    (setv #^ str self.method normalized-method
          self.url url
          self.headers headers
          #^ (| (get dict #(str object)) None) self.params
          (if (isinstance params Mapping) (if (isinstance params dict) params (dict params)) params)
          #^ (| bytes str (get dict #(str object)) None) self.body
          (if (isinstance body Mapping) (if (isinstance body dict) body (dict body)) body)
          self.timeout-seconds timeout-seconds
          self.max-retries max-retries
          self.follow-redirects follow-redirects
          self.failures-as-values failures-as-values
          self.log-each-request log-each-request
          self.connect-timeout-seconds connect-timeout-seconds
          self.trace-phases trace-phases))

  (defn #^ str __repr__ [self]
    (+ "HttpRequest(" self.method " " (repr self.url) ")")))


(defrecord HttpPhases
  "The phases of one exchange, timed when the request set trace-phases (agora card ki-ca4b41612e2e) — where the seconds of a
   request went on the caller's side. new-connection = a connection was made for this request (False = a kept connection was
   reused) · connect-seconds = making it (name lookup and TCP — 0 when reused) · acquire-seconds = from handing the request to
   the client to the first byte written, without connect-seconds (waiting for the pool and the client's own steps) ·
   send-seconds = writing the head and the body · wait-seconds = from the body written to the answer's head read (the server's
   queue and work, the network both ways, and the caller's event loop noticing the answer) · receive-seconds = reading the
   answer's body. Plain data -- not an effect."
  {:check [(all (gfor seconds #(connect-seconds acquire-seconds send-seconds wait-seconds receive-seconds) (>= seconds 0.0)))]}
  (#^ bool new-connection)
  (#^ float connect-seconds)
  (#^ float acquire-seconds)
  (#^ float send-seconds)
  (#^ float wait-seconds)
  (#^ float receive-seconds))


(defclass HttpResponse []
  "Result of HttpRequest. Plain data -- not an effect. phases = the exchange's timed phases when the request set trace-phases
   (None otherwise)."
  (#^ int status)
  (#^ (get dict #(str str)) headers)
  (#^ bytes content)
  (#^ str text)
  (#^ str url)
  (#^ float elapsed-seconds)
  (#^ (| HttpPhases None) phases)

  (defn #^ None __init__ [self #^ int status #^ (get dict #(str str)) headers #^ bytes content #^ str text #^ str url
                          #^ float elapsed-seconds * #^ (| HttpPhases None) [phases None]]
    (setv self.status status
          self.headers headers
          self.content content
          self.text text
          self.url url
          self.elapsed-seconds elapsed-seconds
          self.phases phases))

  (defn #^ None raise-for-status [self]
    (when (>= self.status 400)
      (raise (HttpError self.status self.url (cut self.text 0 500))))))


(defclass HttpError [Exception]
  "Raised by HttpResponse.raise_for_status for HTTP error statuses."
  (#^ int status)
  (#^ str url)
  (#^ str body-snippet)

  (defn #^ None __init__ [self #^ int status #^ str url #^ str body-snippet]
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
(setv HttpFailureKind.__doc__ "Why an HTTP request got no response (HttpFailed.kind): TIMED-OUT = a time limit ran out after
   the connection was made · CONNECT-FAILED = no connection was made, so the request never reached the server · OTHER = the rest.")


(defrecord HttpFailed
  "No response ever arrived (connection refused, timeout, TLS failure …) — answered instead of raising when the request
   set failures-as-values. Plain data -- not an effect. url = the request's URL; kind = why, as HttpFailureKind (branch on
   this); detail = the transport error's class and text, for people and logs (do not branch on it). kind has no default:
   a maker that forgot it must fail, not read as \"not a timeout\"."
  (#^ str url)
  (#^ str detail)
  (#^ HttpFailureKind kind))
