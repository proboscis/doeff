(import httpx)


(defclass ImmediateAwaitable []
  (defn __init__ [self value [exception None]]
    (setv self.value value
          self.exception exception))

  (defn __await__ [self]
    (when False
      (yield None))
    (when (is-not self.exception None)
      (raise self.exception))
    (return self.value)))


(defclass FakeElapsed []
  (defn __init__ [self seconds]
    (setv self.seconds seconds))

  (defn total-seconds [self]
    self.seconds))


(defclass FakeResponse []
  (defn __init__ [self status-code headers content text url elapsed]
    (setv self.status-code status-code
          self.headers headers
          self.content content
          self.text text
          self.url url
          self.elapsed elapsed)))


(defclass FakeAsyncClient []
  (defn __init__ [self responses]
    (setv self.responses responses
          self.calls []
          self.close-calls 0))

  (defn request [self method url * [params None] [content None] [headers None]
                 [timeout None] [follow-redirects True]]
    (.append self.calls {"method" method
                         "url" url
                         "headers" headers
                         "params" params
                         "content" content
                         "timeout" timeout
                         "follow_redirects" follow-redirects})
    (setv response (.pop self.responses 0))
    (if (isinstance response httpx.RequestError)
        (ImmediateAwaitable None response)
        (ImmediateAwaitable response)))

  (defn aclose [self]
    (+= self.close-calls 1)
    (ImmediateAwaitable None)))


(defn noop-sleep [_]
  (ImmediateAwaitable None))


(defn record-sleep [sleeps]
  (defn sleep [delay]
    (.append sleeps delay)
    (ImmediateAwaitable None))
  sleep)


(defn make-response [status headers content text url elapsed-seconds]
  (FakeResponse :status-code status
                :headers (if (is headers None) {} headers)
                :content content
                :text text
                :url url
                :elapsed (FakeElapsed elapsed-seconds)))


(defn timeout-error [message]
  (httpx.TimeoutException message))


(defn is-doeff-handler [handler]
  (is handler._doeff_is_handler_fn True))


(defn handler-name [handler]
  handler.__doeff_name__)
