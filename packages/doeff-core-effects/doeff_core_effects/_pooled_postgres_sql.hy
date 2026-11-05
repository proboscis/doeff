;;; The real PostgreSQL answerer for the generic SQL effects (sql_effects.hy), in the version that does not block the scheduler: pooled-postgres-sql-handler
;;; (G2). postgres-sql-handler (postgres_sql.hy) also does not block the scheduler since #1215 (one thread per call, permits are
;;; an inter-thread lock). This version uses the same statements, the same value mapping, and the same transaction procedure (run-in-transaction), and in order to hold the number of threads down with the caller's pool,
;;; it changes the way of waiting as follows:
;;;   - Waiting for a connection permit = the scheduler's CreateSemaphore / AcquireSemaphore (size of PostgresConnections per database). Other tasks run
;;;     alongside a task waiting for a permit.
;;;   - Only the driver's I/O (opening a connection, running a statement, COMMIT, ROLLBACK, returning a connection) is offloaded to a thread of the caller's pool, and waited for with a promise completed from outside
;;;     (CreateExternalPromise + Wait) (the same practice as thread_pool_compute.hy). Only the task that fired waits.
;;;   - SqlQuery / SqlInsertRows / SqlEnsureTables = take a permit, and in one pool job "lease a connection -> run -> return".
;;;   - SqlTransaction = take a permit, lease one connection, and run each stage of BEGIN (lock - the order of lock-key and locks, the same statement as in postgres-sql-handler
;;;     ) -> program -> COMMIT in the pool. The SqlQuery inside is routed to the pool on the same connection by the transaction scope's handler (the program side's
;;;     promises are the same as postgres-sql-handler). Only a transaction that chose batched = True uses the same way of batching round trips as postgres-sql-handler
;;;     (BEGIN and the lock go in the same round trip as the first statement, and the commit bundle goes in the same round trip as COMMIT - one round trip = one pipeline, #3605), and runs lease, all round trips and the program between them, and return
;;;     in one pool job (no return to the scheduler between round trips - offloaded-batched-transaction in postgres_sql.hy). A SqlBatch outside a transaction is refused by name
;;;     (stray-batch).
;;;   - Notifications (SqlNotify, SqlHangNotice, SqlDropNotice; the name-filtered form is #3688) use the same procedure as postgres-sql-handler
;;;     (notified and hung-notice in postgres_sql.hy - only the bells with overlapping names are rung, after the connection is returned). A SqlNotify outside a transaction is run after taking a connection
;;;     permit. The bell is hung on the listening connection, which does not use a permit.
;;;   - Cancellation: when the waiting task is Cancelled, run-in-transaction runs ROLLBACK, returns the connection (and also rolls back on return if in the middle of a transaction),
;;;     returns the permit, and then lets the cancellation through. Pool jobs that have not started are removed, and an answer produced after the cancellation (a leased connection) is returned in the pool.
;;;     A running statement is not stopped (ROLLBACK waits on the driver's lock until the statement ends).
;;; Contract (kept by the assembly side):
;;;   - The number of workers of the pool (Executor) must be at least the total number of connections declared in the PostgresConnections handed to this handler (number of databases x size).
;;;     So that the driver calls of tasks holding permits do not wait for each other inside the pool. The owner of the pool is the caller (the assembly side makes and closes it).
;;;   - Placement: scheduled and a session value store (state of doeff_core_effects) are needed outside.
;;;   - The same PostgresConnections may be shared with postgres-sql-handler (the connection limit is kept by the lock of PostgresConnections inside the pool's threads).
;;;   - Where the machinery lives: the part that waits with a promise completed from outside (offloaded, the post-cancellation cleanup Handoff) is in offloaded_call.hy, and the job that leases, runs, and returns a connection
;;;     (offloaded-statement) and the transaction stage (offloaded-transaction) are in postgres_sql.hy - used together with postgres-sql-handler (#1215). What this file
;;;     holds is only the way of waiting for permits (the scheduler's semaphore) and the use of the caller's pool.
(require doeff-hy.macros [defhandler defk <- val var])
(import concurrent.futures [Executor])
(import doeff [Program])
(import doeff_core_effects._scheduler [CreateSemaphore AcquireSemaphore ReleaseSemaphore])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice])
(import doeff_core_effects._sql_transaction [stray-batch])
(import doeff_core_effects._postgres_sql [PostgresConnections postgres-query postgres-insert postgres-ensure-tables offloaded-statement
                                         offloaded-transaction transaction-locks notified hung-notice])


(defk permit-for [permits database size]
  {:pre [(: permits dict) (: database str) (: size int)] :post [(: % "scheduler Semaphore")]
   :tags {:context "sql" :role "foundation"}}
  "To look up the connection permit (the scheduler's semaphore) for database (made with size permits if it does not exist yet)."
  (if (in database permits)
      (get permits database)
      (do (<- created (CreateSemaphore size))
          created)))


(defk permitted [permit program]
  {:pre [(: permit "scheduler Semaphore") (: program Program)] :post [(: % "answer of program")]
   :tags {:context "sql" :role "foundation"}}
  "To take a connection permit by waiting in the scheduler, run program, and always return the permit."
  (<- (AcquireSemaphore permit))
  (try
    (<- answer program)
    answer
    (finally (<- (ReleaseSemaphore permit)))))


(defhandler pooled-postgres-sql-handler [#^ PostgresConnections connections #^ Executor pool #^ (get tuple #(str ...)) [serialized-lock-keys #()]]
  ;; Reason it stays an argument: the connection lease is a resource that the assembly side makes and closes (it holds the DSN and credentials), and it is lined up in the same set as the ClickHouse answerer, sorted by the database name it answers.
  ;; The pool is also a resource that the assembly side makes and closes (the worker-count contract is in the header comment).
  ;; permits = database name -> connection permit (the scheduler's semaphore - made on the first beat it is used) (a session value).
  "The real PostgreSQL answerer that does not block the scheduler (header comment). Answers only the database names that connections declared (other names are passed outward)."
  {:tags {:context "sql" :role "foundation"}}
  (session var permits {})
  ;; serialized-lock-keys is a finite tuple fixed for the session. Only registered keys are serialized before acquiring a connection.
  ;; Semaphores are not garbage-collected, so they are kept and reused. The DB's statement_timeout does not apply to the local wait.
  (session var transaction-permits {})
  (SqlQuery [database statement params binary] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    ;; The permit is placed only once, on the first beat (if present, the session value is not rewritten - so a Put per statement does not make a round trip down to the foundation).
    ;; The same for the clauses below.
    (when (not-in database permits)
      (:= permits (| permits {database created})))
    (val request (SqlQuery database statement params binary))
    (<- answer (permitted (get permits database)
                          (offloaded-statement connections pool database (fn [leased] (postgres-query leased request)))))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (when (not-in database permits)
      (:= permits (| permits {database created})))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (permitted (get permits database)
                          (offloaded-statement connections pool database (fn [leased] (postgres-insert leased request)))))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (when (not-in database permits)
      (:= permits (| permits {database created})))
    (<- answer (permitted (get permits database)
                          (offloaded-statement connections pool database (fn [leased] (postgres-ensure-tables leased tables)))))
    (resume answer))
  (SqlTransaction [database program lock-key batched locks] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (when (not-in database permits)
      (:= permits (| permits {database created})))
    (<- claims (transaction-locks lock-key locks))
    (val transaction (permitted (get permits database)
                                (offloaded-transaction connections pool database program claims batched)))
    (var admitted transaction)
    (when (and (is-not lock-key None) (in lock-key serialized-lock-keys))
      (val identity #(database lock-key))
      (var admission (.get transaction-permits identity))
      (when (is admission None)
        (<- created-admission (CreateSemaphore 1))
        (:= admission created-admission)
        (:= transaction-permits (| transaction-permits {identity admission})))
      (:= admitted (permitted admission transaction)))
    (<- answer admitted)
    (resume answer))
  ;; A bundle batches round trips within a transaction - inside a transaction the SqlTransaction scope answers, and only bundles emitted outside reach here.
  (SqlBatch [database queries commit] :when (in database (.names connections))
    (<- refusal (stray-batch database))
    (raise refusal))
  (SqlNotify [database channel topics] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (when (not-in database permits)
      (:= permits (| permits {database created})))
    (<- answer (permitted (get permits database) (notified connections pool database effect)))
    (resume answer))
  (SqlHangNotice [database channel topics] :when (in database (.names connections))
    (<- answer (hung-notice connections pool database channel topics))
    (resume answer))
  (SqlDropNotice [database channel bell] :when (in database (.names connections))
    (.drop (.listener connections database channel) bell)
    (resume None)))
