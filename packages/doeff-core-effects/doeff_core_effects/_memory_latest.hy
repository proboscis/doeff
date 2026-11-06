;;; Memory handler for latest values, memory-latest-handler (ADR-DOE-CORE-EFFECTS-004): answers PublishLatest, ReadLatest and AwaitLatest with
;;; state within one run (a session value).
;;;
;;; Why: in mocks and tests we want to check the handoff of latest values under the same contract as the real one (process_latest.hy) without touching the one-per-process store or threads.
;;; Only the store differs (one per process -> within this run). It is not shared with other runs. An outer state handler is required.
;;;
;;; The store has the same shape as the real one: two mutable dicts (type -> latest value, type -> set of hung bells) are gathered into one value MemoryLatestStore,
;;; and that is the only session value. It is created once at the first answer in the run and placed in the session; later clauses take it out and mutate its contents (it is not re-placed).
;;; So each publish or read costs one session access (this counts toward the step count of tests with heavy publishers -- the older shape that
;;; kept the set of bells in a second session value cost 3 per publish; test_memory_latest.hy).
;;;
;;; Waiting until a change (AwaitLatest) has the same shape as the real one: the waiter hangs a bell (a CreateExternalPromise promise -- a task in the same run may
;;; complete it) in the set of bells and waits in the park form (Wait with TaskPriority.IDLE -- the virtual clock advances even while the promise is open). After PublishLatest
;;; replaces the value, it detaches and rings all the bells of that type. When the caller Cancels the waiting task, the promise's cancel callback (which cannot emit effects)
;;; detaches it from the set of bells. The ringing side completes the bell with the published value and the waiter answers with that value (when the same object is re-published it is still seen, so
;;; it hangs a bell and waits again).
(require doeff-hy.macros [defhandler deff var val <-])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import dataclasses [dataclass])  ; used by the defrecord expansion
(import functools [partial])
(import doeff_core_effects._latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait TaskPriority])


(defrecord MemoryLatestStore
  "The store within one run: values = type -> latest value, bells = type -> set of hung bells. Both are dicts whose contents the clauses mutate (see the header comment)."
  {:tags {:context "latest" :role "type"}}
  (#^ (get dict #(type object)) values)
  (#^ (get dict #(type (get tuple #((get ExternalPromise object) ...)))) bells))


(deff drop-bell [bells kind bell]  ; cannot be a defk: it is called as a plain function inside the scheduler by the bell promise's cancel callback (ExternalPromise.on-cancel)
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "To detach the bell of a finished or cancelled wait from the set of type kind (does nothing if it was already detached by ringing -- bells are compared by object identity)."
  (setv left (tuple (gfor other (.get bells kind #()) :if (is-not other bell) other)))
  (if left
      (setv (get bells kind) left)
      (.pop bells kind None))
  None)


(defhandler memory-latest-handler
  "Answers PublishLatest, ReadLatest and AwaitLatest with the MemoryLatestStore within this run (see the header comment)."
  (session val store (MemoryLatestStore :values {} :bells {}))
  (PublishLatest [value]
    (setv (get store.values (type value)) value)
    (for [bell (.pop store.bells (type value) #())]
      (.complete bell value))
    (resume None))
  (ReadLatest [kind]
    (resume (.get store.values kind)))
  (AwaitLatest [kind seen]
    (var current (.get store.values kind))
    (while (is current seen)
      (<- bell ExternalPromise (CreateExternalPromise))
      (setv (get store.bells kind) (+ (.get store.bells kind #()) #(bell)))
      (.on-cancel bell (partial drop-bell store.bells kind bell))
      (<- rung (Wait bell.future :priority TaskPriority.IDLE))
      (drop-bell store.bells kind bell)
      (:= current rung))
    (resume current)))
