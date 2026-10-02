;;; scripts の層の宣言(agora-redesign #2924)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module だけで、scripts のほかの file の母集団は
;;; 変わらない(今までどおり DOEFF004 が当たる)。module の名は file の名(scripts には __init__.py が無い)。
;;;
;;; warning-baseline-seam = doeff-cluster の warning の基点の道具(scripts/doeff_cluster_warning_baseline.py)。commit の hook と
;;;   make lint-doeff が scripts/lint-doeff-cluster.sh 経由で起こす Program の外の道具で、基点の file の置き場を環境変数
;;;   DOEFF_CLUSTER_WARNING_BASELINE で差し替えられる。差し替えるのは make の入口の検(tests/test_lint_entrypoint_wiring.py)だけで、
;;;   make と shell を通るので引数では渡せない — 環境変数がその検の seam。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり当たる。この file を消すと、scripts の file の母集団は根の設定へ戻る。
(defarchitecture doeff-scripts
  :root "."
  :layers [(layer warning-baseline-seam
             :summary "doeff-cluster の warning の基点の道具 — 基点の置き場を環境変数で差し替える seam を持つ"
             :knows "doeff-linter の JSON・基点の file・環境変数の名"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_cluster_warning_baseline]
             :exempt [(rule DOEFF004 "make lint-doeff と commit の hook が shell 経由で起こす Program の外の道具で、make の入口の検が基点の置き場を差し替える口は、make と shell を通る環境変数しか無い")]
             :forbid-modules [doeff doeff_cluster doeff_core_effects doeff_hy doeff_vm])])
