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


;; defeffect の頭の辞書が受ける鍵(:answer と :tags は必須・:fields は無ければ欄なし・:pre は作る時の検め)。
;; :absent / :failure / :value は答えの型の分け方(ADR-DOE-CORE-EFFECTS-003 R5 — どれも任意・:answer の union の要素の list)。
(setv EFFECT-KEYS #(":fields" ":answer" ":tags" ":pre" ":absent" ":failure" ":value"))
;; 答えの分け方の鍵。<- は :absent の答えを Absent に、:failure の答えを Raise(答え) に変えて呼び手のスコープで出す。
;; :value は業務で普通に扱う答え(失敗と宣言しない印)、どれにも書かない要素は成功。
(setv OUTCOME-KEYS #(":absent" ":failure" ":value"))


(defn outcome-type-forms [contract answer #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数(deff は doeff_hy.macros が定義し、macros.hy がこの module を import するので require できない)
  "defeffect の :absent / :failure / :value を検め、鍵ごとの型の form の list にするため(書かなければ空)。
   要素は型の名か None。:answer が union の form (| A B …) か名前 1 つなら、要素がその中に在ることと、鍵どうしで重ならないことを
   展開の時に断る(それ以外の :answer は定義の時に Outcomes.declare が断る)。"
  (setv members (cond
                  (and (isinstance answer hy.models.Expression) (> (len answer) 0) (= (str (get answer 0)) "|"))
                    (lfor m (cut answer 1 None) (hy.repr m))
                  (isinstance answer Symbol) [(hy.repr answer)]
                  True None)
        seen {}
        out {})
  (for [key OUTCOME-KEYS]
    (setv form (declared-value contract key))
    (when (and (is-not form None) (not (isinstance form List)))
      (raise (SyntaxError (.format "{}: {} は :answer の要素の list([Missing] の形): {}" where key (hy.repr form)))))
    (for [item (or form [])]
      (when (not (isinstance item Symbol))
        (raise (SyntaxError (.format "{}: {} の要素は型の名か None: {}" where key (hy.repr item)))))
      (when (and (is-not members None) (not-in (hy.repr item) members))
        (raise (SyntaxError (.format "{}: {} の {} は :answer {} の要素ではない" where key (str item) (hy.repr answer)))))
      (when (in (str item) seen)
        (raise (SyntaxError (.format "{}: {} が {} と {} の両方にある" where (str item) (get seen (str item)) key))))
      (setv (get seen (str item)) key))
    (setv (get out key) (list (or form []))))
  out)


(defn effect-field-forms [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の `:fields [(: 名 型) (: 名 型 既定値) …]` を検め、dataclass の欄の form の list と欄の名前の list にするため
   (無ければ両方とも空)。既定値を持つ欄の後ろに既定値の無い欄は置けない(dataclass の順の決まりを展開の時に断る)。"
  (when (is form None)
    (return #([] [])))
  (when (not (isinstance form List))
    (raise (SyntaxError (.format "{}: :fields は (: 名 型) か (: 名 型 既定値) の list: {}" where (hy.repr form)))))
  (setv out [] names [] defaulted None)
  (for [item form]
    (when (not (and (isinstance item hy.models.Expression) (in (len item) #(3 4))
                    (isinstance (get item 0) Keyword) (= (str (get item 0)) ":")
                    (isinstance (get item 1) Symbol)))
      (raise (SyntaxError (.format "{}: :fields の要素は (: 名 型) か (: 名 型 既定値): {}" where (hy.repr item)))))
    (setv field-name (str (get item 1)))
    (when (in field-name names)
      (raise (SyntaxError (.format "{}: :fields の欄 {} が重複" where field-name))))
    (.append names field-name)
    (cond
      (= (len item) 4)
        (do (setv defaulted field-name)
            (.append out `(setv #^ ~(get item 2) ~(get item 1) ~(get item 3))))
      (is-not defaulted None)
        (raise (SyntaxError (.format "{}: :fields の欄 {} は既定値が無いので、既定値を持つ欄 {} より前に置く" where field-name defaulted)))
      True
        (.append out `(#^ ~(get item 2) ~(get item 1)))))
  #(out names))


(defn defeffect-form [name docstring #^ Dict contract #^ str where pre-code]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の展開: EffectBase を継ぐ frozen の dataclass と、属性 __doeff_answer__(handler が resume で返す値の型)・
   __doeff_tags__・__doeff_defeffect__(defeffect で作った印 — defk の :effects の検めが読む)を置く form を作るため。
   pre-code = :pre を defk と同じ規則で文にした列(macros.hy の _contract-code が作る)。空でなければ __post_init__ に置き、
   欄の名前をその場の名前として読めるようにする(閉じた語彙・要素の型の検めを、作る時に断る)。"
  (refuse-unknown-keys contract EFFECT-KEYS where)
  (setv answer (declared-value contract ":answer")
        tags (declared-value contract ":tags"))
  (when (is answer None)
    (raise (SyntaxError (.format "{}: :answer(handler が resume で返す値の型)は必須" where))))
  (when (is tags None)
    (raise (SyntaxError (.format "{}: :tags {{:context \"…\" :role \"…\"}} は必須" where))))
  (setv #(fields names) (effect-field-forms (declared-value contract ":fields") where))
  (when (and pre-code (not names))
    (raise (SyntaxError (.format "{}: :pre は欄を検めるので、:fields の無い effect には書けない" where))))
  ;; 答えの分け方(R5)— 1 つでも書けば __doeff_outcomes__ を置く(書かなければ置かない = <- は答えを変換しない)。
  (setv outcome-types (outcome-type-forms contract answer where))
  (setv outcomes
    (if (any (.values outcome-types))
        [`(import doeff_core_effects.outcomes)
         `(setattr ~name "__doeff_outcomes__"
                   (doeff_core_effects.outcomes.Outcomes.declare
                     ~(str name) ~answer
                     #(~@(get outcome-types ":absent"))
                     #(~@(get outcome-types ":failure"))
                     #(~@(get outcome-types ":value"))))]
        []))
  (setv post-init
    (if pre-code
        [`(defn __post-init__ [self]
            ~@(lfor n names `(setv ~(Symbol n) (. self ~(Symbol n))))
            ~@pre-code
            None)]
        []))
  `(do
     (import dataclasses [dataclass :as _doeff-dataclass])
     (import doeff [EffectBase :as _doeff-effect-base])
     (import doeff_hy.declarations)
     (defclass [(_doeff-dataclass :frozen True)] ~name [_doeff-effect-base]
       ~@(if (is docstring None) [] [docstring])
       ~@fields
       ~@post-init)
     (setattr ~name "__doeff_answer__" ~answer)
     (setattr ~name "__doeff_tags__" ~(tags-form tags where))
     (setattr ~name "__doeff_defeffect__" True)
     ~@outcomes))


(defn declaration-setters [name contract #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "定義 name の属性 __doeff_effects__ / __doeff_tags__ を置く form の list(contract = 頭の辞書か None)。"
  (setv effects (if (is contract None) None (declared-value contract ":effects"))
        tags (if (is contract None) None (declared-value contract ":tags")))
  [`(import doeff_hy.declarations)
   `(setattr ~name "__doeff_effects__" ~(effects-form effects where))
   `(setattr ~name "__doeff_tags__" ~(tags-form tags where))])
