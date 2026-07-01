(require doeff-hy.macros [deftest defk <-])

(import pathlib [Path])
(import doeff [run])
(import doeff_core_effects.scheduler [scheduled])
(import doeff-agents [AgentType])
(import doeff-agents.effects [AgentSpec AwaitOutcome AwaitResult AwaitStatus LaunchSession])
(import doeff-agents.effects.agent [deterministic-session-id])
(import doeff-agents.handlers.testing [ScenarioAgentHandler ScenarioStep])


(setv _SCHEMA
      {"type" "object"
       "required" ["ok"]
       "properties" {"ok" {"type" "boolean"}}
       "additionalProperties" False})

(setv _RUN "await-result")
(setv _NODE "transient")
(setv _ATTEMPT 0)
(setv _SID (deterministic-session-id :run-id _RUN :node-id _NODE :attempt _ATTEMPT))


(defk _await-session-once [work-dir]
  {:pre [(: work-dir Path)]
   :post [(: % AwaitOutcome)]}
  (setv spec
        (AgentSpec
          :run-id _RUN
          :node-id _NODE
          :attempt _ATTEMPT
          :agent-type AgentType.CODEX
          :work-dir work-dir
          :prompt "return structured result"
          :result-schema _SCHEMA))
  (<- handle (LaunchSession spec))
  (<- outcome (AwaitResult handle :timeout-seconds 0.05))
  outcome)


(deftest test-await-result-reobserves-transient-awaiting-input [tmp-path]
  (setv handler
        (ScenarioAgentHandler
          :scripts {_SID [(ScenarioStep.awaiting-input "status prompt")
                          (ScenarioStep.success {"ok" True})]}))
  (setv outcome (run (scheduled (.wrap handler (_await-session-once tmp-path)))))
  (assert (= outcome.status AwaitStatus.EXITED))
  (assert (= outcome.result {"ok" True})))


(deftest test-await-result-result-wins-over-awaiting-input-status [tmp-path]
  (setv handler
        (ScenarioAgentHandler
          :scripts {_SID [(ScenarioStep
                            :status AwaitStatus.AWAITING_INPUT
                            :payload {"ok" True})]}))
  (setv outcome (run (scheduled (.wrap handler (_await-session-once tmp-path)))))
  (assert (= outcome.status AwaitStatus.AWAITING_INPUT))
  (assert (= outcome.result {"ok" True})))


(deftest test-await-result-returns-stable-awaiting-input [tmp-path]
  (setv handler
        (ScenarioAgentHandler
          :scripts {_SID [(ScenarioStep.awaiting-input "needs input")]}))
  (setv outcome (run (scheduled (.wrap handler (_await-session-once tmp-path)))))
  (assert (= outcome.status AwaitStatus.AWAITING_INPUT))
  (assert (= outcome.validation-error "needs input")))
