;;; conductor の workflow を書く Hy の macro の持ち主の置き場(doeff-conductor)。
;;;
;;;   (require doeff-conductor.macros [defworkflow defphase agent! gate! workspace! merge!
;;;                                    parallel parallel-for loop <- time! random! pipeline])
;;;
;;; 利用者の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS(by-group の群 E)で、macro の持ち主を doeff-hy から doeff-conductor へ移す
;;; (card ki-f2107259b141)。使い手を壊さない順で移す: 今はこの module が doeff-hy の doeff_hy.conductor の macro を
;;; 出し直し、使い手(doeff の repo の中と agora-controllers)の require をここへ付け替えた後に、定義をここへ移して
;;; doeff_hy.conductor を消す。
(require doeff-hy.conductor [defworkflow defphase agent! gate! workspace! merge! parallel parallel-for loop <- time! random!
                             pipeline])
(export :macros [defworkflow defphase agent! gate! workspace! merge! parallel parallel-for loop <- time! random! pipeline])
