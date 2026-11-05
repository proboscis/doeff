;;; Procedure of the conversation-input processing stage (the core of the 5 layers; one round of the worker and the resident loop of the former controllers/conversation/input_stage.hy and
;;; handlers_input.hy). Judgement is `judge` of conversation_input_judgment.hy; what is asked of the outside is intent effects
;;; (stream intake = ReadIntake, SettleIntake — controllers/messaging/intent/intake.hy; input row = ReadInput, WriteInput —
;;; controllers/messaging/intent/conversation_input.hy) and the clock (GetTime of doeff-time, the shared conversion epoch-seconds-of-time, and Delay of doeff-time).
;;;
;;; One round (input-round): read the stream further from the previous position -> fold the input-* requests that have no conclusion -> for requests whose claimed identity does not match, push a rejection conclusion -> fire the rest
;;; in the order received (up to INPUT-REQUESTS-PER-ROUND — the rest go to the next round).
;;; Procedure for one request (run-input-request): read the named row -> judge -> write (at most one) -> push the conclusion.
;;;   * Write conflict (WriteConflict) -> re-read and judge again (only once — the conflict with the turn's "taken" is settled here: if already taken,
;;;     the second judgement is input-not-pending). If the second also conflicts, go to the next round without pushing a conclusion.
;;;   * The store refused the write (WriteRefused) -> rejected{writer-refused: the refusal verbatim}.
;;;   * Row unreadable, write unreachable, or conclusion cannot be pushed -> go to the next round without pushing a conclusion (do not count unreadable as "absent").
;;;   * A later request naming the same input as a skipped request is not fired in this round (keep the order send -> edit -> withdraw).
;;; Every move is idempotent (send is done if the row exists; edit is done if the last edit has the same editId; withdraw is done if already withdrawn), so even if it dies after the write and
;;; before the conclusion, the next round converges to the same final state.
;;; The requestId of a request whose conclusion was pushed is kept in finished (so the same request is not re-fired in the rounds before the conclusion event appears in the stream read). finished keeps only the requests
;;; remaining in the fold. The fold leaves only requests without conclusions (so the resident fold does not grow with the length of the stream).
;;;
;;; Identity check: the body's by may be claimed only by the writer the store authenticated or by the receiving end — the single point of judgement is identity-problem of the shared core
;;; (controllers/shared/core/intake_rules.hy — read directly from where it is defined, not via the old mail location intake_stream). The refusal word
;;; identity-not-delegated and the bearer name agora-conversation are read from the vocabulary of the stream intake (controllers/messaging/intent/intake_types.hy — moved on the same day
;;; from the old locations of the routing table, intake_model.hy and intake_routes.hy).
;;;
;;; Move of location: moved on 2026-09-28 from controllers/core/conversation_input.hy. The old module is exported under the same name.
(require doeff-hy.macros [defk <- val var])
(import doeff_time [GetTime Delay])
(import datetime [datetime])
;; The current time is read with GetTime of doeff-time, and the conversion to the contract's scale uses the expression at the shared location.
(import controllers.shared.core.time_values [epoch-seconds-of-time])
(import controllers.shared.core.intake_rules [identity-problem])
(import controllers.messaging.intent.intake_types [REASON-IDENTITY ROUTE-INPUT])
(import controllers.messaging.intent.intake [IntakeRequested IntakeConcluded IntakePage IntakeUnreachable IntakeSettlement
                                             IntakeSettleLanded IntakeSettleRefused IntakeOutcome ReadIntake SettleIntake])
(import controllers.messaging.intent.conversation_input [InputRow InputAbsent InputUnreadable ReadInput WriteInput
                                                         WriteLanded WriteConflict WriteRefused WriteUnreachable InputDone InputRejected])
(import controllers.messaging.core.conversation_input_types [InputRequest Judgment RunOutcome InputQueue InputTally InputRound])
(import controllers.messaging.core.conversation_input_judgment [INPUT-REQUESTS-PER-ROUND REJECT-WRITER-REFUSED input-request? request-of
                                                                target-of judge settlement-of])

(val MODULE-TAGS {:context "messaging" :role "program"})

;; Number of attempts to re-read on conflict (the first one + one retry).
(val ATTEMPTS 2)


(defk judged [request]
  {:pre [(: request InputRequest)] :post [(: % (| Judgment None))]
   :effects [ReadInput] :tags {:context "messaging" :role "program"}}
  "Reads the named row and judges it. Could not read (unreachable) = None (skip this round)."
  (<- target (| str None) (target-of request))
  (var row None)
  (when (is-not target None)
    (<- answer (| InputRow InputAbsent InputUnreadable) (ReadInput target))
    (match answer
      (InputUnreadable) (return None)
      (InputRow) (:= row answer)
      (InputAbsent) None))
  (<- judgment Judgment (judge request row))
  judgment)


(defk outcome-of [request]
  {:pre [(: request InputRequest)] :post [(: % (| InputDone InputRejected None))]
   :effects [ReadInput WriteInput] :tags {:context "messaging" :role "program"}}
  "Judges and writes one request, and decides the conclusion to push. None = go to the next round without pushing a conclusion (unreadable, unreachable, or conflicts continued)."
  (for [_ (range ATTEMPTS)]
    (<- judgment (| Judgment None) (judged request))
    (when (is judgment None)
      (return None))
    (when (is judgment.write None)
      (return judgment.outcome))
    (<- answer (| WriteLanded WriteConflict WriteRefused WriteUnreachable) (WriteInput judgment.write))
    (match answer
      (WriteLanded) (return judgment.outcome)
      (WriteRefused) (return (InputRejected :reason REJECT-WRITER-REFUSED :detail answer.detail))
      (WriteUnreachable) (return None)
      (WriteConflict) None))
  None)


(defk run-input-request [request]
  {:pre [(: request InputRequest)] :post [(: % RunOutcome)]
   :effects [ReadInput WriteInput SettleIntake] :tags {:context "messaging" :role "program"}}
  "Materializes one request and pushes the conclusion. Answer = done / rejected (conclusion pushed) / deferred (to the next round)."
  (<- outcome (| InputDone InputRejected None) (outcome-of request))
  (when (is outcome None)
    (return RunOutcome.DEFERRED))
  (<- settlement IntakeSettlement (settlement-of request.request-id ROUTE-INPUT outcome))
  (<- settled (| IntakeSettleLanded IntakeSettleRefused IntakeUnreachable) (SettleIntake settlement))
  (match settled
    (IntakeSettleLanded) (match outcome
                           (InputDone) RunOutcome.DONE
                           (InputRejected) RunOutcome.REJECTED)
    _ RunOutcome.DEFERRED))
