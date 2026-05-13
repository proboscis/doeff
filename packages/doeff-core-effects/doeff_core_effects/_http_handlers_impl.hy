(require doeff-hy.macros [<-])
(require doeff-hy.handle [defhandler])

(import doeff_core_effects.effects [HttpRequest])
(import doeff_core_effects.http_handlers
  [_fixture-key
   _perform-request-with-retries
   _record-fixture-response
   _replay-fixture-response])


(defhandler _http-production-handler [client sleep]
  "Handle HttpRequest through the async transport helper."
  (HttpRequest []
    (<- response (_perform-request-with-retries client effect sleep))
    (resume response)))


(defhandler _http-fixture-record-handler [path fixtures]
  "Record HttpRequest responses by delegating to the outer HTTP handler."
  (HttpRequest []
    (setv key (_fixture-key effect))
    (<- response effect)
    (_record-fixture-response path fixtures key response)
    (resume response)))


(defhandler _http-fixture-replay-handler [fixtures]
  "Replay HttpRequest responses from loaded fixture records."
  (HttpRequest []
    (setv key (_fixture-key effect))
    (resume (_replay-fixture-response fixtures key effect))))
