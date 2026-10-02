;;; worker の drain(preStop)の問いの effect — coordinator への 1 回の呼び出し CoordinatorCall(#2025 の 3 本目で drain_client から分けた)。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "worker" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] CoordinatorCall [EffectBase]
  "coordinator へ要求を 1 つ送る。結果 = {\"status\" int \"body\" dict}、届かなければ {\"error\" 理由の文}。"
  (#^ str method)
  (#^ str path)
  (setv #^ (| dict None) body None))


(defclass [(dataclass :frozen True)] AskDrain [EffectBase]
  "worker name の drain を coordinator に頼む(期限 ttl-seconds・頼み手の process の世代 own-boot — 在れば同じ名の別の世代に drain を
   付けない)。答え = CoordinatorCall と同じ形({\"status\" int \"body\" dict}、届かなければ {\"error\" 理由の文})。
   要求の形(method・path・本文)は答え手(worker/protocol/drain_requests.hy)だけが知る — core は頼みの中身だけを出す(#2541)。"
  (#^ str name)
  (#^ float ttl-seconds)
  (setv #^ (| str None) own-boot None))

