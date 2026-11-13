;;; Dogfood: declare the effect vocabulary of doeff-core-effects as domains (ADR-DOE-DOMAIN-001 D8).
;;;
;;; The split is a proposal that follows the handler structure of doeff_core_effects._handlers / scheduler / http / memo / cache;
;;; the maintainer judges its validity in PR review.
;;; The foundation vocabularies (file / http-server / sql / process / channel / compute /
;;; stop-signal) get one domain per effect module; Absent / Raise get one domain for the
;;; receivers at the bundling boundary (maybe / result / on-raise / absent-as / absent-raises).
;;;
;;; - Raw Python handlers (installer / factory) are declared in :handlers as Handles(handler, effect ...)
;;;   (core-effects is not edited and nothing is attached to those handlers - D5).
;;; - defhandler-made factories (http / memo) are listed as they are, without annotation,
;;;   to dogfood the two-layer derivation (D6) from __doeff_body__.
;;; - This module is the only one in doeff-domain that may depend on
;;;   doeff-core-effects (D1, one-way). Importing it requires the dogfood extra.

(require doeff-domain.macros [defdomain])

(import doeff_domain [DomainLaw DomainTerm Handles])

(import doeff_core_effects [Ask Get Put Local Listen Await Try WriterTellEffect ReadWriterLog SlogEffect])
(import doeff_core_effects [HttpRequest ResolveHost])
(import doeff_core_effects [MemoGetEffect MemoPutEffect MemoDeleteEffect MemoExistsEffect])
(import doeff_core_effects [CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect])
(import doeff_core_effects [Spawn TaskCompleted Gather Wait Cancel Discard Race CreatePromise CompletePromise FailPromise CreateSemaphore AcquireSemaphore ReleaseSemaphore CreateExternalPromise SchedulerIntrospection ShieldEnter ShieldExit scheduled])
(import doeff_core_effects [reader state writer slog-handler slog-discard-handler try-handler local-handler listen-handler await-handler lazy-ask env-var-ask])
(import doeff_core_effects [cache-handler])
;; defhandler-made factories are listed directly from the implementation modules (the real
;; objects that carry __doeff_body__). The public wrappers (http_handlers / memo_handlers) are plain functions
;; with no structural information, so they would not dogfood the derivation.
(import doeff_core_effects._http-handlers-impl [_http-production-handler
                                                _http-fixture-record-handler
                                                _http-fixture-replay-handler])
(import doeff_core_effects._memo-handlers-impl [_memo-layer-handler])

;; Expected absence (Absent, BoundAbsent) and failure (Raise) - the receivers are raw Python functions at the bundling boundary.
(import doeff_core_effects [Absent Raise])
(import doeff_core_effects [BoundAbsent maybe result on-raise absent-as absent-raises])

;; The foundation vocabulary and its defhandler-made responders (derived from __doeff_body__ without annotation - D6).
(import doeff_core_effects [StatPath ReadText ReadBytes WriteText WriteBytes AppendText AppendBytes TruncateFile SyncDirectory MakeDirectory ListDirectory WalkTree StatTree RemoveTree RenamePath CopyFile CopyTree AcquireLock TryLock ReleaseLock ReadDiskFree ReadDiskUsage MeasureTree LinkFile LinkFiles MakeSymlink CompilePythonSources ReadMemoryFiles])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [rooted-file-handler])
(import doeff_core_effects [HttpListen HttpNextRequest HttpTakeQueued HttpReadBody HttpRespond HttpForward HttpShutdown HttpStopListening WsAccept WsSendText WsSendTextEach WsForward WsClose TakeWsSendReport AppendHttpScript ReadHttpServed])
(import doeff_core_effects [aiohttp-http-server])
(import doeff_core_effects [scripted-http-server])
(import doeff_core_effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent])
;; The public installers (aiohttp-ws-client, scripted-ws-client) are plain functions that wrap the program with cleanup and carry no structural information,
;; so the derivation lists the real defhandler-made objects that carry __doeff_body__ (same as _http-production-handler).
(import doeff_core_effects [aiohttp-ws-link-handler])
(import doeff_core_effects [scripted-ws-link-handler])
(import doeff_core_effects [SqlQuery SqlInsertRows SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice SqlTransaction SqlBatch SetSqlOutage])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_core_effects [pooled-postgres-sql-handler])
(import doeff_core_effects [clickhouse-http-sql-handler])
(import doeff_core_effects [sqlite-sql-handler])
(import doeff_core_effects [RunProcess ExecutableAt ReadEnvironment WorkingDirectory ProcessAlive ReadInterpreter ReadMachineName ResolveModule StartProcess PollProcess StopProcess SignalProcess WriteProcessInput])
;; offloaded-subprocess-handler is the factory metered-offloaded-subprocess-handler given a None meter, and has no __doeff_body__ -
;; the derivation lists the factory.
(import doeff_core_effects [subprocess-handler metered-offloaded-subprocess-handler])
(import doeff_core_effects [scripted-process-handler])
(import doeff_core_effects [ForkFromWarm PollWarmChild SignalWarmChild WriteWarmChildInput])
(import doeff_core_effects [os-warm-process-handler])
(import doeff_core_effects [scripted-warm-process-handler])
(import doeff_core_effects [ReadProcTable SignalPid])
(import doeff_core_effects [os-proc-table-handler])
(import doeff_core_effects [scripted-proc-table-handler])
(import doeff_core_effects [AwaitProcessExit])
(import doeff_core_effects [AwaitWarmChildExit])
(import doeff_core_effects [process-exit-handler])
(import doeff_core_effects [CreateChannel PutChannel TakeChannel])
(import doeff_core_effects [scheduler-channel-handler])
(import doeff_core_effects [Compute ReadComputeLoad])
(import doeff_core_effects [thread-pool-compute-handler])
(import doeff_core_effects [inline-compute-handler])
(import doeff_core_effects [StopRequested AwaitStop StopBell RaiseStop])
(import doeff_core_effects [os-signal-stop-handler scripted-stop-handler])
(import doeff_core_effects [CollectAndFreeze])
(import doeff_core_effects [gc-freeze-handler])
(import doeff_core_effects [scripted-freeze-handler])
(import doeff_core_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects [process-latest-handler])
(import doeff_core_effects [memory-latest-handler])
(import doeff_core_effects [ArmStackDump DisarmStackDump ReadStackDumps])
(import doeff_core_effects [faulthandler-stack-dump-handler])
(import doeff_core_effects [memory-stack-dump-handler])
(import doeff_core_effects [CountMetric ObserveSeconds ObserveSecondsBatch SetGauge ReadMeter])
(import doeff_core_effects [process-meter-handler])
(import doeff_core_effects [memory-meter-handler])
(import doeff_core_effects [OpenStepTally CloseStepTally OpenTaskTally ReadTaskTally CloseTaskTally])
(import doeff_core_effects [step-tally-handler])
(import doeff_core_effects [RandomBytes])
(import doeff_core_effects [os-random-handler])
(import doeff_core_effects [seeded-random-handler])


;; --- Declarations for raw Python handlers (D5). Handlers whose clauses cannot be read (installer / factory) are placed in the domain's :handlers
;; as Handles(handler, effect ...) - the declaration lives on the domain side and nothing is attached to core-effects handlers (the function handles()
;; became a type instead of an after-the-fact annotation - ADR-DOE-API-001 R29). A declaration means "takes part in handling" and
;; is not a totality guarantee. lazy-ask places the same declaration in both doeff-reader and doeff-scope.
(setv _lazy-ask-handles (Handles lazy-ask Ask Local))


(defdomain doeff-reader
  :title "Reader vocabulary - looking up values from the environment"
  :effects [Ask]
  :terms [(DomainTerm :name "Ask"
                      :home "doeff_core_effects._effects"
                      :description "Canonical effect for environment key lookup")]
  :handlers [(Handles reader Ask) _lazy-ask-handles (Handles env-var-ask Ask)]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "Covered by reader / lazy_ask / env_var_ask. lazy_ask also handles Local (see doeff-scope).")


(defdomain doeff-state
  :title "State vocabulary - Get/Put of mutable state"
  :effects [Get Put]
  :handlers [(Handles state Get Put)]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-writer
  :title "Writer vocabulary - quiet accumulation by Tell"
  :effects [WriterTellEffect ReadWriterLog]
  :terms [(DomainTerm :name "Tell"
                      :home "doeff_core_effects._effects"
                      :description "Canonical constructor of WriterTellEffect")]
  :handlers [(Handles writer WriterTellEffect ReadWriterLog)]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-001"])


(defdomain doeff-slog
  :title "Slog vocabulary - observability is correct only when visible"
  :effects [SlogEffect]
  :terms [(DomainTerm :name "slog"
                      :home "doeff_core_effects._effects"
                      :description "Canonical constructor of SlogEffect")]
  :handlers [(Handles slog-handler SlogEffect) (Handles slog-discard-handler SlogEffect)]
  :laws [(DomainLaw :name "slog-tell-types-disjoint"
                    :statement "wire_type(slog) = SlogEffect and wire_type(Tell) = WriterTellEffect and SlogEffect is_not WriterTellEffect"
                    :counterexamples ["writer() collects the output of slog / slog_handler consumes Tell (the actual behavior of the old default_interpreter)"])]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-001"])


(defdomain doeff-error
  :title "Error vocabulary - turning results into Ok/Err via Try"
  :effects [Try]
  :handlers [(Handles try-handler Try)]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-scope
  :title "Scoped env vocabulary - local override of the environment via Local"
  :effects [Local]
  :includes [doeff-reader]
  :handlers [(Handles local-handler Local) _lazy-ask-handles]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "Local has meaning only by referring to the Ask vocabulary (doeff-reader) - includes is reference composition, not introduction (D3).")


(defdomain doeff-listen
  :title "Listen vocabulary - collecting the value flow of effects (tee)"
  :effects [Listen]
  :handlers [(Handles listen-handler Listen)]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-await
  :title "Async bridge vocabulary - Await of a coroutine"
  :effects [Await]
  :handlers [(Handles await-handler Await)]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-scheduler
  :title "Scheduler vocabulary - execution foundation for tasks, promises, and semaphores"
  :effects [Spawn TaskCompleted Gather Wait Cancel Discard Race
            CreatePromise CompletePromise FailPromise
            CreateSemaphore AcquireSemaphore ReleaseSemaphore
            CreateExternalPromise SchedulerIntrospection ShieldEnter ShieldExit]
  :handlers [(Handles scheduled
                      Spawn TaskCompleted Gather Wait Cancel Discard Race
                      CreatePromise CompletePromise FailPromise
                      CreateSemaphore AcquireSemaphore ReleaseSemaphore
                      CreateExternalPromise SchedulerIntrospection ShieldEnter ShieldExit)]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "scheduled covers all scheduler effects as the single execution-foundation prompt.")


(defdomain doeff-http
  :title "HTTP vocabulary - executing, recording, and replaying HttpRequest, and resolving a host name alone (ResolveHost)"
  :effects [HttpRequest ResolveHost]
  :handlers [_http-production-handler
             _http-fixture-record-handler
             _http-fixture-replay-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "The handlers are defhandler-made factories - the handled set is derived from __doeff_body__ (D6 second layer).")


(defdomain doeff-memo
  :title "Memo vocabulary - hierarchical cache proxy"
  :effects [MemoGetEffect MemoPutEffect MemoDeleteEffect MemoExistsEffect]
  :handlers [_memo-layer-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "_memo-layer-handler covers all 4 effects (derived from the defhandler structure). MemoDeleteEffect was drift (unhandled) measured on 2026-07-17, but the MemoDeleteEffect clause (broadcast delete) has been implemented and it is resolved.")


(defdomain doeff-cache
  :title "Cache vocabulary - content-addressed persistent cache"
  :effects [CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect]
  :handlers [(Handles cache-handler CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect)]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "cache-handler covers all 4 effects (declared with Handles). CacheDeleteEffect was drift (unhandled) measured on 2026-07-17, but the CacheDeleteEffect branch has been implemented and it is resolved.")


(defdomain doeff-outcome
  :title "Outcome vocabulary - expected absence (Absent) and failure (Raise)"
  :effects [Absent BoundAbsent Raise]
  :terms [(DomainTerm :name "open_bind"
                      :home "doeff_core_effects._outcomes"
                      :description "The one place where <- and ! open the answer according to the declaration, emitting absence as Absent and failure as Raise")]
  ;; The receivers of Absent / Raise are factories that return a handler at the bundling boundary (calling one returns a handler that wraps the scope - ADR-DOE-API-001 R6).
  ;; BoundAbsent is a child of Absent, and only absent-as inspects the mark to decide how to resume - to other receivers it is a plain Absent.
  :handlers [(Handles maybe Absent) (Handles absent-raises Absent) (Handles absent-as Absent BoundAbsent)
             (Handles result Raise) (Handles on-raise Raise)]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-003"]
  :docs "The receivers are factories that return a handler at the bundling boundary, and none of them resumes (the only exception is absent-as, which resumes its own literal BoundAbsent with the default value). maybe folds Absent into Nothing, absent-raises folds Absent into Raise, result folds Raise into Err, and on-raise folds a type-matching Raise into a business answer.")


(defdomain doeff-file
  :title "File vocabulary - generic file system reads and writes"
  :effects [StatPath ReadText ReadBytes WriteText WriteBytes AppendText
            AppendBytes TruncateFile SyncDirectory
            MakeDirectory ListDirectory WalkTree StatTree RemoveTree RenamePath
            CopyFile CopyTree AcquireLock TryLock ReleaseLock ReadDiskFree
            ReadDiskUsage MeasureTree LinkFile LinkFiles MakeSymlink CompilePythonSources
            ReadMemoryFiles]
  :handlers [os-file-handler memory-file-handler rooted-file-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-file-handler (real) and memory-file-handler (no I/O) answer; rooted-file-handler moves paths under a single dir and re-fires them at the outer responder. ReadMemoryFiles is an effect that reads the storage of memory-file-handler, and only memory-file-handler answers it.")


(defdomain doeff-http-server
  :title "HTTP server vocabulary - listening for HTTP and WebSocket"
  :effects [HttpListen HttpNextRequest HttpTakeQueued HttpReadBody HttpRespond HttpForward HttpShutdown HttpStopListening
            WsAccept WsSendText WsSendTextEach WsForward WsClose TakeWsSendReport
            AppendHttpScript ReadHttpServed]
  :handlers [aiohttp-http-server scripted-http-server]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "aiohttp-http-server (real, extra http-server) and scripted-http-server (no I/O) answer. AppendHttpScript / ReadHttpServed are effects that add to the script and read the received commands, and only scripted-http-server answers them.")


(defdomain doeff-ws-client
  :title "WebSocket client vocabulary - connecting, passing frames, and closing on the connecting side"
  :effects [WsConnect WsReceive WsSend WsDisconnect WsDisconnectAll ReadWsSent]
  :handlers [aiohttp-ws-link-handler scripted-ws-link-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "aiohttp-ws-link-handler (real, extra http-server - the public installer is aiohttp-ws-client) and scripted-ws-link-handler (no I/O, scripted - the public installer is scripted-ws-client) answer. The installer wraps the program with closing-links and emits WsDisconnectAll at the end of the scope. ReadWsSent is an effect that reads the sent text and close records of the scripted responder, and only scripted-ws-link-handler answers it. This is a separate vocabulary from WsOpened / WsClose on the listening side (doeff-http-server).")


(defdomain doeff-sql
  :title "SQL vocabulary - generic SQL queries, inserts, and transactions"
  :effects [SqlQuery SqlInsertRows SqlEnsureTables SqlTransaction SqlBatch SqlNotify SqlHangNotice SqlDropNotice SetSqlOutage]
  :handlers [postgres-sql-handler pooled-postgres-sql-handler clickhouse-http-sql-handler sqlite-sql-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "postgres-sql-handler (does not block the scheduler - one thread per call) / pooled-postgres-sql-handler (likewise non-blocking - the caller's pool and the scheduler's semaphore) / clickhouse-http-sql-handler (real - likewise non-blocking, one thread per call) and sqlite-sql-handler (no I/O) answer. The responders share the SqlTransaction procedure, run-in-transaction of sql_transaction (it is not a handler itself). Only a transaction that chose the SqlTransaction field batched (default False) batches round trips (the procedure is run-in-batched-transaction). SqlBatch is a bundle that groups the round trips inside it, and it is answered by the scope that run-in-batched-transaction wraps - a bundle emitted inside a non-batched transaction or outside a transaction is refused by name (the ClickHouse responder does not answer it). SetSqlOutage is an effect that switches a simulated outage, and only sqlite-sql-handler answers it. SqlNotify, SqlHangNotice, and SqlDropNotice are LISTEN / NOTIFY doorbells, and only the two PostgreSQL responders answer them.")


(defdomain doeff-process
  :title "Process vocabulary - child processes and own environment"
  :effects [RunProcess ExecutableAt ReadEnvironment WorkingDirectory
            ProcessAlive ReadInterpreter ReadMachineName ResolveModule
            StartProcess PollProcess StopProcess SignalProcess WriteProcessInput]
  :handlers [subprocess-handler metered-offloaded-subprocess-handler scripted-process-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "subprocess-handler (real), metered-offloaded-subprocess-handler (real - the form without a meter is offloaded-subprocess-handler) and scripted-process-handler (no I/O, scripted) answer all 13 effects.")


(defdomain doeff-warm-process
  :title "Warm-child vocabulary - asking an already-loaded child process to work, and reading its end"
  :effects [ForkFromWarm PollWarmChild SignalWarmChild WriteWarmChildInput]
  :handlers [os-warm-process-handler scripted-warm-process-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-warm-process-handler (real - asks the warm child through its unix socket, and reads / sends after checking against the /proc start-ticks) and scripted-warm-process-handler (no I/O, scripted) answer all 4 effects (WriteWarmChildInput writes to the input pipe of a child requested with hold-input). A requested child is not a child of the requester, so its end cannot be read with PollProcess of the child-process vocabulary (doeff-process) - read it with this vocabulary.")


(defdomain doeff-proc-table
  :title "Process-table vocabulary - reading process parent/child relations and memory of the machine, and sending a signal to one process"
  :effects [ReadProcTable SignalPid]
  :handlers [os-proc-table-handler scripted-proc-table-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-proc-table-handler (real - reads Linux /proc, checks against start-ticks, and sends with os.kill) and scripted-proc-table-handler (no I/O, scripted table) answer both effects. Sending to a process group belongs to the vocabulary of SignalProcess in doeff-process and SignalWarmChild in doeff-warm-process.")


(defdomain doeff-process-exit
  :title "Child-exit wait vocabulary - waiting until a started or requested child ends"
  :effects [AwaitProcessExit AwaitWarmChildExit]
  :handlers [process-exit-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "process-exit-handler (real - waits in the event loop for a fd that becomes readable when the child ends [Linux = pidfd, macOS = kqueue EVFILT_PROC]) answers both effects. A wait that wakes at the end without re-polling periodically.
        PollProcess and PollWarmChild, which read the end only once, belong to the vocabularies of doeff-process and doeff-warm-process respectively.")


(defdomain doeff-channel
  :title "Channel vocabulary - a queue read in arrival order"
  :effects [CreateChannel PutChannel TakeChannel]
  :handlers [scheduler-channel-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "The only responder is scheduler-channel-handler (it has no I/O and is the same in production and simulation). Use it under scheduled.")


(defdomain doeff-compute
  :title "Compute vocabulary - running pure computation and receiving the answer as a value"
  :effects [Compute ReadComputeLoad]
  :handlers [thread-pool-compute-handler inline-compute-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "thread-pool-compute-handler (thread pool) and inline-compute-handler (no I/O, synchronous) answer both effects. The load reading ReadComputeLoad is the cumulative seconds of pool queue, running, and waiting (always 0 without I/O).")


(defdomain doeff-stop-signal
  :title "Stop signal vocabulary - requests to stop the process"
  :effects [StopRequested AwaitStop StopBell RaiseStop]
  :handlers [os-signal-stop-handler scripted-stop-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-signal-stop-handler reads a real SIGINT / SIGTERM with StopRequested, waits until stop with AwaitStop, and hands over a Future filled on stop (one per session) with StopBell. Only the no-I/O scripted-stop-handler raises a stop with RaiseStop.")


(defdomain doeff-heap
  :title "Heap vocabulary - collecting and freezing the heap of a continuously running process"
  :effects [CollectAndFreeze]
  :handlers [gc-freeze-handler scripted-freeze-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "gc-freeze-handler (real - touches the GC of the process) and scripted-freeze-handler (no I/O - does not touch the GC and answers with a decided number) answer.")


(defdomain doeff-latest
  :title "Latest vocabulary - publishing and reading the latest value, and waiting until it changes"
  :effects [PublishLatest ReadLatest AwaitLatest]
  :handlers [process-latest-handler memory-latest-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "process-latest-handler (real - storage inside the process) and memory-latest-handler (no I/O) answer.")


(defdomain doeff-stack-dump
  :title "StackDump vocabulary - a watchdog that writes the stacks of all threads while the shared execution flow is stalled"
  :effects [ArmStackDump DisarmStackDump ReadStackDumps]
  :handlers [faulthandler-stack-dump-handler memory-stack-dump-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "faulthandler-stack-dump-handler (real - the C thread of faulthandler counts down the deadline and writes to sys.stderr) and memory-stack-dump-handler (no I/O - only counts) answer.")


(defdomain doeff-meter
  :title "Meter vocabulary - meters for counts, seconds, and values"
  :effects [CountMetric ObserveSeconds ObserveSecondsBatch SetGauge ReadMeter]
  :handlers [process-meter-handler memory-meter-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "process-meter-handler (real - a meter inside the process) and memory-meter-handler (no I/O) answer all 5 effects.")


(defdomain doeff-step-tally
  :title "StepTally vocabulary - counting the steps the scheduler ran while a window was open (overall total and a per-task table)"
  :effects [OpenStepTally CloseStepTally OpenTaskTally ReadTaskTally CloseTaskTally]
  :handlers [step-tally-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "step-tally-handler (installs a sink in the measuring port of the Python scheduler and counts) answers all 5 effects.")


(defdomain doeff-random
  :title "Random vocabulary - random bytes"
  :effects [RandomBytes]
  :handlers [os-random-handler seeded-random-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-random-handler (real - os.urandom) and seeded-random-handler (no I/O - determined by the seed) answer.")


;; Effects for VM counts and switches (turned into effects from doeff_vm functions - there was no declaration, and check (c) was red in the 2026-10-29
;; daily check). vm_probe_handler is a raw Python handler whose clauses cannot be read, so it is declared with Handles.
(import doeff_core_effects [ReadVmLiveCounts ReadVmWorkCounts ReadVmScanCounts ReadInvariantChecks SetInvariantChecks
                            SetSchedulerStepBudgetArmed SetSchedulerTraceInstalled vm-probe-handler])

(defdomain doeff-vm-probe
  :title "VM probe vocabulary - reading doeff-vm counts and changing switches"
  :effects [ReadVmLiveCounts ReadVmWorkCounts ReadVmScanCounts ReadInvariantChecks SetInvariantChecks
            SetSchedulerStepBudgetArmed SetSchedulerTraceInstalled]
  :handlers [(Handles vm-probe-handler
                      ReadVmLiveCounts ReadVmWorkCounts ReadVmScanCounts ReadInvariantChecks SetInvariantChecks
                      SetSchedulerStepBudgetArmed SetSchedulerTraceInstalled)]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "vm-probe-handler (queries the VM of this process, holds no state) answers all 7 effects.")
