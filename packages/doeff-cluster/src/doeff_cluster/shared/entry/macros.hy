;;; 系を宣言する macro defsystem の持ち主の置き場(doeff-cluster・ADR-DOE-CLUSTER-001)。
;;;
;;;   (require doeff-hy.macros [defk <-])
;;;   (require doeff-cluster.shared.entry.macros [defsystem])
;;;
;;; 利用者の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS(by-group の群 E)で、defsystem の持ち主を doeff-hy から doeff-cluster へ移す
;;; (card ki-f2107259b141)。使い手を壊さない順で移す: 今はこの module が doeff-hy の doeff_hy.macros の defsystem を
;;; 出し直し、使い手(doeff の repo の中と、doeff の先端を読む repo)の require をここへ付け替えた後に、定義と形の読みを
;;; ここへ移して doeff_hy.macros の defsystem を消す。
(require doeff-hy.macros [val defsystem])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
;; `(require doeff-cluster.shared.entry.macros *)` が引く macro は defsystem だけ(この module が require した val を出し直さない)・
;; Python の名は出さない(MODULE-TAGS は linter が読む印で、使い手の import する名ではない)。
(export :objects [] :macros [defsystem])
