;;; Executable ADR (decision): make the instruments of a long-running process (counting, observing seconds, current values), the handoff of the latest
;;; value across runs, and heap freezing into generic effects that know nothing of the business, and have the real answerer and the memory answerer pass the same contract tests.
;;;
;;; The agora-controllers screen passed its instrument (Meter), the breach ledger, and the startup-sync progress
;;; as the same object to the processing-loop run and to the separate probe run. Once the layers are split, the translation layer (protocol) cannot import the foundation's
;;; objects, so the only way to pass them is through effects. agora's ADR 'the environment is chosen by a set of handlers' R8 decided that 'the translation target is only doeff's generic effects; if one is missing,
;;; add it to doeff', and doeff's origin/main had no generic instrument effect (confirmed by the manager by searching all of packages by name,
;;; 2026-09-29 19:33 JST).
;;;
;;; Rollback: revert the commit that added this ADR (the 9 new modules and the checks disappear; no existing public shape was changed, so with no users
;;; nothing breaks). Set agora-controllers' doeff-min-commit back to the previous sha.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest val do! <-])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-004
  :title "Make the instruments, latest values, and heap freezing of a long-running process into generic effects — a write within one name is seen all at once; writes across names are not guaranteed to be"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/_meter_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/_process_meter.hy"
          "packages/doeff-core-effects/doeff_core_effects/_memory_meter.hy"
          "packages/doeff-core-effects/doeff_core_effects/_latest_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/_process_latest.hy"
          "packages/doeff-core-effects/doeff_core_effects/_memory_latest.hy"
          "packages/doeff-core-effects/doeff_core_effects/_heap_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/_gc_freeze.hy"
          "packages/doeff-core-effects/doeff_core_effects/_scripted_freeze.hy"]
  :problem
    [(fact
       "The agora-controllers screen's instrument Meter (controllers/screen/entry/meter.py, 592 lines) held in one class the judgment that decides the origin of an answer from the screen's types, a generic part that accumulates counters, gauges, and sums of seconds, and the raw side effects of time.monotonic, uuid, gc.callbacks, and threading. The processing-loop run writes, and the separate probe run (/metrics, /latency/samples) reads without waiting for the lock.")
     (fact
       "Once the layers are split, the translation layer cannot import the foundation's objects, and the foundation cannot read the business types. Passing something across runs needs an effect. doeff's origin/main had no generic instrument effect; all it had were doeff-cluster's ReportMetrics (reporting to the coordinator) and doeff-jev-proxy's ReadCounters (fixed purpose)."
       :evidence "the manager's decision (2026-09-29 19:46 JST)")
     (fact
       "State inside a handler (session values) exists only within one run. The probe runs in a separate run (a separate thread) so that it can answer while the processing loop is computing, so session values cannot read the same instrument."
       :evidence "the header comment of agora-controllers controllers/foundation/side_run.hy")]
  :context
    [(interpretation
       "The home is doeff-core-effects, because these are generic effects of a long-running process that know neither the business nor the cluster (the same kind as the neighboring stop_signal, thread_pool_compute, and scheduler_channel). doeff-cluster's ReportMetrics is an effect that sends a snapshot to the coordinator and has a different role, so only the shape of the snapshot (counters, gauges, sums and counts of seconds) was aligned, so that ReadMeter's answer can later be passed to ReportMetrics.")
     (interpretation
       "The real store is not made a class (ADR-DOE-HY-007 R3, R4 — it holds a lock and a gc callback, and its state changes). The answerer's module holds one 'name → store' map per process, and answerers installed with the same name (in different runs, different threads) read and write the same store. Precedent for a module holding a one-per-process object = the Await bridge in doeff_core_effects/_handlers.py. The answerer's arguments are only immutable values (name and settings). A reversible decision.")
     (interpretation
       "Heap freezing is a separate module from the instruments. Instruments only accumulate and read observations and do not change the process's behavior. Freezing is an operation that changes the process's GC state, and programs without instruments use it too. Putting them together would mean that a fake replacing the instruments also replaces the GC operation (condition 2 of the manager's decision).")
     (interpretation
       "The key of a latest value is not a string but exactly the value's type. A reader knows the answer's type from the key (the same convention as agora's settings reads — Ask key = type). If 2 owners publish the same type they conflict, so each owner publishes a distinct type. Rollback = add a key field.")
     (interpretation
       "An instrument write changes only one name per effect. agora's screen Meter showed the writes of one event (total and breakdown, etc., across names) all at once, but this shape cannot keep that across names. This shape is adopted because it matches the usual contract of a Prometheus client; a reader that wants to see something across names at once folds it into one name or tolerates the discrepancy. An effect that writes in bulk (e.g. RecordMetrics) is added when it turns out to be needed (adding it is enough). The list of agora-side checks and invariants that rely on the current property is written on the agora side (condition 2 of the manager's decision).")]
  :decision
    [(rule R1 "There are 5 instrument effects (meter_effects.hy): CountMetric (name, amount = 1.0 — adds to a counter; amount = 0 sets 0); ObserveSeconds (name, seconds — accumulates into the sum and count of seconds, and also advances the cumulative counters <name>_<label> for each matching bucket in the settings' bucket table and <name>_<inf-label>); ObserveSecondsBatch (observations = a sequence of SecondsObservation (name, seconds) — accumulates in one go into the same snapshot as firing ObserveSeconds one by one in sequence order, so that a caller observing seconds many times in one event fires the effect only once; writes across the names in the sequence are not promised, per R6); SetGauge (name, value — replaces); ReadMeter (answer = MeterSnapshot). Every answer other than ReadMeter's is None. Settings are MeterSettings (buckets, inf-label, gc-pause-name).")
     (rule R2 "There are 3 latest-value effects (latest_effects.hy): PublishLatest (value — replaces the entry keyed by type type(value)); ReadLatest (kind — the latest value of exactly that type, or None); AwaitLatest (kind, seen — waits until the latest value of that type is a different object from seen [compared with is] and answers that value. If it already differs, it does not wait. The wait is on a doorbell [CreateExternalPromise], rung by the publisher's PublishLatest. It has no timeout; a caller that waits up to a limit races it against a clock task and Cancels the loser — a cancelled wait removes its doorbell. The wait is a park that does not stop the virtual clock, used under scheduled. It was added so that another run can read a snapshot published by the writer's run when it is published, instead of re-checking at a fixed interval). Published values are immutable values.")
     (rule R3 "There is 1 heap-freezing effect (heap_effects.hy): CollectAndFreeze (answer = the number of objects frozen). It is in a separate module from the instruments.")
     (rule R4 "Real and I/O-free answerers are split by module: process-meter-handler (process_meter.hy) and memory-meter-handler (memory_meter.hy); process-latest-handler (process_latest.hy) and memory-latest-handler (memory_latest.hy); gc-freeze-handler (gc_freeze.hy) and scripted-freeze-handler (scripted_freeze.hy). Only the real modules touch the clock, gc, and threads; the effect modules and the I/O-free answerer modules do not. The 3 real ones are listed in doeff-linter's catalog of real-I/O handlers (packages/doeff-linter/data/world_handlers.json) — so that a user's repo can name them in :wraps of :world-handlers in architecture.hy (the I/O-free answerers are left out, per the catalog's rules).")
     (rule R5 "For the real instruments and latest values, answerers installed with the same name read and write one store even across runs and threads. Reads do not wait for the writer's lock (they take one reference to the current snapshot). The GC-pause callback does not take the lock (if collection starts in the thread holding the lock, re-acquiring it would wait on itself and hang). Installing the same name with different settings is refused with ValueError. The store lives until the process ends.")
     (rule R6 "One instrument write is seen all at once within one name (sum of seconds, count, and that name's bucket counters). Writes across names are not necessarily seen all at once — a read between writes to 2 names sees a snapshot in which only one has advanced.")
     (rule R7 "Real and I/O-free answerers pass the same contract tests: the :interpreters of test_meter_contract.hy, test_latest_contract.hy, and test_heap_freeze.hy in packages/doeff-core-effects/tests. Properties only the real ones have (sharing across runs and threads, refusing different settings, GC pauses) are in test_process_meter.hy and test_process_latest.hy.")
     (rule R8 "This change only adds. It does not rename any existing doeff type, function, or import.")]
  :laws
    [(law meter-write-is-whole-within-one-name
       :statement "write(meter, name) => every_read_sees(summary(name), buckets(name)) both_before or both_after"
       :counterexamples
         [(counterexample "ObserveSeconds swaps in the count of seconds first and the bucket counters later — a read in between sees count 3 and inf bucket 2")
          (counterexample "A read in another thread reads the writer's dict mid-write and sees a snapshot in which one name is only half written")])
     (law meter-writes-across-names-are-not-atomic
       :statement "write(meter, a); write(meter, b) => a_read_between may_see(a) without(b)"
       :counterexamples
         [(counterexample "A reader assumes the total (answers) always equals the sum of the breakdown (answers_entity_*) and checks it — a read in between disagrees, and a correct instrument is read as red")
          (counterexample "An ADR or comment promises 'the writes of one event are seen all at once even across names' — answerers of this shape cannot keep that")])
     (law real-and-memory-answer-one-contract
       :statement "effect in {meter, latest, heap} => real_answerer and io_free_answerer pass the_same_deftests"
       :counterexamples
         [(counterexample "Only the memory answerer does not set the name on amount = 0, so in the fake 'absent' and '0' cannot be told apart")
          (counterexample "Only the real one accumulates bucket counters, so checks against the fake cannot see bucket counts")])
     (law raw-side-effects-stay-in-real-answerers
       :statement "module in {effects, io_free_answerers} => imports none_of(gc, threading, time)"
       :counterexamples
         [(counterexample "memory_meter.hy measures GC pauses with time.monotonic, and the fake's answers wobble with the real clock")
          (counterexample "A pure function in meter_effects.hy takes a threading.Lock, and checks against the fake touch threads")])]
  :enforcement
    [(deftest test-adr-doe-core-effects-004-one-name-is-written-at-once
       ;; Mechanical side of law meter-write-is-whole-within-one-name (within one run): read after every observation; the inf bucket and the count are always equal.
       ;; The cross-thread side is test-a-reader-in-another-thread-never-sees-half-of-one-name in packages/doeff-core-effects/tests/test_process_meter.hy.
       (import doeff [run with_handlers])
       (import doeff_core_effects [state])
       (import doeff_core_effects [MeterBucket MeterSettings ObserveSeconds ReadMeter])
       (import doeff_core_effects [memory-meter-handler])
       (val settings (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf"))
       (val snapshots (run (with_handlers [(state) (memory-meter-handler settings)]
                             (do! (<- (ObserveSeconds "fold" 0.5)) (<- after-short (ReadMeter))
                                  (<- (ObserveSeconds "fold" 2.0)) (<- after-long (ReadMeter))
                                  (<- (ObserveSeconds "fold" 0.1)) (<- after-shorter (ReadMeter))
                                  #(after-short after-long after-shorter)))))
       (for [snapshot snapshots]
         (assert (= (get snapshot.counters "fold_le_inf") (float (. (get snapshot.durations "fold") count))))))
     (deftest test-adr-doe-core-effects-004-two-names-are-not-written-at-once
       ;; Counterexample check for law meter-writes-across-names-are-not-atomic: a read between writes to 2 names sees a snapshot in which only one has advanced.
       (import doeff [run with_handlers])
       (import doeff_core_effects [state])
       (import doeff_core_effects [CountMetric MeterSettings ReadMeter])
       (import doeff_core_effects [memory-meter-handler])
       (val reads (run (with_handlers [(state) (memory-meter-handler (MeterSettings))]
                         (do! (<- (CountMetric "answers"))
                              (<- between (ReadMeter))
                              (<- (CountMetric "answers_entity_board"))
                              (<- after (ReadMeter))
                              #(between after)))))
       (val between (get reads 0))
       (val after (get reads 1))
       (assert (= (get between.counters "answers") 1.0))
       (assert (not-in "answers_entity_board" between.counters))
       (assert (= (get after.counters "answers_entity_board") 1.0)))
     (deftest test-adr-doe-core-effects-004-io-free-modules-import-no-clock-gc-or-thread
       ;; Mechanical side of law raw-side-effects-stay-in-real-answerers: read the imports of the effect modules and the I/O-free answerer modules.
       (import pathlib [Path])
       (import re)
       (val root (/ (. (Path __file__) parent parent parent) "packages" "doeff-core-effects" "doeff_core_effects"))
       (val io-free ["_meter_effects.hy" "_memory_meter.hy" "_latest_effects.hy" "_memory_latest.hy" "_heap_effects.hy" "_scripted_freeze.hy"])
       (val raw (re.compile r"\(import\s+(gc|threading|time)\b"))
       (val found (lfor file io-free :if (.search raw (.read-text (/ root file) :encoding "utf-8")) file))
       (assert (= found []) (.format "I/O-free modules that import the clock, gc, or threads: {!r}" found)))]
  :plans ["docs/adr/defadr_doeff_core_effects_004_process_meter_and_latest_values.hy"
          "packages/doeff-core-effects/tests/test_meter_contract.hy"
          "packages/doeff-core-effects/tests/test_process_meter.hy"
          "packages/doeff-core-effects/tests/test_latest_contract.hy"
          "packages/doeff-core-effects/tests/test_process_latest.hy"
          "packages/doeff-core-effects/tests/test_heap_freeze.hy"
          "agora-controllers: move the screen's instruments onto these effects"])
