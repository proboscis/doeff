(require doeff-hy.macros [defk defhandler deftest <-])
(import collections.abc [Callable])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Gather])
(import doeff_agents.sessionhost.acp.async_dispatch [async-dispatch])
(import doeff_agents.sessionhost.acp.effects [AcpGet SessionGet])
(import doeff_agents.sessionhost.acp.effects [SessionSend SessionRefused])
(import doeff_agents.sessionhost.acp.worker_loop [cache-send-serialization])
(import doeff_agents.sessionhost.acp.loop_model [LoopDelay])

(defhandler normal-send-test [#^ dict state #^ bool cache-busy]
  (LoopDelay [seconds]
    (assert (= seconds 0.25))
    (setv (get state "delays") (+ (get state "delays") 1))
    (resume None))
  (SessionSend []
    (setv (get state "attempts") (+ (get state "attempts") 1))
    (cond
      (not cache-busy) (resume (SessionRefused "session gone" "not-found"))
      (< (get state "attempts") 3)
        (resume (SessionRefused "ping running" "cache-maintenance-active"))
      True (resume None))))

(deftest test-cache-maintenance-conflict-waits-without-failing-normal-job
  (setv state {"attempts" 0 "delays" 0})
  (<- answer ((normal-send-test state True)
    ((cache-send-serialization) (SessionSend :session-id "s" :text "work" :awaiting True))))
  (assert (is answer None))
  (assert (= state {"attempts" 3 "delays" 2})))

(deftest test-other-send-refusal-is-not-retried
  (setv state {"attempts" 0 "delays" 0})
  (<- answer ((normal-send-test state False)
    ((cache-send-serialization) (SessionSend :session-id "s" :text "work" :awaiting True))))
  (assert (= answer.error-code "not-found"))
  (assert (= state {"attempts" 1 "delays" 0})))

(defk two-independent-io []
  {:pre [True] :post [(: % "None")]}
  (<- slow (Spawn (AcpGet :kind "blocked-network")))
  (<- fast (Spawn (SessionGet :session-id "cache-ping")))
  (<- (Gather slow fast))
  None)

(defk exercise-async-dispatch [dispatcher]
  {:pre [(: dispatcher Callable)] :post [(: % "None")]}
  (<- (scheduled ((await-handler)
    ((async-dispatch dispatcher "doeff_agents.sessionhost.acp.") (two-independent-io)))))
  None)
