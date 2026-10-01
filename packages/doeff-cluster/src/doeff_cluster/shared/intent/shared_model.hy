;;; 共有の保存(cluster のどの worker で走っても同じ意味になる読み書き)の effect。service と task の業務コードが
;;; 状態を読み書きする口はこれだけ(file・lock・socket を直に触らない — 規則 = repo の root の .semgrep.yaml)。
;;; service どうしは戻り値でやり取りせず、この effect を通してだけつながる。本番では業務の系の共有の置き場に当たる物の、実験用の代役。
;;;
;;;   ReadShared prefix              → {鍵: 値}(鍵が prefix で始まる行だけ)
;;;   WriteShared key value expect [ttl-seconds] → bool。ttl-seconds を付けた行は期限を過ぎると coordinator が消す。expect = ANY なら無条件・None なら「行が無い時だけ」・値なら「今の値がそれと等しい時だけ」
;;;                                     (compare-and-set。複数の実行役が同じ行を取り合わないため)
;;; 値は JSON にできる物だけ。handler = shared_handlers.hy(shared-memory はテストの dict・shared-http は coordinator の /board)。
;;; この module は型と effect だけを持つ(SDK の型の置き場・agora-redesign #2107)。書いてよいかの判断(cas-allows・期限の検め)は
;;; doeff_cluster.shared.core.board_rules — coordinator の /board と fake の保存が同じ定義を使う。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass AnyExpect []
  "書きの条件(expect)を付けない印 ANY の型 — 書きの handler が expect の型に書けるよう公開の名にする(以前は内部名・#1692)。"
  (defn #^ str __repr__ [self] "ANY"))


(setv ANY (AnyExpect))

;; 盤の行の値の型(JSON にできる値 — 本番は coordinator の /board へ JSON で運ぶ)。WriteShared と送り手の要求の形が同じ型を使う。
(val JsonValue (| dict list str int float bool None))


(defclass [(dataclass :frozen True)] ReadShared [EffectBase]
  (#^ str prefix))


(defclass [(dataclass :frozen True)] WriteShared [EffectBase]
  (#^ str key)
  (#^ JsonValue value)
  (setv #^ (| JsonValue AnyExpect) expect ANY)
  ;; 行の期限(秒)。coordinator は期限を過ぎた行を消す(盤の掃除・2026-09-25)。None = ずっと残す。
  (setv #^ (| int float None) ttl-seconds None))
