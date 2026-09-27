;;; defhandler / handle の節の終わり方(ADR-DOE-CORE-EFFECTS-003 R15)— 終わる節 (finish 値) と、再開の書き忘れを
;;; 3 つの時点で止める検め。
;;;
;;;   展開の時   節のすべての道が resume・transfer・finish・reperform・raise のどれかで終わる(handle.hy の _terminates)。
;;;              節が名指す effect の名が Raise / Absent なら、resume・transfer を断る。ほかの effect を finish で打ち切る節は
;;;              理由 (:finish-reason "…") が要る。
;;;   定義の時   handler を作る式を評価した時に、節の effect の型が宣言する再開の扱い(__doeff_resumption__ —
;;;              doeff_core_effects.effects.Resumption)と節の終わり方を照らす(別名で import した Raise も捕まえる)。
;;;   実行の時   節が resume も finish もせずに抜けたら RuntimeError(黙ってスコープを None で終えない)。
;;;
;;; handle.hy は展開の時に関数の中でこの module を import する(handle.hy は macros.hy に require されるので、deff を使う
;;; 補助は handle.hy の外に置く)。展開した handler は定義の時の検めのためにこの module を import する。

(require doeff-hy.macros [deff])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import types [UnionType])
(import typing [Union get-args get-origin])
(import hy)
(import hy.models [Expression Symbol Keyword String Sequence Object Tuple])
(import doeff_core_effects.effects [Resumption resumption-of])


;; 節を終える操作(resume は非末尾なら節へ戻るので、実行の時の検めが見る)。
(setv ENDING-OPS #{"resume" "transfer" "finish" "reperform" "pass" "raise"})
;; 続きを再開する操作。
(setv RESUMING-OPS #{"resume" "transfer"})
;; 名前で分かる effect(展開の時に断れる物)と、その再開の扱い。
(setv NAMED-RESUMPTION {"Raise" Resumption.NEVER "Absent" Resumption.ABSENT-AS-ONLY})


(defrecord MatchArm
  #^ Object pattern
  #^ bool guarded
  #^ Object body)


(defrecord ClauseOptions
  #^ (| Object None) guard
  #^ (| String None) finish-reason
  #^ list body)


(defclass ClauseEndingError [TypeError]
  "節の終わり方が effect の再開の宣言に反する(定義の時に上げる)。")


(deff parse-clause-options [forms where]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: forms list) (: where str)] :post [(: % ClauseOptions)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "節の本体の頭の鍵 `:when 番` と `:finish-reason \"理由\"`(順は問わない)を分けるため。知らない鍵・理由が空でない文字列の
   literal でないものは SyntaxError。"
  (let [items (list forms)
        found {}]
    (while (and (>= (len items) 2) (isinstance (get items 0) Keyword))
      (let [key (str (.pop items 0))
            value (.pop items 0)]
        (when (not-in key #{":when" ":finish-reason"})
          (raise (SyntaxError (+ where ": 節の鍵 " key " は受けない — 受ける鍵は :when と :finish-reason"))))
        (when (in key found)
          (raise (SyntaxError (+ where ": 節の鍵 " key " が 2 回ある"))))
        (when (and (= key ":finish-reason") (not (and (isinstance value String) (.strip (str value)))))
          (raise (SyntaxError (+ where ": :finish-reason は打ち切る理由の文字列(空でない literal): " (.lstrip (hy.repr value) "'")))))
        (setv (get found key) value)))
    (ClauseOptions :guard (.get found ":when") :finish-reason (.get found ":finish-reason") :body items)))


(deff irrefutable-pattern? [pattern]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: pattern Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "match の最後の枝が何でも受ける形(`_` か名前 1 つの capture)かを見るため — 無ければ合わない値が素通りする道が残る。"
  (and (isinstance pattern Symbol) (not-in "." (str pattern))))


(deff match-terminates? [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Expression)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "(match 主題 パターン [:if 番] 本体 …) の節がすべての道で終わるかを見るため: どの枝の本体も終わり、最後の枝が番の無い
   何でも受ける形であること(合わない値が素通りして黙ってスコープを終える道を作らない — R15)。"
  (import doeff-hy.handle [_terminates])
  (let [arms (match-arms form)]
    (and (is-not arms None)
         (bool arms)
         (not (. (get arms -1) guarded))
         (irrefutable-pattern? (. (get arms -1) pattern))
         (all (gfor arm arms (_terminates arm.body))))))


(deff match-arms [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Expression)] :post [(: % (| list None))]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "(match 主題 パターン [:if 番] 本体 …) を枝の list に分けるため(本体の欠けた枝があれば None)。"
  (let [items (list (cut form 2 None))
        arms []]
    (while items
      (let [pattern (.pop items 0)
            guarded (and (bool items) (isinstance (get items 0) Keyword) (= (str (get items 0)) ":if"))]
        (when guarded
          (.pop items 0)
          (when items (.pop items 0)))
        (if items
            (.append arms (MatchArm :pattern pattern :guarded guarded :body (.pop items 0)))
            (return None))))
    arms))


(deff try-terminates? [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Expression)] :post [(: % bool)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "(try 本体 … (except [...] …) …) の節がすべての道で終わるかを見るため: 本体が終わり、各 except の本体も終わる
   (例外を受けた道も resume・finish・raise のどれかへ届く)。"
  (import doeff-hy.handle [_head-name _seq-terminates])
  (let [parts (list (cut form 1 None))
        body (lfor f parts :if (not-in (_head-name f) #{"except" "except*" "else" "finally"}) f)
        handlers (lfor f parts :if (in (_head-name f) #{"except" "except*"}) f)]
    (and (_seq-terminates body)
         (all (gfor h handlers (_seq-terminates (list (cut h 2 None))))))))


(deff ops-in [node]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: node Object)] :post [(: % frozenset)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "form の中で使う終わりの操作の名の集合(quote の中は数えない)— effect の再開の宣言と照らすため。"
  (let [head (when (and (isinstance node Expression) (> (len node) 0) (isinstance (get node 0) Symbol))
               (str (get node 0)))]
    (match node
      (Expression) :if (in head #{"quote" "quasiquote"}) (frozenset)
      (Expression) :if (in head ENDING-OPS) (| (frozenset [head]) #* (gfor child node (ops-in child)))
      (Sequence) (| (frozenset) #* (gfor child node (ops-in child)))
      _ (frozenset))))


(deff clause-ops [forms]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: forms list)] :post [(: % frozenset)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "節の本体が使う終わりの操作の名の集合。"
  (| (frozenset) #* (gfor form forms (ops-in form))))


(deff effect-name [etype]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: etype Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "節の頭の effect の型の名(dotted なら最後の綴り)— 名前で分かる effect(Raise / Absent)を見分けるため。"
  (when (isinstance etype Symbol)
    (get (.split (str etype) ".") -1)))


(deff check-clause-by-name [etype ops options where]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: etype Object) (: ops frozenset) (: options ClauseOptions) (: where str)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "展開の時に分かる違反を SyntaxError にするため: Raise の節の resume / transfer(失敗したのに成功したかのように続く)、
   Absent の節の resume / transfer(再開してよいのは absent-as だけ)、ほかの effect を理由なしに finish で打ち切る節、
   finish の無い節の :finish-reason。"
  (let [named (.get NAMED-RESUMPTION (effect-name etype))
        resumes (& ops RESUMING-OPS)]
    (when (and (= named Resumption.NEVER) resumes)
      (raise (SyntaxError (+ where ": Raise の節は " (.join "/" (sorted resumes)) " できない — Raise は誰も再開しない"
                             "(失敗したのに成功したかのように続くため)。(finish 値) でスコープを終えるか (reperform effect) で外へ渡す"
                             " [ADR-DOE-CORE-EFFECTS-003 R15]"))))
    (when (and (= named Resumption.ABSENT-AS-ONLY) resumes)
      (raise (SyntaxError (+ where ": Absent の節は " (.join "/" (sorted resumes)) " できない — Absent を再開できるのは"
                             " absent-as だけ。(finish 値) でスコープを終えるか (reperform effect) で外へ渡す"
                             " [ADR-DOE-CORE-EFFECTS-003 R8・R15]"))))
    (when (and (is named None) (in "finish" ops) (is options.finish-reason None))
      (raise (SyntaxError (+ where ": 普通の effect の節を (finish …) で打ち切るには、節に :finish-reason \"理由\" を書く"
                             "(時間切れで処理全体を止める等 — 再開の書き忘れと見分けるため) [ADR-DOE-CORE-EFFECTS-003 R15]"))))
    (when (and (is-not options.finish-reason None) (not-in "finish" ops))
      (raise (SyntaxError (+ where ": :finish-reason を書いたが節に (finish …) が無い"))))
    None))


(deff ending-spec-form [etype ops options]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: etype Object) (: ops frozenset) (: options ClauseOptions)] :post [(: % Tuple)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "定義の時の検めへ渡す節 1 つの記述の form: #(effect の型 使う操作の名の tuple 理由があるか)。"
  `#(~etype #(~@(lfor op (sorted ops) (String op))) ~(is-not options.finish-reason None)))


(deff check-clause-endings [handler clauses]  ; defk にできない: 展開した handler が定義の時に呼ぶ検め(effect を出さない)
  {:pre [(: handler str) (: clauses list)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "handler の各節の終わり方を、節の effect の型が宣言する再開の扱い(Resumption)と照らすため — 名前では分からない物
   (別名で import した Raise・__doeff_resumption__ を宣言した型)をここで止める。違反は ClauseEndingError。"
  (for [#(etype ops has-reason) clauses]
    (let [types (match etype
                  (type) [etype]
                  (tuple) (list etype)
                  (UnionType) (list (get-args etype))
                  _ :if (is (get-origin etype) Union) (list (get-args etype))
                  _ [])
          resumes (& (set ops) RESUMING-OPS)]
      (for [effect-type types]
        (let [declared (resumption-of effect-type)
              where (+ handler " の節 " effect-type.__name__)]
          (match declared
            Resumption.NEVER :if resumes
              (raise (ClauseEndingError (+ where ": " effect-type.__name__ " は再開しない effect(Resumption.NEVER)なのに節が "
                                           (.join "/" (sorted resumes)) " する [ADR-DOE-CORE-EFFECTS-003 R15]")))
            Resumption.ABSENT-AS-ONLY :if resumes
              (raise (ClauseEndingError (+ where ": " effect-type.__name__ " を再開できるのは absent-as だけなのに節が "
                                           (.join "/" (sorted resumes)) " する [ADR-DOE-CORE-EFFECTS-003 R8・R15]")))
            Resumption.REQUIRED :if (and (in "finish" ops) (not has-reason))
              (raise (ClauseEndingError (+ where ": 再開する effect を (finish …) で打ち切るのに :finish-reason が無い"
                                           " [ADR-DOE-CORE-EFFECTS-003 R15]")))
            _ None)))))
  None)


(deff check-clause-endings-once [data handler specs]  ; defk にできない: 展開した handler が本文に被さる時に呼ぶ検め(effect を出さない)
  {:pre [(: data Callable) (: handler str) (: specs Callable)] :post [(: % None)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "defhandler の節の終わり方の照合を、handler を初めて本文に被せた時に 1 回だけ行うため(module を読み終えた後なので、
   handler より後に定義した effect の型も引ける)。済んだ印は handler の本体(data)の属性に置く。"
  (when (not (getattr data "__doeff_clause_endings_checked__" False))
    (check-clause-endings handler (specs))
    (setattr data "__doeff_clause_endings_checked__" True))
  None)


(deff fell-through [handler effect-label]  ; defk にできない: 展開した handler の節が実行の時に呼ぶ(例外を作るだけ)
  {:pre [(: handler str) (: effect-label str)] :post [(: % RuntimeError)]
   :tags {:context "doeff-hy-handler" :role "foundation"}}
  "節が resume も finish もせずに抜けた時の誤り(黙ってスコープを None で終えない — R15)。"
  (RuntimeError (+ handler " の節 " effect-label " が resume / transfer / finish / reperform のどれにも届かずに抜けた — "
                   "黙ってスコープを終えない。すべての道で再開するか (finish 値) で終える [ADR-DOE-CORE-EFFECTS-003 R15]")))
