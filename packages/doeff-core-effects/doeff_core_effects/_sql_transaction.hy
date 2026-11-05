;;; The home of the realization of the three promises of SqlTransaction (the header note of sql_effects.hy) -- the two PostgreSQL handlers and the two sqlite handlers use the same
;;; procedure. There are two procedures, chosen by the declaration that opens the transaction (the field batched of SqlTransaction):
;;;   run-in-transaction          The default (batched = False). Runs one statement per round trip: BEGIN -> lock -> the statements of the program one at a time in the order emitted; SqlNotify is also
;;;                               run on the spot when emitted -> COMMIT / ROLLBACK. Even a program that emits no SQL runs BEGIN, the lock, and COMMIT.
;;;   run-in-batched-transaction  Only for a transaction that chose batched = True (when the recording service and the DB are on different machines, one
;;;                               round trip to the DB rides on the wait of one write). Bundles the round trips: see "The bundled transaction" below.
;;;
;;; Realization: the handler runs the program wrapped in a scope bound to one connection (a handler dedicated to SqlQuery / SqlInsertRows). If effect.program were run as is,
;;; the SqlQuery inside would go outside the handler and would not ride on the same connection and the same transaction (the same reason that try_handler of doeff_core_effects._handlers
;;; re-wraps the inner handlers).
;;;   - When a SqlQuery / SqlInsertRows inside answers a failure value (SqlFailed / SqlUnreachable), the scope discards the continuation (without resuming the program)
;;;     and makes TransactionAborted the answer. If an exception were thrown into the program, the program could catch it and continue, so none is thrown (promise 1).
;;;   - Any other effect likewise discards the continuation and makes TransactionMisused the answer, and run-in-transaction rolls back and then throws SqlTransactionMisuse
;;;     (promise 2; a nested SqlTransaction is the same -- the refusal of nesting in promise 3). SqlBatch can be emitted only inside a transaction that chose batched, so
;;;     a SqlBatch inside a transaction that did not choose it is also refused by name.
;;;   - If the program throws an exception, roll back and let the exception through.
;;;   - SqlNotify (a signal to the same database) is run on the same connection only when the handler passes execute-notify (the PostgreSQL handlers -- it is
;;;     delivered only on commit). Under a handler that does not pass it (sqlite), it is TransactionMisused, like the other forbidden effects.
;;; A handler that discards the continuation is not accepted by the terminal check of defhandler (every branch must end in resume / transfer / reperform / raise), so the scope is
;;; written as a plain handler function.
;;;
;;; The bundled transaction (run-in-batched-transaction): the handler receives the statements inside the transaction as a TransactionFlush, which it runs in one round trip (in running order:
;;; opening = BEGIN and the lock, requests = statements, notices = signals, closing = COMMIT) and answers (PostgreSQL = one pipeline; sqlite = run in order).
;;;   - Start: BEGIN and the lock are deferred until the first statement (SqlQuery, SqlInsertRows, SqlBatch) and run in the same round trip as that statement.
;;;   - Signals: SqlNotify is only remembered (the answer is None) and run in the same round trip as COMMIT -- a signal is delivered only on commit, so delaying the time it is run does not change
;;;     how it is delivered. If the statement of a signal fails, the failure of that round trip becomes the answer of the transaction (promise 1).
;;;   - End: a SqlBatch with commit = True runs its statements and COMMIT in the same round trip. If the program then emits a SQL effect, it is refused (promise 2).
;;;     If the program ends without emitting a commit bundle, a round trip of only COMMIT (and the remembered signals) is run. If it ends without emitting any SQL, neither BEGIN nor COMMIT is
;;;     run (0 round trips). Even if the program throws an exception after a commit bundle, the commit is not undone (the exception is let through).
;;;   - If a round trip fails (any statement, including BEGIN, the lock, and COMMIT), run ROLLBACK if BEGIN was run.
;;;   - The progress (whether BEGIN was run, whether COMMIT was run, the remembered signals) lives in one TransactionProgress per transaction, and only the scope
;;;     rewrites it. The pure function planned-flush makes the next round trip.
;;; For a SqlBatch outside a transaction, the handler throws the stray-batch refusal (a bundle bundles the round trips inside a bundled transaction -- it has no meaning outside).
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Generator])
(import dataclasses [dataclass])
(import doeff [do :as program-handler Resume Program EffectBase K WithHandlerType])
(import doeff_core_effects._scheduler [shielded])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlNotify SqlRows SqlFailed SqlUnreachable SqlTransactionMisuse T])


(defrecord TransactionAborted
  "A mark that the scope discarded the continuation: a query inside failed (failure = the value of that failure -- in a bundled transaction, the failure of the first statement that failed). A type the program cannot
   make, so it is never mixed with the answer of the program."
  (#^ (| SqlFailed SqlUnreachable) failure))


(defrecord TransactionMisused
  "A mark that the scope discarded the continuation: it emitted a forbidden effect inside (reason = what was emitted)."
  (#^ str reason))


(defn #^ Callable transaction-scope [#^ str database #^ Callable execute-query #^ Callable execute-insert #^ (| Callable None) execute-notify]  ; cannot be a defk: a handler function that discards the continuation (header note)
  "Makes the handler of the scope bound to one connection. execute-query / execute-insert = a Program of (effect) -> SqlRows | SqlFailed | SqlUnreachable;
   execute-notify = a Program of (effect) -> SqlRows | SqlFailed | SqlUnreachable, or None (a handler that does not accept signals -- it resumes with None on success)."
  (defn [program-handler] scope [effect k]  ; cannot be a defk: a handler function (receives the effect and the continuation k)
    (match effect
      (SqlQuery :database name) :if (= name database)
        (do (setv answer (yield (execute-query effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k answer)))))
      (SqlInsertRows :database name) :if (= name database)
        (do (setv answer (yield (execute-insert effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k answer)))))
      (SqlNotify :database name) :if (and (= name database) (is-not execute-notify None))
        (do (setv answer (yield (execute-notify effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k None)))))
      (SqlBatch :database name) :if (= name database)
        (return (TransactionMisused :reason (.format (+ "the transaction of database {!r} did not choose batched, so it cannot emit SqlBatch "
                                                        "(it can be emitted only inside a transaction opened with :batched True of SqlTransaction)")
                                                     database)))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlNotify :database name) (SqlBatch :database name)) :if (!= name database)
        (return (TransactionMisused :reason (.format "queried another database {1!r} inside the transaction of database {0!r}" database name)))
      _
        (return (TransactionMisused :reason (.format "emitted {1} inside the transaction of database {0!r} (only SqlQuery, SqlInsertRows, -- if the handler accepts it -- SqlNotify, and pure computation can be emitted)"
                                                     database (. (type effect) __name__))))))
  scope)


(defk run-in-transaction [database program execute-query execute-insert begin commit rollback [execute-notify None]]
  {:pre [(: database str) (: program Program) (: execute-query Callable) (: execute-insert Callable) (: begin Callable) (: commit Callable)
         (: rollback Callable) (: execute-notify (| Callable None))]
   :post [(: % "the answer of program | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Runs the program inside one transaction, one statement per round trip (the default procedure -- header note). begin / commit = a Program of () -> None | SqlFailed |
   SqlUnreachable; rollback = a Program of () -> None (a failure of rollback is not made the answer -- the earlier event is answered)."
  (<- began (begin))
  (when (is-not began None)
    (return began))
  (try
    (<- value (WithHandlerType (transaction-scope database execute-query execute-insert execute-notify) program))
    (except [Exception]
      ;; shielded: wait out ROLLBACK even when arriving by cancellation (the wait of a cancelled task is cancelled again)
      (<- (shielded (rollback)))
      (raise)))
  (match value
    (TransactionAborted :failure failure)
      (do (<- (rollback)) failure)
    (TransactionMisused :reason reason)
      (do (<- (rollback)) (raise (SqlTransactionMisuse reason)))
    _
      (do (<- committed (commit))
          (if (is committed None) value committed))))


;; --- The bundled transaction (only a transaction that chose batched = True -- header note) ---------------------------------------------------------

(defrecord TransactionFlush
  "What the handler runs in one round trip in a bundled transaction (in running order -- header note): opening = run BEGIN and the lock first / requests = statements (SqlQuery |
   SqlInsertRows -- the answer is a tuple of SqlRows in this order) / notices = the signals to emit (in the order of SqlNotify -- the channel and the related name; carried only when closing) /
   closing = run COMMIT last."
  (#^ bool opening)
  (#^ (get tuple #((| SqlQuery SqlInsertRows) ...)) requests)
  (#^ (get tuple #(SqlNotify ...)) notices)
  (#^ bool closing))


;; The values the scope of a bundled transaction receives from the Programs it ran (the plan of the next round trip, the answer of a round trip, the answer T of the resumed program -- T is the type variable of the answer of
;; SqlTransaction in sql_effects).
(val ScopeReceived (| TransactionFlush tuple SqlFailed SqlUnreachable T None))


(defclass [(dataclass :kw-only True)] TransactionProgress []
  "The progress of one bundled transaction (header note -- only the scope rewrites it, so it is not a value type): opened = BEGIN was run (or an attempt was made) / closed =
   COMMIT was run (or an attempt was made -- later SQL effects are refused) / notices = the signals not yet run (in the order of SqlNotify)."
  (setv #^ bool opened False)
  (setv #^ bool closed False)
  (setv #^ (get tuple #(SqlNotify ...)) notices #()))


(defk planned-flush [progress requests closing]
  {:pre [(: progress TransactionProgress) (: requests tuple) (: closing bool)] :post [(: % (| TransactionFlush None))]
   :tags {:context "sql" :role "foundation"}}
  "Makes the next round trip that runs requests (and COMMIT last if closing) in a bundled transaction (header note): if BEGIN has not been run yet, put
   BEGIN and the lock at the head of the same round trip, and if closing, put the remembered signals. None if there is nothing to run -- a bundle without statements, and the commit of a transaction that has run neither BEGIN nor signals
   (0 round trips)."
  (cond
    (and (not requests) (not closing)) None
    (and (not requests) (not progress.opened) (not progress.notices)) None
    True (TransactionFlush :opening (not progress.opened) :requests requests :notices (if closing progress.notices #()) :closing closing)))


(defk stray-batch [database]
  {:pre [(: database str)] :post [(: % SqlTransactionMisuse)]
   :tags {:context "sql" :role "foundation"}}
  "Makes the refusal of a SqlBatch emitted outside a transaction (the handler throws it -- header note)."
  (SqlTransactionMisuse (.format "SqlBatch to database {!r} was emitted outside a transaction (SqlBatch can be emitted only inside a transaction opened with :batched True of SqlTransaction)"
                                 database)))


(defn #^ Callable batched-transaction-scope [#^ str database #^ Callable flush #^ bool accepts-notices #^ TransactionProgress progress]  ; cannot be a defk: a handler function that discards the continuation (header note)
  "Makes the handler of the scope bound to one connection, for a bundled transaction. flush = a Program of (TransactionFlush) -> a tuple of SqlRows in the same order as requests |
   the first failure (SqlFailed | SqlUnreachable); accepts-notices = whether to remember SqlNotify and run it in the round trip of COMMIT; progress = the progress of this transaction
   (only the scope rewrites it)."
  (defn [program-handler] #^ (get Generator #(Program ScopeReceived (| TransactionAborted TransactionMisused T))) scope [#^ EffectBase effect #^ K k]  ; cannot be a defk: a handler function (receives the effect and the continuation k)
    (match effect
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name) (SqlNotify :database name))
        :if (and (= name database) progress.closed)
        (return (TransactionMisused :reason (.format "the transaction of database {!r} emitted {} after it ended with a commit bundle"
                                                     database (. (type effect) __name__))))
      (SqlNotify :database name) :if (and (= name database) accepts-notices)
        (do (setv progress.notices (+ progress.notices #(effect)))
            (return (yield (Resume k None))))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name)) :if (= name database)
        (do (setv batch (isinstance effect SqlBatch)
                  closing (and batch effect.commit)
                  planned (yield (planned-flush progress (if batch effect.queries #(effect)) closing)))
            (when (is planned None)
              (setv progress.closed (or progress.closed closing))
              (return (yield (Resume k #()))))
            (setv progress.opened True
                  progress.closed closing
                  progress.notices (if closing #() progress.notices)
                  answer (yield (flush planned)))
            (cond
              (isinstance answer #(SqlFailed SqlUnreachable)) (return (TransactionAborted :failure answer))
              batch (return (yield (Resume k answer)))
              True (return (yield (Resume k (get answer 0))))))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name) (SqlNotify :database name)) :if (!= name database)
        (return (TransactionMisused :reason (.format "queried another database {1!r} inside the transaction of database {0!r}" database name)))
      _
        (return (TransactionMisused :reason (.format "emitted {1} inside the transaction of database {0!r} (only SqlQuery, SqlInsertRows, SqlBatch, -- if the handler accepts it -- SqlNotify, and pure computation can be emitted)"
                                                     database (. (type effect) __name__))))))
  scope)


(defk run-in-batched-transaction [database program flush rollback [accepts-notices False]]
  {:pre [(: database str) (: program Program) (: flush Callable) (: rollback Callable) (: accepts-notices bool)]
   :post [(: % "the answer of program | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Runs the program of a transaction that chose batched = True with the round trips bundled (\"The bundled transaction\" in the header note). flush = a Program of (TransactionFlush) ->
   a tuple of SqlRows in the same order as requests | the first failure; rollback = a Program of () -> None (called only when BEGIN was run. A failure of rollback is not made
   the answer -- the earlier event is answered)."
  (val progress (TransactionProgress))
  (try
    (<- value (WithHandlerType (batched-transaction-scope database flush accepts-notices progress) program))
    (except [Exception]
      (when progress.opened
        (<- (rollback)))
      (raise)))
  (match value
    (TransactionAborted :failure failure)
      (do (when progress.opened
            (<- (rollback)))
          failure)
    (TransactionMisused :reason reason)
      (do (when progress.opened
            (<- (rollback)))
          (raise (SqlTransactionMisuse reason)))
    _
      (do (when progress.closed
            (return value))
          (<- planned (planned-flush progress #() True))
          (if (is planned None)
              value
              (do (<- committed (flush planned))
                  (if (isinstance committed tuple)
                      value
                      (do (<- (rollback))
                          committed)))))))
