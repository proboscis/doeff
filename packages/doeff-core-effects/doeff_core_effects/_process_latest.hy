;;; Real handler for latest values, process-latest-handler (ADR-DOE-CORE-EFFECTS-004): answers PublishLatest, ReadLatest and AwaitLatest
;;; with one store per process.
;;;
;;; Why: in a long-running process, the run of the processing loop places a snapshot (a record of failures, sync progress, a ring of samples ...) and another run (the probe's HTTP)
;;; reads it without waiting for the writer. Session values exist only within one run, so this module keeps one "name -> board (type -> latest value)" per process,
;;; and handlers installed under the same name (different runs, different threads) read and write the same board. No class is made (ADR-DOE-HY-007 R3, R4).
;;;
;;; A write is one replacement of one key in the board's dict and a read is one lookup of one key; neither takes a lock (a single dict read or write is not torn even under
;;; free-threaded Python). A lock is taken only when creating a board. Boards remain until the process ends.
;;; Only this module touches threads (the memory handler memory_latest.hy does not).
;;;
;;; Waiting until a change (AwaitLatest): the waiter hangs a bell (a CreateExternalPromise promise -- the only means that may be completed from another thread) in
;;; "name -> type -> set of hung bells" (BELLS), then re-checks the store, and if it is still seen, waits on the bell's promise in the park form (Wait with TaskPriority.IDLE --
;;; the virtual clock is not stopped). After replacing the value, PublishLatest on the publishing side detaches and rings all the bells of that type. Order that loses nothing: the waiter does
;;; "hang -> re-check" and the publisher does "replace -> detach and ring", and only the insertion and removal of the set of bells happen inside BELLS-LOCK -- if there is no
;;; bell when the publisher detaches, then when the waiter reads again it sees the replaced value. When the caller Cancels the waiting task, the bell promise's cancel callback
;;; (ExternalPromise.on-cancel) detaches the bell (does not leave it). When the same object is re-published it is still seen, so the waiter hangs a bell and waits again.
(require doeff-hy.macros [defhandler defk deff val var <-])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import threading)
(import functools [partial])
(import doeff_core_effects._latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait TaskPriority])


;; One per process: name -> board (type -> latest value), and the lock for creating a board.
(val BOARDS {})
(val BOARDS-LOCK (threading.Lock))
;; One per process: name -> store of bells (type -> set of hung bells), and the lock for inserting and removing the set of bells (the wait until a change in the header comment).
(val BELLS {})
(val BELLS-LOCK (threading.Lock))


(defk latest-board [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "latest" :role "foundation"}}
  "To look up the board of name (type -> latest value) when installing the handler (created if absent -- shared with other runs)."
  (with [BOARDS-LOCK]
    (when (not-in name BOARDS)
      (setv (get BOARDS name) {}))
    (get BOARDS name)))


(defk latest-bells [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "latest" :role "foundation"}}
  "To look up the store of bells of name (type -> set of hung bells) when installing the handler (created if absent -- shared with other runs)."
  (with [BOARDS-LOCK]
    (when (not-in name BELLS)
      (setv (get BELLS name) {}))
    (get BELLS name)))


(defk hang-bell [bells kind bell]
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "To hang the waiter's bell at the end of the set of type kind (the \"hang\" of the order that loses nothing in the header comment)."
  (with [BELLS-LOCK]
    (setv (get bells kind) (+ (.get bells kind #()) #(bell))))
  None)


(deff unhang-bell [bells kind bell]  ; cannot be a defk: it is called as a plain function inside the scheduler by the bell promise's cancel callback (ExternalPromise.on-cancel)
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "To detach the bell of a finished or cancelled wait from the set of type kind (does nothing if it was already detached by ringing -- bells are compared by object identity)."
  (with [BELLS-LOCK]
    (setv left (tuple (gfor other (.get bells kind #()) :if (is-not other bell) other)))
    (if left
        (setv (get bells kind) left)
        (.pop bells kind None)))
  None)


(defk ring-bells [bells kind]
  {:pre [(: bells dict) (: kind type)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "To detach and ring all the bells hung on type kind (the \"detach and ring\" in the header comment -- ringing happens outside the lock. A second completion is ignored)."
  (with [BELLS-LOCK]
    (val rung (.pop bells kind #())))
  (for [bell rung]
    (.complete bell True))
  None)


(defhandler process-latest-handler [#^ str name]
  "Answers PublishLatest, ReadLatest and AwaitLatest with the one-per-process store name (see the header comment)."
  ;; Why it stays an argument: name is the key that shares a board with other runs -- a value decided per place of installation, which Ask cannot distinguish.
  (session val board (! (latest-board name)))
  (session val bells (! (latest-bells name)))
  (PublishLatest [value]
    (setv (get board (type value)) value)
    (<- (ring-bells bells (type value)))
    (resume None))
  (ReadLatest [kind]
    (resume (.get board kind)))
  (AwaitLatest [kind seen]
    (var current (.get board kind))
    (while (is current seen)
      (<- bell ExternalPromise (CreateExternalPromise))
      (<- (hang-bell bells kind bell))
      ;; Hang first, then re-check (so a value published right after the read and before hanging is not lost -- header comment). If the wait is cancelled, the callback detaches the bell.
      (when (is (.get board kind) seen)
        (.on-cancel bell (partial unhang-bell bells kind bell))
        (<- (Wait bell.future :priority TaskPriority.IDLE)))
      (unhang-bell bells kind bell)
      (.complete bell True)
      (:= current (.get board kind)))
    (resume current)))
