;;; TypeCheck の effect — 実行時の型検査を handler で切り替える(柵 3)。
;;;
;;; 出自 = agora-redesign 段 7 lane 7c(決定 1.4 の柵「TypeCheck の effect
;;; (静的は常時・実行時は handler で切替)」・`docs/plans/decisions-merge-2026-09-12.md`)。
;;;
;;; 二層の型検査:
;;;   - 静的は**常時**: Hy の source を Python の AST へ投影して pyright に
;;;     かける(共通の品質検査 `~/dotfiles/agent/quality/hy_typecheck.py`)。
;;;     切替の口は無い — 型が付いていない公開面はそれだけで赤になる。
;;;   - 実行時は**切替**: 値そのものを見る検査は要求(TypeCheck)として出し、
;;;     果たすかどうかは handler が決める。既定は果たさない(off)家で、
;;;     検だけが果たす(on)家を据える。
;;;
;;; なぜ既定が off か: 実行時の検査は静的に分かることを二度払う。静的が常時
;;; 効いている面では冗長で、hot path の値を毎回触る。境界(外から来た値・
;;; 台帳から読んだ行)だけを検で on にして確かめ、本番は静的の保証で走る。
;;;
;;; 既知の形: algebraic effects。要求は値・handler は実行の家・
;;; composition root がどちらの家を当てるかを選ぶ。


(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass TypeCheckError [TypeError]
  "実行時の型検査が破れた。静的に見えない境界の値が契約と違う。")


(defclass [(dataclass :frozen True :kw-only True)] TypeCheck [EffectBase]
  "値が期待する型かを確かめてほしいという要求。

   owner    = 検査を要求した関数・境界の名前(赤の読み手への手がかり)
   target   = 値の名前(引数名・`%` = 返値)
   expected = 期待する型(isinstance に渡せる型か型の組)
   value    = 見てほしい値
   果たす家は値をそのまま返す(off)か、破れていれば TypeCheckError を送る(on)。"
  #^ str owner
  #^ str target
  #^ object expected
  #^ object value)


(defn #^ TypeCheck type-check [#^ str owner #^ str target #^ object expected #^ object value]
  "要求を組む(まだ果たされていない)。呼び手は `<-` で bind する。

   `expected` は isinstance に渡せる型(か型の組)で、`value` はどんな値でも
   取り得る — 契約つきの defk にすると :pre の型が object になり、柵 2
   (`\"value\"`・Any・object の禁止)を自分で破ることになる。だから要求の
   構築は素の関数で、契約は要求の欄の型(TypeCheck の宣言)が持つ。"
  (TypeCheck :owner owner :target target :expected expected :value value))


(defn #^ (| str None) type-check-verdict [#^ str owner #^ str target #^ object expected #^ object value]
  "純粋な判断: 破れているなら赤の文、通っているなら None。

   実行時の家(on)と静的な検の両方がこの 1 点を読む — 文言の定義点を
   handler の中に埋めない。"
  (when (not (or (isinstance expected type) (isinstance expected tuple)))
    (raise (TypeError (+ owner ": 期待する型が型ではない: " (repr expected)))))
  (if (isinstance value expected)
      None
      (+ owner ": " target " の型が違う: 期待 " (str expected)
         " / 実際 " (. (type value) __name__))))
