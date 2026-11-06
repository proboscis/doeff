;;; Tests of cancellation for offloaded-statement (postgres_sql.hy - used by both postgres-sql-handler and
;;; pooled-postgres-sql-handler), which answers the single-statement SQL effects (SqlQuery, SqlInsertRows, SqlEnsureTables). It uses no real PostgreSQL and replaces the connection lease with a fake:
;;; permits are counted with the same per-database inter-thread lock as the real one, and a token is lent instead of a connection. Tests against real PG are skipped without a DSN, so these tests
;;; do not depend on that.
;;;   - Scenario (the shape where the record's DB stalled for 30 seconds - time is mapped only for ordering): 4 permits, 12 reads. The statements of the first 4 do not return because of the DB stall, and the remaining
;;;     8 wait for a permit. At the request limit (10 seconds) all 12 are cancelled, and afterwards the stall clears and the permits return. What is counted = the number of statements that a request cancelled while waiting for a permit
;;;     ran after it got a permit. Before the fix it was 8 (once a permit was obtained, the statement was run without looking at the cancellation), after the fix 0. The 4 statements that had started
;;;     running are not stopped (outside the fix).
;;;   - A request cancelled before the work of lease-and-statement starts has its work dropped and does not lease a connection (so there is nothing to return either).
;;;   - One statement answers lease, statement, and return in one pool job (before, it was two: lease and statement).
(require doeff-hy.macros [deftest defk deff <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import queue [Queue])
(import concurrent.futures [Future])
(import doeff_core_effects._offloaded_call [ReusedThreads])
(import doeff_core_effects [PostgresConnections PostgresDatabase]
        doeff_core_effects._postgres_sql [DRIVER-THREADS offloaded-statement])
(import doeff_core_effects [SqlRows])
(import doeff_core_effects [CreateExternalPromise Wait Spawn Cancel TaskCancelledError])

(val DB "store")
;; The number of permits per database (the same 4 as the name for the record's requests) and the number of reads that arrive at the same time.
(val PERMITS 4)
(val READS 12)
;; Upper bound (seconds) of waiting in the test shell. When the scenario falls apart, fail by name instead of hanging until pytest's limit (60 seconds).
(val SETTLE-SECONDS 20.0)


(defrecord FakeLease
  "The token that the fake lease lends instead of a connection. granted-after-cancel = whether the cancellation mark was already set when the permit was obtained."
  (#^ bool granted-after-cancel))


(defclass Countdown []
  "A counter that counts things happening across threads and calls done exactly once at the total-th one (the test shell - a resource, so not a value type)."

  (deff __init__ [self total done]  ; defk にできない: initialization of a test-shell resource
    {:pre [(: self Countdown) (: total int) (: done "callable () -> None")] :post [(: % None)]}
    "To receive the count to reach and the call to make when it is reached."
    (setv self.lock (threading.Lock)
          self.left total
          self.done done))

  (deff tick [self]  ; defk にできない: entry called from the driver's thread (outside the VM)
    {:pre [(: self Countdown)] :post [(: % None)]}
    "To count one and call done if it is the total-th."
    (with [_ self.lock]
      (setv self.left (- self.left 1))
      (when (= self.left 0)
        (self.done)))))


(defclass StalledConnections [PostgresConnections]
  "The fake connection lease (see the header comment). Permits are counted with the same per-database inter-thread lock (PERMITS of them) as the real acquire, and
   FakeLease is lent instead of a connection. arrivals = the counter of arrivals to lease, cancelled = the cancellation mark, returned = the counter of returns."

  (deff __init__ [self arrivals cancelled returned]  ; defk にできない: initialization of a test-shell resource
    {:pre [(: self StalledConnections) (: arrivals Countdown) (: cancelled threading.Event) (: returned Countdown)] :post [(: % None)]}
    "To receive the counters and the cancellation mark and make a lease of one database with PERMITS permits."
    (PostgresConnections.__init__ self #((PostgresDatabase :name DB :dsn "")) :size PERMITS)
    (setv self.arrivals arrivals
          self.cancelled cancelled
          self.returned returned))

  (deff acquire [self name]  ; defk にできない: same entry as the real acquire (waits blockingly on the driver's thread)
    {:pre [(: self StalledConnections) (: name str)] :post [(: % FakeLease)]}
    "To count the arrival, wait for a permit, and when obtained write into the token whether the cancellation mark was set and lend it."
    (.tick self.arrivals)
    (.acquire (get self.permits name))
    (FakeLease :granted-after-cancel (.is-set self.cancelled)))

  (deff release [self name connection]  ; defk にできない: same entry as the real release (called on the driver's thread)
    {:pre [(: self StalledConnections) (: name str) (: connection FakeLease)] :post [(: % None)]}
    "To return the permit and count the return."
    (.release (get self.permits name))
    (.tick self.returned)))


(defclass HoldingFirstCall [ReusedThreads]
  "An Executor that runs the given work on threads the same as ReusedThreads, but only holds the first work (the work of lease, statement, and return of one statement) without starting it (test shell -
   a resource, so not a value type). When it holds it, it completes holding with the held Future. The held work does not run."

  (deff __init__ [self holding]  ; defk にできない: initialization of a test-shell resource
    {:pre [(: self HoldingFirstCall) (: holding "scheduler ExternalPromise")] :post [(: % None)]}
    "To receive the promise that announces it held, and also place the ReusedThreads field that runs other work."
    (ReusedThreads.__init__ self)
    (setv self.holding holding
          self.lock (threading.Lock)
          self.calls 0))

  (deff submit [self call #* args]  ; defk にできない: Executor's entry (a concurrent.futures promise - outside the VM)
    {:pre [(: self HoldingFirstCall) (: call "callable (args) -> value") (: args tuple)] :post [(: % Future)]}
    "To hold only the first work as a Future that does not start, and run the others the same as ReusedThreads."
    (with [_ self.lock]
      (setv self.calls (+ self.calls 1)
            nth self.calls))
    (match nth
      1 (do (setv held (Future))
            (.complete self.holding held)
            held)
      _ (ReusedThreads.submit self call #* args))))


(defclass CountingPool [ReusedThreads]
  "An Executor that runs the given work on threads the same as ReusedThreads and counts the number of works given (test shell - a resource, so not a value type)."

  (deff __init__ [self]  ; defk にできない: initialization of a test-shell resource
    {:pre [(: self CountingPool)] :post [(: % None)]}
    "To place the counting field and the ReusedThreads field that runs other work."
    (ReusedThreads.__init__ self)
    (setv self.lock (threading.Lock)
          self.calls 0))

  (deff submit [self call #* args]  ; defk にできない: Executor's entry (a concurrent.futures promise - outside the VM)
    {:pre [(: self CountingPool) (: call "callable (args) -> value") (: args tuple)] :post [(: % Future)]}
    "To count the given work and then run it the same as ReusedThreads."
    (with [_ self.lock]
      (setv self.calls (+ self.calls 1)))
    (ReusedThreads.submit self call #* args)))


(defk read-on [lease stalls stall ran]
  {:pre [(: lease FakeLease) (: stalls Countdown) (: stall threading.Event) (: ran Queue)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "To run one read on a fake connection (runs on the driver's thread). Writes the token it ran into ran. A read leased before the cancellation mark counts that it stalled, and
   does not return until the DB stall clears."
  (.put ran lease)
  (when (not lease.granted-after-cancel)
    (.tick stalls)
    (.wait stall SETTLE-SECONDS))
  (SqlRows :rows #() :rowcount 0))


(defk spawned-reads [connections pool work count]
  {:pre [(: connections PostgresConnections) (: pool ReusedThreads) (: work "callable (token) -> Program") (: count int)]
   :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To pass count read requests, each in a separate task, to offloaded-statement (answer = the sequence of tasks)."
  (match count
    0 #()
    _ (do (<- task (Spawn (offloaded-statement connections pool DB work)))
          (<- rest (spawned-reads connections pool work (- count 1)))
          (+ #(task) rest))))


(defk ended-by-cancel [task]
  {:pre [(: task "scheduler Task")] :post [(: % bool)]
   :tags {:context "sql" :role "program"}}
  "To wait for the cancelled task and answer whether it ended by cancellation."
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk within [promise what]
  {:pre [(: promise "scheduler ExternalPromise") (: what str)] :post [(: % "answer of promise")]
   :tags {:context "sql" :role "program"}}
  "To wait for a promise completed from outside up to SETTLE-SECONDS (fail naming what if it passes)."
  (val watchdog (threading.Timer SETTLE-SECONDS (fn [] (.fail promise (AssertionError (.format "{} did not complete in {} seconds" what SETTLE-SECONDS))))))
  (.start watchdog)
  (try
    (<- answer (Wait promise.future))
    answer
    (finally (.cancel watchdog))))


(deftest test-reads-cancelled-while-waiting-for-a-permit-send-no-statement
  ;; Scenario (header comment): 4 permits, 12 reads, the first 4 statements stall, all 12 are cancelled at 10 seconds, the stall clears and the permits return.
  (<- ready (CreateExternalPromise))
  (<- settled (CreateExternalPromise))
  (val cancelled (threading.Event))
  (val stall (threading.Event))
  (val ran (Queue))
  ;; Complete = the 12 arrived to lease, and the statements of the first 4 stalled (the remaining 8 wait for a permit).
  (val arrivals (Countdown (+ READS PERMITS) (fn [] (.complete ready None))))
  (val connections (StalledConnections arrivals cancelled (Countdown READS (fn [] (.complete settled None)))))
  (<- tasks (spawned-reads connections (ReusedThreads) (fn [lease] (read-on lease arrivals stall ran)) READS))
  (<- (within ready "12 leases and 4 stalled statements"))
  ;; t=10: at the request limit, cancel all 12.
  (.set cancelled)
  (for [task tasks]
    (<- (Cancel task)))
  (for [task tasks]
    (<- ended (ended-by-cancel task))
    (assert ended task))
  ;; t=30: the DB stall clears, the 4 running statements finish and the permits return. Wait until all 12 leases are returned.
  (.set stall)
  (<- (within settled "12 returns"))
  (val statements (tuple ran.queue))
  (val before-cancel (len (lfor lease statements :if (not lease.granted-after-cancel) lease)))
  (val after-cancel (len (lfor lease statements :if lease.granted-after-cancel lease)))
  ;; The 4 statements that had started running are not stopped (outside the fix).
  (assert (= before-cancel PERMITS) statements)
  ;; The 8 cancelled while waiting for a permit do not run a statement even when a permit is obtained (before the fix, 8).
  (assert (= after-cancel 0) (.format "{} statements run by requests cancelled while waiting for a permit" after-cancel)))


(deftest test-a-read-cancelled-before-its-work-starts-leases-nothing
  ;; A request cancelled before the work of lease and statement starts: the work is dropped, no connection is leased, and no statement is run (no lease, so nothing to return).
  (<- holding (CreateExternalPromise))
  (val cancelled (threading.Event))
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val arrivals (Countdown 1 (fn [] None)))
  (val returned (Countdown 1 (fn [] None)))
  (val connections (StalledConnections arrivals cancelled returned))
  (<- tasks (spawned-reads connections (HoldingFirstCall holding) (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran)) 1))
  (<- held (within holding "the work of lease and statement"))
  (.set cancelled)
  (<- (Cancel (get tasks 0)))
  (<- ended (ended-by-cancel (get tasks 0)))
  (assert ended tasks)
  (assert (.cancelled held) held)
  (assert (= arrivals.left 1) "the work cancelled before starting leased a connection")
  (assert (= returned.left 1) "returned a connection that was not leased")
  (assert (= (tuple ran.queue) #()) (tuple ran.queue)))


(deftest test-a-statement-is-one-pool-job
  ;; The effect of one statement answers lease, statement, and return in one pool job (the round trip of the promise that the scheduler's thread completes from outside is paid
  ;; only once per statement). The leased connection has been returned after the answer.
  (<- settled (CreateExternalPromise))
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val connections (StalledConnections (Countdown 1 (fn [] None)) (threading.Event) (Countdown 1 (fn [] (.complete settled None)))))
  (val pool (CountingPool))
  (<- answer (offloaded-statement connections pool DB (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran))))
  (assert (= answer (SqlRows :rows #() :rowcount 0)) answer)
  (<- (within settled "return of the leased connection"))
  (assert (= pool.calls 1) (.format "the pool jobs of one statement were {}" pool.calls))
  (assert (= (len (tuple ran.queue)) 1) (tuple ran.queue)))


(deftest test-statements-in-a-row-reuse-one-thread
  ;; Statements run in succession reuse the thread that ran the previous statement's work and do not start a thread per statement (the wait to start one
  ;; was 20% of the custody's ordinary CPU). The Executor of postgres-sql-handler has this shape too.
  (val statements 20)
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val connections (StalledConnections (Countdown statements (fn [] None)) (threading.Event) (Countdown statements (fn [] None))))
  (val pool (ReusedThreads))
  (for [_ (range statements)]
    (<- (offloaded-statement connections pool DB (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran)))))
  (assert (= (len (tuple ran.queue)) statements) (tuple ran.queue))
  (assert (= pool.started 1) (.format "{} threads started for {} statements" pool.started statements))
  (assert (isinstance DRIVER-THREADS ReusedThreads) DRIVER-THREADS))
