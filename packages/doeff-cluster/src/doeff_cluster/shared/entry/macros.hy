;;; 系を宣言する macro defsystem(ADR-DOE-CLUSTER-001・形の読みと展開は同じ dir の _system_form.hy)。
;;;
;;;   (require doeff-hy.macros [defk <-])
;;;   (require doeff-cluster.shared.entry.macros [defsystem])
;;;
;;; 持ち主は doeff-cluster(2026-10-27 に doeff-hy の doeff_hy.macros から移した — 利用者の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS の
;;; by-group・card ki-f2107259b141)。展開が呼ぶ構成子(job・system-of)は doeff_cluster.shared.entry.service_build、型は
;;; doeff_cluster.shared.intent.service_model。関数の本体の中に書いた時の組み方は doeff-hy の定義の macro の土台
;;; (doeff_hy.macros の _scoped-definition)に任せる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
;; `(require doeff-cluster.shared.entry.macros *)` が引く macro は defsystem だけ(この module が require した val を出し直さない)・
;; Python の名は出さない(MODULE-TAGS は linter が読む印で、使い手の import する名ではない)。
(export :objects [] :macros [defsystem])

;; macro の引数の型 — 展開の時に macro が受けるのは、Hy の compiler(`_hy-compiler`)と、書かれた form(`hy.models.Object`)。
;; defmacro は compile の時に関数を作り、3.13 までは引数の注釈をその場で評価するので、compile の時の名前空間にも置く。
(eval-and-compile
  (import hy.compiler [HyASTCompiler])
  (import hy.models [Object]))


(defmacro defsystem [#^ HyASTCompiler _hy-compiler #^ Object name #^ Object params #^ Object #* body]
  "系を宣言する: 土台を引数に受け、名前 → Program と約束の組(doeff_cluster.shared.intent.service_model.System)を返す関数を定義する。

   (defsystem land-notices [foundation]
     \"着地の報せの系\"
     (land-notice (land-notice foundation)
       :needs #{\"pg-network\"} :replicas 1 :peak-mib 300 :settle-mib 120
       :readiness {\"windowSeconds\" 30} :update \"handoff\" :environ {\"POLL\" \"5.0\"}))

   :replicas は job の望む台数(0 = 取り下げ・1 = 動かす)で、必ず書く。
   :peak-mib・:settle-mib は起動の memory の山と落ち着いた値(MiB の整数・落ち着きは山以下)で、必ず書く(既定の値は無い)。
   :single-host \"<訳>\" は job を 1 台の worker に縛ってよい訳(任意 — 書いた job は置き先が 2 台以上の照らしの外になる)。
   説明の文の後・job の行の前に :task-limits {\"<組>\" {\"scope\" \"worker\" \"limit\" 6}} を置くと、系が組の同時数の上限を宣言する
   (task は組の名だけを名乗る — 値の持ち主は系の宣言)。
   形は静的に決まる物だけを受け、外れれば展開の時に SyntaxError(doeff-linter が実行せずに読めるように)。
   関数には静的な記述 __doeff_system__(名・引数・job ごとの関数の名と約束)が付く。
   関数の本体の中に書くと、import と定義をすぐ呼ぶ関数に入れて name だけを束ねる(_scoped-definition — card ki-93395c51de1e)。"
  (import doeff_hy.macros [_scoped-definition])
  (import doeff-cluster.shared.entry._system-form [defsystem-form])
  (_scoped-definition _hy-compiler name [(defsystem-form name params body)]))
