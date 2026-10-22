;;; 汎用の計算の effect(compute_effects.hy)の I/O なしの答え手 inline-compute-handler(agora-redesign #802 便 4)。thread を使わず、撃った
;;; その場で program を新しい VM で回して答える。本物の thread-pool-compute-handler と答えの値は同じで、違うのは「回っている間に他の task が
;;; 進むか」だけ。仮想の時計の模擬で決定的に回すための答え手。列を持たない(撃った task がその場で回し切る)ので、負荷の読み
;;; (ReadComputeLoad)は常に全部 0 の断面で答える。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "compute" :role "foundation"})
(import doeff [DoExpr])
(import doeff_vm [PyVM])
(import doeff_core_effects.compute_effects [Compute Computed ComputeFailed ComputeLoad ReadComputeLoad])

;; その場で回す答え手の負荷(並びも走りも、待った仕事も無い)。
(val INLINE-LOAD (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0))


(defk computed [program]
  {:pre [(: program DoExpr)] :post [(: % (| Computed ComputeFailed))]}
  "program を handler の無い新しい VM で回し、答えを値にするため(本物の答え手も worker の thread でこれを回す)。"
  (try
    (Computed :value (.run (PyVM) program))
    (except [error Exception]  ; 境界: 落ちた program の例外は答えの値として呼び手へ運ぶ(compute_effects.hy の頭の註)
      (ComputeFailed :error error))))


(defhandler inline-compute-handler
  ;; その場で同期に回す(頭の註)。
  (Compute [program]
    (<- outcome (computed program))
    (resume outcome))
  (ReadComputeLoad []
    (resume INLINE-LOAD)))
