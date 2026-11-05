;;; Fixture for the body-text test — copied from controllers/messaging/intent/conversation_input.hy of agora-controllers d89796e67,
;;; keeping only the effects (ReadInput, WriteInput) fired by judged and run-input-request in core/conversation_input.hy, and the answer types.
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])

(defrecord InputRow "an input row" (#^ str id))
(defrecord InputAbsent "absent" (#^ str id))
(defrecord InputUnreadable "unreadable" (#^ str id))
(defrecord InputDone "done" (#^ str id))
(defrecord InputRejected "rejection" (#^ str reason) (#^ str detail))
(defrecord WriteLanded "written" (#^ str id))
(defrecord WriteConflict "conflict" (#^ str id))
(defrecord WriteRefused "refused" (#^ str detail))
(defrecord WriteUnreachable "unreachable" (#^ str id))

(defeffect ReadInput
  "Reads one input row by id (the answer = the comment at the head)."
  {:fields [(: id str)]
   :answer (| InputRow InputAbsent InputUnreadable)
   :tags {:context "messaging" :role "intent"}})

(defeffect WriteInput
  "Writes an input row."
  {:fields [(: write str)]
   :answer (| WriteLanded WriteConflict WriteRefused WriteUnreachable)
   :tags {:context "messaging" :role "intent"}})
