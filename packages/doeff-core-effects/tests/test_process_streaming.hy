;;; Properties of the real process answerers only -- the scripted world has neither elapsed time nor concurrency, so properties that cannot go in the contract (test_process_contract.hy)
;;; are checked with the two real answerers (subprocess-handler, offloaded-subprocess-handler). The interpreter is
;;; process_contract_handlers.hy (conftest stacks scheduled).
;;;
;;;   * the output-path of stream-output can be read while the child runs (when the child reads its own log back, the line already written is there)
;;;   * children of offloaded-subprocess-handler run side by side (two Spawned sleeps finish in the time of one) -- subprocess-handler runs them in order
(require doeff-hy.macros [defk deftest <- val var])
(import time)
(import doeff_core_effects [Spawn Gather])
(import doeff_core_effects [EnvEntry EnvMode ProcessOutcome RunProcess])
(import process_contract_handlers [ContractRoot])

;; A statement where the child reads back the first line of its own log (waits 0.3 s after writing -- while the reader thread writes the line).
(val READ-OWN-LOG "echo first; sleep 0.3; head -n 1 \"$LIVE_LOG\"")
;; The sleep seconds of one side-by-side child, and the upper limit in seconds that counts as having run side by side (well below two sleeps' worth).
(val NAP "0.6")
(val SIDE-BY-SIDE-LIMIT 1.0)


(defk nap []
  {:pre [] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "program"}}
  "Run one child that sleeps NAP seconds (the unit that is Spawned side by side)."
  (<- outcome ProcessOutcome (RunProcess :argv #("sleep" NAP)))
  outcome)


(deftest test-the-streamed-output-path-is-readable-while-the-child-runs
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (val log (+ root "/live.log"))
  (<- read-back ProcessOutcome (RunProcess :argv #("/bin/sh" "-c" READ-OWN-LOG)
                                           :env #((EnvEntry :name "LIVE_LOG" :value log)) :env-mode EnvMode.EXTEND
                                           :output-path log :stream-output True))
  ;; The child reads the log after the first line is written, so the output has up to the second first (if added after the child ends, the file is missing when read back).
  (assert (= read-back (ProcessOutcome :exit-code 0 :stdout "first\nfirst\n" :stderr "")) (.format "log read back by the child {}" read-back)))


(deftest test-offloaded-children-run-side-by-side
  {:interpreters ["offloaded-subprocess"]}
  (val started (time.monotonic))
  (<- one (Spawn (nap)))
  (<- two (Spawn (nap)))
  (<- outcomes list (Gather one two))
  (val elapsed (- (time.monotonic) started))
  (assert (= (lfor o outcomes o.exit-code) [0 0]) outcomes)
  (assert (< elapsed SIDE-BY-SIDE-LIMIT) (.format "two children of {} s took {:.2f} s (not run side by side)" NAP elapsed)))
