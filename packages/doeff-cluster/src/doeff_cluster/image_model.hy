;;; container image の LABEL を読む effect(版の追随 — base_follow_policy.hy)。
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass ImageUnavailable [Exception]
  "registry に届かない・image が無い・形が読めない。追随はその image を読めなかったと記録し、base を動かさない。")

(defclass [(dataclass :frozen True)] ReadImageLabels [EffectBase]
  "image(「registry の host:port/名:tag」)の config の Labels(dict)。読めなければ ImageUnavailable。"
  (#^ str image))
