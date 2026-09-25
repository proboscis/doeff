;;; 共有の保存(cluster のどの worker で走っても同じ意味になる読み書き)の effect。service と task の業務コードが
;;; 状態を読み書きする口はこれだけ(file・lock・socket を直に触らない — 規則 = repo の root の .semgrep.yaml)。
;;; service どうしは戻り値でやり取りせず、この effect を通してだけつながる。本番では業務の系の共有の置き場に当たる物の、実験用の代役。
;;;
;;;   ReadShared prefix              → {鍵: 値}(鍵が prefix で始まる行だけ)
;;;   WriteShared key value expect [ttl-seconds] → bool。ttl-seconds を付けた行は期限を過ぎると coordinator が消す。expect = ANY なら無条件・None なら「行が無い時だけ」・値なら「今の値がそれと等しい時だけ」
;;;                                     (compare-and-set。複数の実行役が同じ行を取り合わないため)
;;; 値は JSON にできる物だけ。handler = shared_handlers.hy(shared-memory はテストの dict・shared-http は coordinator の /board)。
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_cluster.cluster_policy [board-allows])


(defclass _Any []
  (defn __repr__ [self] "ANY"))


(setv ANY (_Any))


(defclass [(dataclass :frozen True)] ReadShared [EffectBase]
  (#^ str prefix))


(defclass [(dataclass :frozen True)] WriteShared [EffectBase]
  (#^ str key)
  (#^ object value)
  (setv #^ object expect ANY)
  ;; 行の期限(秒)。coordinator は期限を過ぎた行を消す(盤の掃除・2026-09-25)。None = ずっと残す。
  (setv #^ (| int float None) ttl-seconds None))


(defn #^ bool cas-allows [current #^ bool present expect]
  "純粋: いまの値(無ければ present=False)と期待の値から、書いてよいかを決める。判断は coordinator の /board と同じ関数。"
  (board-allows current present (is-not expect ANY) expect))
