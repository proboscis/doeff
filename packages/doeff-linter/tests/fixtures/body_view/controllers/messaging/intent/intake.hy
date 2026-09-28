;;; 本体の文字のテストの fixture — agora-controllers d89796e67 の controllers/messaging/intent/intake.hy から、
;;; core/conversation_input.hy の run-input-request が撃つ effect(SettleIntake)と答えの型だけを写した。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])

(defrecord IntakeSettlement "結末" (#^ str request-id))
(defrecord IntakeSettleLanded "積めた" (#^ str request-id))
(defrecord IntakeSettleRefused "断られた" (#^ str detail))
(defrecord IntakeUnreachable "届かない" (#^ str detail))

(defeffect SettleIntake
  "列 intake に要求 1 つの結末を積む(答え = 頭の註)。"
  {:fields [(: settlement IntakeSettlement)]
   :answer (| IntakeSettleLanded IntakeSettleRefused IntakeUnreachable)
   :tags {:context "messaging" :role "intent"}})
