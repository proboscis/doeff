;;; Verify that defhandler is now available via doeff-hy.macros re-export.
;;; After the #387 fix, users only need ONE require line.

(require doeff-hy.macros [defk <- defhandler])

(import doeff [Pass Resume WithHandler do :as _doeff-do])
(import doeff [EffectBase])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Add [EffectBase]
  #^ int x
  #^ int y)

(defclass [(dataclass :frozen True)] Init [EffectBase]
  #^ str label)


(defn make-handler [base-url]
  "Factory using defhandler via macros re-export."
  (setv _state {"client" None})

  (defhandler _handler
    (Add [x y]
      (when (is (get _state "client") None)
        (<- _init (Init :label "setup"))
        (setv (get _state "client") "ready"))
      (resume (+ x y))))

  _handler)
