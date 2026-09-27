;;; Executable ADR: 名を持ち道具が読む宣言は def* で書き、class を作らない — データの型は defrecord、
;;; 振る舞いは defk / defhandler、外の library が class を要求する所だけ理由の註つきの逃げ道。
;;;
;;; 出自 = operator 裁定 2026-09-27(Claude Code の会話・agora-redesign #798・逐語は :problem の fact)。
;;; coordinator の提案を operator が "perfect, lets go with def*" で採った。
;;;
;;; 置き場の判断: ADR-DOE-HY-004(関数の語彙は defk のみ)の続きに足さず、新しい冊にした。HY-004 は関数の定義
;;; (defn / deff / defk)の語彙と、理由の註の無い deff の台帳を持つ冊で、型と class の定義は別の軸 — 同じ冊に混ぜると
;;; 台帳・針・law が 2 つの軸にまたがる。この冊は HY-004(関数)・HY-005 R5(macro は doeff-hy にだけ置く)と組になる。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つが消える。DOEFF119 と defrecord の :tags / :check は
;;; 別の便の実装なので、それぞれの commit を別に戻す)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass FrozenInstanceError])


;; 生きた probe — データの型は defrecord で 1 行に建ち、実行時の値はその型を呼んで作る。
(defrecord ProbeBudgetRow
  #^ str name
  #^ int used
  #^ int limit)


(defadr ADR-DOE-HY-007
  :title "名を持ち道具(linter・索引・エディタ・型検査)が読む宣言は def*(doeff-hy に置き、読み方の規則つき)で書く。データの型は defrecord(:tags と :check の節を持つ)、振る舞いは defk / defhandler で書き、振る舞いを持つ class は作らない。実行時の値は defrecord の型を呼んで作る。外の library が class を要求する所だけ、deff と同じく理由の註つきの逃げ道"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_007_def_macros_over_classes.hy"
          "packages/doeff-hy/src/doeff_hy/record.hy"
          "packages/doeff-linter"]
  :problem
    [(fact
       "operator 裁定 2026-09-27(逐語 2 つ): \"and i wonder, if we should keep preferring def* macros  instead of making classes.\" / \"perfect, lets go with def*\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)— coordinator 経由")
     (fact
       "実測: agora-controllers に defclass 848・defrecord 338(coordinator の計測・2026-09-27)。同じ日のこの席の数え(git grep の出現数・origin/main 380ae943)は defclass 819・defrecord 414。doeff(origin/main 772a4405)は defclass 614・defrecord 98。"
       :evidence "git grep -c '(defclass' / '(defrecord' -- '*.hy'")
     (fact
       "defrecord は doeff-hy が所有する template macro で、欄の名前と型を宣言した凍結の dataclass を 1 行で建てる。今は :tags と :check の節を持たない — 検めを要する型は素の defclass と __post_init__ で書かれている。"
       :evidence "packages/doeff-hy/src/doeff_hy/record.hy(defrecord)・ADR-DOE-HY-005 R4")
     (fact
       "defk / deff / defp / defhandler / defeffect の頭の辞書は :tags を受け、定義の属性に残す(agora-redesign #800)。素の defclass にはタグの置き場が無く、タグから定義を並べる閲覧に現れない。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy")]
  :context
    [(interpretation
       "def* の宣言は、名前・欄・契約・タグを macro の形で固定するので、linter・索引・エディタ・型検査が読み方の規則(HY-005 R5 の投影)1 つで読める。素の class は method の中に何でも書けるので、道具はそれが値の型なのか振る舞いなのかを読み分けられない。")
     (interpretation
       "振る舞いを class の method に置くと、effect を出せない層(HY-004 が defn を禁じたのと同じ理由)が生まれ、handler で差し替えられない。振る舞いを defk / defhandler に置けば、値の型(defrecord)と計算(defk)と解釈(defhandler)が別々の宣言になる。")
     (interpretation
       "dataclass の __post_init__ で書いていた検めは、defrecord の :check の節へ移す — 検めが型の宣言の一部として道具から読める。")]
  :decision
    [(rule R1 "名を持ち道具(linter・索引・エディタ・型検査)が読む宣言は def* で書く。def* の macro は doeff-hy に置き、読み方の規則(投影)を同じ便で持つ(ADR-DOE-HY-005 R4・R5)。")
     (rule R2 "データの型は defrecord で書く。defrecord は :tags({:context … :role …})と :check(値を作る時の検め)の節を持つ。dataclass の __post_init__ に書いていた検めは :check へ移す。")
     (rule R3 "振る舞いを持つ class(method の中に処理を書く class)は作らない。振る舞いは defk(計算)と defhandler(effect の解釈)に置く。実行時の値は defrecord の型を呼んで作る。")
     (rule R4 "外の library が class を要求する所(基底 class を継ぐことが library の規約の所)だけ、素の defclass を逃げ道として許す。同じ行に『; defrecord にできない: <理由>』を書く(deff の逃げ道と同じ形 — ADR-DOE-HY-004 R1)。")
     (rule R5 "判定の正本は doeff-linter の DOEFF119(素の defclass を違反にする・既存分は登録簿で持ち、縮める向きにだけ動く)。DOEFF119 と defrecord の :tags / :check が着地するまで、この冊の law は未配線。")]
  :laws
    [(law declarations-are-def-macros
       :statement "for_all 型の定義 t(Hy): t は defrecord(または defenum・defeffect などの def*)で書かれている ∨ t は登録簿 DOEFF119 に載った既存の defclass ∨ t の行に『defrecord にできない』の理由の註がある(外の library の基底を継ぐ)"
       :counterexamples
         [(counterexample "(defclass [(dataclass :frozen True)] BudgetRow [] …) を新しく書く — defrecord で 1 行に書け、:tags を持てる")
          (counterexample "__post_init__ で欄を検める dataclass — 検めは defrecord の :check に書ける")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "未配線(2026-09-27)— DOEFF119 は doeff-linter に未着地。defrecord の :tags / :check も未実装")
     (law no-behavior-in-classes
       :statement "for_all class c(Hy・逃げ道を除く): c は method の中に処理を持たない — 振る舞いは defk / defhandler に在る"
       :counterexamples
         [(counterexample "(defclass BudgetClient [] (defn spend [self n] …)) — 振る舞いが effect を出せない method に閉じ、handler で差し替えられない(client は effect と handler にする)")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "未配線(2026-09-27)— DOEFF119 は doeff-linter に未着地")]
  :enforcement
    [(deftest test-adr-doe-hy-007-values-are-made-by-calling-a-defrecord-type
       ;; 実演: データの型は defrecord の 1 行で建ち、値はその型を呼んで作る(凍結 — 書き換えは断られる)。
       (val row (ProbeBudgetRow :name "lab" :used 3 :limit 10))
       (assert (= #(row.name row.used row.limit) #("lab" 3 10)))
       (assert (= row (ProbeBudgetRow :name "lab" :used 3 :limit 10)))
       (var refused False)
       (try
         (setv row.used 4)  ; 値の中身の書き換えを試す(断られることの検)
         (except [FrozenInstanceError]
           (:= refused True)))
       (assert refused "defrecord の値は凍結されている(書き換えは断られる)"))]
  :plans ["agora-redesign #798"])
