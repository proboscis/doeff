;;; 本体の文字のテストの fixture — agora-controllers d89796e67 の controllers/messaging/intent/conversation_input.hy から、
;;; core/conversation_input.hy の judged・run-input-request が撃つ effect(ReadInput・WriteInput)と答えの型だけを写した。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])

(defrecord InputRow "入力の行" (#^ str id))
(defrecord InputAbsent "無い" (#^ str id))
(defrecord InputUnreadable "読めない" (#^ str id))
(defrecord InputDone "済み" (#^ str id))
(defrecord InputRejected "断り" (#^ str reason) (#^ str detail))
(defrecord WriteLanded "書けた" (#^ str id))
(defrecord WriteConflict "競合" (#^ str id))
(defrecord WriteRefused "断られた" (#^ str detail))
(defrecord WriteUnreachable "届かない" (#^ str id))

(defeffect ReadInput
  "入力の行を id で 1 つ読む(答え = 頭の註)。"
  {:fields [(: id str)]
   :answer (| InputRow InputAbsent InputUnreadable)
   :tags {:context "messaging" :role "intent"}})

(defeffect WriteInput
  "入力の行を書く。"
  {:fields [(: write str)]
   :answer (| WriteLanded WriteConflict WriteRefused WriteUnreachable)
   :tags {:context "messaging" :role "intent"}})
