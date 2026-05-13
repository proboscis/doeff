(import doeff_vm [EffectBase])


(setv _HTTP-METHODS (frozenset ["GET" "POST" "PUT" "PATCH" "DELETE" "HEAD" "OPTIONS"]))


(defclass HttpRequest [EffectBase]
  "HTTP request effect: dispatch a generic HTTP call.

   yield HttpRequest(method=\"GET\", url=\"https://...\") -> HttpResponse"

  (defn __init__ [self method url * [headers None] [params None] [body None]
                  [timeout-seconds 30.0] [max-retries 3] [follow-redirects True]]
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
          self.follow-redirects follow-redirects))

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
