;;; TypeCheck を果たす 2 つの家(柵 3)。既定は off・検で on。
;;;
;;; 出自 = agora-redesign 段 7 lane 7c(決定 1.4 の柵「TypeCheck の effect」)。
;;; 要求の語彙と赤の文言は `doeff_hy.typecheck` の 1 点が持ち、ここは
;;; 「果たすか果たさないか」だけを持つ(handler に判断を置かない)。
;;;
;;; ⚠ この module は共通の品質検査の静的投影に届かない(`defhandler` の投影が
;;; 未対応 — 2026-09-12 時点)。だから判断は 1 行も持たせず、要求の語彙と
;;; verdict の側(`typecheck.hy`)を投影可能に保つ。

(require doeff-hy.handle [defhandler])

(import doeff [run])
(import doeff_hy.typecheck [TypeCheck TypeCheckError type-check-verdict])


(defhandler type-checks-off
  "既定の家: 要求を受けても値を見ない(実行時の検査は off)。

   静的の検査は常時効いているので、本番はこの家で走る。"

  (TypeCheck [owner target expected value]
    (resume value)))


(defhandler type-checks-on
  "検の家: 要求のとおり値を見て、破れていれば TypeCheckError を送る。"

  (TypeCheck [owner target expected value]
    (setv verdict (type-check-verdict owner target expected value))
    (when (is-not verdict None)
      (raise (TypeCheckError verdict)))
    (resume value)))


(defn #^ object run-with-type-checks [#^ object program]
  "検の composition root: 実行時の型検査を on にして program を回す。"
  (run (type-checks-on program)))


(defn #^ object run-without-type-checks [#^ object program]
  "本番の composition root: 実行時の型検査を off にして program を回す(既定)。"
  (run (type-checks-off program)))
