;;; この設計の検証の模型(seat-home-common-instructions-AJ8C0B/model)の層の宣言(agora-redesign #2924)。
;;;
;;; 今は、規則の母集団から外す層 2 つ(counterexamples・sealed)だけを置く。doeff-linter は Python の文ごとの規則の母集団を、
;;; file から上へ最も近い architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module・file だけで、模型の
;;; ほかの file の母集団は変わらない。module の名は file の名(この dir には __init__.py が無い)。
;;;
;;; counterexamples = 盲検の反例を修正後の模型へ撃つ検(test_counterexamples.py)。模型の据え付けは、宣言の無い日に HOME を
;;;   環境から読む戻り先を持ち得る — 盲検 B の逃げ道は「検の世界の HOME が空なので戻り先の枝が 1 度も実行されない」だった。
;;;   だからこの検は、世界に HOME を据えてから(os.environ へ書いてから)模型を撃つ。HOME を書くことが検の中身。
;;;   - 外す規則: DOEFF004(os.environ を直に読み書きする)— 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module(模型は標準の library と模型の module だけで書く)。
;;; sealed = hash で封をした記録の file(agora-redesign #2934)。中身を、この記録の evidence/SHA256SUMS.txt が控えている
;;;   (`sha256sum -c evidence/SHA256SUMS.txt` を記録の dir で撃つと一致する)ので、規則に合わせて書き換えると封が壊れる。
;;;   - 名指し: :files で file を 1 つずつ(この dir からの相対 path)。封の表に足した file は自動では外れない。
;;;   - 外す規則: 名指しの file に今当たっている規則だけ(DOEFF004・DOEFF007)。
;;;   - 名指しが本当に封の表に控えられ、hash が一致することは doeff の tests/test_sealed_records_are_named.py が確かめる。
;;; 戻し方: :exempt の行を消せば、その規則が元どおり当たる。この file を消すと、模型の file の母集団は根の設定へ戻る。
(defarchitecture seat-home-model
  :root "."
  :layers [(layer counterexamples
             :summary "盲検の反例を修正後の模型へ撃つ検 — 世界に HOME を据えることが検の中身"
             :knows "模型(chain)・反例の手順・HOME"
             :does-not-know "doeff の Program・effect・handler"
             :modules [test_counterexamples]
             :exempt [(rule DOEFF004 "盲検 B の逃げ道(検の世界の HOME が空で戻り先の枝が実行されない)を塞ぐため、模型を撃つ前に世界の HOME を os.environ へ据えるのが検の中身")]
             :forbid-modules [doeff doeff_agents doeff_core_effects doeff_hy doeff_vm])
           (layer sealed
             :summary "hash で封をした記録の file — 中身を evidence/SHA256SUMS.txt が控えるので書き換えない"
             :knows "封の表(evidence/SHA256SUMS.txt)"
             :does-not-know "doeff の Program・effect・handler"
             :files ["chain.py" "test_violations_are_rejected.py"]
             :exempt [(rule DOEFF004 "中身を evidence/SHA256SUMS.txt が控える記録の file で、書き換えると封が壊れる")
                      (rule DOEFF007 "中身を evidence/SHA256SUMS.txt が控える記録の file で、書き換えると封が壊れる")])])
