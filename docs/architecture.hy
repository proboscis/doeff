;;; docs の層の宣言(agora-redesign #2934)。
;;;
;;; 今は、規則の母集団から外す層 1 つ(sealed)だけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy から決める(#2811)— docs の下で、もっと近い宣言を持たない file はこの宣言を読む。ここで外すのは下の名指しの
;;; file だけで、docs のほかの file の母集団は変わらない(今どおり全部の規則が当たる)。
;;;
;;; sealed = hash で封をした記録の file。中身を、その記録の封の表が控えているので、規則に合わせて書き換えると封が壊れる。
;;;   - design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/link_artifact_doors.py — 記録の evidence/SHA256SUMS.txt が、script と
;;;     それが出した log の両方を控える(`sha256sum -c evidence/SHA256SUMS.txt` を記録の dir で撃つと一致する)。
;;;   - design-checks/lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH/evidence/race_b_probe.py — 記録の design-check.json の sha256 が控える。
;;;   - 名指し: :files で file を 1 つずつ(この dir からの相対 path)。封の表に足した file は自動では外れない。
;;;   - 外す規則: 名指しの file に今当たっている規則だけ(DOEFF002・DOEFF007)。link_artifact_doors.py の DOEFF004(回数の環境変数の
;;;     直の読み)は agora-redesign #3012 で入口の ReadEnvironment へ直し、封の表の行を同じ変更で作り直した。
;;;   - 名指しが本当に封の表に控えられ、hash が一致することは doeff の tests/test_sealed_records_are_named.py が確かめる。
;;; 戻し方: :exempt の行を消せば、その規則が元どおり当たる。この file を消すと、docs の file の母集団は根の設定へ戻る。
(defarchitecture doeff-docs
  :root "."
  :layers [(layer sealed
             :summary "hash で封をした記録の file — 中身を記録の封の表が控えるので書き換えない"
             :knows "封の表(evidence/SHA256SUMS.txt・design-check.json の sha256)"
             :does-not-know "doeff の Program・effect・handler"
             :files ["design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/link_artifact_doors.py"
                     "design-checks/lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH/evidence/race_b_probe.py"]
             :exempt [(rule DOEFF002 "中身を記録の封の表(SHA256SUMS.txt・design-check.json の sha256)が控える file で、書き換えると封が壊れる")
                      (rule DOEFF007 "中身を記録の封の表(SHA256SUMS.txt・design-check.json の sha256)が控える file で、書き換えると封が壊れる")])])
