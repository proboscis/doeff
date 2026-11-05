;;; Contract test for the warm child effects -- the real one (os-warm-process-handler) and the fake
;;; (scripted-warm-process-handler) that answer the same effects (ForkFromWarm, PollWarmChild, SignalWarmChild) pass through the same deftest. The interpreter assembly and the contract world are in warm_contract_handlers.hy.
;;;
;;;   * on request, child A's pid and start-ticks come back; while running it is WarmRunning, and once ended, the exit code in the exit file (WarmExited)
;;;   * the signal is sent to the child's group, and a child whose entry point ended by a signal has a negative exit code. Nothing is sent to an ended child (WarmGone)
;;;   * a reused pid (different start-ticks) is WarmLost if there is no exit file, and no signal is sent (WarmGone)
;;;   * a child that vanished without writing the exit file is WarmLost
;;;   * refusals: no socket, the warm child answers with a refusal, the answer deadline expires (the real one takes 5 seconds of WARM-ANSWER-SECONDS)
;;;   * env values appear in no answer or refusal text
;;;   * the one-line promise with the warm child round-trips both the request and the answer (a line of a different shape is an unreadable refusal)
(require doeff-hy.macros [defk deftest <- val var])
(import doeff_hy._wire [Malformed])
(import os)
(import unittest [mock])
(import doeff_core_effects [EnvEntry ProcessSignal ProcessSignalRefused RunProcess])
(import doeff_core_effects [ForkFromWarm PollWarmChild SignalWarmChild WarmForked WarmRefused WarmRunning WarmExited WarmLost WarmSignaled WarmGone]
        doeff_core_effects._warm_effects [warm-socket-missing warm-answer-late warm-answer-unreadable warm-lost warm-gone])
(import doeff_core_effects [warm-request-of warm-answer-line]
        doeff_core_effects._os_warm_process [WarmRequestWire warm-request-line warm-answer-of])
(import warm_contract_handlers [WarmPaths WarmWorldPaths REFUSAL EXIT-3 SLEEPER VANISH])

(val SECRET "doeff-warm-secret-value")


(defk forked-at [paths socket-path entry name [env #()]]
  {:pre [(: paths WarmPaths) (: socket-path str) (: entry str) (: name str) (: env (of tuple EnvEntry ...))]
   :post [(: % (| WarmForked WarmRefused))] :tags {:context "warm-test" :role "program"}}
  "To request one job in the contract world (cwd = root; the log and exit files are named name under the root)."
  (<- answer (| WarmForked WarmRefused)
      (ForkFromWarm :socket-path socket-path :entry entry :cwd paths.root :env env
                    :log-path (+ paths.root "/" name ".log") :exit-path (+ paths.root "/" name ".exit")))
  answer)


(defk exit-path-of [paths name]
  {:pre [(: paths WarmPaths) (: name str)] :post [(: % str)] :tags {:context "warm-test" :role "judgment"}}
  "To look up the path of the exit file that forked-at gave to the job named name."
  (+ paths.root "/" name ".exit"))


(defk done-soon [forked exit-path [ticks None]]
  {:pre [(: forked WarmForked) (: exit-path str) (: ticks (| int None))] :post [(: % (| WarmRunning WarmExited WarmLost))]
   :tags {:context "warm-test" :role "program"}}
  "Ask the requested child every 0.05 seconds, for up to 5 seconds until it ends -- the real child may still be running. Answer = the last PollWarmChild answer."
  (val start-ticks (if (is ticks None) forked.start-ticks ticks))
  (var seen (WarmRunning :pid forked.pid))
  (var tries 0)
  (while (and (isinstance seen WarmRunning) (< tries 100))
    (<- polled (PollWarmChild :pid forked.pid :start-ticks start-ticks :exit-path exit-path))
    (:= seen polled)
    (when (isinstance seen WarmRunning)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  seen)


(deftest test-a-forked-child-runs-and-its-exit-code-is-read-from-the-exit-file
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting EXIT-3 "exit3"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "exit3"))
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code 3)) ended))


(deftest test-a-signal-ends-the-childs-group-and-an-ended-child-is-not-signalled
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting SLEEPER "sleeper"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "sleeper"))
  (<- running (PollWarmChild :pid forked.pid :start-ticks forked.start-ticks :exit-path exit-path))
  (assert (= running (WarmRunning :pid forked.pid)) running)
  (<- sent (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.TERM))
  (assert (= sent (WarmSignaled :pid forked.pid)) sent)
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code -15)) (.format "The exit code of an entry point ended by TERM is -15: {}" ended))
  (<- again (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL))
  (<- gone WarmGone (warm-gone forked.pid))
  (assert (= again gone) again))

(deftest test-a-signal-the-os-refuses-is-answered-as-refused-and-the-child-keeps-running
  ;; A machine where the OS refuses the send (EPERM -- on helios on 2026-11-01, AppArmor denied signals between processes inside a container). A refused
  ;; signal is not answered as WarmSignaled -- the answer is ProcessSignalRefused, and the child keeps running. Counterexample = a PermissionError being raised, or a shape that answers
  ;; as if sent (before the fix) is red. The scripted answerer has no OS refusal, so this is for the real one only.
  {:interpreters ["os-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting SLEEPER "refused"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "refused"))
  (with [_ (mock.patch.object os "killpg" :side-effect (PermissionError 1 "Operation not permitted"))]
    (<- sent (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL)))
  (assert (= sent (ProcessSignalRefused :pid forked.pid)) sent)
  (<- running (PollWarmChild :pid forked.pid :start-ticks forked.start-ticks :exit-path exit-path))
  (assert (= running (WarmRunning :pid forked.pid)) running)
  (<- killed (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL))
  (assert (= killed (WarmSignaled :pid forked.pid)) killed))



(deftest test-a-reused-pid-is-lost-and-is-not-signalled
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting SLEEPER "reused"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "reused"))
  (val other-ticks (+ forked.start-ticks 1))
  (<- polled (PollWarmChild :pid forked.pid :start-ticks other-ticks :exit-path exit-path))
  (<- lost WarmLost (warm-lost forked.pid exit-path))
  (assert (= polled lost) (.format "A pid with different start-ticks is WarmLost if there is no exit file: {}" polled))
  (<- refused-signal (SignalWarmChild :pid forked.pid :start-ticks other-ticks :signal ProcessSignal.KILL))
  (<- gone WarmGone (warm-gone forked.pid))
  (assert (= refused-signal gone) (.format "Nothing is sent to a pid with different start-ticks: {}" refused-signal))
  ;; Cleanup: stop it by sending KILL with the true start-ticks (the real entry point sleeps 30 seconds, so do not leave it). KILL also takes down child A at the head of the group,
  ;; so the exit file is not written and it ends as WarmLost.
  (<- sent (SignalWarmChild :pid forked.pid :start-ticks forked.start-ticks :signal ProcessSignal.KILL))
  (assert (= sent (WarmSignaled :pid forked.pid)) sent)
  (<- ended (done-soon forked exit-path))
  (assert (= ended lost) (.format "A child stopped together with A by KILL has no exit file and is WarmLost: {}" ended)))


(deftest test-a-child-that-vanishes-without-an-exit-file-is-lost
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- forked (forked-at paths paths.accepting VANISH "vanish"))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "vanish"))
  (<- ended (done-soon forked exit-path))
  (<- lost WarmLost (warm-lost forked.pid exit-path))
  (assert (= ended lost) ended))


(deftest test-a-missing-socket-and-a-refusing-waiting-child-are-refused-by-name
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- missing (forked-at paths paths.missing EXIT-3 "missing"))
  (<- missing-text str (warm-socket-missing paths.missing))
  (assert (= missing (WarmRefused :detail missing-text)) missing)
  (<- refused (forked-at paths paths.refusing EXIT-3 "refused"))
  (assert (= refused (WarmRefused :detail REFUSAL)) refused))


(deftest test-a-silent-waiting-child-is-refused-at-the-answer-deadline
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (<- late (forked-at paths paths.silent EXIT-3 "late"))
  (<- late-text str (warm-answer-late paths.silent))
  (assert (= late (WarmRefused :detail late-text)) late))


(deftest test-env-values-appear-in-no-answer-and-no-refusal
  {:interpreters ["os-warm" "scripted-warm"]}
  (<- paths WarmPaths (WarmWorldPaths))
  (val env #((EnvEntry :name "DOEFF_WARM_SECRET" :value SECRET)))
  (<- missing (forked-at paths paths.missing EXIT-3 "env-missing" env))
  (<- refused (forked-at paths paths.refusing EXIT-3 "env-refused" env))
  (<- forked (forked-at paths paths.accepting EXIT-3 "env-forked" env))
  (for [answer #(missing refused forked)]
    (assert (not-in SECRET (repr answer)) (.format "An env value appeared in the answer: {}" (type answer))))
  (assert (isinstance forked WarmForked) forked)
  (<- exit-path str (exit-path-of paths "env-forked"))
  (<- ended (done-soon forked exit-path))
  (assert (= ended (WarmExited :pid forked.pid :exit-code 3)) ended))


(deftest test-the-request-and-the-answer-lines-round-trip
  {:interpreters ["scripted-warm"]}
  (val request (ForkFromWarm :socket-path "/s.sock" :entry "e" :args #("a" "b") :cwd "/w" :env #((EnvEntry :name "N" :value "value"))
                             :log-path "/w/l" :exit-path "/w/x" :grace-seconds 2.5))
  (<- line bytes (warm-request-line request))
  (<- read-back (| WarmRequestWire Malformed) (warm-request-of line))
  (assert (isinstance read-back WarmRequestWire) read-back)
  (assert (= #(read-back.entry read-back.args read-back.cwd read-back.log-path read-back.exit-path read-back.grace-seconds)
             #("e" #("a" "b") "/w" "/w/l" "/w/x" 2.5))
          read-back)
  (assert (= (tuple (gfor e read-back.env #(e.name e.value))) #(#("N" "value"))) read-back.env)
  (<- broken (| WarmRequestWire Malformed) (warm-request-of b"{\"entry\": 1}"))
  (assert (isinstance broken Malformed) broken)
  (for [answer #((WarmForked :pid 12 :start-ticks 34) (WarmRefused :detail "refusal"))]
    (<- answer-line bytes (warm-answer-line answer))
    (<- answer-back (| WarmForked WarmRefused) (warm-answer-of answer-line "/s.sock"))
    (assert (= answer-back answer) answer-back))
  (<- unreadable-answer (| WarmForked WarmRefused) (warm-answer-of b"{\"pid\": \"12\"}" "/s.sock"))
  (<- unreadable-text str (warm-answer-unreadable "/s.sock"))
  (assert (= unreadable-answer (WarmRefused :detail unreadable-text)) unreadable-answer))
