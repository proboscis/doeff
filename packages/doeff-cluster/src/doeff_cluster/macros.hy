;;; 系を宣言する macro defsystem の公開の置き場(ADR-DOE-CLUSTER-001・ADR-DOE-API-001 R15)。
;;;
;;;   (require doeff-hy.macros [defk <-])
;;;   (require doeff-cluster.macros [defsystem])
;;;
;;; Hy の require は module の path で macro を読むので、根の入口(__init__ の遅延の名)からは出せない。この module は名に _ を
;;; 付けずに根のすぐ下に置き、R5 の外の境界にする(R14・R31 の doeff_conductor.macros・doeff_domain.macros と同じ)。macro の本体と
;;; 形の読みは共有の区画の中(doeff_cluster._shared.entry.macros と _system_form)に在り、この module は defsystem だけを出し直す。
(require doeff-hy.macros [val])
(require doeff-cluster._shared.entry.macros [defsystem])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
;; `(require doeff-cluster.macros *)` が引く macro は defsystem だけ(この module が require した val を出し直さない)・
;; Python の名は出さない(MODULE-TAGS は linter が読む印で、使い手の import する名ではない)。
(export :objects [] :macros [defsystem])
