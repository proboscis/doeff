;;; doeff-conductor の層の宣言(agora-redesign #2894)。
;;;
;;; 今は、規則の母集団から外す層 2 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module・file だけで、この package のほかの
;;; file の母集団は変わらない(今どおり全部の規則が当たる)。
;;;
;;; environment = conductor が環境変数から読む値の module(doeff_conductor.env_places)。CLI と状態の記録は、どの Program・
;;;   handler よりも前に状態の置き場の dir と profile の設定を決めるので、設定を Ask で受ける入口が無い。module は変数の素の値を
;;;   返すだけで、既定の値・読み方の判断は呼び手(journal・workflow_effect_journal・environment)に残す。
;;;   - 外す規則: DOEFF004 — 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module(この module は os だけで足りる)。
;;; runner = 検の走らせ方を決める環境変数の値の file(tests/runner_env.py)。E2E の旗と OpenCode の URL は、検を集める時点で
;;;   pytest の外から渡す旗(README の走らせ方)で、検の Program の Ask で受ける入口が無い。旗の読み方の判断は conftest.py に残す。
;;;   - 名指し: :files で file の path を(tests は package なので module の名は __init__.py の置き方で変わる)。
;;;   - 外す規則: DOEFF004 — 名指しの file に限る。
;;;   - 禁じる import: doeff の業務の module。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり当たる。この file を消すと、package の file の母集団は根の設定へ戻る。
(defarchitecture doeff-conductor
  :root "src"
  :layers [(layer environment
             :summary "conductor が環境変数から読む値の module — Program の外で状態の置き場と profile の設定を決める"
             :knows "環境変数の名・XDG の state の根"
             :does-not-know "doeff の Program・effect・handler・どの値を使うかの判断"
             :modules [doeff_conductor.env_places]
             :exempt [(rule DOEFF004 "CLI と状態の記録が、どの Program・handler よりも前に状態の置き場と profile の設定を決めるので、設定を Ask で受ける入口が無い(変数の素の値を返すだけで、判断は呼び手に残す)")]
             :forbid-modules [doeff doeff_conductor doeff_core_effects doeff_hy doeff_vm])
           (layer runner
             :summary "検の走らせ方を決める環境変数の値の file — pytest の外から渡す旗"
             :knows "検の旗の環境変数の名"
             :does-not-know "doeff の Program・effect・handler・旗の読み方の判断"
             :files ["tests/runner_env.py"]
             :exempt [(rule DOEFF004 "E2E の旗と OpenCode の URL は、検を集める時点で pytest の外から渡す旗で、検の Program の Ask で受ける入口が無い(変数の素の値を返すだけで、判断は conftest.py に残す)")]
             :forbid-modules [doeff doeff_conductor doeff_core_effects doeff_hy doeff_vm])])
