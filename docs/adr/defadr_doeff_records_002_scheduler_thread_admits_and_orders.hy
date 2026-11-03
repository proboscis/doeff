;;; Executable ADR: the records service's scheduler thread holds only request admission, ordering control, and computations whose size is set by the request body or by one row.
;;; Computations whose size is set by the number of stored rows (decoding the rows of batch reads and encoding batch-read answers) run off the scheduler thread via Compute.
;;; The per-request computation limit is decided by the answer type (batch-read answer or not) and the read shape (batch read or single-row read).
;;;
;;; Goal = keep read p95 even under load (proposal 1 of the production investigation).
;;; Where the mechanism lives = packages/doeff-records/src/doeff_records/_loop_work.hy (the single point of the split), decoded-batch in _pg.hy (row decoding),
;;; answer-reply in service.hy (answer encoding), records-connected in main.hy and start-records-server in _http_server.hy (the Compute handlers).
;;;
;;; Rollback: delete this file and _loop_work.hy, change the decoded-batch call in _pg.hy back to a direct bind of the decoding program, and change answer-reply in service.hy back to
;;; reply-of (leaving the Compute handlers in place is harmless — they just no longer receive effects).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import typing [get-args])
(import doeff_records._wire [WireAnswer])
(import doeff_records._effects [DEFAULT-LIST-LIMIT LIST-LIMIT-MAX])
(import doeff_records._loop_work [BATCH-READ-ANSWERS ON-LOOP-ANSWERS])


(defadr ADR-DOE-RECORDS-002
  :title "The records service's scheduler thread holds only request admission and ordering control; computations whose size is set by the number of stored rows (decoding the rows of batch reads and encoding the answers) run off the thread via Compute"
  :status "accepted"
  :scope ["packages/doeff-records/src/doeff_records/_loop_work.hy"
          "packages/doeff-records/src/doeff_records/_pg.hy"
          "packages/doeff-records/src/doeff_records/_service.hy"
          "packages/doeff-records/src/doeff_records/_http_server.hy"
          "packages/doeff-records/src/doeff_records/_main.hy"
          "packages/doeff-records/src/doeff_records/_effects.hy"
          "packages/doeff-records/src/doeff_records/_wire.hy"
          "docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy"]
  :problem
    [(fact
       "The records service process runs with one run and one scheduler, and the accept loop, request tasks, the write gate, and change waits all run on a single scheduler thread. While that thread is blocked by one request's computation, requests arriving later, after the listener accepts them, wait in the accept queue until their request task starts."
       :evidence "The head comment of packages/doeff-records/src/doeff_records/_http_server.hy (the shape of the entry)")
     (fact
       "Production measurement over 100 minutes on 2026-10-22 (per process: the accept queue of a probe every 200 ms, meters every 1 second, scheduler-thread run seconds every 50 ms): excluding write-congestion windows, 97–100% of probes whose accept queue jumped by 60 ms or more coincided with a 1-second window in which a list read returned 300 rows or more. Scheduler-thread run time in the 50 ms before a jump was 88–98% (overall average 35–53%), and run-queue wait was 0.6–2.0% — the thread was getting CPU and was blocked by its own work."
       :evidence "Record of the production investigation (measurement tables, breakdown and mitigations)")
     (fact
       "For list reads returning 300–500 rows at a time, decoding the page's rows (json.loads of the PostgreSQL payload and deep-freezing with frozen-json-object — listed-rows-of in _pg.hy) and encoding the answer (thaw-json and json.dumps — encode-answer in _wire.hy, json-answer in service.hy) ran synchronously on the scheduler thread and blocked it for 40–125 ms per page. Local measurement (a 500-row page of 2.6 KB rows, 3.14t): decoding 55 ms, encoding 37 ms, UTF-8 bytes 1.8 ms."
       :evidence "Record of the production investigation (breakdown and mitigations), measurements in this change's work")
     (fact
       "doeff has the general effect Compute, which runs a pure program and receives the answer as a value, and its handlers thread-pool-compute-handler (runs on the caller's pool; only the task that issued it waits — other scheduler tasks keep running) and inline-compute-handler (runs in place — deterministic in virtual-clock simulation). Production Python is free-threaded 3.14t, and pool threads run alongside the scheduler thread. The overhead of one thread handoff is about 0.5 ms locally (during which the scheduler thread is free)."
       :evidence "packages/doeff-core-effects/doeff_core_effects/_compute_effects.hy, thread_pool_compute.hy, inline_compute.hy")
     (fact
       "Even after batch reads were moved to Compute, the amount of computation per request was set by \"rows per page × row size\", and only the row-size side was bounded by type (the table's size-budget). The wire's list-rows limit had no cap, and any number of rows could be requested within the 16 MiB body limit. On the main branches of the 4 user repos (2026-10-22), the largest fixed list-read limit was 5,000 rows (one page of an all-rows list — chosen to balance the fixed cost of about 0.2 s per request). There were 2 reads that passed an externally supplied number straight through (the caller CLI's --limit N as N + 1, and a read that doubled its window without bound when the cursor was stale — both were changed on the same day to page by continuation position). By the ratio of local measurements, decoding and encoding a 5,000-row page takes about 0.9 s per page and blocks one compute-pool thread (92 ms at 500 rows)."
       :evidence "Measurements of the work that added the cap, LIST-LIMIT-MAX in packages/doeff-records/src/doeff_records/_effects.hy")
     (fact
       "Compute-pool congestion (large batch reads blocking all pool threads, with later batch-read computations waiting in the pool queue) was counted mixed into the per-request phase seconds (handler and encode) and had no meter of its own — from /metrics numbers one could not tell whether a delay was pool-queue wait or a storage read."
       :evidence "Phase names in packages/doeff-records/src/doeff_records/_request_timing.hy (queue, body, decode, handler, wait, encode, send)")]
  :context
    [(interpretation
       "What determines accept-queue wait is how long the scheduler thread continuously computes for one request. A batch read's length is set by the number of stored rows, up to the limit, so a small request (a list read with only a limit) triggers a large computation — this is what made other requests wait in production. Decoding a request body or encoding a single row also makes other requests wait while it blocks, but its size is set by the request itself (body limit REQUEST-MAX-BYTES = 16 MiB) or by one row (the table's size-budget — only for tables that declare it), and does not grow with the number of stored rows. In the production investigation they were not the cause (decoding write bodies and encoding answers took 0.5–26 ms per second, under 20% of list encoding), so for now they stay on the thread, with their limits written in R6.")
     (interpretation
       "Rather than mapping the current shape onto threads as-is (a thread per request, running all handlers on the pool), the scheduler thread's holdings are decided closed by answer type and read shape: only batch-read answer types (Page, Changes, Events) and batch reads (list pages, change streams, event streams) go off-thread, and everything else stays on the thread. Small requests do not pay the handoff overhead, and what goes off-thread can be counted by type.")
     (interpretation
       "Handlers belong to the assembly side (composition root): the thread pool is a resource, and the assembly opens and closes it (like the SQL handler's pool). The entry Program does not answer Compute — virtual-clock simulation installs the in-place handler and runs deterministically.")]
  :decision
    [(rule R1 "The records service's scheduler thread holds only request admission (listener events, body-read commands), ordering control (task creation and waiting, the write gate, locks, change waits, cancellation), meters, the answer-send command, and computations whose size is set by the request itself or by one row (decoding request bodies, decoding and encoding one row and one event, encoding write answers).")
     (rule R2 "Row decoding of storage batch reads (reads streamed outside the lock that return rows up to the limit — list pages, change streams, event streams, and the maintenance archive reads) issues pure programs (listed-rows-of, changes-of, events-of in _pg.hy) via Compute and runs them off the scheduler thread (decoded-batch). If there are no rows there is nothing to run, so nothing is issued.")
     (rule R3 "For batch-read answers (BATCH-READ-ANSWERS in _loop_work.hy = Page, Changes, Events) carrying one or more rows, changes, or events, encoding (wire object → JSON string → UTF-8 bytes — sendable-reply-of in service.hy) runs off the scheduler thread via Compute (answer-reply). Sending does not re-encode (HttpAnswer's data). Other wire answers (ON-LOOP-ANSWERS) are encoded in place.")
     (rule R4 "Each wire answer type (WireAnswer in _wire.hy) belongs to exactly one of BATCH-READ-ANSWERS and ON-LOOP-ANSWERS. A change that adds an answer type puts it into one of them in the same change.")
     (rule R5 "Assemblies that start the records service install a Compute handler: the production foundation entry (records-connected in main.hy) opens and closes a pool of COMPUTE-WORKERS threads, and the test shell (start-records-server in _http_server.hy) also installs a thread pool. Scripted tests and simulation (virtual clock) install inline-compute-handler. The entry Program (serve-records) and the records entry flow (respond in service.hy) install no handler. The handler answers both Compute and the load read ReadComputeLoad (read by GET /metrics — R6. The in-place handler has no queue, so all zeros).")
     (rule R6 "What stays on the scheduler thread for now, and its limits (none was a cause of accept-queue jumps in the production investigation — if jumps appear, move it off-thread starting from these limits): (a) decoding request bodies (JSON reading and freezing, write judgment) and encoding write answers — up to the body limit REQUEST-MAX-BYTES (16 MiB). (b) decoding and encoding one row and one event (ReadRow, ReadEventByKey, the row at a top-down page position) — the limit is the table's size-budget, and a row of a table that does not declare size-budget has no limit (the user bounds it by declaring size-budget). (c) decoding inside the write-lock transaction (rows past their reclaim deadline, the memory and fingerprints of discarded event keys, uses before an append) — the transaction program issues only SQL effects and pure computation (head comment of _pg.hy). Reclaim is up to EXPIRE-BATCH (500) items per transaction, on every maintenance tick. (d) rendering GET /metrics — proportional to the number of series (declared tables × callers), once per read interval (the meter-read tick). (e) filtering and sorting of lists in the memory store (the simulation and test store) — that is the store's answer itself. Per-request volume limits and meters for batch reads run off the scheduler thread (compute pool): (f) the limit of one list read (ListRows) is up to effects.hy's LIST-LIMIT-MAX = 5,000 rows — the ListRows type refuses at construction (ValueError), so the memory store's simulation and tests, the PostgreSQL store, and the client refuse at the same place, and the HTTP entry refuses an over-limit body with 400 malformed (a copy of decode-request's type refusal). The per-request volume is closed by 5,000 rows × row size (size-budget). The value matches the users' largest page (one page of an all-rows list — one page blocks one pool thread for about 0.9 s). The cap only bounds the worst case of one request; it is not isolation between large reads (production's 4-thread pool is blocked for about 0.9 s by 4 overlapping 5,000-row pages — lowering the value to 2,000 just makes it 4 threads × 0.4 s, the same shape). If the (g) meters name pool wait as the cause of jumps, the next step is isolation, not lowering the value — admission that limits concurrent large pages (e.g. over 1,000 rows) to pool threads − 1 (before off-loop in _loop_work.hy), or routing large batch reads to a separate pool. The limits of change streams (watch-changes) and event streams (read-events) have no cap yet (outside this change's scope — the users' maximum is 5,000 for change reads. If jumps appear, bound them the same way). (g) Compute-pool congestion is read from GET /metrics series: records_compute_queued (number of computations queued waiting for a pool thread — current value), records_compute_running (number running — current value), records_compute_workers (number of pool threads the assembly opened — RecordsServing's compute-workers), records_compute_wait_seconds_sum and _count (total seconds and count that started computations waited from issue to start — cumulative since startup), records_compute_run_seconds_sum and _count (total seconds and count that finished computations ran — cumulative since startup). The values are answered by the Compute handler via ReadComputeLoad (thread-pool-compute-handler counts per pool) and added to the snapshot when GET /metrics renders (not accumulated into meters — with-compute-load in _http_server.hy). The reader takes the difference of 2 snapshots per tick to get that tick's average wait (difference of total wait ÷ difference of count), the time-average number queued (difference of total wait ÷ tick seconds), and pool utilization (difference of total run ÷ tick seconds ÷ number of threads), and reads them side by side with the tick in which request-delay p95 jumped. Threshold for naming pool wait as the cause of a jump: average wait exceeds 50 ms for 2 consecutive ticks, with queued staying above 0 throughout.")
     (rule R7 "The maintenance worker (maintain, started by serve-records after setup — reclaim, pruning, and the archive functions archive and archive-events supplied by the user) does not run on the same scheduler thread as request tasks: the entry runs the maintenance Program with doeff-core-effects' run-apart in a new VM on a separate thread, and only the effects the maintenance issues (storage, SQL, meters, clock, scheduler) are answered by the handlers of the maintenance task (the same run as requests). What the archive functions do is decided by the user's composition (they may continue computations sized by the number of rows without issuing effects, like checking rows read back from the archive destination), and the entry cannot bound them with the R1–R6 shape (issuing to Compute), so the whole computation is removed from the request thread. While the maintenance waits for an effect's answer, only the separate thread waits. Try is answered inside the separate thread's VM (run-apart installs try-handler in that VM). Other effects that carry a program as a value (Spawn, Gather, Race, SqlTransaction's program) are run by the maintenance task's run, and the inner program runs outside the maintenance thread and without the handlers inside the maintenance Program (such as the writer's record handler), so the maintenance does not put heavy computation or effects needing inner handlers there (the current maintenance path has only SqlTransaction's program — one reclaim transaction is up to EXPIRE-BATCH items, R6 (c), and the transaction program issues only SQL effects and pure computation). When serving ends, the entry cancels the maintenance task and waits for its end (daemon tasks are not cancelled at the end of a run, and without cancelling, the channel does not close). How to choose: only long effectful programs whose size the entry cannot bound and which the user's composition decides are run with run-apart; single computations the entry can see go to Compute per R1–R6.")]
  :laws
    [(law the-scheduler-thread-does-no-work-sized-by-stored-rows
       :statement "for_all batch-read request r on records: the CPU seconds the scheduler thread spends for r do not depend on the number of rows r's answer carries (row decoding and encoding are inside Compute programs)"
       :counterexamples
         [(counterexample "Decoding the rows of a 500-row list page (json.loads and deep freezing) and encoding the answer (thaw-json and json.dumps) on the scheduler thread, blocking it for 40–125 ms per page — other requests arriving meanwhile wait in the accept queue (production, 2026-10-22)")
          (counterexample "Moving decoding to Compute but leaving answer encoding or the conversion to UTF-8 bytes on the scheduler thread — computation proportional to the number of rows remains on the thread")]
       :enforced-by ["packages/doeff-records/tests/test_loop_work.hy::test-the-scheduler-thread-spends-no-more-on-a-page-of-many-rows-than-on-a-page-of-few"
                     "packages/doeff-records/tests/test_loop_work.hy::test-a-batch-answer-is-spelled-to-its-bytes-off-the-scheduler-thread"
                     "packages/doeff-records/tests/test_loop_work.hy::test-batch-reads-decode-and-spell-their-rows-off-the-scheduler-thread"]
       :wiring "Wired (2026-10-22) — the difference in scheduler-thread CPU seconds between 500-row and 5-row pages is at most 5 ms per request with the thread pool, and at most 1/10 of running in place (local measurement: in place 53 ms, pool 0.6 ms). Small computations hard to distinguish by CPU-second differences (UTF-8 conversion of 500 rows, 1.8 ms) are rejected by type — a page answer carrying rows issues one Compute each for decoding and encoding, and builds the bytes to send, data, off-thread (a store standing in for SQL answers — a check that needs no PostgreSQL). On real PostgreSQL too, the Compute names of the 3 kinds of batch reads are checked")
     (law the-maintenance-never-holds-the-serving-thread
       :statement "for_all maintenance round m and request r arriving during m's computation: r's answer does not wait for the end of m's computation (the maintenance Program runs with run-apart in a VM on a separate thread and does not occupy the request scheduler thread)"
       :counterexamples
         [(counterexample "Running the maintenance worker as a task on the same scheduler thread as requests — the first archive pass of maintenance right after a restart occupied the thread twice for 4 minutes each with a computation checking, row by row, 190,000 rows read back from the archive destination (a step of a defk issuing no effects); all requests on the 2 entries waited in the accept queue and were answered all at once after the callers had disconnected (production, 2026-10-27 01:26–01:44 and 02:08–02:16 JST)")
          (counterexample "Moving only the maintenance archive reads (R2) to Compute — the computation in which the user's archive function checks answer rows is invisible to the entry and stays on the thread")]
       :enforced-by ["packages/doeff-records/tests/test_maintenance_runs_apart.hy::test-reads-are-answered-while-the-maintenance-computes"
                     "packages/doeff-core-effects/tests/test_apart_call.hy::test-the-program-computes-apart-and-its-effects-are-answered-here"
                     "packages/doeff-core-effects/tests/test_apart_call.hy::test-computing-in-place-holds-the-thread"]
       :wiring "Wired (2026-10-27) — serving-body in _http_server.hy starts maintenance with (run-apart (maintain …)). The test shell is given an archive maintenance that computes for 3 seconds without issuing effects, and 8 reads fired in parallel during the computation are answered within 1 second, before the computation ends (in the pre-fix shape the slowest read took 2.6 seconds, after the computation)")
     (law every-wire-answer-has-one-spelling-place
       :statement "the set of WireAnswer types in _wire.hy = BATCH-READ-ANSWERS ⊎ ON-LOOP-ANSWERS (disjoint)"
       :counterexamples
         [(counterexample "Adding to the wire an answer type that carries a list of rows without putting it into either group — the new batch answer is silently encoded on the scheduler thread")]
       :enforced-by ["docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy::test-adr-doe-records-002-every-wire-answer-has-one-spelling-place"]
       :wiring "Wired (2026-10-22) — the test in this file")
     (law every-records-assembly-answers-compute
       :statement "for_all assembly a that starts the records service: a installs a Compute handler (production and test shell = thread pool; scripted tests and simulation = in place)"
       :counterexamples
         [(counterexample "Starting the records service in an assembly without a Compute handler — batch reads carrying rows become 500 internal with UnhandledEffect")]
       :enforced-by ["packages/doeff-records/tests/test_pg_reconcile_indexes.hy::test-the-service-answers-while-the-reconciler-builds-and-does-not-end"
                     "packages/doeff-records/tests/test_http_list_rows_metrics.hy"
                     "packages/doeff-records/tests/test_laws.hy (the interpreter in tests/interpreters.hy)"
                     "the closure check of the records service in user repos (reads, without executing, effects left unanswered by the production foundation)"]
       :wiring "Wired (2026-10-22) — list reads on the production foundation entry records-connected, list reads on the test shell, and all laws of the scripted interpreter")
     (law a-list-read-asks-at-most-the-cap
       :statement "for_all list read r (ListRows): r.limit <= LIST-LIMIT-MAX — refused by the type at construction, so the memory store, the PostgreSQL store, and the client refuse at the same place, and the HTTP entry refuses an over-limit body with 400 malformed"
       :counterexamples
         [(counterexample "Passing a list read with an uncapped limit to the store — the number a caller received from outside (CLI's --limit N) goes into one read and blocks a compute-pool thread in proportion to the number of stored rows")
          (counterexample "Putting the cap only in wire encoding — a program tested and simulated on the memory store passes with limit 100000 and is first refused in production (finding 1 of the configuration review)")]
       :enforced-by ["packages/doeff-records/tests/test_wire.hy::test-a-list-above-the-limit-cap-is-refused-by-its-type"
                     "packages/doeff-records/tests/test_http_metrics.hy::test-metrics-show-the-compute-pool-and-the-wire-refuses-a-list-above-the-cap"
                     "docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy::test-adr-doe-records-002-the-default-list-fits-the-wire-cap"]
       :wiring "Wired (2026-10-22) — ListRows' __post_init__, and the 400 at the test shell's entry")
     (law the-compute-pool-wait-is-on-metrics
       :statement "GET /metrics carries the Compute handler's load (queued, running, number of pool threads, and the totals and counts of seconds started computations waited and finished computations ran) — when the compute pool is blocked, the queued count and wait seconds rise and utilization approaches 1"
       :counterexamples
         [(counterexample "Not metering pool wait — the delay from large batch reads blocking the pool is mixed into the per-request handler and encode phase seconds and cannot be distinguished (until 2026-10-22)")
          (counterexample "Setting the queued count on a gauge only when work is issued — the last value set remains after the queue empties")]
       :enforced-by ["packages/doeff-records/tests/test_http_metrics.hy::test-metrics-name-a-clogged-pool"
                     "packages/doeff-records/tests/test_http_metrics.hy::test-metrics-show-the-compute-pool-and-the-wire-refuses-a-list-above-the-cap"
                     "packages/doeff-core-effects/tests/test_compute_effects.hy::test-thread-pool-load-counts-the-queue-and-the-wait"
                     "packages/doeff-core-effects/tests/test_compute_effects.hy::test-thread-pool-load-drops-a-queued-job-that-is-cancelled"]
       :wiring "Wired (2026-10-22) — the handler's ReadComputeLoad is read at render time (production foundation entry and test shell = thread pool; scripts and simulation = in-place zeros)")]
  :enforcement
    [(deftest test-adr-doe-records-002-every-wire-answer-has-one-spelling-place
       ;; Each wire answer type belongs to exactly one of batch-read answers (encoded off-thread) and other answers (encoded in place) (R4).
       (val wire (frozenset (get-args WireAnswer)))
       (val batch (frozenset BATCH-READ-ANSWERS))
       (val on-loop (frozenset ON-LOOP-ANSWERS))
       (assert (not (& batch on-loop)) (+ "Answer types in both groups: " (str (sorted (gfor t (& batch on-loop) t.__name__)))))
       (assert (= (| batch on-loop) wire)
               (+ "Answer types with no decided encoding place (ADR-DOE-RECORDS-002 R4 — put them in BATCH-READ-ANSWERS or ON-LOOP-ANSWERS in _loop_work.hy): "
                  (str (sorted (gfor t (^ (| batch on-loop) wire) t.__name__))))))
     (deftest test-adr-doe-records-002-the-default-list-fits-the-wire-cap
       ;; A list read with no limit written (ListRows' default DEFAULT-LIST-LIMIT) is within the wire cap (R6 (f) — the default read is not refused by the wire).
       (assert (<= DEFAULT-LIST-LIMIT LIST-LIMIT-MAX) #(DEFAULT-LIST-LIMIT LIST-LIMIT-MAX)))]
  :plans [])
