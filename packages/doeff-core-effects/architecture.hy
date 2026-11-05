;;; Layer declaration for doeff-core-effects.
;;;
;;; doeff-linter decides the population of per-statement Python rules from the nearest architecture.hy above a file (this file) (#2811).
;;; Only one layer, foundation, is declared, and it names just two modules: the entry point that selects the scheduler handler, and the handler that
;;; answers Ask from environment variables. The population for the other files in this package is unchanged (DOEFF004 applies as before).
;;;
;;; foundation = the layer of foundation handlers that may read environment variables (environment reads are
;;;   allowed only inside foundation handlers; reads outside them are violations).
;;;   - doeff_core_effects._scheduler: the entry point that selects the scheduler handler (python / rust) before the handler stack exists; it reads DOEFF_SCHEDULER.
;;;     It selects before any handler stack exists, so asking via ReadEnvironment would add a fixed cost of about 40% to every doeff run
;;;     (measured: 173 us per query, 415 us per scheduled run - decided by cisco-c8 2026-10-02 23:4x).
;;;   - doeff_core_effects._handlers: the handler that answers Ask from environment variables (reads names with a prefix).
;;;   - Rule exempted: DOEFF004 (reading os.environ directly) - limited to the two named modules; no other files are to be added.
;;; How to revert: delete the :exempt line and DOEFF004 applies to the two modules again as before. If this file is deleted, the population of this package's files
;;;   goes back to the root configuration.
;;;
;;; Invariants (scheduler stall watch - this is a judgment inside the scheduler, outside any Program, so it cannot go in defservice :invariants; the clauses are
;;; written here in one place. The judging function is the single _external_wait_is_stall in doeff_core_effects._scheduler, and the Python and Rust schedulers call the same
;;; function):
;;;   S1 a-stall-is-a-block-no-registered-wake-will-end - when the scheduler waits for external completion and nothing arrives for one period (EXTERNAL_STALL_LOG_INTERVAL_SECONDS,
;;;     30 seconds in production), it counts that as a stall and writes a warning (the #495b watch) only in one of these cases:
;;;       - There is no wait with a registered wake-up promise. A wait with a registered wake-up promise = a clock wait with a known deadline (CreateExternalPromise's
;;;         deadline - Delay, WaitUntil, etc. - #765), or a wait that watches the source of its completion notice (source_alive - e.g. the exit
;;;         fd of a child process).
;;;       - There is a wait that broke its promise (a lost wake-up). A clock wait counts from its deadline, a watching wait from the moment the source is first seen to have ended;
;;;         nothing arrived within one period after that.
;;;     While blocked only on external waits that have neither a deadline nor a source, it warns every period as it does now.
;;;     Failure cases = TestDeadlockDiagnostics in tests/test_scheduler.py (stay silent on a watching wait whose source is alive; report a wait whose source has ended;
;;;     report a wait with no deadline), test-waiting-on-a-running-child-is-not-a-stall in packages/doeff-core-effects/tests/test_process_exit_wait.hy
;;;     (stay silent while waiting on a running child - before the fix it warned every period) and
;;;     test-a-child-that-ended-without-its-exit-notice-is-a-stall (report a wait on a child whose exit notice never arrives),
;;;     and packages/doeff-time/tests/test_async_handler.py (stay silent on a clock wait; report a clock wait past its deadline). All of them run on both the python and rust
;;;     schedulers.
(defarchitecture doeff-core-effects
  :root "."
  :layers [(layer foundation
             :summary "The layer of foundation handlers that may read environment variables - the entry point that selects the scheduler handler, and the handler that answers Ask from environment variables"
             :knows "Names of environment variables, names of scheduler implementations, the mapping from Ask keys to environment variable names"
             :does-not-know "Business Programs, which business uses which settings"
             :modules [doeff_core_effects._scheduler doeff_core_effects._handlers]
             :exempt [(rule DOEFF004 "The foundation layer - the entry point that selects the scheduler handler before the handler stack exists, and the handler that answers Ask from environment variables. Environment reads are allowed only inside foundation handlers")])])
