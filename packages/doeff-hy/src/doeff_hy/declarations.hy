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
;;;   __doeff_needs__    要る能力の名の frozenset(:needs が無ければ None — ADR-DOE-CLUSTER-001 R4b)
;;;
;;; :needs #{"pg-network" "claude-cli"} = その定義(主に土台の handler と、それを並べる Program)が実行先に求める能力の名。
;;; 置き場所の名(kind=k3s・role=…・機体の名)ではなく能力を書く。値は文字列の literal の集合 #{…} だけ(静的に読めるように)。
;;;
;;; 変換の時に検める(macro の展開の中): 知らない鍵・:effects が名の list でない・:tags の鍵が :context と :role ちょうどでない・
;;; 値が文字列の literal でない・役が ROLES の外。どれも SyntaxError で、書いた file と定義の名を名指す。
(import dataclasses [dataclass])
(import hy)
(import hy.models [Dict List Set Symbol String Keyword])

;; 役の閉じた一覧(agora-redesign #780 の層の確定 — operator 2026-09-27 逐語 "core->intent->protocol"):
;; core の type・judgment・program / intent(core が外へ求める事の型 — defeffect)/ protocol(intent を相手の話し方へ訳す handler)/
;; foundation(汎用の I/O)/ entry(組み立て)。前の一覧の effect は intent に、translation は protocol に置き換わった。
;; 層 entry の役は 3 種 — system(系の宣言)・process(handler の並び)・main(薄い main)(agora-redesign #1108 の決め・#1187・operator
;; 2026-09-29 の案 A)。どの層にどの役を置くかは使う側の宣言(architecture.hy の :roles)が決め、ここは綴りの閉じた一覧だけを持つ。
;; entry は 3 種へ移る前の綴りで、使う側が付け替え終えるまで残す。
(setv ROLES #("type" "judgment" "program" "intent" "protocol" "foundation" "entry" "system" "process" "main"))
;; 契約の辞書が受ける鍵。
(setv CONTRACT-KEYS #(":pre" ":post" ":effects" ":tags" ":needs"))
;; 頭の辞書に :pre / :post を持たない定義(defhandler)が受ける鍵。
(setv DECLARATION-KEYS #(":effects" ":tags" ":needs"))
(setv TAG-KEYS #(":context" ":role"))
;; :tags の省ける鍵。:spells = その定義が wire の形を綴る(読む)のが目的の 1 点だという名乗り — doeff-linter の DOEFF172(写像の置き場)が
;; 『dict を組む事が目的の 1 点』として数えない(agora-redesign #2265 の決め・#2299 で書けるようにした)。値は SPELLS の閉じた一覧。
(setv OPTIONAL-TAG-KEYS #(":spells"))
(setv SPELLS #("json"))


(defclass [(dataclass :frozen True)] DefinitionTags []
  "定義の文脈と役。context = 文脈の名(空でない文字列)・role = ROLES の 1 つ・spells = 綴る wire の形(SPELLS の 1 つ・名乗らなければ None)。"
  (#^ str context)
  (#^ str role)
  (setv #^ (| str None) spells None)
  (defn #^ None __post_init__ [self]
    (when (or (not (isinstance self.context str)) (not self.context))
      (raise (ValueError (.format "DefinitionTags.context は空でない文字列: {!r}" self.context))))
    (when (not-in self.role ROLES)
      (raise (ValueError (.format "DefinitionTags.role は {} のどれか: {!r}" (.join " / " ROLES) self.role))))
    (when (and (is-not self.spells None) (not-in self.spells SPELLS))
      (raise (ValueError (.format "DefinitionTags.spells は {} のどれか: {!r}" (.join " / " SPELLS) self.spells))))))


(defn #^ None refuse-unknown-keys [#^ Dict contract #^ tuple allowed #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "契約の辞書に allowed の外の鍵が在れば SyntaxError(黙って捨てないため)。where = 誤りの文の定義の名。"
  (for [key (cut contract None None 2)]
    (when (not-in (str key) allowed)
      (raise (SyntaxError (.format "{}: 頭の辞書の鍵 {} は受けない — 受ける鍵は {}" where (str key) (.join " " allowed)))))))


;; defrecord / defwire の欄の読み方の Hy 側の正本(Rust 側の正本は doeff-indexer の hy_index::fields — hy-index と doeff-linter が呼ぶ)。
;; 2 つの正本が同じ答えを出すことは、同じ入力の表 tests/data/record_field_cases.json を両側の検が読んで確かめる。
(defn field-targets [forms]  ; defk にできない: macro の展開の時に呼ぶ関数
  "record の body の form の列から欄の名(記号)を書いた順に返す — 裸の記号 x・#^ T x・(#^ T x)・(setv #^ T1 a v1 #^ T2 b v2 …) の
   注記つきの的(組ごとに 1 つ)。注記の無い (setv x v) の的は dataclass の欄ではない(class の属性)ので数えない。"
  (import hy.models [Expression])
  (setv annotated (fn [form] (when (and (isinstance form Expression) (= (len form) 3)
                                         (= (str (get form 0)) "annotate") (isinstance (get form 1) Symbol))
                               (get form 1)))
        out [])
  (for [form forms]
    (cond
      (isinstance form Symbol) (.append out form)
      (annotated form) (.append out (annotated form))
      (and (isinstance form Expression) (= (len form) 1)) (when (annotated (get form 0)) (.append out (annotated (get form 0))))
      (and (isinstance form Expression) (>= (len form) 2) (= (str (get form 0)) "setv"))
        (for [target (cut form 1 None 2)]
          (when (annotated target) (.append out (annotated target))))))
  out)


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
        `(doeff_hy.declarations.effect-types ~where (tuple [~@form])))))


(defn #^ (| str None) effect-refusal [item]  ; defk にできない: macro が展開した定義の頭が module の読み込みの時に呼ぶ(Program の外)
  "item を :effects に書けない理由(書ければ None)。書けるのは effect の型か、effect を作る関数(答えの注釈が effect の型 —
   doeff_time の GetTime・Delay や doeff の Tell の形)。"
  (import typing)
  (import doeff [EffectBase])
  (setv effect-type? (fn [t] (and (isinstance t type) (issubclass t EffectBase))))
  (cond
    (isinstance item type) (if (effect-type? item) None "EffectBase を継がない class")
    (not (callable item)) "型でも関数でもない値"
    True (try
           (if (effect-type? (.get (typing.get-type-hints item) "return"))
               None
               "答えの注釈が effect の型でない関数")
           (except [e #(NameError TypeError)]
             (.format "答えの注釈を解けない関数({}: {})" (. (type e) __name__) e)))))


(defn #^ tuple effect-types [#^ str where #^ tuple items]  ; defk にできない: macro が展開した定義の頭が module の読み込みの時に呼ぶ(Program の外)
  "`:effects` の値を定義の時に検めて返す — effect の型でも effect を作る関数でもない名(関数・値・effect でない class)を書いた誤りを、
   定義の名と理由を名指して TypeError で断る(#800 — 前は名の list であることしか検めず、何でも黙って通していた)。"
  (for [item items]
    (setv refusal (effect-refusal item))
    (when (is-not refusal None)
      (raise (TypeError (.format "{}: :effects の {!r} は書けない({})— EffectBase を継ぐ class か、答えの注釈が EffectBase の型の関数を書く"
                                 where item refusal)))))
  items)


(defn tags-form [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:tags` の値の form を変換の時に検め、実行時に DefinitionTags を作る form(無ければ None の form)を返す。"
  (when (is form None)
    (return 'None))
  (when (not (isinstance form Dict))
    (raise (SyntaxError (.format "{}: :tags は {{:context \"…\" :role \"…\"}} の辞書: {}" where (hy.repr form)))))
  (setv keys (lfor k (cut form None None 2) (str k)))
  (when (!= (sorted (lfor k keys :if (not-in k OPTIONAL-TAG-KEYS) k)) (sorted TAG-KEYS))
    (raise (SyntaxError (.format "{}: :tags の鍵は :context と :role ちょうど(省ける鍵は {}): {}" where (.join " " OPTIONAL-TAG-KEYS)
                                 (.join " " keys)))))
  (setv context (declared-value form ":context")
        role (declared-value form ":role")
        spells (declared-value form ":spells"))
  (for [#(name value) [#(":context" context) #(":role" role)]]
    (when (not (isinstance value String))
      (raise (SyntaxError (.format "{}: :tags の {} は文字列の literal: {}" where name (hy.repr value))))))
  (when (not (str context))
    (raise (SyntaxError (.format "{}: :tags の :context が空" where))))
  (when (not-in (str role) ROLES)
    (raise (SyntaxError (.format "{}: :tags の :role {!r} は {} のどれでもない" where (str role) (.join " / " ROLES)))))
  (when (and (is-not spells None) (not (and (isinstance spells String) (in (str spells) SPELLS))))
    (raise (SyntaxError (.format "{}: :tags の :spells は {} のどれかの文字列の literal: {}" where (.join " / " SPELLS) (hy.repr spells)))))
  (if (is spells None)
      `(doeff_hy.declarations.DefinitionTags :context ~context :role ~role)
      `(doeff_hy.declarations.DefinitionTags :context ~context :role ~role :spells ~spells)))


;; defeffect の頭の辞書が受ける鍵(:answer と :tags は必須・:fields は無ければ欄なし・:pre は作る時の検め)。
;; :absent / :failure / :value は答えの型の分け方(ADR-DOE-CORE-EFFECTS-003 R5 — どれも任意・:answer の union の要素の list)。
;; :runs-carried は、答え手が effect を出した所(その所の handler の下)で走らせる Program の欄の名の list(agora-redesign #1456 —
;; doeff_core_effects.effects の runs-carried-of が読む)。
(setv EFFECT-KEYS #(":fields" ":answer" ":tags" ":pre" ":absent" ":failure" ":value" ":runs-carried"))
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


(defn runs-carried-names [form names #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の :runs-carried を検め、答え手が effect を出した所で走らせる Program の欄の名(Python の属性の名)の整列した list に
   するため(書かなければ空)。形は :fields の欄の名の list だけ — 無い欄の名は展開の時に断る。"
  (when (is form None)
    (return []))
  (when (not (isinstance form List))
    (raise (SyntaxError (.format "{}: :runs-carried は :fields の欄の名の list([program] の形): {}" where (hy.repr form)))))
  (for [item form]
    (when (not (and (isinstance item Symbol) (in (str item) names)))
      (raise (SyntaxError (.format "{}: :runs-carried の {} は :fields の欄の名ではない(欄 = {})" where (hy.repr item) names)))))
  (sorted (sfor item form (hy.mangle (str item)))))


(defn answer-value-form [answer]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の本体の __doeff_answer__ に置く値の form を作るため。実行時は :answer の式そのもの(handler が resume で返す値の型)。
   型検査の展開(doeff_hy/static_view.py)では `cast(object, (A, B, …))`(:answer の union の要素の tuple・union でなければ
   要素 1 つ)にする: pyright は値の位置の `A | B` も型として評価し、素の総称(`dict | X` — reportMissingTypeArgument)や
   `Callable | None`(reportOperatorIssue)を赤にするが、要素を並べた tuple なら名を読むだけ。cast は、要素に型の分からない名
   (stub の無い module から import した名)があっても属性の型を Unknown にしない(agora-redesign #2322)。
   cast の別名 _doeff-cast は defeffect-form が import する。"
  (import doeff_hy.static_view [static-view-enabled])
  (if (static-view-enabled)
      `(_doeff-cast object
                    #(~@(if (and (isinstance answer hy.models.Expression) (> (len answer) 0) (= (str (get answer 0)) "|"))
                            (cut answer 1 None)
                            [answer])))
      answer))


(defn defeffect-form [name docstring #^ Dict contract #^ str where pre-code]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defeffect の展開: EffectBase を継ぐ frozen の dataclass(本体に ClassVar の __doeff_answer__ = handler が resume で返す
   値の型)と、属性 __doeff_tags__・__doeff_defeffect__(defeffect で作った印 — defk の :effects の検めが読む)を置く form を作るため。
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
  (setv runs-carried (runs-carried-names (declared-value contract ":runs-carried") names where))
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
     ;; 答えの型は class の本体の `__doeff_answer__: ClassVar[object] = …` で置く(agora-redesign #2322)。型検査の展開は
     ;; 記帳の setattr(`__doeff_…__`)を外すので、setattr で置くと :answer の式が型検査から消え、答えの型のためだけに
     ;; import した名が reportUnusedImport の赤になっていた(消すと実行時に壊れるので書き手は直せない)。本体の代入は
     ;; 記帳ではないので型検査に残り、その名は使われた import と読まれる。注記は object なので、答えの型の読み方
     ;; (`<-` の束ねの型・handler の resume の型)は変えない。ClassVar は dataclass の欄にならない(pyright・実行時とも)。
     ;; from-import の別名の作法は defwire と同じ(record.hy)。
     (import typing [ClassVar :as _doeff-ClassVar cast :as _doeff-cast])
     (defclass [(_doeff-dataclass :frozen True)] ~name [_doeff-effect-base]
       ~@(if (is docstring None) [] [docstring])
       (setv #^ (get _doeff-ClassVar object) __doeff-answer__ ~(answer-value-form answer))
       ~@fields
       ~@post-init)
     (setattr ~name "__doeff_tags__" ~(tags-form tags where))
     (setattr ~name "__doeff_defeffect__" True)
     ~@(if runs-carried
           [`(setattr ~name "__doeff_runs_carried__" (frozenset [~@(lfor n runs-carried (String n))]))]
           [])
     ~@outcomes))


(defn needs-names [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数(defsystem も読む)
  "`:needs` の値の form を変換の時に検め、能力の名の整列した list を返す。形は文字列の literal の集合 #{\"a\" \"b\"} だけ
   (名・式・空の文字列は断る — linter が実行せずに読めるように)。"
  (when (not (isinstance form Set))
    (raise (SyntaxError (.format "{}: :needs は能力の名の文字列の集合 #{{\"pg-network\" …}}: {}" where (hy.repr form)))))
  (for [item form]
    (when (not (and (isinstance item String) (str item)))
      (raise (SyntaxError (.format "{}: :needs の要素は空でない文字列の literal: {}" where (hy.repr item))))))
  (sorted (sfor item form (str item))))


(defn needs-form [form #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:needs` の値の form → 実行時に frozenset を作る form(無ければ None の form)。"
  (if (is form None)
      'None
      `(frozenset [~@(lfor n (needs-names form where) (String n))])))


(defn declaration-setters [name contract #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "定義 name の属性 __doeff_effects__ / __doeff_tags__ / __doeff_needs__ を置く form の list(contract = 頭の辞書か None)。"
  (setv effects (if (is contract None) None (declared-value contract ":effects"))
        tags (if (is contract None) None (declared-value contract ":tags"))
        needs (if (is contract None) None (declared-value contract ":needs")))
  [`(import doeff_hy.declarations)
   `(setattr ~name "__doeff_effects__" ~(effects-form effects where))
   `(setattr ~name "__doeff_tags__" ~(tags-form tags where))
   `(setattr ~name "__doeff_needs__" ~(needs-form needs where))])
