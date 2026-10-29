;;; defhandler / handle の節の操作(resume・transfer・finish・reperform・pass・raise)の表 — 操作の名と性質の唯一の定義元
;;; (card ki-b4c015d8ba1f)。
;;;
;;; 操作 1 つの行(ClauseOp)が持つ物:
;;;   name          節の中で書く頭の名
;;;   ending        節を終えた後に続きをどうするか(Ending)— effect の再開の宣言と照らす所(clause_endings.hy)
;;;   arity         書き換える形の引数の数(rewrite が None の操作は問わない)
;;;   returns       続きが終わると節へ戻るか(resume)— 戻る操作の書き換えの後に、handle.hy が「再開した」の印を立てる
;;;                 (実行の時の検め。印の名は handle.hy だけが知り、行の rewrite は知らない)
;;;   tail          節の末尾の位置で替える操作の名(resume → transfer — handler の frame を残さない)。None は替えない
;;;   nodes         実行時の展開が handler の頭で doeff から import する node の名(別名 `_doeff_<名>` で束ねる — 書き手の同じ名の effect を
;;;                 隠さない・doeff_hy/expansion_names.py。rewrite が読む綴りも alias-symbol で同じ別名を引く)
;;;   static-nodes  型検査のための展開(doeff_hy/_static_view.py)が handler の頭で doeff から import する node の名(同じく別名)
;;;   rewrite       (rewrite 引数の list 中を書き換える関数) → VM へ渡す form。None は書いたまま残す(Python の raise)
;;;
;;; 表を読む所 — 操作の名の一覧をこの表の外に写さない。handle.hy の _terminates と clause_endings.hy の ENDING-OPS・RESUMING-OPS が
;;; 写しだった頃、新しい操作を _terminates と書き換えにだけ足すと、Raise の節のその操作を、展開・handle の評価・実行のどの検めも
;;; 止めなかった(設計検証 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の盲検 A — (Raise [reason] (throw …)) で Raise の続きが走った)。
;;;   handle.hy          _terminates(節のすべての道が終わるか)・_rewrite-ops(書き換えと戻る操作の印)・_tail-resume-to-transfer
;;;                      (末尾の替え)・_vm-imports(node の import)
;;;   clause_endings.hy  ops-in / clause-ops(節が使う操作)・check-clause-by-name / check-clause-endings(ending ごとの照合)
;;;   doeff-linter       business_fakes.rs の節の判じ(DOEFF143 ほか)— Rust は Hy を評価できないので、表から名・ending・arity だけを
;;;                      linter-table-json で書き出した packages/doeff-linter/data/clause_ops.json を include_str! で読む(card ki-d3ef2a50cc04)。
;;;                      file は scripts/export_clause_ops.py --write が書き、tests/test_linter_clause_ops.py が表との食い違いを赤にする。
;;; doeff_hy のどの module も、表の外に操作の名を文字列か quote した記号で書かない(tests/test_handle_clause_ops.py が全 module を
;;; 拾う — 名の集まりを写した新しい読み手は、表への追加・退役に追従しない・設計検証 lt-BV79NHMNKA6E42N4TQ1M638MDR の盲検 B)。
;;; doeff-linter の側も同じ — business_fakes.rs は操作の名を literal で書かない(その file の検が拾う)。
;;;
;;; 行を足す前に(操作の名は節の本体の頭の名として予約される): 書き手が同じ名の関数を節の本体(入れ子の fn の中も)で呼んでいると、
;;; 行を足した瞬間にその呼びが操作へ黙って書き換わる(`throw` の行を足すと、書き手の関数 `throw` の呼びが ResumeThrow になり、
;;; 検めの関数が generator になって検めごと消えた — 設計検証 lt-2SJXY98Q643WGJK2ASWJ3N8PWH の盲検 B)。node の名の衝突は
;;; expansion_names.py の別名が防ぐが、操作の名はこの機構の外 — 機械の検では止められない(足す前の呼びと足した後の呼びを source から見分けられない)ので、
;;; 足す前に agora・agora-controllers・doeff・dotfiles の origin/main の節の本体で、その名を頭にした呼びを引く(0 件を確かめてから足す)。
;;; 足す行は packages/doeff-linter/data/clause_ops.json の書き直し(scripts/export_clause_ops.py --write)も要る。
;;;
;;; handle.hy が module の頭で import する(macros.hy が handle.hy を require する途中で読まれる)ので、doeff-hy.macros も
;;; doeff-hy.record も require しない — defrecord・deff・val を使わず、declarations.hy の DefinitionTags と同じ素の dataclass と
;;; module の直下の setv にする。

(import dataclasses [dataclass])
(import collections.abc [Callable])
(import json)
(import enum [StrEnum])
(import hy.models [Expression Symbol])
(import doeff-hy._static-view [static-view-enabled])
(import doeff-hy.expansion-names [alias-symbol import-form effect-symbol continuation-symbol])


(defclass Ending [StrEnum]
  "節を終えた後に続きをどうするか(ADR-DOE-CORE-EFFECTS-003 R15)。"
  ;; 続きを再開する — Raise / Absent の節では断る
  (setv RESUMES "resumes")
  ;; 続きを捨て、handler を置いたスコープを値で終える — 普通の effect の節は :finish-reason が要る
  (setv FINISHES "finishes")
  ;; effect と続きを外の handler へ渡す
  (setv FORWARDS "forwards")
  ;; 例外で節を抜ける
  (setv RAISES "raises"))


(defclass [(dataclass :frozen True :kw-only True)] ClauseOp []
  "節の操作 1 つ(欄の意味は module の頭)。"
  (#^ str name)
  (#^ Ending ending)
  (#^ (| int None) arity)
  (#^ bool returns)
  (#^ (| str None) tail)
  (#^ (get tuple #(str ...)) nodes)
  (#^ (get tuple #(str ...)) static-nodes)
  (#^ (| Callable None) rewrite))


(defn _resume [args recur]
  "(resume 値) → 続きを値で再開する(続きが終わると節へ戻る — 行の returns。戻った後の印は handle.hy が立てる)。型検査のための
   展開は core の typed-resume で、値を effect の答えの型(EffectBase[T] の T)と突き合わせる(節の effect は節の
   `(isinstance _doeff_effect EffectType)` で絞られている)。節の effect・続きは `_doeff_effect`・`_doeff_k` — 書き手の欄の名と
   分け合わない(expansion_names.py)。"
  (setv value (recur (get args 0)))
  (if (static-view-enabled)
      `(yield (do ~(import-form ["typed_resume"])
                  (~(alias-symbol "typed_resume") ~(effect-symbol) ~(continuation-symbol) ~value)))
      `(yield (~(alias-symbol "Resume") ~(continuation-symbol) ~value))))


(defn _transfer [args recur]
  "(transfer 値) → 続きを値で再開し、handler を外す(末尾の呼び)。型検査のための展開は core の typed-transfer。"
  (setv value (recur (get args 0)))
  (if (static-view-enabled)
      `(yield (do ~(import-form ["typed_transfer"])
                  (~(alias-symbol "typed_transfer") ~(effect-symbol) ~(continuation-symbol) ~value)))
      `(yield (~(alias-symbol "Transfer") ~(continuation-symbol) ~value))))


(defn _finish [args recur]
  "(finish 値) → 続きを捨て、handler を置いたスコープの答えを値にする(再開しない終わり方)。"
  `(return ~(recur (get args 0))))


(defn _reperform [args recur]
  "(reperform effect) → effect と続きを外の handler へ渡す(OCaml 5 の reperform)。渡す effect は書き手の式(欄の名が `effect` なら
   欄の値 — 受けた effect を渡すなら `(pass)`)で、続きは節の続き `_doeff_k`。"
  `(yield (~(alias-symbol "Pass") ~(recur (get args 0)) ~(continuation-symbol))))


(defn _pass [args recur]
  "(pass) → 節の effect と続きを外の handler へ渡す — 旧い形で、(reperform effect) へ移すよう警告する。"
  (import warnings)
  (warnings.warn
    "(pass) is deprecated in defhandler — use (reperform effect) instead"
    DeprecationWarning)
  `(yield (~(alias-symbol "Pass") ~(effect-symbol) ~(continuation-symbol))))


;; 節の操作の表。行の順は handler の頭の import の名の順(node-names)。表を引くのはこの module の関数だけ(呼び手は名で表を
;; 写さない・引かない)。
(setv CLAUSE-OPS
  #((ClauseOp :name "resume" :ending Ending.RESUMES :arity 1 :returns True :tail "transfer"
              :nodes #("Resume") :static-nodes #() :rewrite _resume)
    (ClauseOp :name "transfer" :ending Ending.RESUMES :arity 1 :returns False :tail None
              :nodes #("Transfer") :static-nodes #() :rewrite _transfer)
    (ClauseOp :name "finish" :ending Ending.FINISHES :arity 1 :returns False :tail None
              :nodes #() :static-nodes #() :rewrite _finish)
    (ClauseOp :name "reperform" :ending Ending.FORWARDS :arity 1 :returns False :tail None
              :nodes #("Pass") :static-nodes #("Pass") :rewrite _reperform)
    (ClauseOp :name "pass" :ending Ending.FORWARDS :arity 0 :returns False :tail None
              :nodes #("Pass") :static-nodes #("Pass") :rewrite _pass)
    (ClauseOp :name "raise" :ending Ending.RAISES :arity None :returns False :tail None
              :nodes #() :static-nodes #() :rewrite None)))


(defn named-op [name]
  "名の節の操作の行(表に無い名は None)。"
  (next (gfor op CLAUSE-OPS :if (= op.name name) op) None))


(defn clause-op [form]
  "form が節の操作の形(頭の名が表に在る式)ならその行、でなければ None。引数の数は問わない — 節を終えるかの検めと、節が
   使う操作の集まりは頭の名だけで決める(書き換えるかは呼び手が arity と照らす)。"
  (when (and (isinstance form Expression) (> (len form) 0) (isinstance (get form 0) Symbol))
    (named-op (str (get form 0)))))


(defn ops-ending [names ending]
  "操作の名の集まりのうち、終わり方が ending の物(frozenset)— 節が使う操作と effect の再開の宣言を照らすため。"
  (frozenset (gfor op CLAUSE-OPS :if (and (in op.name names) (= op.ending ending)) op.name)))


(defn node-names [static?]
  "handler の展開が頭で doeff から import する、節の操作の node の名(表の順・重ねなし)。"
  (list (dict.fromkeys (gfor op CLAUSE-OPS
                             name (if static? op.static-nodes op.nodes)
                         name))))


(setv _LINTER-ABOUT
  (+ "doeff-hy の表 CLAUSE-OPS(packages/doeff-hy/src/doeff_hy/clause_ops.hy)の行から書き出した、節の操作の名・終わり方・引数の数。"
     "生成物 — 手で直さない(直す所は表で、書き直しは uv run python scripts/export_clause_ops.py --write)。"
     "doeff-linter の business_fakes(DOEFF143 ほか)が節の終わりを判じるのに読み、終わり方ごとの読みは src/project/clause_ops.rs と business_fakes.rs が持つ。"))


(defn linter-table-json []
  "doeff-linter が節の判じに読む file(packages/doeff-linter/data/clause_ops.json)の中身 — 表の行から名・終わり方・引数の数だけを
   書き出す(書き換え・node・returns・tail は実行時と型検査の展開の物で、linter は読まない)。file はこの文字列と 1 字も違わない
   (scripts/export_clause_ops.py --write が書き、tests/test_linter_clause_ops.py が照らす)。"
  (setv rows (gfor op CLAUSE-OPS
                   (+ "  " (json.dumps (dict :name op.name :ending op.ending.value :arity op.arity) :ensure-ascii False))))
  (+ "{\n"
     " \"_about\": " (json.dumps _LINTER-ABOUT :ensure-ascii False) ",\n"
     " \"ops\": [\n"
     (.join ",\n" rows) "\n"
     " ]\n"
     "}\n"))
