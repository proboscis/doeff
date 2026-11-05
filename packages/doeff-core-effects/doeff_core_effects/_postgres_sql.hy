;;; postgres-sql-handler: the real PostgreSQL handler for the generic SQL effects (sql_effects.hy). It only calls psycopg 3 and repacks
;;; values; it holds no judgment. psycopg is imported only inside this module's I/O functions (even in an environment without psycopg, the pure
;;; functions for rewriting and mapping can be imported and tested).
;;;   - The connection lease PostgresConnections is built by the assembling side (composition root) from a database declaration (PostgresDatabase: name and DSN),
;;;     and closed on shutdown. One request = one connection (the doeff-records store also uses this lease, so transactions of concurrent requests are
;;;     never mixed on one connection). Connections are autocommit; json / jsonb columns are read as text (the value normalization rule). Connections that
;;;     broke, and connections returned in the middle of a transaction, are discarded / rolled back
;;;     on return. A connection that carried a value over the threshold (LARGE-CARRY-BYTES) (a set of sent arguments or one row, or one received row) is also
;;;     discarded on return: libpq grows each connection's send and receive buffers up to the largest message passed through and does not shrink them until close, so returning it to the idle queue would keep tens of MB
;;;     for a single large body. SqlQuery's binary receives the answer rows in binary form and does not
;;;     double bytea into hex (reading large bodies). The lease counts opened connections and, per reason, closed connections (tallies).
;;;   - Connection limits (PostgresTimeouts): a connection has a limit for opening, TCP keepalive, a limit for waiting for a reply to sent bytes
;;;     (tcp_user_timeout), and a statement limit (statement_timeout). When a DB pod is replaced, idle connections remain as TCP with no peer, and without limits
;;;     the next statement would hang for as long as the kernel retransmits (tens of minutes) while holding its permit. With limits the statement fails with OperationalError, answers SqlUnreachable,
;;;     is discarded on return as a broken connection, and the next borrow opens a fresh one.
;;;   - Arguments: the neutral `:name` is rewritten to `%(name)s`, and `%` in the statement becomes `%%` (postgres-statement).
;;;   - Failures: the engine's SQLSTATE goes into SqlFailed as is. A driver error with no SQLSTATE is mapped to the class code via the class table (DRIVER-CLASS-SQLSTATES, the same table as agora-controllers
;;;     services/record/handlers_wire.hy). OperationalError / InterfaceError with no SQLSTATE, failure to connect, and
;;;     the connection-exception SQLSTATEs (class 08 and 57P01, 57P02, 57P03) are SqlUnreachable (postgres-failure).
;;;   - SqlTransaction = BEGIN on one connection, then locks (in the order of lock-key and locks: exclusive uses pg_advisory_xact_lock, shared uses pg_advisory_xact_lock_shared on
;;;     hashtext(key)), then program, then COMMIT (sql_transaction.hy).
;;;     The default (batched = False) sends one statement at a time (run-in-transaction: BEGIN, locks, statements, signal, and COMMIT each take one round trip).
;;;     Only transactions that choose batched = True: one round trip (TransactionFlush: BEGIN plus locks, statements, signal, and COMMIT) is sent in
;;;     one pipeline of psycopg 3 pipeline mode, and the answers are received in order at a single sync at the exit (postgres-flush, run-in-batched-transaction). When a statement in the middle
;;;     fails, PostgreSQL does not run the statements up to the next sync, so the answer is the failure of the first failed statement (mapped to TransactionAborted), and ROLLBACK is sent by
;;;     run-in-batched-transaction. Entering and leaving the pipeline is always closed with `with`, and a returned connection still in pipeline mode is discarded (release).
;;;     A batched transaction runs borrowing, all round trips and the program in between, and returning as one job of the driver's thread (a new VM), and does not return to the scheduler
;;;     thread between round trips (offloaded-batched-transaction: no scheduler wait is inserted while holding locks).
;;;     Driver calls on the borrowed connection (statements, round trips, ROLLBACK, return) are serialized one at a time by a per-transaction lock (offloaded-transaction: ROLLBACK does not interrupt
;;;     before the thread of a cancelled round trip has finished running). For a psycopg / libpq without pipeline mode (libpq older than 14), building the connection lease
;;;     (at startup) fails by naming it: a batched transaction is not silently downgraded to the one-statement-at-a-time path.
;;;   - Do not block the scheduler: postgres-sql-handler does not fire driver I/O (waiting for a connection permit, opening a connection, running a statement, COMMIT,
;;;     ROLLBACK, returning a connection) on the scheduler's thread; it runs it on an Executor that reuses idle threads (ReusedThreads in offloaded_call.hy: a thread is started only when none is
;;;     free), and only the task that fired it
;;;     waits, on a promise completed from outside. Other tasks of the same run (the listener's /healthz, clock ticks) keep running during a slow statement. The limit on connections used at the same time
;;;     is held by PostgresConnections' permits (a lock between threads), and the wait for a permit also happens inside a thread. Since there is no limit on the number of threads, the next statement of a transaction
;;;     holding a permit never gets stuck waiting for a free thread. The procedure (statements, value mapping, transaction stages, cleanup after cancel) uses the same offloaded-transaction and offloaded-statement as
;;;     pooled-postgres-sql-handler; the only differences are the Executor and how permits are waited for (pooled uses the caller's
;;;     pool and the scheduler's semaphore). A `scheduled` is required outside (CreateExternalPromise and Wait); a place for session values (state) is not.
;;;   - Notifications (the name-filtered form is #3688): SqlNotify = sends pg_notify(channel, body) (inside a transaction, on the same connection, so
;;;     it is delivered only on commit). The body (notice-payload) is a JSON object carrying the names the signal concerns (topics: words chosen by the caller; for the records store, table and
;;;     column names) and a mark of the lease that emitted the signal (origin: a random value per PostgresConnections). SqlHangNotice = hangs a bell (an external promise), together with the names it waits for, on a listener
;;;     (PostgresListener) that holds, in a daemon thread, one connection with LISTEN on each channel. A signal rings only the bells whose
;;;     names overlap. A signal whose names are unknown (topics = None, an unreadable body, or a signal whose names would reach the 8000-byte body limit) means a signal concerning all
;;;     waiters, and rings every bell (so a signal that cannot be narrowed never fails to wake anyone: a mistaken name mapping would leave a waiter that never wakes,
;;;     so when unsure, all of them). A bell whose waited names are None rings on every signal.
;;;     Bells in the same process are rung on the spot, after the handler that sent the signal has returned its connection (ring-local: the rest of the writer [returning the connection] does not queue behind the re-read of the waiter it woke.
;;;     It does not wait for NOTIFY delivery, so even under a virtual clock the wait's deadline does not arrive before the signal). The listener
;;;     does not ring notifications bearing its own mark (the bells in the same process were already rung; one write must not wake a waiter twice). The reason for telling them apart by mark is that
;;;     telling them apart by a set of backend pids would silently drop another process's signals when a backend of that process inherits the pid of a broken connection.
;;;     For a transaction, the ring happens when COMMIT was sent (or an attempt to send it was made): even if COMMIT's answer is a failure, it is sometimes unknown whether it committed (the connection
;;;     broke), and since the listener does not ring notifications with its own mark, we do not lean toward failing to ring (ringing too much costs a waiter only one re-read). Even if cancelled
;;;     before receiving the answer, it rings inside the finally that returns the connection. SqlNotify outside a transaction also rings after the connection is returned.
;;;     The listener reconnects when it breaks, and on reconnecting rings every bell currently hung (notifications during the outage are not delivered, so waiters are made to re-read).
;;;     SqlHangNotice while it is not established is SqlUnreachable. The seconds between reconnects are time only inside the boundary handler and are not visible to the caller.
;;;   - Cancellation: for a single-statement effect, as with a transaction, "borrow a connection" and "run and return" are separate jobs. A request cancelled while waiting for a
;;;     permit returns immediately without running the statement even if the permit is obtained (so reads cancelled while the DB was down do not all flush when the DB returns and
;;;     make later requests wait). A statement that has started running is not stopped.
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(require doeff-hy.record [defrecord defwire])
(import queue [Queue Empty])
(import threading)
(import time)
(import uuid)
(import weakref)
(import concurrent.futures [Executor])
(import collections.abc [Callable])
(import contextlib [AbstractContextManager])
(import typing [Protocol runtime-checkable])
(import dataclasses [dataclass field replace :as with-fields])
(import doeff [Program])
(import doeff_hy._wire [Malformed dump-json parse-json])
(import doeff_core_effects._offloaded_call [ReusedThreads :as _ReusedThreads offloaded run-detached keep-nothing])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise TaskCancelledError shielded])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice
                                        SqlRows SqlFailed SqlUnreachable SqlLock
                                        SqlSchemaApplied SqlParam SqlColumnType SqlText SqlPlaceholder split-statement checked-params
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects._sql_transaction [TransactionFlush run-in-transaction run-in-batched-transaction stray-batch])

;; Map from the class of a driver error that has no SQLSTATE to the SQLSTATE class code of that class (the inverse of the table psycopg uses to choose a class from SQLSTATE).
(val DRIVER-CLASS-SQLSTATES {"DataError" "22000" "IntegrityError" "23000" "ProgrammingError" "42000" "NotSupportedError" "0A000"
                             "InternalError" "XX000"})
;; Classes read as unreachable (cannot connect / broken) when there is no SQLSTATE.
(val UNREACHABLE-CLASSES #("OperationalError" "InterfaceError"))
;; Codes read as unreachable even with a SQLSTATE (connection broke, or the engine does not accept connections): class 08 = all connection exceptions (first 2 characters), and
;; 57P01 disconnect by administrator, 57P02 disconnect by crash, 57P03 starting up / shutting down and not accepting connections (the other class 57 codes, such as 57014 cancel, are engine answers).
;; This is not a business-statement error but a failure that clears if retried after a while, so it is not SqlFailed.
(val UNREACHABLE-SQLSTATE-CLASS "08")
(val UNREACHABLE-SQLSTATES #("57P01" "57P02" "57P03"))
;; Default limit on connections lent at the same time (per database).
(val DEFAULT-POOL-SIZE 8)
;; The Executor on which postgres-sql-handler runs driver I/O (reuses idle threads and has no limit; see the header comment). One per process.
(val DRIVER-THREADS (_ReusedThreads))

;; Seconds from when the listener's connection breaks until it reconnects (see the header comment; time only inside the boundary handler).
(val LISTEN-RETRY-SECONDS 1)
;; Upper limit in bytes of the signal body (the NOTIFY payload) (PostgreSQL's default build requires under 8000 bytes; the delivered body does not carry names, see the header comment).
(val NOTICE-PAYLOAD-LIMIT 8000)

;; Threshold (in bytes: the sum of the str / bytes lengths of sent arguments and rows, and of received rows, for one statement or one round trip) above which a connection that carried a large value is closed on return. A libpq connection
;; grows its send and receive buffers to the next power of 2 above the largest message passed through and does not shrink them until closed, so returning one to the idle queue would keep
;; tens of MB per connection for a single large body. A connection that only carried values under the threshold keeps its buffers to 1 MiB each for send and receive
;; (2 MiB per connection, 16 MiB at the lease limit of 8 connections). The threshold was raised from 256 KiB to 512 KiB: the record service's
;; rows with truncated bodies (turn-memory-part and worktree-diff; a 256 KiB body slice plus the column names makes about 262,260 bytes per row) slightly
;; exceeded 256 KiB, so every request that read or wrote them closed and reopened a connection (in agora-records-twin, 242 connections in 30 seconds, and 13% of the pg thread samples were spent opening connections).
(val LARGE-CARRY-BYTES (* 512 1024))
;; Marks connections that carried a value over the threshold (release closes them instead of returning them to the idle queue). When the connection goes away, the mark goes too (weak reference).
(val CARRIED-LARGE (weakref.WeakSet))
;; Record of statements of batched transactions turned into named prepared statements (connection -> PreparedBook; see the note on postgres-flush). Discarding the connection discards the record.
;; The number of statements recorded is up to PREPARED-MAX per connection (statements beyond that are sent without a name; statement spellings are a finite set determined by declarations, but the limit guards it).
;; The only owner of the name store on a connection is this record: psycopg's automatic prepare is turned off (PostgresConnections.acquire), and
;; there is no path that sends name-removing statements (DEALLOCATE / DISCARD): adding one would be a removal the record does not know about, and the connection's next round trip would fail with 26000.
;; For a statement you want named for speed, route it to this record's path (postgres-flush) instead of restoring psycopg's prepare.
(val PREPARED (weakref.WeakKeyDictionary))
(val PREPARED-LOCK (threading.Lock))
(val PREPARED-MAX 256)

;; Declared column type -> PostgreSQL type.
(val POSTGRES-TYPES {SqlColumnType.INTEGER "bigint" SqlColumnType.FLOAT "double precision" SqlColumnType.TEXT "text"
                     SqlColumnType.BYTES "bytea" SqlColumnType.BOOLEAN "boolean" SqlColumnType.JSON "json"})


(defclass [(dataclass :frozen True :kw-only True)] PostgresDatabase []
  "Declaration of a PostgreSQL database (name = the name business code writes in SqlQuery; dsn = the psycopg connection string, which contains credentials and so is kept out of repr)."
  #^ str name
  #^ str dsn
  (setv dsn (field :repr False)))


(defrecord PostgresTimeouts
  "Connection limits (see the header comment): open time (seconds), silence before keepalive's first probe (seconds), probe interval (seconds), number of unanswered probes, limit for waiting for a reply to
   sent bytes (milliseconds), statement limit (milliseconds; None = none), and limit for doing nothing in the middle of a transaction (milliseconds; None = none).
   The mid-transaction limit exists so that the engine cuts a connection whose program stopped advancing after BEGIN (it never reaches the returning finally; in the 2026-09-30 landing ledger,
   one stayed idle in transaction for 6.5 minutes holding pg_advisory_xact_lock, and all following writes failed with statement timeout),
   rolls back the transaction, and releases the lock."
  (#^ int connect-seconds)
  (#^ int keepalive-idle-seconds)
  (#^ int keepalive-interval-seconds)
  (#^ int keepalive-count)
  (#^ int unacknowledged-milliseconds)
  (#^ (| int None) statement-milliseconds)
  (#^ (| int None) idle-transaction-milliseconds))


;; Default limits: a dead peer is found in 10 + 5 x 3 = 25 seconds by keepalive, and 15 seconds after a statement is sent. The statement limit of 60 seconds is well longer than the record service's statements (short
;; reads and writes, and table preparation waiting for a lock). The mid-transaction idle limit of 30 seconds is well longer than the record service's transactions (only pure
;; computation between statements), and caps at 30 seconds the time a connection stalled while holding a lock can block later writes.
(val DEFAULT-TIMEOUTS (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10 :keepalive-interval-seconds 5 :keepalive-count 3
                                        :unacknowledged-milliseconds 15000 :statement-milliseconds 60000
                                        :idle-transaction-milliseconds 30000))


(defrecord PostgresStatement
  "A statement rewritten into the form handed to psycopg (text = a statement with `%(name)s`; params = the arguments; converting to a dict happens at only one point, where the driver is called)."
  (#^ str text)
  (#^ tuple params))


(defclass [(dataclass :kw-only True)] RaisedNotices []
  "Record of the signals emitted by one transaction (or one SqlNotify outside a transaction), used to ring same-process bells after the handler has returned its connection
   (see the header comment). Only the handler's procedure rewrites it, so it is not a value type. topics = channel -> names the signal concerns (the union of repeated signals on the same channel;
   None = a signal with unknown names = all; the dict is an index keyed by channel and is not exposed) / sent = the signal was sent far enough to be delivered (for a transaction,
   COMMIT was sent or an attempt to send it was made; the mark for whether to ring)."
  (setv #^ (get dict #(str (| (get frozenset str) None))) topics (field :default-factory dict))
  (setv #^ bool sent False))

(defrecord PostgresTallies
  "Events counted by the connection lease (PostgresConnections.tallies): opened = connections opened; and connections closed on return, per reason
   (broken = was broken; pipeline = left in pipeline mode; large = carried a value over the threshold [CARRIED-LARGE]; rollback = returned in the middle of a transaction
   and the rollback failed)."
  (#^ int opened)
  (#^ int broken)
  (#^ int pipeline)
  (#^ int large)
  (#^ int rollback))


(defclass PostgresConnections []
  "The connection lease (see the header comment). It is a resource, so not a value type (its contents are rewritten and it is handled by identity). size = the limit on connections lent at the same time per database
   (read by pooled-postgres-sql-handler as the number of scheduler permits); timeouts = limits put on connections that are opened; origin = the mark of signals this lease emits
   (the lease rings same-process bells after sending a signal, so the listener does not ring notifications with this mark; see the header comment)."

  (defn __init__ [self #^ (get tuple #(PostgresDatabase ...)) databases * [size DEFAULT-POOL-SIZE] [timeouts DEFAULT-TIMEOUTS]]  ; cannot be a defk: initialization of a resource class
    "Receives the sequence of database declarations, the limit on connections lent at once per database, and the connection limits. Transaction round trips are sent in pipeline mode
     (see the header comment), so if psycopg and libpq lack pipeline mode this place (built by the assembling side at startup) fails by naming it."
    (import psycopg)
    (when (not (psycopg.Pipeline.is-supported))
      (raise (RuntimeError (.format (+ "The PostgreSQL handler sends transaction round trips in psycopg's pipeline mode, but this environment does not have pipeline mode"
                                       " (psycopg {} implementation {}, libpq {}; libpq 14 or later is required)")
                                    psycopg.__version__ psycopg.pq.__impl__ (psycopg.pq.version)))))
    (setv self.size size
          self.timeouts timeouts
          self.origin (. (uuid.uuid4) hex)
          self.databases (dfor d databases d.name d)
          self.idle (dfor d databases d.name (Queue))
          self.permits (dfor d databases d.name (threading.BoundedSemaphore size))
          self.listeners {}
          self.listeners-lock (threading.Lock)
          self.counts (PostgresTallies :opened 0 :broken 0 :pipeline 0 :large 0 :rollback 0)
          self.counts-lock (threading.Lock)))

  (defn #^ None counted [self #^ str tally]  ; cannot be a defk: a count called synchronously from the borrowing and returning threads (a lock outside the VM)
    "Counts one event tally (one of the field names of PostgresTallies)."
    (with [_ self.counts-lock]
      (setv self.counts (with-fields self.counts #** {tally (+ (getattr self.counts tally) 1)}))))

  (defn #^ PostgresTallies tallies [self]  ; cannot be a defk: a resource count read between threads by the meter and tests (a lock outside the VM)
    "Reads the number of opened connections and of closed connections per reason."
    (with [_ self.counts-lock]
      self.counts))

  ;; The return type is written as a string forward reference: PostgresListener is defined after this class, and through Python 3.13 annotations are evaluated at definition time,
  ;; so a bare name would make the module load fail with NameError (PEP 649, which evaluates annotations lazily, is from 3.14).
  (defn #^ "PostgresListener" listener [self #^ str name #^ str channel]  ; cannot be a defk: looks up the resource listener (creates it and starts a thread if absent; a lock outside the VM)
    "Looks up the listener (PostgresListener) for channel of database name (creating it for a channel seen for the first time)."
    (with [_ self.listeners-lock]
      (setv key #(name channel))
      (when (not-in key self.listeners)
        (setv (get self.listeners key) (PostgresListener self name channel)))
      (get self.listeners key)))

  (defn #^ None ring-local [self #^ str name #^ RaisedNotices raised]  ; cannot be a defk: called synchronously inside the finally where the handler returns the connection (a lock outside the VM; rings without emitting an effect even in the middle of cancellation)
    "If the signal was sent far enough to be delivered (raised.sent), rings on the spot, among the bells hung per channel in the same process, those that concern the signal's names
     (see the header comment; if there is no listener yet, there are no bells hung either)."
    (when raised.sent
      (for [[channel topics] (.items raised.topics)]
        (with [_ self.listeners-lock]
          (setv listener (.get self.listeners #(name channel))))
        (when (is-not listener None)
          (.ring listener topics)))))

  (defn names [self]  ; cannot be a defk: a read called from the handler's guard (:when) (returning a Program would not be a boolean)
    "Reads the sequence of database names this lease answers for."
    (tuple self.databases))

  (defn connection-options [self #^ str name]  ; cannot be a defk: arguments for psycopg read inside the thread that opens the connection (returning a Program could not be passed to psycopg)
    "Reads the limits put on opened connections as a mapping of libpq connection parameters (passed to connect's keywords; if the DSN has the same name, these win).
     However, options (a single value chaining -c) does not win; the engine-limit -c settings are appended to the DSN options of database name. If it won, the DSN's
     -c search_path and the like would silently disappear."
    (setv timeouts self.timeouts
          options {"connect_timeout" timeouts.connect-seconds
                   "keepalives" 1
                   "keepalives_idle" timeouts.keepalive-idle-seconds
                   "keepalives_interval" timeouts.keepalive-interval-seconds
                   "keepalives_count" timeouts.keepalive-count
                   "tcp_user_timeout" timeouts.unacknowledged-milliseconds})
    ;; Engine limits (chained -c): the statement limit, and the limit for doing nothing in the middle of a transaction. A limit of None is not set.
    (setv bounds (lfor [setting value] [#("statement_timeout" timeouts.statement-milliseconds)
                                        #("idle_in_transaction_session_timeout" timeouts.idle-transaction-milliseconds)]
                       :if (is-not value None)
                       (.format "-c {}={}" setting value)))
    (when bounds
      (setv bound (.join " " bounds)
            dsn (. (get self.databases name) dsn)
            declared (when dsn
                       ;; The DSN is read in libpq's spelling (both URI and key=value), so leave the reading to psycopg (if the DSN is empty it is not read;
                       ;; psycopg is an extra dependency of postgres, and like acquire that opens connections it is imported only when used).
                       (import psycopg.conninfo [conninfo-to-dict])
                       (.get (conninfo-to-dict dsn) "options")))
      (setv (get options "options") (if declared (+ declared " " bound) bound)))
    options)

  (defn acquire [self #^ str name]  ; cannot be a defk: borrowing a resource (blocks waiting between threads)
    "Borrows one connection (opens one if none is idle; waits until one is returned if at the limit). If it cannot be opened, the psycopg exception passes through."
    (import psycopg)
    (import psycopg.types.string [TextLoader])
    (.acquire (get self.permits name))
    (try
      (try
        (.get-nowait (get self.idle name))
        (except [Empty]
          ;; The sole owner of prepared statement names on a connection is doeff (see the note on PREPARED): turn off psycopg's automatic prepare
          ;; (prepare_threshold=None). If psycopg held names, its cursor path would prepare the same statement as _pg3_N from the 6th execution on, and on seeing ROLLBACK (or DROP /
          ;; ALTER) it would discard its own record and send DEALLOCATE ALL, which also removes doeff_pq_N. PreparedBook does not know about that, so on that connection
          ;; batched round trips would fail with 26000 until it is closed.
          (setv connection (psycopg.connect (. (get self.databases name) dsn) :autocommit True :prepare-threshold None
                                            #** (.connection-options self name)))
          (.register-loader connection.adapters "json" TextLoader)
          (.register-loader connection.adapters "jsonb" TextLoader)
          (.counted self "opened")
          connection))
      (except [BaseException]
        (.release (get self.permits name))
        (raise))))

  (defn release [self #^ str name connection]  ; cannot be a defk: returning a resource (called from with / finally)
    "Returns a borrowed connection (discard it if broken; discard it if left in pipeline mode, see the header comment; discard it if it carried a value over the threshold,
     CARRIED-LARGE; if in the middle of a transaction, roll back, and discard it if that fails). The reason for discarding is counted (tallies)."
    (import psycopg)
    (try
      (setv dropped
            (cond
              (or connection.closed connection.broken) "broken"
              (!= connection.pgconn.pipeline-status psycopg.pq.PipelineStatus.OFF) "pipeline"
              (in connection CARRIED-LARGE) "large"
              (!= connection.info.transaction-status psycopg.pq.TransactionStatus.IDLE)
                (try
                  (.rollback connection)
                  None
                  (except [psycopg.Error] "rollback"))
              True None))
      (if (is dropped None)
          (.put (get self.idle name) connection)
          (do (.close connection)
              (.counted self dropped)))
      (finally (.release (get self.permits name)))))

  (defn close [self]  ; cannot be a defk: cleanup of a resource, called when the assembling side shuts down
    "Closes all idle connections (connections on loan are not closed when returned, so call this after stopping)."
    (for [idle (.values self.idle)]
      (while (not (.empty idle))
        (.close (.get-nowait idle))))))


(defclass PostgresListener []
  "The listener for one channel (see the header comment): holds, in a daemon thread, one connection with LISTEN established, and rings, among the hung bells (external promises), those whose
   names concern the notification (it does not ring notifications with its own lease's mark). It is a resource, so not a value type. bells = bell -> names waited for (frozenset | None = rings on
   every signal; a dict, which rings in the order hung. With a set the order would be decided by object addresses, and the wake-up order would change on every run) / listening = whether LISTEN is
   currently established / listened = whether it was ever established / failure = the reason it is not established / settled = the mark that the first connection attempt has finished."

  (defn #^ None __init__ [self #^ PostgresConnections connections #^ str name #^ str channel]  ; cannot be a defk: initialization of a resource class
    "Creates the listener and starts the LISTEN thread."
    (setv self.connections connections
          self.name name
          self.channel channel
          self.lock (threading.Lock)
          self.bells {}
          self.listening False
          self.listened False
          self.failure None
          self.settled (threading.Event))
    (.start (threading.Thread :target self.listen :name "doeff-postgres-listen" :daemon True)))

  (defn #^ None ring [self #^ (| (get frozenset str) None) topics]  ; cannot be a defk: called by the listener's thread and by the handler after it has returned the connection (outside the VM)
    "Rings and removes the bells concerning a signal with names topics (see the header comment: topics = None means a signal with unknown names, so all of them; a bell whose waited names are None rings on
     any signal; the others ring only if their names overlap. A rung bell completes with True, to be told apart from the None of a WaitWithin timeout)."
    (with [_ self.lock]
      (setv bells (tuple (gfor [bell waiting] (.items self.bells)
                               :if (or (is topics None) (is waiting None) (not (.isdisjoint waiting topics)))
                               bell)))
      (for [bell bells]
        (.pop self.bells bell)))
    (for [bell bells]
      (.complete bell True)))

  (defn #^ (| str None) hang [self #^ (get ExternalPromise (| bool None)) bell #^ (| (get frozenset str) None) topics]  ; cannot be a defk: runs on a driver thread (waits for the first connection)
    "If LISTEN has ever been established, hangs a bell waiting for names topics (None = every signal) and returns None; if it has never been established, returns the sentence of the reason
     (waits until the first connection attempt has finished). A bell hung in the middle of a reconnect rings when it reconnects (notifications in between are not delivered, so the waiter
     re-reads after being rung)."
    (.wait self.settled)
    (with [_ self.lock]
      (if self.listened
          (do (setv (get self.bells bell) topics) None)
          (or self.failure "LISTEN is not established"))))

  (defn #^ None drop [self #^ (get ExternalPromise (| bool None)) bell]  ; cannot be a defk: called by the handler's clause and the cancellation cleanup (a lock outside the VM)
    "Removes a bell that did not ring and finishes its external promise (if it is already removed, there is nothing to remove). There is no waiter any more (this is called after the wait has ended,
     or when hanging was cancelled), so completing with None wakes no one. Without finishing it, the scheduler's promise row would stay
     pending and fall outside the cleanup that removes only finished ones (#3508, #3494). A second completion of a bell that already rang and completed is
     ignored by the scheduler."
    (with [_ self.lock]
      (.pop self.bells bell None))
    (.complete bell None))

  (defn #^ None listen [self]  ; cannot be a defk: the body of the daemon thread (blocks waiting for notifications)
    "Establishes LISTEN and, for each notification, rings the bells whose names concern it (notifications with its own lease's mark are not rung; same-process bells were already rung by the
     handler that sent the signal), and when the connection breaks, waits LISTEN-RETRY-SECONDS and reconnects (it also rings when it establishes; see the header comment)."
    (import psycopg)
    (import psycopg [sql])
    (while True
      (try
        (with [connection (psycopg.connect (. (get self.connections.databases self.name) dsn) :autocommit True
                                           #** (.connection-options self.connections self.name))]
          (.execute connection (.format (sql.SQL "LISTEN {}") (sql.Identifier self.channel)))
          ;; What rings on establishing is only the bells hung at the instant of establishing (waiters that missed notifications during the reconnect): take a copy inside the lock,
          ;; then set settled. If settled were set first and ring done afterward, the bell that a hang waiting for the first connection had just hung
          ;; would be rung without any signal (it would ring after the rollback; the daily test_pg_notice went red depending on timing).
          (with [_ self.lock]
            (setv self.listening True
                  self.listened True
                  self.failure None)
            (setv pending (tuple self.bells))
            (.clear self.bells))
          (for [bell pending]
            (.complete bell True))
          (.set self.settled)
          (for [notice (.notifies connection)]
            (setv heard (run-detached (heard-notice notice.payload)))
            (when (!= heard.origin self.connections.origin)
              (.ring self (if (is heard.topics None) None (frozenset heard.topics))))))
        (except [error psycopg.Error]
          (with [_ self.lock]
            (setv self.listening False
                  self.failure (str error)))
          (.set self.settled)
          (.ring self None)
          (time.sleep LISTEN-RETRY-SECONDS))))))


(defk postgres-statement [statement params]
  {:pre [(: statement str) (: params tuple)] :post [(: % PostgresStatement)]
   :tags {:context "sql" :role "foundation"}}
  "Rewrites a statement in neutral notation into a psycopg `%(name)s` statement (`%` in the statement becomes `%%`; a mismatch in argument names is a ValueError)."
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (var text "")
  (for [part parts]
    (match part
      (SqlText :text piece) (:= text (+ text (.replace piece "%" "%%")))
      (SqlPlaceholder :name name) (:= text (+ text (.format "%({})s" name)))))
  (PostgresStatement :text text :params params))


(defk postgres-insert-statement [table columns]
  {:pre [(: table str) (: columns tuple)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "Renders the executemany statement (positional `%s`) for SqlInsertRows."
  (.format "INSERT INTO {} ({}) VALUES ({})" (! (checked-identifier table)) (.join ", " (! (checked-identifiers columns)))
           (.join ", " (* ["%s"] (len columns)))))


(defk postgres-failure [sqlstate class-names message]
  {:pre [(: sqlstate (| str None)) (: class-names tuple) (: message str)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Maps a psycopg exception (SQLSTATE, the sequence of class names = names in the MRO, and the message) to a failure value (see the header comment). The one point of
   distinguishing that postgres-sql-handler and pooled-postgres-sql-handler both use."
  (cond
    (and (is-not sqlstate None) (or (.startswith sqlstate UNREACHABLE-SQLSTATE-CLASS) (in sqlstate UNREACHABLE-SQLSTATES)))
      (SqlUnreachable :reason message)
    (is-not sqlstate None) (SqlFailed :sqlstate sqlstate :reason message)
    (any (gfor name class-names (in name UNREACHABLE-CLASSES))) (SqlUnreachable :reason message)
    True (SqlFailed :sqlstate (next (gfor name class-names :if (in name DRIVER-CLASS-SQLSTATES) (get DRIVER-CLASS-SQLSTATES name)) None)
                    :reason message)))


(defk postgres-schema-statements [tables]
  {:pre [(: tables tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Renders table declarations as PostgreSQL DDL (CREATE TABLE / INDEX IF NOT EXISTS)."
  (var statements [])
  (for [table tables]
    (val table-name (! (checked-identifier table.name)))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}{}" (! (checked-identifier column.name)) (get POSTGRES-TYPES column.type)
                                    (if column.nullable "" " NOT NULL"))))
    (when table.primary-key
      (.append definitions (.format "PRIMARY KEY ({})" (.join ", " (! (checked-identifiers table.primary-key))))))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({})" table-name (.join ", " definitions)))
    (for [index table.indexes]
      (.append statements (.format "CREATE {}INDEX IF NOT EXISTS {} ON {} ({})" (if index.unique "UNIQUE " "") (! (checked-identifier index.name))
                                   table-name (.join ", " (! (checked-identifiers index.columns)))))))
  (tuple statements))


(defk postgres-error [error]
  {:pre [(: error Exception)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Splits a psycopg exception into the inputs of postgres-failure."
  (<- failure (postgres-failure (getattr error "sqlstate" None) (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
  failure)


;; cannot be a defk: a pure function called once per row value (returning a Program would round-trip through the VM for every value)
(defn #^ int cell-bytes [#^ object value #^ bool doubled]
  "Estimates the bytes one row value takes in a libpq message (str = UTF-8 length; bytes = length, or doubled as hex in text form if doubled;
   other types are not counted)."
  (match value
    (str) (if (.isascii value) (len value) (len (.encode value "utf-8")))
    (bytes) (if doubled (* 2 (len value)) (len value))
    _ 0))


(defk largest-message-bytes [rows doubled]
  {:pre [(: rows tuple) (: doubled bool)] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "Estimates the bytes of the largest message among the sequence of rows rows (one row = one libpq message: a set of sent arguments, or a received row)
   (compared with LARGE-CARRY-BYTES; the buffer grows to the largest message. doubled = bytes were received as hex in text form)."
  (max (gfor row rows (sum (gfor value row (cell-bytes value doubled)))) :default 0))


(defk noted-carry [connection carried]
  {:pre [(: connection "a psycopg connection") (: carried int)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "If the bytes carried of a message the connection passed through exceed the threshold (LARGE-CARRY-BYTES), marks the connection to be closed on return (CARRIED-LARGE)."
  (when (>= carried LARGE-CARRY-BYTES)
    (.add CARRIED-LARGE connection))
  None)


(defk postgres-run [connection text params binary]
  {:pre [(: connection "a psycopg connection") (: text str) (: params (| tuple None)) (: binary bool)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Runs one statement and turns the answer into a value (params = None is a statement that skips argument rewriting, i.e. DDL and transaction delimiters; binary = receive the answer rows in
   binary form, SqlQuery's binary). If the sent arguments or the received rows exceed the threshold, marks the connection to be closed on return (noted-carry; the send is
   marked before running: even a failed statement has grown the buffer). connection is a psycopg connection (the type is not written in the
   contract so that the module can be imported even in an environment without psycopg)."
  (import psycopg)
  (when (is-not params None)
    (<- sent (largest-message-bytes #((tuple (gfor p params p.value))) False))
    (<- (noted-carry connection sent)))
  (try
    (with [cursor (.cursor connection)]
      (.execute cursor text (if (is params None) None (dfor p params p.name p.value)) :binary binary)
      (if (is cursor.description None)
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
          (do (val rows (! (normalized-rows (.fetchall cursor))))
              (<- received (largest-message-bytes rows (not binary)))
              (<- (noted-carry connection received))
              (SqlRows :rows rows :rowcount (len rows)))))
    (except [error psycopg.Error]
      (<- failure (postgres-error error))
      failure)))


(defk postgres-query [connection request]
  {:pre [(: connection "a psycopg connection") (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Answers one SqlQuery."
  (<- bound (postgres-statement request.statement request.params))
  (<- answer (postgres-run connection bound.text bound.params request.binary))
  answer)


(defk postgres-insert [connection request]
  {:pre [(: connection "a psycopg connection") (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Answers one SqlInsertRows with executemany (one row = one sent message; if the largest row exceeds the threshold, the connection is marked to be closed on return)."
  (import psycopg)
  (<- (checked-rows request.columns request.rows))
  (<- text (postgres-insert-statement request.table request.columns))
  (<- sent (largest-message-bytes request.rows False))
  (<- (noted-carry connection sent))
  (if (not request.rows)
      (SqlRows :rows #() :rowcount 0)
      (try
        (with [cursor (.cursor connection)]
          (.executemany cursor text (list request.rows))
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None)))
        (except [error psycopg.Error]
          (<- failure (postgres-error error))
          failure))))


(defk postgres-ensure-tables [connection tables]
  {:pre [(: connection "a psycopg connection") (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Renders table declarations as DDL and runs them in order (stops at the first failure)."
  (<- statements (postgres-schema-statements tables))
  (for [statement statements]
    (<- answer (postgres-run connection statement None False))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


;; The statements of the lock that serializes transactions with the same key (neutral notation); run after BEGIN in a transaction's first round trip. The shared lock (SqlLock's shared) is
;; LOCK-SHARED-STATEMENT (shared locks can be held side by side and wait on each other only with an exclusive lock of the same key).
(val LOCK-STATEMENT "SELECT pg_advisory_xact_lock(hashtext(:key))")
(val LOCK-SHARED-STATEMENT "SELECT pg_advisory_xact_lock_shared(hashtext(:key))")


(defk transaction-locks [lock-key locks]
  {:pre [(: lock-key (| str None)) (: locks tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Turns SqlTransaction's locks (lock-key and locks; see the header comment of sql_effects.hy) into a sequence of SqlLock in the order they are taken (lock-key first, exclusive)."
  (+ (if (is lock-key None) #() #((SqlLock :key lock-key))) locks))


(defk lock-text [lock]
  {:pre [(: lock SqlLock)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "Chooses the spelling of the statement that takes one lock (neutral notation; argument :key = the lock's key; a shared lock uses LOCK-SHARED-STATEMENT)."
  (if lock.shared LOCK-SHARED-STATEMENT LOCK-STATEMENT))


;; --- Signals (see the header comment) -----------------------------------------------------------------------------------------------

;; The statement that sends a signal to a channel (neutral notation). The handler runs it with the same postgres-query as other statements (inside a transaction it is delivered only on commit).
;; The body is notice-payload.
(val NOTICE-STATEMENT "SELECT pg_notify(:channel, :payload)")


(defwire NoticeWire
  "The JSON object of the signal body (NOTIFY's payload; see the header comment): origin = the mark of the lease that emitted the signal and rings same-process bells (None = there is no
   lease that rings); topics = the sequence of names the signal concerns (in name order, without duplicates; None = a signal with unknown names = concerns all waiters)."
  {:tags {:context "sql" :role "type" :reads "json"} :names :camel :unknown :reject}
  (setv #^ (| str None) origin None)
  (setv #^ (| (get tuple #(str ...)) None) topics None))


(defk notice-payload [origin topics]
  {:pre [(: origin (| str None)) (: topics (| tuple None))] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "Spells the signal body (NOTIFY's payload; NoticeWire's JSON) (see the header comment). A write whose body would reach NOTICE-PAYLOAD-LIMIT bytes if the names were carried
   (a write touching many names) does not carry the names and becomes a signal concerning all (a signal that cannot be narrowed means waking everyone, so no one is missed)."
  (<- named str (dump-json (NoticeWire :origin origin :topics (if (is topics None) None (tuple (sorted (set topics)))))))
  (<- marked str (dump-json (NoticeWire :origin origin)))
  (if (< (len (.encode named "utf-8")) NOTICE-PAYLOAD-LIMIT) named marked))


(defk heard-notice [payload]
  {:pre [(: payload str)] :post [(: % NoticeWire)]
   :tags {:context "sql" :role "foundation"}}
  "Reads the body of a signal the listener received. A body not in NoticeWire's form (empty, not JSON, wrong field shape; a writer without a mark, or a hand-sent NOTIFY) is read as
   a signal with no mark and unknown names = a signal concerning all waiters (the definition in the header comment)."
  (<- heard (| NoticeWire Malformed) (parse-json NoticeWire payload))
  (match heard
    (NoticeWire) heard
    (Malformed) (NoticeWire)))


(defk notice-params [origin notify]
  {:pre [(: origin (| str None)) (: notify SqlNotify)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Builds the arguments of the statement (NOTICE-STATEMENT) that sends signal notify (the body is the mark origin and the signal's names; notice-payload)."
  (<- payload (notice-payload origin notify.topics))
  #((SqlParam :name "channel" :value notify.channel) (SqlParam :name "payload" :value payload)))


(defk postgres-notice [connection database origin notify]
  {:pre [(: connection PipelineConnection) (: database str) (: origin (| str None)) (: notify SqlNotify)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Sends signal notify on the connection (inside a transaction it is delivered only on commit; the mark origin is the mark of the lease that rings same-process bells after the signal;
   None = there is no lease that rings)."
  (<- params (notice-params origin notify))
  (<- answer (postgres-query connection (SqlQuery database NOTICE-STATEMENT params)))
  answer)


(defk noted-notice [raised notify]
  {:pre [(: raised RaisedNotices) (: notify SqlNotify)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "Adds signal notify to the record raised (same-process bells are rung after the connection is returned; names on the same channel are unioned, and if either is a
   signal with unknown names, None, the result is None)."
  (val held (.get raised.topics notify.channel (frozenset)))
  (setv (get raised.topics notify.channel)
        (if (or (is held None) (is notify.topics None)) None (| held (frozenset notify.topics))))
  None)


(defrecord PostgresStep
  "One statement to pile into the pipeline (the order within a postgres-flush round trip): text = the statement handed to psycopg / params = named arguments (None = a statement that skips
   argument rewriting, i.e. BEGIN and COMMIT) / rows = rows to run with executemany (None = a single statement) / binary = receive the answer rows in binary form (SqlQuery's
   binary) / answered = whether to put its answer in the round trip's answers (the answers for requests) (BEGIN, locks, signals, and COMMIT are not put in)."
  (#^ str text)
  (#^ (| (get tuple #(SqlParam ...)) None) params)
  (#^ (| (get tuple #((get tuple #((| int float str bytes bool None) ...)) ...)) None) rows)
  (#^ bool binary)
  (#^ bool answered))


(defk step-sent-bytes [step]
  {:pre [(: step (| PostgresStep None))] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "Estimates the bytes of the largest message that one statement piled into the pipeline sends (one set of arguments, or one executemany row; passed to noted-carry)."
  (cond
    (is step None) 0
    (is-not step.rows None) (! (largest-message-bytes step.rows False))
    (is-not step.params None) (! (largest-message-bytes #((tuple (gfor p step.params p.value))) False))
    True 0))


(defk request-step [request]
  {:pre [(: request (| SqlQuery SqlInsertRows))] :post [(: % (| PostgresStep None))]
   :tags {:context "sql" :role "foundation"}}
  "Turns one statement in a transaction (SqlQuery | SqlInsertRows) into a pipeline statement (arguments and rows are checked before sending; mistakes are ValueError / TypeError).
   A SqlInsertRows with no rows sends no statement, so it is None (its answer is a SqlRows with 0 rows)."
  (match request
    (SqlQuery :statement statement :params params :binary binary)
      (do (<- bound (postgres-statement statement params))
          (PostgresStep :text bound.text :params bound.params :rows None :binary binary :answered True))
    (SqlInsertRows :table table :columns columns :rows rows)
      (do (<- (checked-rows columns rows))
          (<- text (postgres-insert-statement table columns))
          (if rows (PostgresStep :text text :params None :rows rows :binary False :answered True) None))))


(defk flush-steps [locks origin flush]
  {:pre [(: locks tuple) (: origin (| str None)) (: flush TransactionFlush)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Turns one transaction round trip (TransactionFlush) into the sequence of statements piled into the pipeline (order = BEGIN, locks [in the order of locks; transaction-locks], statements, signals,
   COMMIT). Positions in requests match the order of the answers, and a request that sends no statement (a SqlInsertRows with no rows) stays in place as None. The signal body carries the lease's
   mark origin (see the header comment; None = there is no lease that rings same-process bells after the signal)."
  (var opening #())
  (when flush.opening
    (:= opening #((PostgresStep :text "BEGIN" :params None :rows None :binary False :answered False)))
    (for [claim locks]
      (<- text (lock-text claim))
      (<- lock (postgres-statement text #((SqlParam :name "key" :value claim.key))))
      (:= opening (+ opening #((PostgresStep :text lock.text :params lock.params :rows None :binary False :answered False))))))
  (var requests #())
  (for [request flush.requests]
    (<- step (request-step request))
    (:= requests (+ requests #(step))))
  (var notices #())
  (for [notify flush.notices]
    (<- params (notice-params origin notify))
    (<- notice (postgres-statement NOTICE-STATEMENT params))
    (:= notices (+ notices #((PostgresStep :text notice.text :params notice.params :rows None :binary False :answered False)))))
  (+ opening requests notices
     (if flush.closing #((PostgresStep :text "COMMIT" :params None :rows None :binary False :answered False)) #())))


(defclass [runtime-checkable] StatementCursor [Protocol]
  "The shape of the driver cursor that receives the answer to one statement piled into the pipeline (psycopg 3's Cursor has this shape; psycopg is imported only inside this module's I/O functions,
   so the contract is written with this shape rather than psycopg's type). description = the sequence of columns of a statement that returns rows (None for one that does not) / rowcount = the number of rows
   the engine counted / fetchall = reads the rows / close = closes / executemany = piles the same statement per row."
  #^ (| tuple list None) description
  #^ int rowcount
  #^ (get Callable #([] list)) fetchall
  #^ (get Callable #([] None)) close
  #^ (get Callable #([str list] None)) executemany)


(defclass [runtime-checkable] PipelineConnection [Protocol]
  "The shape of the driver connection that sends a transaction's round trip in one pipeline (psycopg 3's Connection has this shape; for the same reason as StatementCursor,
   psycopg's type is not written in the contract). pipeline = entering and leaving pipeline mode (with) / execute = piles one statement and returns a cursor / cursor = creates a cursor that piles
   executemany."
  #^ (get Callable #([] AbstractContextManager)) pipeline
  #^ (get Callable #([str (| dict None)] StatementCursor)) execute
  #^ (get Callable #([] StatementCursor)) cursor)


(defk step-queries [transformer step]
  {:pre [(: transformer "a psycopg Transformer") (: step (| PostgresStep None))] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Turns one statement piled into the pipeline into a sequence of the forms sent to libpq (psycopg's PostgresQuery: statement bytes, argument bytes, types, formats) (one statement gives one;
   an executemany statement gives one per row; a request that sends no statement gives 0). Argument rewriting and value mapping use the same things as psycopg's cursor execute
   (the same statement and values give the same messages reaching the server as the cursor path)."
  (import psycopg._queries [PostgresQuery])
  (when (is step None)
    (return #()))
  (val values (if (is step.rows None)
                  #((if (is step.params None) None (dfor p step.params p.name p.value)))
                  step.rows))
  (var queries #())
  (for [one values]
    (val query (PostgresQuery transformer))
    (.convert query step.text one)
    (:= queries (+ queries #(query))))
  queries)


(defclass [(dataclass :kw-only True)] PreparedBook []
  "Record of statements named on one connection (see the note on postgres-flush): names = (statement bytes, sequence of argument type oids) -> the name on the server / issued = the number
   of the next name to assign. Only the driver thread that borrowed the connection rewrites it, so it is not a value type."
  (setv #^ dict names (field :default-factory dict))
  (setv #^ int issued 0))


(defk prepared-book [connection]
  {:pre [(: connection "a psycopg connection")] :post [(: % PreparedBook)]
   :tags {:context "sql" :role "foundation"}}
  "Looks up the connection's record of named statements (creates an empty one if absent; the record table is shared between threads, so this is done inside a lock)."
  (with [_ PREPARED-LOCK]
    (when (not-in connection PREPARED)
      (setv (get PREPARED connection) (PreparedBook)))
    (get PREPARED connection)))


(defk socket-ready [pgconn events]
  {:pre [(: pgconn "a psycopg PGconn") (: events int)] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "Waits on a driver thread until the connection's socket becomes any of events (select's POLLIN | POLLOUT) and returns the events that occurred (no limit is held here:
   a dead peer is turned into a socket error by the connection limits tcp_user_timeout and keepalive, and the statement limit is held by the server's statement_timeout; see the header comment)."
  (import select)
  (val poller (select.poll))
  (.register poller pgconn.socket events)
  (var happened 0)
  (while (not happened)
    (:= happened (sum (gfor #(_ seen) (.poll poller) seen))))
  happened)


(defk libpq-sent [pgconn]
  {:pre [(: pgconn "a psycopg PGconn")] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "Sends all the statements piled in libpq's send buffer to the socket (while it cannot send, waits until it is writable or readable, and when readable receives first so that the server's
   sends do not get stuck; this is the manner of libpq's PQflush)."
  (import select)
  (while (.flush pgconn)
    (<- happened (socket-ready pgconn (| select.POLLIN select.POLLOUT)))
    (when (& happened select.POLLIN)
      (.consume-input pgconn)))
  None)


(defk libpq-results [pgconn]
  {:pre [(: pgconn "a psycopg PGconn")] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "Receives the answers up to the pipeline's sync (a PGresult per statement, in the order sent; the sync mark is not included) (waits until readable before receiving)."
  (import select)
  (import psycopg [pq])
  (var results #())
  (while True
    (while (.is-busy pgconn)
      (<- (socket-ready pgconn select.POLLIN))
      (.consume-input pgconn))
    (val result (.get-result pgconn))
    (cond
      (is result None) None
      (= result.status pq.ExecStatus.PIPELINE-SYNC) (break)
      True (:= results (+ results #(result)))))
  results)


(defk result-failure [connection results]
  {:pre [(: connection "a psycopg connection") (: results tuple)] :post [(: % (| Exception None))]
   :tags {:context "sql" :role "foundation"}}
  "Turns, among the round trip's answers, the failure of the first failed statement into a psycopg exception (statements after the failed one are PIPELINE_ABORTED until sync; None if none failed)."
  (import psycopg [pq errors])
  (val failed (next (gfor result results :if (= result.status pq.ExecStatus.FATAL-ERROR) result) None))
  (cond
    (is-not failed None) (errors.error-from-result failed :encoding connection.info.encoding)
    (any (gfor result results (= result.status pq.ExecStatus.PIPELINE-ABORTED)))
      (errors.PipelineAborted "the pipeline's statements were not run (there is no answer for the failed statement)")
    True None))


(defk results-answer [transformer step results]
  {:pre [(: transformer "a psycopg Transformer") (: step (| PostgresStep None)) (: results tuple)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "foundation"}}
  "After the pipeline's sync, turns the answer of one statement (a sequence of PGresult; executemany gives one per row) into SqlRows (a request that sent no statement gives 0 rows;
   a statement that returns no rows gives the number of rows the engine counted; for executemany, the sum)."
  (import psycopg [pq])
  (cond
    (not results) (SqlRows :rows #() :rowcount 0)
    (!= (. (get results 0) status) pq.ExecStatus.TUPLES-OK)
      (do (val counts (tuple (gfor result results result.command-tuples)))
          (SqlRows :rows #() :rowcount (if (any (gfor count counts (is count None))) None (sum counts))))
    True (do (val result (get results 0))
             (.set-pgresult transformer result)
             (val rows (! (normalized-rows (tuple (.load-rows transformer 0 result.ntuples tuple)))))
             (SqlRows :rows rows :rowcount (len rows)))))


(defk postgres-flush [connection locks origin flush]
  {:pre [(: connection PipelineConnection) (: locks tuple) (: origin (| str None)) (: flush TransactionFlush)]
   :post [(: % (| tuple SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Sends one transaction round trip (TransactionFlush) in a single pipeline and receives the answers in order (see the header comment). Answer = a tuple of SqlRows in the same order as requests
   | the failure of the first failed statement (including BEGIN, locks, signals, and COMMIT; PostgreSQL does not run from the failed statement through sync). Since this is driver I/O, it runs on a
   driver thread. locks = the sequence of locks taken after BEGIN in the first round trip / origin = the lease mark carried in the signal body (flush-steps).
   The statements do not go through psycopg's cursor: they are all piled into the pipeline-mode send buffer through libpq's interface (the connection's pgconn), one sync is piled, then
   everything is sent at once and the answers up to sync are received in order (the cursor pipeline creates, per statement, a cursor, a mapping set, and socket sends and
   waits; in production's free-threaded Python psycopg [the pure-python libpq interface with no C extension] it used about 0.25 ms of CPU per statement, and
   the DB woke for each statement that arrived and waited for the next [ClientRead inside the pipeline]. In a local 8-statement round trip, client CPU went from 2.09 to 0.62 ms).
   Statements are reused as named prepared statements per connection (PREPARED: a statement seen for the first time is prepared in the same round trip and then run. Without names the server
   builds a plan every time, taking about 0.4 ms per row-locking read on a table with 76 indexes. The cursor path does not name statements: psycopg's automatic prepare is turned off).
   Argument rewriting, value mapping, and reading of answer values use the same psycopg parts as the cursor (step-queries and results-answer). Pipeline mode is left after the answers are
   received up to sync (even for a failed round trip; a connection that broke during sending or receiving and so remained unable to leave is discarded on return)."
  (import psycopg)
  (import psycopg.adapt [Transformer])
  (<- steps (flush-steps locks origin flush))
  ;; Mark the send before piling it (even a failed round trip has grown the buffer, as in postgres-run).
  (for [step steps]
    (<- sent (step-sent-bytes step))
    (<- (noted-carry connection sent)))
  (val pgconn connection.pgconn)
  (val transformer (Transformer connection))
  (<- book (prepared-book connection))
  ;; Statements newly named in this round trip (added to the record only if the round trip passed; names from a failed round trip remain on the server, but names are numbered and do not collide).
  (var fresh {})
  ;; The number of answers per statement (a statement in a round trip where it was named has two answers: the prepare's and the run's), in step order and, within a step, in statement order.
  (var spans #())
  (var counts #())
  (var name None)
  (var made False)
  (try
    (with [_ connection.lock]
      (var queued #())
      (for [step steps]
        (<- queries (step-queries transformer step))
        (:= queued (+ queued #(queries))))
      (.enter-pipeline-mode pgconn)
      (for [#(step queries) (zip steps queued :strict True)]
        (:= counts #())
        (for [query queries]
          (val shape #(query.query (tuple (or query.types #()))))
          (:= name (or (.get book.names shape) (.get fresh shape)))
          (:= made False)
          (when (and (is name None) (< (+ (len book.names) (len fresh)) PREPARED-MAX))
            (:= name (.encode (.format "doeff_pq_{}" book.issued) "ascii"))
            (setv book.issued (+ book.issued 1))
            (setv (get fresh shape) name)
            (.send-prepare pgconn name query.query :param-types query.types)
            (:= made True))
          (val shaped (if step.binary psycopg.pq.Format.BINARY psycopg.pq.Format.TEXT))
          (if (is name None)
              (.send-query-params pgconn query.query query.params :param-types query.types :param-formats query.formats
                                  :result-format shaped)
              (.send-query-prepared pgconn name query.params :param-formats query.formats :result-format shaped))
          (:= counts (+ counts #((if made 2 1)))))
        (:= spans (+ spans #(counts))))
      (.pipeline-sync pgconn)
      (<- (libpq-sent pgconn))
      (<- results (libpq-results pgconn))
      (.exit-pipeline-mode pgconn))
    (except [error psycopg.Error]
      (<- failure (postgres-error error))
      (return failure)))
  (<- failed (result-failure connection results))
  (when (is-not failed None)
    (<- failure (postgres-error failed))
    (return failure))
  (.update book.names fresh)
  (var answers #())
  (var at 0)
  (var mine #())
  (for [#(step step-counts) (zip steps spans :strict True)]
    ;; For each statement, take the last of its answers (for a newly named statement, the one after the prepare's answer).
    (:= mine #())
    (for [count step-counts]
      (:= mine (+ mine #((get results (+ at count -1)))))
      (:= at (+ at count)))
    (when (or (is step None) step.answered)
      (<- answer (results-answer transformer step mine))
      (<- received (largest-message-bytes answer.rows (not (and (is-not step None) step.binary))))
      (<- (noted-carry connection received))
      (:= answers (+ answers #(answer)))))
  answers)


(defk postgres-begin [connection locks]
  {:pre [(: connection PipelineConnection) (: locks tuple)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "Starts a transaction and takes the sequence of locks locks (transaction-locks) in order (success is None; the default procedure's BEGIN and one round trip per lock)."
  (<- began (postgres-run connection "BEGIN" None False))
  (when (not (isinstance began SqlRows))
    (return began))
  (for [claim locks]
    (<- text (lock-text claim))
    (<- locked (postgres-query connection (SqlQuery "" text #((SqlParam :name "key" :value claim.key)))))
    (when (not (isinstance locked SqlRows))
      (return locked)))
  None)


(defk postgres-control [connection statement]
  {:pre [(: connection "a psycopg connection") (: statement str)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "Sends a transaction delimiter statement (COMMIT or ROLLBACK) (success is None)."
  (<- answer (postgres-run connection statement None False))
  (if (isinstance answer SqlRows) None answer))


(defk postgres-lease [connections database]
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "a psycopg connection | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Borrows one connection (SqlUnreachable if it cannot be opened). psycopg is imported only for distinguishing when the borrow ends in an error; the driver is opened by the lease's
   acquire, so the path where the borrow succeeds can be exercised with a fake lease."
  (try
    (.acquire connections database)
    (except [error Exception]
      (import psycopg)
      (match error
        (psycopg.Error) (SqlUnreachable :reason (str error))
        _ (raise)))))


(deff lease-now [connections database]  ; cannot be a defk: the entry run on a driver thread (outside the VM)
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "a psycopg connection | SqlUnreachable")]}
  "Borrows one connection on a driver thread (the wait for a permit is also on this thread; SqlUnreachable if it cannot be opened)."
  (run-detached (postgres-lease connections database)))


(deff lease-run-return [connections database claim work]  ; cannot be a defk: the entry run on a driver thread
  {:pre [(: connections PostgresConnections) (: database str) (: claim "threading.Lock") (: work "a callable (connection) -> Program")]
   :post [(: % "the answer of work | SqlUnreachable | None")]}
  "In a single driver-thread job, borrows a connection, runs work (connection -> Program) on the borrowed connection, and always returns it (the answer of a single-statement effect;
   see the note on offloaded-statement). SqlUnreachable if it cannot be borrowed. It runs only if claim could be taken first after borrowing; if it could not, the waiter was cancelled before the statement
   started, so it returns immediately without running the statement (the answer reaches no one, so None)."
  (setv leased (lease-now connections database))
  (cond
    (isinstance leased SqlUnreachable) leased
    (not (.acquire claim :blocking False)) (do (.release connections database leased) None)
    True (try
           (run-detached (work leased))
           (finally (.release connections database leased)))))


(defclass [(dataclass :kw-only True)] TransactionLease []
  "Record of the connection one transaction borrowed (see the note on offloaded-transaction; so that the job of borrowing the connection is the same job as the first driver call, and the job of returning is the same
   job as the last call). Only the handler's procedure and the driver thread that holds the lock guard rewrite it, so it is not a value type. connection = the borrowed connection |
   SqlUnreachable (could not be borrowed) | None (not yet borrowed) / submitted = at least one driver call was fired / returned = the connection was returned
   (also set when it could not be borrowed, as the mark that it will not borrow again) / abandoned = the waiter left (calls fired after this are not run)."
  (setv #^ (| PipelineConnection SqlUnreachable None) connection None)
  (setv #^ bool submitted False)
  (setv #^ bool returned False)
  (setv #^ bool abandoned False))


(defrecord TransactionDriver
  "The tools for one transaction's driver calls (see the note on offloaded-transaction): connections = the connection lease / pool = the driver threads / database = the name /
   lease = the record of the borrowed connection / guard = the lock that runs driver calls on one connection one at a time."
  (#^ PostgresConnections connections)
  (#^ Executor pool)
  (#^ str database)
  (#^ TransactionLease lease)
  (#^ AbstractContextManager guard))


(deff drive-leased [driver work closing]  ; cannot be a defk: the entry run on a driver thread (outside the VM)
  {:pre [(: driver TransactionDriver) (: work (get Callable #([PipelineConnection] Program))) (: closing bool)]
   :post [(: % "the answer of work | SqlUnreachable | None")]}
  "In a single driver-thread job, under the lock guard, borrows a connection if not yet borrowed, runs work (connection -> Program), and, if closing (the last call, which sends COMMIT or
   ROLLBACK), returns the connection in the same job (see the note on offloaded-transaction). Calls after the waiter left or after the return are not run (None; the answer reaches
   no one). A call after a failed borrow is not run and returns SqlUnreachable."
  (setv lease driver.lease)
  (with [_ driver.guard]
    (when (or lease.abandoned lease.returned)
      (return None))
    (when (is lease.connection None)
      (setv lease.connection (lease-now driver.connections driver.database)))
    (setv held lease.connection)
    (when (isinstance held SqlUnreachable)
      (when closing
        (setv lease.returned True))
      (return held))
    (try
      (run-detached (work held))
      (finally
        (when closing
          (.release driver.connections driver.database held)
          (setv lease.returned True))))))


(deff abandon-lease [driver]  ; cannot be a defk: the entry for cleanup run on a driver thread
  {:pre [(: driver TransactionDriver)] :post [(: % "None")]}
  "Returns a connection that the last call did not return (under the lock guard: after any running call; see the note on offloaded-transaction). It sets the mark abandoned, so calls that have not
   started yet finish without running (a call before borrowing the connection does not even borrow)."
  (setv lease driver.lease)
  (with [_ driver.guard]
    (setv lease.abandoned True)
    (setv held lease.connection)
    (when (and (not lease.returned) (is-not held None) (not (isinstance held SqlUnreachable)))
      (.release driver.connections driver.database held))
    (setv lease.returned True)))


(defk driven [driver work closing]
  {:pre [(: driver TransactionDriver) (: work Callable) (: closing bool)] :post [(: % "the answer of work | SqlUnreachable | None")]
   :tags {:context "sql" :role "foundation"}}
  "Runs one driver call on a transaction's connection as one job on a pool thread (borrow if not yet; run; and, if closing, return; drive-leased), and only the task that fired it
   waits (see the note on offloaded-transaction)."
  (setv driver.lease.submitted True)
  (<- answer (offloaded driver.pool (fn [] (drive-leased driver work closing)) keep-nothing))
  answer)


(defclass TransactionAbandoned [Exception]
  "The mark that the waiter left while a batched transaction was running on a driver thread (the next round trip is not sent and it falls to ROLLBACK; offloaded-batched-transaction).")


(defk leased-here [driver]
  {:pre [(: driver TransactionDriver)] :post [(: % (| PipelineConnection SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Inside a batched transaction's driver thread, borrows one connection if not yet borrowed (at the time of the first round trip: a program that sends no statement
   does not borrow; SqlUnreachable if it cannot be opened)."
  (when (is driver.lease.connection None)
    (<- leased (postgres-lease driver.connections driver.database))
    (setv driver.lease.connection leased))
  driver.lease.connection)


(defk flushed-here [driver locks origin raised flush]
  {:pre [(: driver TransactionDriver) (: locks tuple) (: origin str) (: raised RaisedNotices) (: flush TransactionFlush)]
   :post [(: % (| tuple SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Sends one round trip of a batched transaction on the spot inside the driver thread (see the note on offloaded-batched-transaction): if the waiter has left, raises
   TransactionAbandoned without sending (falling to ROLLBACK), records the signals carried in the round trip, and, for a round trip carrying COMMIT, sets the mark 'sent far enough to be delivered' before sending."
  (when driver.lease.abandoned
    (raise (TransactionAbandoned "the waiter left, so the next round trip is not sent")))
  (for [notify flush.notices]
    (<- (noted-notice raised notify)))
  (<- held (leased-here driver))
  (when (isinstance held SqlUnreachable)
    (return held))
  (when flush.closing
    (setv raised.sent True))
  (<- answer (postgres-flush held locks origin flush))
  answer)


(defk rolled-back-here [driver]
  {:pre [(: driver TransactionDriver)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "Sends a batched transaction's ROLLBACK on the borrowed connection inside the driver thread (if not borrowed or could not be borrowed, there is nothing to send)."
  (val held driver.lease.connection)
  (when (or (is held None) (isinstance held SqlUnreachable))
    (return None))
  (<- answer (postgres-control held "ROLLBACK"))
  answer)


(deff run-batched-here [driver locks origin raised program]  ; cannot be a defk: the entry run on a driver thread (starts a new VM from outside the VM)
  {:pre [(: driver TransactionDriver) (: locks tuple) (: origin str) (: raised RaisedNotices) (: program Program)]
   :post [(: % "the answer of program | SqlFailed | SqlUnreachable | None")]}
  "In a single driver-thread job, under the lock guard, runs the whole of a batched transaction (the program and round trips, and ROLLBACK if needed) on a new VM, and returns the borrowed
   connection in the same job (see the note on offloaded-batched-transaction). If the waiter left before it started, does nothing (None; the answer reaches no one)."
  (setv lease driver.lease)
  (with [_ driver.guard]
    (when (or lease.abandoned lease.returned)
      (return None))
    (try
      (run-detached (run-in-batched-transaction driver.database program
                                                (fn [flush] (flushed-here driver locks origin raised flush))
                                                (fn [] (rolled-back-here driver))
                                                :accepts-notices True))
      (finally
        (setv held lease.connection)
        (when (and (is-not held None) (not (isinstance held SqlUnreachable)))
          (.release driver.connections driver.database held))
        (setv lease.returned True)))))


(deff abandoned-now [driver]  ; cannot be a defk: the entry for the stopping method run on an Executor thread (outside the VM)
  {:pre [(: driver TransactionDriver)] :post [(: % "None")]}
  "Sets the mark that the waiter left on a running batched transaction (does not take the lock guard: the running job holds the guard and reads the mark before
   the next round trip)."
  (setv driver.lease.abandoned True)
  None)


(defk offloaded-batched-transaction [connections pool database program locks]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: locks tuple)]
   :post [(: % "the answer of program | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Runs a transaction with batched = True as a single driver-thread job (borrow -> program and round trips -> return; run-batched-here), and only the task that fired it
   waits. It does not return to the scheduler's thread between round trips: the program of a batched transaction can emit only SQL effects and pure computation
   (the scope of sql_transaction.hy; other effects are TransactionMisused), so running the program on a new VM on the driver thread does not change the answer.
   If it returned to the scheduler on every round trip, the time from the answer of the first round trip to firing the second (the wait in the scheduler's queue plus dispatch) would also hold the store's write lock,
   and the more crowded the scheduler, the longer the lock would be held, and writes waiting for the same lock piled up (PutRows in agora-redesign's record service).
   Cancellation: once the waiter has left, before the next round trip it reads the mark (abandoned), does not send, issues ROLLBACK, and returns the connection; a running round trip is not stopped (the same as the
   one-statement-at-a-time procedure). The mark is set by the cancellation callback (the Executor's stopping method abandoned-now) and, on the spot, by the task that received the cancellation. The finally waits for the return
   abandon-lease under the lock guard, so the signal (ring-local) rings after the round trip that was running (including COMMIT)."
  (val raised (RaisedNotices))
  (val driver (TransactionDriver :connections connections :pool pool :database database :lease (TransactionLease) :guard (threading.Lock)))
  (setv driver.lease.submitted True)
  (try
    (<- answer (offloaded pool (fn [] (run-batched-here driver locks connections.origin raised program)) keep-nothing
                          (fn [] (abandoned-now driver))))
    answer
    (except [TaskCancelledError]
      (setv driver.lease.abandoned True)
      (raise))
    (finally
      (try
        (when (not driver.lease.returned)
          ;; shielded: the wait of a cancelled task is cancelled again, so the return of the connection is enclosed and waited out
          (<- (shielded (offloaded pool (fn [] (abandon-lease driver)) keep-nothing))))
        (finally
          (.ring-local connections database raised))))))


(defk offloaded-transaction [connections pool database program locks batched]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: locks tuple) (: batched bool)]
   :post [(: % "the answer of program | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Borrows one connection, runs program in one transaction, and always returns the connection (driver I/O on the pool's threads; see the header comment). Used by both postgres-sql-handler and
   pooled-postgres-sql-handler. batched = True runs run-in-batched-transaction, which sends one round trip in one pipeline, as a single driver job
   (offloaded-batched-transaction: it does not return to the scheduler between round trips). batched = False (the default) runs run-in-transaction, which sends one statement at a time, with the
   procedure below:
   Driver calls on the borrowed connection (statements, ROLLBACK, return) are run one at a time under the lock guard (driven): even if the waiter is cancelled, the thread of a call that has started
   runs to the end, so the following ROLLBACK and return come from another thread to the same connection (psycopg's connection lock keeps the same order, so guard does not change which statements are sent or their order).
   The job of borrowing the connection is made the same pool job as the first driver call (BEGIN), and the job of returning is made the same pool job as the last call (the call that sends COMMIT or ROLLBACK)
   (child (3) of #3688: as separate jobs, switching between the shared loop and the driver thread would increase by two per write).
   A program that sends no statement does not borrow a connection. If it cannot be borrowed, the answer of the first call is SqlUnreachable, and the transaction's answer is that too.
   For a transaction that did not reach the last call (cancellation, BEGIN failure, exception), finally fires one return job, abandon-lease: it is under guard, so it returns after a
   running call, and a call that has not yet started finishes without running.
   Signals (see the header comment): the signals the transaction emitted are recorded (raised), and after the connection is returned, same-process bells are rung. Sent COMMIT (or an attempt to send it)
   The ringing happens inside the finally, so it rings even if cancelled before receiving COMMIT's answer or while waiting for the return."
  (when batched
    (<- whole (offloaded-batched-transaction connections pool database program locks))
    (return whole))
  (val raised (RaisedNotices))
  (val driver (TransactionDriver :connections connections :pool pool :database database :lease (TransactionLease) :guard (threading.Lock)))
  (try
    (<- answer (run-in-transaction database program
                                   (fn [request] (driven driver (fn [leased] (postgres-query leased request)) False))
                                   (fn [request] (driven driver (fn [leased] (postgres-insert leased request)) False))
                                   (fn [] (driven driver (fn [leased] (postgres-begin leased locks)) False))
                                   (fn [] (raised-commit driver raised))
                                   (fn [] (driven driver (fn [leased] (postgres-control leased "ROLLBACK")) True))
                                   :execute-notify (fn [request] (raised-notice driver connections.origin raised request))))
    answer
    (finally
      (try
        (when (and driver.lease.submitted (not driver.lease.returned))
          ;; shielded: the wait of a cancelled task is cancelled again, so the return of the connection is enclosed and waited out
          (<- (shielded (offloaded pool (fn [] (abandon-lease driver)) keep-nothing))))
        (finally
          (.ring-local connections database raised))))))


(defk raised-commit [driver raised]
  {:pre [(: driver TransactionDriver) (: raised RaisedNotices)]
   :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "Sends COMMIT of a one-statement-at-a-time transaction (see the note on offloaded-transaction): sets the mark 'sent far enough to be delivered' before sending (so that even if cancelled before receiving
   COMMIT's answer, the bells are rung after the connection is returned), and returns the connection in the same driver job."
  (setv raised.sent True)
  (<- answer (driven driver (fn [leased] (postgres-control leased "COMMIT")) True))
  answer)


(defk raised-notice [driver origin raised request]
  {:pre [(: driver TransactionDriver) (: origin str) (: raised RaisedNotices) (: request SqlNotify)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "Sends a SqlNotify inside a one-statement-at-a-time transaction on the same connection (delivered only on commit) and records it so that the bells are rung after the connection is returned."
  (<- (noted-notice raised request))
  (<- answer (driven driver (fn [leased] (postgres-notice leased driver.database origin request)) False))
  answer)


(defk offloaded-statement [connections pool database work]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: work "a callable (connection) -> Program")]
   :post [(: % "the answer of work | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "Answers a single-statement effect with borrowing the connection, the statement, and the return as one pool job (lease-run-return) (only the task that fired it waits; agora-redesign
   #2792). If the borrow and the statement were separate jobs, the scheduler thread would pay twice per statement for the round trip of a promise completed from outside (the same reason a transaction makes the borrow the same job as the first
   driver call).
     - A request cancelled while waiting for a permit (in the middle of the borrow) does not run the statement even if the permit is obtained, and returns immediately (so requests cancelled while the DB was down do not
       all flush their statements when the DB returns and make later requests wait). The finally that exits on cancellation takes the claim (lock) first, and the borrowing job, unable to
       take the claim, returns without running the statement.
     - A statement that has started running is not stopped, and the thread that finishes returns it (the cancelled task does not wait for the statement to end; the job has taken the claim first).
     - A job that has not started is removed by offloaded (it does not borrow, and has nothing to return)."
  (val claim (threading.Lock))
  (try
    (<- answer (offloaded pool (fn [] (lease-run-return connections database claim work)) keep-nothing))
    answer
    (finally
      (.acquire claim :blocking False))))


(defk notified [connections pool database notify]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: notify SqlNotify)]
   :post [(: % (| None SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Answers a SqlNotify outside a transaction (after sending the signal and returning the connection, rings same-process bells; see the header comment). It rings even if cancelled before receiving the statement's
   answer, and also rings when the statement did not arrive (when it is unknown whether it went through, we do not lean toward failing to ring)."
  (val raised (RaisedNotices :sent True))
  (<- (noted-notice raised notify))
  (try
    (<- answer (offloaded-statement connections pool database (fn [leased] (postgres-notice leased database connections.origin notify))))
    (if (isinstance answer SqlRows) None answer)
    (finally
      (.ring-local connections database raised))))


(defk hung-notice [connections pool database channel topics]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: channel str) (: topics (| tuple None))]
   :post [(: % (| ExternalPromise SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Answers SqlHangNotice (hangs on the listener a bell waiting for names topics; None = rings on every signal. The wait for the first connection is on a pool thread.
   If cancelled, the hung bell is removed)."
  (val listener (.listener connections database channel))
  (val waiting (if (is topics None) None (frozenset topics)))
  (<- bell ExternalPromise (CreateExternalPromise))
  (<- refused (offloaded pool (fn [] (.hang listener bell waiting)) (fn [_] (.drop listener bell))))
  (if (is refused None)
      bell
      (SqlUnreachable :reason (.format "PostgreSQL's LISTEN is not established: {}" refused))))


(defhandler postgres-sql-handler [#^ PostgresConnections connections]
  ;; Why it stays an argument: the connection lease is a resource that the assembling side creates and closes (it holds the DSN and credentials), and it is sorted into the same set as the ClickHouse handler
  ;; by the name of the database it answers.
  "The real PostgreSQL handler (see the header comment; does not block the scheduler). It answers only the database names that connections declared (other names are passed to the outside)."
  {:tags {:context "sql" :role "foundation"}}
  (SqlQuery [database statement params binary] :when (in database (.names connections))
    (val request (SqlQuery database statement params binary))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-query leased request))))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-insert leased request))))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-ensure-tables leased tables))))
    (resume answer))
  (SqlTransaction [database program lock-key batched locks] :when (in database (.names connections))
    (<- claims (transaction-locks lock-key locks))
    (<- answer (offloaded-transaction connections DRIVER-THREADS database program claims batched))
    (resume answer))
  ;; A batch bundles round trips inside a transaction; inside a transaction SqlTransaction's scope answers, so only batches issued outside arrive here.
  (SqlBatch [database queries commit] :when (in database (.names connections))
    (<- refusal (stray-batch database))
    (raise refusal))
  (SqlNotify [database channel topics] :when (in database (.names connections))
    (<- answer (notified connections DRIVER-THREADS database effect))
    (resume answer))
  (SqlHangNotice [database channel topics] :when (in database (.names connections))
    (<- answer (hung-notice connections DRIVER-THREADS database channel topics))
    (resume answer))
  (SqlDropNotice [database channel bell] :when (in database (.names connections))
    (.drop (.listener connections database channel) bell)
    (resume None)))
