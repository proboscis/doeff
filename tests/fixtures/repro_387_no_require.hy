;;; Repro for #387 — defhandler WITHOUT require (only <- required).
;;;
;;; This simulates the actual handlers.hy bug:
;;;   (require doeff-hy.macros [defk <-])  ← has <- macro
;;;   ;; MISSING: (require doeff-hy.handle [defhandler])
;;;
;;; Without the defhandler require, (defhandler ...) is compiled as a
;;; function call. The <- macro still expands yield, but the yield
;;; ends up in the outer defn scope instead of the inner (fn [effect k]).

(require doeff-hy.macros [defk <-])
;; NOTE: intentionally NOT requiring defhandler

(import doeff [Pass Resume WithHandler do :as _doeff-do])
(import doeff [EffectBase])
(import dataclasses [dataclass])


;; --- Effects ---

(defclass [(dataclass :frozen True)] Add [EffectBase]
  #^ int x
  #^ int y)

(defclass [(dataclass :frozen True)] Init [EffectBase]
  #^ str label)


;; --- The bug pattern: defhandler used without require ---

(defn make-handler-missing-require [base-url]
  "Factory using defhandler WITHOUT require — yield leaks to outer defn."
  (setv _state {"client" None})

  (defhandler _handler
    (Add [x y]
      (when (is (get _state "client") None)
        (<- _init (Init :label "setup"))
        (setv (get _state "client") "ready"))
      (resume (+ x y))))

  _handler)
