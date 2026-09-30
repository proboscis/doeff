;;; 共有の保存(cluster のどの worker で走っても同じ意味になる読み書き)の effect。service と task の業務コードが
;;; 状態を読み書きする口はこれだけ(file・lock・socket を直に触らない — 規則 = repo の root の .semgrep.yaml)。
;;; service どうしは戻り値でやり取りせず、この effect を通してだけつながる。本番では業務の系の共有の置き場に当たる物の、実験用の代役。
;;;
;;;   ReadShared prefix              → {鍵: 値}(鍵が prefix で始まる行だけ)
;;;   WriteShared key value expect [ttl-seconds] → bool。ttl-seconds を付けた行は期限を過ぎると coordinator が消す。expect = ANY なら無条件・None なら「行が無い時だけ」・値なら「今の値がそれと等しい時だけ」
;;;                                     (compare-and-set。複数の実行役が同じ行を取り合わないため)
;;; 値は JSON にできる物だけ。handler = shared_handlers.hy(shared-memory はテストの dict・shared-http は coordinator の /board)。
(require doeff-hy.macros [defk val])
(import dataclasses [dataclass])
(import json)
(import doeff [EffectBase])
(import doeff_cluster.cluster_policy [board-allows])


(defclass _Any []
  (defn #^ str __repr__ [self] "ANY"))


(setv ANY (_Any))

;; 盤の行の値の型(JSON にできる値 — 本番は coordinator の /board へ JSON で運ぶ)。WriteShared と送り手の要求の形が同じ型を使う。
(val JsonValue (| dict list str int float bool None))


(defclass [(dataclass :frozen True)] ReadShared [EffectBase]
  (#^ str prefix))


(defclass [(dataclass :frozen True)] WriteShared [EffectBase]
  (#^ str key)
  (#^ JsonValue value)
  (setv #^ (| JsonValue _Any) expect ANY)
  ;; 行の期限(秒)。coordinator は期限を過ぎた行を消す(盤の掃除・2026-09-25)。None = ずっと残す。
  (setv #^ (| int float None) ttl-seconds None))


(defk json-snapshot [value]
  {:pre [(: value JsonValue)] :post [(: % JsonValue)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の値を JSON に通した写しにするため: fake の保存(shared-memory)が、本物(coordinator の /board へ JSON で運ぶ)と同じく
   書いた・読んだ値を呼び手の object と切り離し、同じ形(dict の鍵は文字列・tuple は list)で返す。JSON にできない値は TypeError
   (本物の client が本文を JSON にする時と同じ)。"
  (json.loads (json.dumps value)))


;; current / expect は盤の値そのもの(等しいかと ANY かだけを見る)。
(defn #^ bool cas-allows [#^ object current #^ bool present #^ object expect]
  "純粋: いまの値(無ければ present=False)と期待の値から、書いてよいかを決める。判断は coordinator の /board と同じ関数。"
  (board-allows current present (is-not expect ANY) expect))
