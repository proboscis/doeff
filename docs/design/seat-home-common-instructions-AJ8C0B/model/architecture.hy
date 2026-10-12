;;; この設計の検証の模型(seat-home-common-instructions-AJ8C0B/model)の層の宣言(agora-redesign #2924)。
;;;
;;; 今は層 1 つ(sealed)だけを置く。sealed は名指しの file を規則の母集団から外す。doeff-linter は Python の文ごとの規則の母集団を、
;;; file から上へ最も近い architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの file だけで、模型の
;;; ほかの file の母集団は変わらない。
;;;
;;; 検の 4 本(test_chain・test_counterexamples・test_scenario_checks・test_violations_are_rejected)は、2026-10-12 に `.py.txt` の名へ替えた
;;; (中身も hash も同じ・card ki-d153ab595504)。`test_*.py` の名のままでは、日次の母集団の除外の表へ載せるほかなく、make test-changed の
;;; 選びが検として走らせ、本物の検をここへ置いても日次が緑になった(設計検証 lt-9R40Q1PM496VA4HVD1FMXVYW8Z の盲検 A・B)。この 4 本は
;;; pytest の検でもない(`def test_*` を持たず `t_*` を __main__ から回す script — 母集団の根へ入れると「収集 0 件」の rc 5 で日次が赤)。
;;; `.py.txt` は linter の対象の外なので、以前ここに置いた層 counterexamples(test_counterexamples の禁じる import)は外した。走らせ直す
;;; 手順は ../RECOVERY.md の「検の 4 本の走らせ方」。
;;;
;;; sealed = hash で封をした記録の file(agora-redesign #2934)。中身を、この記録の evidence/SHA256SUMS.txt が控えている
;;;   (`sha256sum -c evidence/SHA256SUMS.txt` を記録の dir で撃つと、封の表の行のうち復元で一致した物が一致する — 不一致の行は
;;;   ../RECOVERY.md の表)ので、規則に合わせて書き換えると封が壊れる。
;;;   - 名指し: :files で file を 1 つずつ(この dir からの相対 path)。封の表に足した file は自動では外れない。
;;;   - 外す規則: 名指しの file に今当たっている規則だけ(DOEFF007)。DOEFF004 の当たりは #3012 で直した — 直した file の封の表の行は
;;;     同じ変更で作り直した(元の hash は RECOVERY.md の表の備考)。
;;;   - 名指しが本当に封の表に控えられ、hash が一致することは doeff の tests/test_sealed_records_are_named.py が確かめる。
;;; 戻し方: :exempt の行を消せば、その規則が元どおり当たる。この file を消すと、模型の file の母集団は根の設定へ戻る。
(defarchitecture seat-home-model
  :root "."
  :layers [(layer sealed
             :summary "hash で封をした記録の file — 中身を evidence/SHA256SUMS.txt が控えるので書き換えない"
             :knows "封の表(evidence/SHA256SUMS.txt)"
             :does-not-know "doeff の Program・effect・handler"
             :files ["chain.py"]
             :exempt [(rule DOEFF007 "中身を evidence/SHA256SUMS.txt が控える記録の file で、書き換えると封が壊れる")])])
