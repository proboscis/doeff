;;; Shared effect definitions
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] FetchPrice [EffectBase]
  #^ str ticker)

(defclass [(dataclass :frozen True)] FetchNews [EffectBase]
  #^ str day)

(defclass [(dataclass :frozen True)] LLMRank [EffectBase]
  #^ str prompt)

(defclass [(dataclass :frozen True)] SendSlack [EffectBase]
  #^ str message)
