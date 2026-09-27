;;; 定義の宣言 — defk / deff / defp / defpp / defhandler の頭の辞書の :effects と :tags(agora-redesign #800)。
;;;
;;; 何のためか: 契約の辞書は :pre / :post のほかの鍵を黙って捨てていた(:effects や :tags を書いても何も起きない)。
;;; 受ける鍵を閉じ、:effects(その定義が出す effect の型の列)と :tags(文脈と役)を定義の属性として残して、
;;; 静的検査と一覧の道具が読めるようにする。
;;;
;;;   {:pre [...] :post [...]
;;;    :effects [ReadRow PutRow]                    ;; effect の型の名(module の中で解ける名)の list — 空の list = effect を出さない
;;;    :tags {:context "kanban" :role "program"}}   ;; 文脈(自由な名)と役(閉じた一覧 ROLES)
;;;
;;; 定義の属性:
;;;   __doeff_effects__  effect の型の tuple(:effects が無ければ None = 宣言していない)
;;;   __doeff_tags__     DefinitionTags(:tags が無ければ None)
;;;
;;; 変換の時に検める(macro の展開の中): 知らない鍵・:effects が名の list でない・:tags の鍵が :context と :role ちょうどでない・
;;; 値が文字列の literal でない・役が ROLES の外。どれも SyntaxError で、書いた file と定義の名を名指す。
(import dataclasses [dataclass])
(import hy)
(import hy.models [Dict List Symbol String Keyword])

;; 役の閉じた一覧(agora-redesign #780 の層の確定 — operator 2026-09-27 逐語 "core->intent->protocol"):
;; core の type・judgment・program / intent(core が外へ求める事の型 — defeffect)/ protocol(intent を相手の話し方へ訳す handler)/
;; foundation(汎用の I/O)/ entry(組み立て)。前の一覧の effect は intent に、translation は protocol に置き換わった。
(setv ROLES #("type" "judgment" "program" "intent" "protocol" "foundation" "entry"))
;; 契約の辞書が受ける鍵。
(setv CONTRACT-KEYS #(":pre" ":post" ":effects" ":tags"))
;; 頭の辞書に :pre / :post を持たない定義(defhandler)が受ける鍵。
(setv DECLARATION-KEYS #(":effects" ":tags"))
(setv TAG-KEYS #(":context" ":role"))


(defclass [(dataclass :frozen True)] DefinitionTags []
  "定義の文脈と役。context = 文脈の名(空でない文字列)・role = ROLES の 1 つ。"
  (#^ str context)
  (#^ str role)
  (defn #^ None __post_init__ [self]
    (when (or (not (isinstance self.context str)) (not self.context))
      (raise (ValueError (.format "DefinitionTags.context は空でない文字列: {!r}" self.context))))
    (when (not-in self.role ROLES)
      (raise (ValueError (.format "DefinitionTags.role は {} のどれか: {!r}" (.join " / " ROLES) self.role))))))


(defn #^ None refuse-unknown-keys [#^ Dict contract #^ tuple allowed #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "契約の辞書に allowed の外の鍵が在れば SyntaxError(黙って捨てないため)。where = 誤りの文の定義の名。"
  (for [key (cut contract None None 2)]
    (when (not-in (str key) allowed)
      (raise (SyntaxError (.format "{}: 頭の辞書の鍵 {} は受けない — 受ける鍵は {}" where (str key) (.join " " allowed)))))))


(defn declared-value [#^ Dict contract #^ str key]  ; defk にできない: macro の展開の時に呼ぶ関数
  "契約の辞書の key の値の form(無ければ None)。"
  (setv found None)
  (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
    (when (= (str k) key)
      (setv found v)))
  found)


(defn effects-form [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:effects` の値の form → 実行時に effect の型の tuple を作る form(無ければ None の form)。"
  (cond
    (is form None) 'None
    (not (isinstance form List))
      (raise (SyntaxError (.format "{}: :effects は effect の型の名の list([ReadRow PutRow] の形): {}" where (hy.repr form))))
    True
      (do
        (for [item form]
          (when (not (isinstance item Symbol))
            (raise (SyntaxError (.format "{}: :effects の要素は effect の型の名: {}" where (hy.repr item))))))
        `(tuple [~@form]))))


(defn tags-form [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:tags` の値の form を変換の時に検め、実行時に DefinitionTags を作る form(無ければ None の form)を返す。"
  (when (is form None)
    (return 'None))
  (when (not (isinstance form Dict))
    (raise (SyntaxError (.format "{}: :tags は {{:context \"…\" :role \"…\"}} の辞書: {}" where (hy.repr form)))))
  (setv keys (lfor k (cut form None None 2) (str k)))
  (when (!= (sorted keys) (sorted TAG-KEYS))
    (raise (SyntaxError (.format "{}: :tags の鍵は :context と :role ちょうど: {}" where (.join " " keys)))))
  (setv context (declared-value form ":context")
        role (declared-value form ":role"))
  (for [#(name value) [#(":context" context) #(":role" role)]]
    (when (not (isinstance value String))
      (raise (SyntaxError (.format "{}: :tags の {} は文字列の literal: {}" where name (hy.repr value))))))
  (when (not (str context))
    (raise (SyntaxError (.format "{}: :tags の :context が空" where))))
  (when (not-in (str role) ROLES)
    (raise (SyntaxError (.format "{}: :tags の :role {!r} は {} のどれでもない" where (str role) (.join " / " ROLES)))))
  `(doeff_hy.declarations.DefinitionTags :context ~context :role ~role))


;; defeffect の頭の辞書が受ける鍵(:answer と :tags は必須・:fields は無ければ欄なし)。
(setv EFFECT-KEYS #(":fields" ":answer" ":tags"))


(defn effect-field-forms [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の `:fields [(: 名 型) …]` を検め、dataclass の欄の form `(#^ 型 名)` の list にするため(無ければ空の list)。"
  (when (is form None)
    (return []))
  (when (not (isinstance form List))
    (raise (SyntaxError (.format "{}: :fields は (: 名 型) の list: {}" where (hy.repr form)))))
  (setv out [] seen (set))
  (for [item form]
    (when (not (and (isinstance item hy.models.Expression) (= (len item) 3)
                    (isinstance (get item 0) Keyword) (= (str (get item 0)) ":")
                    (isinstance (get item 1) Symbol)))
      (raise (SyntaxError (.format "{}: :fields の要素は (: 名 型): {}" where (hy.repr item)))))
    (when (in (str (get item 1)) seen)
      (raise (SyntaxError (.format "{}: :fields の欄 {} が重複" where (str (get item 1))))))
    (.add seen (str (get item 1)))
    (.append out `(#^ ~(get item 2) ~(get item 1))))
  out)


(defn defeffect-form [name docstring #^ Dict contract #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の展開: EffectBase を継ぐ frozen の dataclass と、属性 __doeff_answer__(handler が resume で返す値の型)・
   __doeff_tags__・__doeff_defeffect__(defeffect で作った印 — defk の :effects の検めが読む)を置く form を作るため。"
  (refuse-unknown-keys contract EFFECT-KEYS where)
  (setv answer (declared-value contract ":answer")
        tags (declared-value contract ":tags"))
  (when (is answer None)
    (raise (SyntaxError (.format "{}: :answer(handler が resume で返す値の型)は必須" where))))
  (when (is tags None)
    (raise (SyntaxError (.format "{}: :tags {{:context \"…\" :role \"…\"}} は必須" where))))
  (setv fields (effect-field-forms (declared-value contract ":fields") where))
  `(do
     (import dataclasses [dataclass :as _doeff-dataclass])
     (import doeff [EffectBase :as _doeff-effect-base])
     (import doeff_hy.declarations)
     (defclass [(_doeff-dataclass :frozen True)] ~name [_doeff-effect-base]
       ~@(if (is docstring None) [] [docstring])
       ~@fields)
     (setattr ~name "__doeff_answer__" ~answer)
     (setattr ~name "__doeff_tags__" ~(tags-form tags where))
     (setattr ~name "__doeff_defeffect__" True)))


(defn declaration-setters [name contract #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "定義 name の属性 __doeff_effects__ / __doeff_tags__ を置く form の list(contract = 頭の辞書か None)。"
  (setv effects (if (is contract None) None (declared-value contract ":effects"))
        tags (if (is contract None) None (declared-value contract ":tags")))
  [`(import doeff_hy.declarations)
   `(setattr ~name "__doeff_effects__" ~(effects-form effects where))
   `(setattr ~name "__doeff_tags__" ~(tags-form tags where))])
