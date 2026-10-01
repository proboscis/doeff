;;; job の Program が本番の土台で閉じているか(答えの無い effect が残らないか)を、実行せずに確かめる(構成のレビューの B の残り)。
;;;
;;; sim-cluster は sim の土台で job を走らせるので、本番の土台に scheduler・時計・宿の読み・環境変数の読みを入れ忘れても sim では
;;; 見つからない。job の関数の土台の引数に本番の土台を束ねて doeff-effect-analyzer で読み(analyze_program の bindings)、Program が
;;; 自分で並べた handler(本体の with-handlers・土台の with-handlers と scheduled)を通した後に残る effect を見る(check_coverage)。
;;;
;;;   (foundation-closure job :foundation production-foundation)
;;;     → FoundationClosure(gaps unknown unresolved)。closed? = 3 つとも空。
;;;
;;; 読めない物は閉じていると数えない: 節を読めない handler(unknown — gap を隠しうる)・analyzer が追えなかった所(unresolved — 束ねて
;;; いない土台の引数など)。analyzer は開発の時の道具で、doeff-cluster の実行時の依存ではない(ここだけが import し、呼んだ時に読む)。
(require doeff-hy.macros [deff val])
;; 置き場 = foundation(#2110): 解析の道具(doeff-effect-analyzer)を呼んで source を読む口と、その答えの型と読みの組。
;; 答えの型と closed? もこの口の値の読みなので、foundation の層の中に閉じる(foundation は foundation 以外を import しない)。
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])


(defrecord FoundationClosure
  "閉じているかの答え: gaps = 答えの無い effect(`型の名 ← 出した所`)・unknown = 節を読めなかった handler・unresolved = analyzer が
   追えなかった所(理由と場所)。3 つとも空の時だけ閉じている。"
  (#^ tuple gaps)
  (#^ tuple unknown)
  (#^ tuple unresolved))


(deff closed? [#^ FoundationClosure closure]  ; defk にできない: 検と CLI が値を読むだけの純粋な判断
  {:pre [(: closure FoundationClosure)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "閉じているか — gap・読めない handler・追えない所のどれも無い時だけ真(読めない物を閉じていると数えない)。"
  (not (or closure.gaps closure.unknown closure.unresolved)))


(deff foundation-closure [#^ Callable job * #^ (| Callable None) [foundation None] #^ str [parameter "foundation"]
                          #^ tuple [fold #()]]  ; defk にできない: 開発の道具(analyzer)を呼ぶ検の入口 — Program の外で source を読む
  {:pre [(: job Callable) (: foundation (| Callable None)) (: parameter str) (: fold tuple)] :post [(: % FoundationClosure)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "job の関数(土台を引数 parameter で受けて本体を包む module の最上位の Program 関数)が、土台 foundation で閉じているかを実行せずに
   確かめる。foundation を渡さなければ束ねずに読む(土台の先を追えないので unresolved になる)。運ばれた Program は、運び手の effect が
   「答え手が出した所の handler の下で走らせる」と宣言していれば(__doeff_runs_carried__ — Spawn・Try・Local・Listen・SqlTransaction)、
   出した所で同じ handler の下で数える(以前の既定は名 Spawn だけで、Try と SqlTransaction の中の effect が検の外にあった)。fold = 宣言の外で同じく数える運び手の名(既定なし)。"
  (import doeff_effect_analyzer.program_effects [analyze_program qualified-name runs-where-performed])
  (import doeff_effect_analyzer.handler_effects [check_coverage])
  (import doeff_effect_analyzer.result_cache [cached-result importable-name])
  (setv analyze
        (fn []
          (setv report (analyze_program job :bindings (if (is foundation None) None {parameter foundation}))
                coverage (check_coverage report [] :include (fn [carrier] (or (runs-where-performed carrier)
                                                                              (in (getattr carrier "__name__" "") fold)))))
          (FoundationClosure :gaps (tuple (lfor g coverage.gaps (.format "{} ← {}" (qualified-name g.effect) g.origin)))
                             :unknown (tuple coverage.unknown-handlers)
                             :unresolved (tuple (lfor u coverage.unresolved (.format "{}: {}({})" u.reason u.text u.location))))))
  ;; 答えは、読み込んだ module の file が変わらない間 disk から読む(result_cache — 鍵に入る物と入らない物はその module の説明)。job か土台が名で引けない時は鍵が無いので毎回読む。
  (setv job-name (importable-name job)
        foundation-name (if (is foundation None) "-" (importable-name foundation)))
  (cached-result (if (and job-name foundation-name)
                   #("doeff-cluster.foundation-closure" job-name foundation-name parameter #* (map str fold))
                   None)
                 analyze))
