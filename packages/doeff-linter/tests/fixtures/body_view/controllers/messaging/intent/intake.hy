;;; Fixture for the body-text tests — copied from agora-controllers d89796e67 controllers/messaging/intent/intake.hy,
;;; only the effect (SettleIntake) fired by run-input-request in core/conversation_input.hy and its answer types.
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])

(defrecord IntakeSettlement "Outcome" (#^ str request-id))
(defrecord IntakeSettleLanded "Landed" (#^ str request-id))
(defrecord IntakeSettleRefused "Refused" (#^ str detail))
(defrecord IntakeUnreachable "Unreachable" (#^ str detail))

(defeffect SettleIntake
  "Land the outcome of one request on the intake queue (answer = the head annotation)."
  {:fields [(: settlement IntakeSettlement)]
   :answer (| IntakeSettleLanded IntakeSettleRefused IntakeUnreachable)
   :tags {:context "messaging" :role "intent"}})
