;;; 不在と失敗(ADR-DOE-CORE-EFFECTS-003)の展開の時の補助 — <- の :absent・束ねが yield する open-bind の形・
;;; on-raise と absent-as の展開。
;;;
;;; 実行時の意味は doeff_core_effects.outcomes が持ち、ここは形だけを作る:
;;;
;;;   (<- x e)              → (setv x (yield (open-bind e)))       宣言を持つ effect の答えを開く・Result / Maybe の値を開く・
;;;                                                                 ほかは e そのものを yield する(今までと同じ物)
;;;   (<- x e :absent F)    → (setv x (yield (open-bind e (fn [] F))))   束ねの中で出た Absent を Raise(F) に変える
;;;   (on-raise body (Conflict d) (WriteConflict :detail d) …)       合う型の Raise だけを業務の答えへ写す(何でも受ける形は断る)
;;;   (absent-as 0 body)                                             Absent を既定値に畳む(字面の中の <- の不在だけ再開する)
;;;
;;; macros.hy と handle.hy は、展開の時に関数の中でこの module を import する(macros.hy を compile している間は deff の
;;; 展開が呼ぶ補助がまだ無いので、deff で書く補助は macros.hy の外に置く)。

(require doeff-hy.macros [deff])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hy)
(import hy.models [Expression Symbol Keyword List Tuple Dict FString FComponent Sequence Object])


(defrecord SplitBind
  #^ Expression core
  #^ (| Object None) absent)


;; 字面の中の束ねを数えない形(自分の do の文脈を持つ・値として持つ・入れ子の関数)— absent-as は越えない。
(setv OPAQUE-HEADS
  #{"for/do" "traverse" "fnk" "do!" "defhandler" "defk" "deff" "defp" "defpp" "deftest" "defmcp-tool"
    "defmacro" "quote" "quasiquote" "validate" "absent-as"
    "fn" "fn/a" "defn" "defn/a" "defclass" "lfor" "gfor" "dfor" "sfor"})

;; on-raise のパターンに置けない型の名(何でも受ける形・Python の例外 — R7)。
(setv CATCH-ALL-NAMES #{"_" "object" "Exception" "BaseException"})


(deff head-name [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "form の頭の symbol の名(式でなければ・頭が symbol でなければ None)— 形で分けるため。"
  (when (and (isinstance form Expression) (> (len form) 0) (isinstance (get form 0) Symbol))
    (str (get form 0))))


(deff source-of [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % str)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "誤りの文に出す form の綴り。"
  (.lstrip (hy.repr form) "'"))


(deff performs-effect? [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "form が効果を出すか(`!`・`<-`・`yield` を含むか — quote の中は数えない)。値の式だけを置く所(`:absent` の失敗・
   on-raise の写し先)を検めるため。"
  (let [head (head-name form)]
    (match form
      (Expression) :if (in head #{"quote" "quasiquote"}) False
      (Expression) :if (in head #{"!" "<-" "yield"}) True
      (Sequence) (any (gfor child form (performs-effect? child)))
      _ False)))


(deff split-absent [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Expression)] :post [(: % SplitBind)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(<- … :absent 失敗) の末尾の `:absent 失敗` を分けるため(R6)。`:absent` のほかの鍵と、効果を出す失敗の式
   (失敗は値の式 — 不在の時にだけ評価する)は SyntaxError。"
  (let [suffix? (and (>= (len form) 4)
                     (isinstance (get form -2) Keyword)
                     (= (str (get form -2)) ":absent"))
        core (if suffix? (Expression (cut form 0 -2)) form)]
    (for [item (cut core 1 None)]
      (when (isinstance item Keyword)
        (raise (SyntaxError (+ "<-: 知らない鍵 " (str item) " — <- が受ける鍵は末尾の :absent <失敗> だけ: "
                               (source-of form))))))
    (when (and suffix? (performs-effect? (get form -1)))
      (raise (SyntaxError (+ "<-: :absent の失敗は値の式(不在の時にだけ評価する — !・<- は使えない): "
                             (source-of (get form -1))))))
    (SplitBind :core core :absent (when suffix? (get form -1)))))


(deff open-form [expr absent [helpers None]]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: expr Object) (: absent (| Object None)) (: helpers (| str None))] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "束ね 1 つの式(R5・R6): `(open-bind expr)` の答えが `Pure` ならその値、それ以外は yield して VM の答え。
   open-bind は効果を出さない @do の呼び(本体に yield の無い defk など)をその場で呼んで答えの `Pure` を返すので、
   その束ねは VM を往復しない(agora-redesign #844)。`Pure` を yield しても同じ答えなので、意味は yield する形と同じ。
   open-bind と `Pure` の引きを形の中に持つ — defhandler の節・利用者の defn など、どこに書いた `<-` / `!` でも名前が
   解けるように。形は文を持たない 1 つの式(条件式と代入式)にする — `!` は式の中のどこにでも書けるので、
   `(setv (get (! e) 鍵) 値)` のように代入の的の中にも来る。import の文を持つ `(do (import …) …)` では Hy が的を組めずに
   compile が落ちる(agora-controllers の kanban_services.hy・2026-09-28)。module の引き(`__import__`)は 1 回の束ねで
   1 回・約 0.3µs(import の文と同じ桁・hy.I は 4µs — 2026-09-28 に測った)。
   helpers = module の globals に在る outcomes の module の名(defk の本体の束ね — defk が globals に置く
   `_doeff_outcomes`・HELPERS-NAME)。在れば `__import__` の代わりにその名で引く(1 回の束ねの `__import__` 約 0.2µs を
   消す — agora-redesign #844 の案 a)。名が在ると分かっているのは defk の本体だけなので、ほかは None のまま。
   helpers は名の文字列で受け、差し込む所ごとに新しい Symbol を作る — 展開のあいだで 1 つの model を使い回すと、
   locate-synthesized が付けた位置がそれに残り、後の束ねの行がずれる(agora-redesign #1004)。"
  (let [module (if (is helpers None) (hy.gensym "outcomes") (Symbol helpers))
        bound (hy.gensym "bound")
        imported (if (is helpers None)
                     `(setx ~module (__import__ "doeff_core_effects.outcomes" :fromlist #("open_bind")))
                     (Symbol helpers))
        opened (if (is absent None)
                   `(. ~imported (open_bind ~expr))
                   `(. ~imported (open_bind ~expr (fn [] ~absent))))]
    ;; 組み込みの名(type など)を名で引かない — 呼び手の引数・局所の名が同じ綴りだと、Python はそれを関数全体の
    ;; 局所の名と見て束ねが壊れる(defhandler の腕の引数 type で UnboundLocalError・L562 の後始末)。
    ;; 型は属性 __class__ で読み、名で引くのは gensym の束縛だけにする。
    `(if (is (. (setx ~bound ~opened) __class__) (. ~module Pure))
         (. ~bound value)
         (yield ~bound))))


(deff bind-form [form [helpers None]]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Expression) (: helpers (| str None))] :post [(: % Object)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(<- …) の form 1 つの展開 — <- の macro と、本体を先に読む macro(do!・defp・deftest・for/do・defhandler の節・defk)が
   共有する 1 点。分け方は macros.hy の _bind-parts、形は _bind-yield。helpers は open-form へ渡す(defk の本体だけ)。"
  (import doeff-hy.macros [_bind-parts _bind-yield])
  (let [parts (_bind-parts form)]
    (when (is parts None)
      (raise (SyntaxError (+ "<-: expected (<- expr) / (<- name expr) / (<- name Type expr), optionally followed by "
                             ":absent <失敗>, got " (source-of form)))))
    (_bind-yield (get parts 0) (get parts 1) (get parts 2) (. (split-absent form) absent) helpers)))


;; ---------------------------------------------------------------------------
;; on-raise(R7)
;; ---------------------------------------------------------------------------

(deff pattern-types [pattern]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: pattern Object)] :post [(: % list)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "on-raise のパターン 1 つが名指す失敗の型の form の list。型のパターン `(型 …)` と、その or `(| (A …) (B …))` だけを受け、
   何でも受ける形(`_`・名前 1 つの capture・object・Exception・BaseException)は SyntaxError(R7)。"
  (let [head (head-name pattern)]
    (match pattern
      (Expression) :if (= head "|")
        (lfor alternative (cut pattern 1 None) :setv types (pattern-types alternative) t types t)
      (Expression) :if (and (is-not head None) (not-in head CATCH-ALL-NAMES))
        [(get pattern 0)]
      _ (raise (SyntaxError
                 (+ "on-raise: パターン " (source-of pattern) " は受けられない — 失敗の型を名指す (型 欄 …) の形だけを"
                    "書く(何でも受ける形・Python の例外は受けない: ADR-DOE-CORE-EFFECTS-003 R7)"))))))


(deff on-raise-form [program clauses]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: program Object) (: clauses tuple)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(on-raise 本文 パターン [:if 番] 写し先 …) の展開(R7)。受けごとに、パターンの型と、パターンに合えば Some(写し先)・
   合わなければ Nothing を返す関数を RaiseCase にする。写し先と番は値の式(効果は使えない)。"
  (let [items (list clauses)
        reason (hy.gensym "reason")
        cases []]
    (when (not items)
      (raise (SyntaxError "on-raise: (on-raise 本文 パターン 写し先 …) — 受けが 1 つも無い")))
    (while items
      (let [pattern (.pop items 0)
            guard (when (and items (isinstance (get items 0) Keyword) (= (str (get items 0)) ":if"))
                    (.pop items 0)
                    (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: :if の番が無い: " (source-of pattern))))))
            mapped (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: パターン " (source-of pattern) " の写し先が無い"))))]
        (for [value [guard mapped]]
          (when (and (is-not value None) (performs-effect? value))
            (raise (SyntaxError (+ "on-raise: 番と写し先は値の式(!・<- は使えない — 効果の要る写しは本文の中で済ませる): "
                                   (source-of value))))))
        (.append cases
          `(_doeff-raise-case
             #(~@(pattern-types pattern))
             (fn [~reason]
               (match ~reason
                 ~pattern ~@(if (is guard None) [] [(Keyword "if") guard]) (_doeff-some ~mapped)
                 _ _doeff-nothing))))))
    `(do (import doeff_core_effects.outcomes [on-raise :as _doeff-on-raise RaiseCase :as _doeff-raise-case])
         (import doeff.result [Some :as _doeff-some Nothing :as _doeff-nothing])
         (_doeff-on-raise ~program ~@cases))))


;; ---------------------------------------------------------------------------
;; absent-as(R8)
;; ---------------------------------------------------------------------------

(deff mark-direct-binds [node token]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: node Object) (: token Symbol)] :post [(: % Object)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "absent-as の字面の中に直に書いた <- と ! の式を (direct-bind 印 式) で包むため — その束ねが出した Absent だけを absent-as が
   既定値で再開する(R8)。入れ子の do の文脈・関数・値の形(OPAQUE-HEADS)の中へは入らない(その中の不在は奥の不在)。"
  (let [head (head-name node)]
    (match node
      (Expression) :if (= head "<-")
        (let [split (split-absent node)
              core (. split core)
              marked (+ (list (cut core 0 -1))
                        [`(_doeff-direct-bind ~token ~(mark-direct-binds (get core -1) token))])]
          (Expression (+ marked (if (is (. split absent) None) [] [(Keyword "absent") (. split absent)]))))
      (Expression) :if (and (= head "!") (= (len node) 2))
        `(! (_doeff-direct-bind ~token ~(mark-direct-binds (get node 1) token)))
      (Expression) :if (in head OPAQUE-HEADS)
        node
      (Expression) :if (and (in head #{"handle" "on-raise"}) (>= (len node) 2))
        (Expression [(get node 0) (mark-direct-binds (get node 1) token) #* (cut node 2 None)])
      (Expression) (Expression (lfor child node (mark-direct-binds child token)))
      (FString) (FString (lfor child node (mark-direct-binds child token)) :brackets (. node brackets))
      (FComponent) (FComponent (lfor child node (mark-direct-binds child token)) :conversion (. node conversion))
      (| (List) (Tuple) (Dict)) ((type node) (lfor child node (mark-direct-binds child token)))
      _ node)))


(deff absent-as-form [default program]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: default Object) (: program Object)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(absent-as 既定値 本文) の展開(R8)。評価のたびに印を作り、本文が (do! …) ならその字面の中の <- と ! を印で包む。
   本文そのものも印で包む(宣言を持つ effect を直に渡した時、その不在を既定値で再開する)。"
  (let [token (hy.gensym "absent_token")
        body (if (= (head-name program) "do!")
                 (Expression [(get program 0) #* (lfor form (cut program 1 None) (mark-direct-binds form token))])
                 program)]
    `(do (import doeff_core_effects.outcomes [absent-as :as _doeff-absent-as
                                              new-absent-token :as _doeff-new-absent-token
                                              direct-bind :as _doeff-direct-bind])
         (let [~token (_doeff-new-absent-token)]
           (_doeff-absent-as ~default (_doeff-direct-bind ~token ~body) ~token)))))
