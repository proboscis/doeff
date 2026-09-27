;;; 検の口: process の死を注入する effect(公開 effect ではない)。
;;;
;;; 「手番の途中に process が消えたら BackendLost・次の ResumeSession は通る」(設計 7 節・8 節 8)を、fake と本番の handler の両方で
;;; 同じ筋書きで確かめるための口。本番の handler は会話の process に SIGKILL を送る(OOM・kill と同じ形 — 読み手は終わりの行を
;;; 読まずに stdout の EOF を見る)。fake は走っている手番を BackendLost で終える。業務の Program はこの effect を使わない。
;;;
;;; ClaudeForgetSession は家を空にした形(上の層の Pod を作り直して claude の家が消えた — 上の層が記憶の写しを持ち込んで続ける筋書きの口)。
;;; fake だけが答える(本番の handler は家の file を消さない)。
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ClaudeDropProcess [EffectBase]
  "会話の process を消す。答え = 消す process が在ったか(bool)。"
  (#^ str session-id))


(defclass [(dataclass :frozen True)] ClaudeForgetSession [EffectBase]
  "家から会話を消す(走っている手番は BackendLost で終わる)。答え = 忘れた物が在ったか(bool)。fake だけが答える。"
  (#^ str session-id))
