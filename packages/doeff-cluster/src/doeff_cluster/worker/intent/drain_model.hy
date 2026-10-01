;;; worker の drain(preStop)の問いの effect — coordinator への 1 回の呼び出し CoordinatorCall(#2025 の 3 本目で drain_client から分けた)。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] CoordinatorCall [EffectBase]
  "coordinator へ要求を 1 つ送る。結果 = {\"status\" int \"body\" dict}、届かなければ {\"error\" 理由の文}。"
  (#^ str method)
  (#^ str path)
  (setv #^ (| dict None) body None))
