;;; Repro for #387 — defhandler yield leak inside defn factory.
;;;
;;; Simulates the handlers.hy pattern:
;;;   1. require both <- and defhandler
;;;   2. Multiple _doeff-do handlers defined before
;;;   3. defn factory with defhandler + nested <- inside (when ...)

(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler])

(import doeff [Pass Resume WithHandler do :as _doeff-do])
(import doeff [EffectBase])
(import dataclasses [dataclass])


;; --- Effects ---

(defclass [(dataclass :frozen True)] Add [EffectBase]
  #^ int x
  #^ int y)

(defclass [(dataclass :frozen True)] Store [EffectBase]
  #^ object value)

(defclass [(dataclass :frozen True)] Init [EffectBase]
  #^ str label)


;; --- Pre-existing _doeff-do handlers (before defhandler) ---

(defn pre-handler-1 []
  "Plain _doeff-do handler — passthrough."
  (_doeff-do
    (fn [effect k]
      (yield (Pass effect k)))))

(defn pre-handler-2 [config]
  "Another _doeff-do handler with closures + yield."
  (setv count 0)
  (_doeff-do
    (fn [effect k]
      (nonlocal count)
      (if (isinstance effect Store)
          (do
            (+= count 1)
            (yield (Resume k None)))
          (yield (Pass effect k))))))

(defn pre-handler-3 [base-url]
  "Factory returning _doeff-do handler — similar to kabu-board-handler."
  (setv client None)
  (_doeff-do
    (fn [effect k]
      (nonlocal client)
      (if (isinstance effect Init)
          (do
            (when (is client None)
              (setv client "connected"))
            (yield (Resume k client)))
          (yield (Pass effect k))))))


;; --- The failing pattern: defhandler inside defn with nested <- ---

(defn make-handler [base-url]
  "Factory using defhandler (no params) — #387 repro."
  (setv _state {"client" None})

  (defhandler _handler
    (Add [x y]
      (when (is (get _state "client") None)
        (<- _init (Init :label "add-setup"))
        (setv (get _state "client") "ready"))
      (resume (+ x y)))
    (Store [value]
      (when (is (get _state "client") None)
        (<- _init (Init :label "store-setup"))
        (setv (get _state "client") "ready"))
      (resume None)))

  _handler)


;; --- Additional pattern: many clauses with nested <- (closer to real code) ---

(defn make-handler-many-clauses [base-url]
  "6-clause defhandler inside defn — close to kabu-live-handler."
  (setv _state {"client" None})

  (defhandler _handler
    (Add [x y]
      (when (is (get _state "client") None)
        (<- _s (Init :label "c1"))
        (setv (get _state "client") "ok"))
      (<- delegated (Add :x x :y y))
      (resume delegated))
    (Store [value]
      (when (is (get _state "client") None)
        (<- _s (Init :label "c2"))
        (setv (get _state "client") "ok"))
      (resume None))
    (Init [label]
      (when (is (get _state "client") None)
        (<- _s (Init :label "c3"))
        (setv (get _state "client") "ok"))
      (resume label)))

  _handler)
