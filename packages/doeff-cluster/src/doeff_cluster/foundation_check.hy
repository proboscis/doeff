;;; job の Program が本番の土台で閉じているか(答えの無い effect が残らないか)を、実行せずに確かめる(構成のレビューの B の残り)。
;;;
;;; sim-cluster は sim の土台で job を走らせるので、本番の土台に scheduler・時計・宿の読み・環境変数の読みを入れ忘れても sim では
;;; 見つからない。本番の土台と job の本体の組み合わせを doeff-effect-analyzer(Python の front end — analyze_program・analyze_env・
;;; analyze_handler・check_coverage)で読み、本体が出す effect を、内側の handler の組 → 本番の土台の handler の組 → 外側(scheduler)の
;;; 順に通して、残る effect(gap)が無いかを見る。
;;;
;;;   (foundation-closure body :foundation-handlers m:production-handlers :inner #(m:translation-handlers))
;;;     → FoundationClosure(gaps unknown unresolved)。closed? = 3 つとも空。
;;;
;;; analyzer の今の力で読めない物は「閉じている」と数えない(読めない handler は gap を隠しうるので unknown・追えない所は unresolved)。
;;; 足りない analyzer の機能(2026-09-27 の実測 — 報告と ADR の案に残す):
;;;   1. Program の中の with-handlers で置いた handler の引き算が無い(with_handlers は Program を運ぶ物として中の effect を並べるだけで、
;;;      渡した handler の組を持たない)。ここでは内側の組を inner で別に渡して近似する。
;;;   2. 組み立ての関数は list の literal を return する素の関数しか読めない(defk・deff の契約で包んだ本体は読めない)。
;;;   3. Python で書いた handler の工場(sync-time-handler・env_var_ask・reader など)は節を読めず unknown になる。
;;;   4. 引数で受けた土台(job の defk の foundation)は追えない(unresolved)— 土台の関数と本体を分けて渡す。
;;;   5. 節が (resume 値) の 1 つだけの defhandler は、末尾の resume の書き換えで lambda になり、定義を source に見つけられない
;;;      (unknown・unresolved)。節に 2 つ以上の式を置けば読める。
;;; analyzer は開発の時の道具で、doeff-cluster の実行時の依存ではない(ここだけが import し、呼んだ時に読む)。
(require doeff-hy.macros [deff])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_core_effects.scheduler [scheduled])


(defrecord FoundationClosure
  "閉じているかの答え: gaps = 答えの無い effect(`型の名 ← 出した所`)・unknown = 節を読めなかった handler・unresolved = analyzer が
   追えなかった所(理由と場所)。3 つとも空の時だけ閉じている。"
  (#^ tuple gaps)
  (#^ tuple unknown)
  (#^ tuple unresolved))


(deff closed? [#^ FoundationClosure closure]  ; defk にできない: 検と CLI が値を読むだけの純粋な判断
  {:pre [(: closure FoundationClosure)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "閉じているか — gap・読めない handler・追えない所のどれも無い時だけ真(読めない物を閉じていると数えない)。"
  (not (or closure.gaps closure.unknown closure.unresolved)))


(deff foundation-closure [#^ Callable body * #^ Callable foundation-handlers #^ tuple [inner #()] #^ tuple [outer None]
                          #^ tuple [fold #("Spawn" "with_handlers")]]  ; defk にできない: 開発の道具(analyzer)を呼ぶ検の入口 — Program の外で source を読む
  {:pre [(: body Callable) (: foundation-handlers Callable) (: inner tuple) (: fold tuple)] :post [(: % FoundationClosure)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "job の本体 body(土台に包まれて走る module の最上位の Program 関数)が、本番の土台で閉じているかを実行せずに確かめる。
   foundation-handlers = 本番の土台が並べる handler の組を返す組み立ての関数・inner = 本体の中で並べる組の組み立ての関数(外側が先 —
   翻訳など)・outer = 土台の外側に効く物(既定 = scheduled)・fold = 運ばれた Program を同じ handler の下で数える運び手の名。"
  (import doeff_effect_analyzer.program_effects [analyze_program qualified-name])
  (import doeff_effect_analyzer.handler_effects [analyze_env analyze_handler check_coverage])
  (setv report (analyze_program body)
        env (+ (lfor o (if (is outer None) #(scheduled) outer) (analyze_handler o))
               (analyze_env foundation-handlers)
               (sum (lfor b inner (analyze_env b)) []))
        effects (.effect-types-with report (fn [carrier] (in (get (.rsplit (qualified-name carrier) "." 1) -1) fold)))
        coverage (check_coverage effects env :origin report.target)
        unresolved (+ (lfor u report.unresolved (.format "{}: {}({})" u.reason u.text u.location))
                      (lfor h env u h.unresolved (.format "{}: {}: {}({})" h.name u.reason u.text u.location))))
  (FoundationClosure :gaps (tuple (lfor g coverage.gaps (.format "{} ← {}" (qualified-name g.effect) g.origin)))
                     :unknown (tuple coverage.unknown-handlers)
                     :unresolved (tuple unresolved)))
