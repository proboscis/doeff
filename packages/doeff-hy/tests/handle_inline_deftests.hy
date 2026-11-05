;;; Check that handle (applying a one-off handler to a program in place) is equivalent to a program with a named defhandler applied via with-handler.
;;;
;;; What it checks: a program with the same clause written via handle and one written via defhandler + with-handler return the same answer (resume, transfer,
;;; finish, the :when guard and reperform outward, a clause that asks outward with <-); writings that cannot use resume (resume in a Raise clause, a branch missing resume) give
;;; the same SyntaxError as defhandler; a session val directly under handle is refused with a SyntaxError that points to defhandler.
;;; Exposed by test_handle_inline.py (exposed as is without re-wrapping -- ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defeffect defhandler handle with-handler defk <- val])
(import hy)
(import pytest)
(import types)
(import doeff [Pure])

(defeffect Greeting
  "Ask for the greeting in language lang."
  {:fields [(: lang str)]
   :answer str
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Count
  "Ask for the recounted value of number n (exercises a guarded clause and reperform outward)."
  {:fields [(: n int)]
   :answer int
   :tags {:context "doeff-hy" :role "intent"}})

(defk greet-twice [lang]
  {:pre [(: lang str)] :post [(: % str)] :tags {:context "doeff-hy" :role "judgment"}}
  "Ask for the greetings in lang and in English, join them, and return."
  (<- first (Greeting lang))
  (<- second (Greeting "en"))
  (+ first " / " second))

(defk count-both [small large]
  {:pre [(: small int) (: large int)] :post [(: % int)] :tags {:context "doeff-hy" :role "judgment"}}
  "Recount a small number and a large number, and return the sum."
  (<- a (Count small))
  (<- b (Count large))
  (+ a b))

(defhandler greeting-by-name
  (Greeting [lang] (resume (+ "hello " lang))))

(defhandler greeting-transfer
  (Greeting [lang] (transfer (+ "hi " lang))))

(defhandler greeting-finish
  (Greeting [lang] :finish-reason "decide the answer at the first question" (finish (+ "stopped at " lang))))

(defhandler small-count
  (Count [n] :when (< n 10) (resume (* n 2))))

(defhandler outer-count
  (Count [n] (resume (+ n 1000))))

(defhandler count-via-outer
  (Count [n]
    (<- outer (Count (+ n 1)))
    (resume (- outer 1))))


(deftest test-handle-answers-like-defhandler-with-handler
  (<- by-handle (handle (greet-twice "ja") (Greeting [lang] (resume (+ "hello " lang)))))
  (<- by-name (with-handler [greeting-by-name] (greet-twice "ja")))
  (assert (= by-handle by-name "hello ja / hello en") #(by-handle by-name))
  (<- by-handle-transfer (handle (greet-twice "ja") (Greeting [lang] (transfer (+ "hi " lang)))))
  (<- by-name-transfer (with-handler [greeting-transfer] (greet-twice "ja")))
  (assert (= by-handle-transfer by-name-transfer) #(by-handle-transfer by-name-transfer))
  (<- by-handle-finish (handle (greet-twice "ja")
                         (Greeting [lang] :finish-reason "decide the answer at the first question" (finish (+ "stopped at " lang)))))
  (<- by-name-finish (with-handler [greeting-finish] (greet-twice "ja")))
  (assert (= by-handle-finish by-name-finish "stopped at ja") #(by-handle-finish by-name-finish)))


(deftest test-handle-guard-and-outer-delegation-answer-like-defhandler
  ;; An effect whose :when is false is passed to the outer handler (10 and above gets outer-count's +1000).
  (<- by-handle (with-handler [outer-count]
                  (handle (count-both 3 20) (Count [n] :when (< n 10) (resume (* n 2))))))
  (<- by-name (with-handler [outer-count small-count] (count-both 3 20)))
  (assert (= by-handle by-name (+ 6 1020)) #(by-handle by-name))
  ;; Ask the outer handler with <- and continue the clause with its answer.
  (<- by-handle-delegate (with-handler [outer-count]
                           (handle (count-both 1 2)
                             (Count [n]
                               (<- outer (Count (+ n 1)))
                               (resume (- outer 1))))))
  (<- by-name-delegate (with-handler [outer-count count-via-outer] (count-both 1 2)))
  (assert (= by-handle-delegate by-name-delegate (+ 1001 1002)) #(by-handle-delegate by-name-delegate)))


(defn expansion-error [form]
  "Expand form in a namespace that requires doeff-hy's macros, and return the message of the SyntaxError the macro raised (None if none)."
  (setv module (types.ModuleType "handle_inline_expansion"))
  (hy.eval '(require doeff-hy.macros [defhandler handle with-handler]) :module module)
  (hy.eval '(import doeff-core-effects [Raise]) :module module)
  (setv (. module __dict__ ["Greeting"]) Greeting)
  (try
    (hy.eval form :module module)
    None
    (except [error hy.errors.HyMacroExpansionError]
      ;; Hy wraps a macro error into a single message "expanding macro NAME\n  SyntaxError: message" (the original error is not kept in __cause__).
      (setv #(_head sep reason) (.partition (str error) "SyntaxError: "))
      (assert sep (str error))
      (.strip reason))))

(defn after-clause [message]
  "The reason part of a SyntaxError message, with the clause location prefix (`handle clause …:` / `defhandler NAME clause …:`) removed."
  (get (.split message ": " 1) 1))


(deftest test-handle-refuses-resume-like-defhandler
  (<- _ (Pure None))
  ;; A Raise clause cannot resume; the reason text is the same.
  (val by-handle (expansion-error '(handle (Pure 1) (Raise [reason] (resume 1)))))
  (val by-name (expansion-error '(defhandler resumes-raise (Raise [reason] (resume 1)))))
  (assert (is-not by-handle None))
  (assert (in "A Raise clause cannot resume" by-handle) by-handle)
  (assert (= (after-clause by-handle) (after-clause by-name)) #(by-handle by-name))
  ;; A clause with a branch missing resume -- the message is identical.
  (val missing-handle (expansion-error '(handle (Pure 1) (Greeting [lang] (if lang (resume 1) None)))))
  (val missing-name (expansion-error '(defhandler misses-resume (Greeting [lang] (if lang (resume 1) None)))))
  (assert (is-not missing-handle None))
  (assert (in "missing resume/transfer/finish/reperform" missing-handle) missing-handle)
  (assert (= missing-handle missing-name) #(missing-handle missing-name)))


(deftest test-handle-refuses-session-and-names-defhandler
  (<- _ (Pure None))
  (val message (expansion-error '(handle (Pure 1) (session val greeting "hello") (Greeting [lang] (resume greeting)))))
  (assert (is-not message None))
  (assert (in "session val / session var" message) message)
  (assert (in "defhandler" message) message)
  ;; Putting the same declaration directly under defhandler is accepted.
  (assert (is (expansion-error '(defhandler with-session (session val greeting "hello") (Greeting [lang] (resume greeting))))
              None)))
