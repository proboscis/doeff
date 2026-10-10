;; 版上げの Program の退役(2026-10-10 20:03 の決め — Program・型と effect・模擬の Flux・不変条件を丸ごと)。
;; worker と coordinator を新しい版へ入れ替える Program(upgrade-workers・upgrade-coordinator・upgrade-cluster)と、その型と effect
;; (ConfirmCleanBoot・CleanBootPassed・CleanBootRefused ほか)・模擬の Flux(sim/flux.hy)・入れ替えの順の不変条件 V1〜V5 は、本番が静的な worker を
;; Flux が node の宣言(deploy/k8s/nodes)から作り直す形に移り(2026-10-09 15:5x・10-10 16:40 の決定)、この repo の外の使い手が 0 になった。
;; 使い手 0 の入口を残さない(後方互換を書かない)。条は守る物(Program)と一緒に消す(条を残して守り手だけ消す形にしない)。
;; 失敗ケース = 退役した module が import できる・architecture.hy に守る物の無い条が残る。
(require doeff-hy.macros [deftest val])
(import importlib.util [find-spec])
(import pathlib [Path])

(val RETIRED-MODULES #("doeff_cluster.shared.core.upgrade_program" "doeff_cluster.shared.intent.upgrade_model"
                       "doeff_cluster.sim.flux" "doeff_cluster.coordinator.core.upgrade_invariants"))
(val ARCHITECTURE (/ (. (.resolve (Path __file__)) parent parent) "architecture.hy"))


(deftest test-the-upgrade-program-modules-are-gone
  (val left (tuple (gfor name RETIRED-MODULES :if (is-not (find-spec name) None) name)))
  (assert (= left #()) left))


(deftest test-the-architecture-declares-no-upgrade-invariants
  (assert (not-in "upgrade_invariants" (.read-text ARCHITECTURE :encoding "utf-8"))))
