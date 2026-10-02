;;; doeff-hy の層の宣言(agora-redesign #2811)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy
;;; (この file)から決める — doeff の根の実行(pre-commit・make lint-doeff)も、この宣言の除外を読む。
;;;
;;; startup = Python の起動の時点で `.pth`(src/doeff_hy_bytecode_guard.pth)から入る、Hy の bytecode の見張り
;;;   (doeff_hy_bytecode_guard)。venv の Python が起動する時、どの Program・どの handler よりも前に走るので、設定を依存の注入
;;;   (doeff の Ask)で受ける入口が無い。共有の code の置き場の dir と置き場を切る旗は環境変数(DOEFF_HY_CODE_STORE・XDG_CACHE_HOME)で
;;;   読む(置き場を切る手 DOEFF_HY_CODE_STORE=off は #2799 の当座の手として要る)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(doeff_hy_bytecode_guard とその下)に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする(外した規則が業務の code にも
;;;     当たらなくなる形を作らない)。
;;; environment = 道具(doeff-hy-check の CLI・pytest の収集)が使う置き場を環境変数から読む module(doeff_hy.env_places)
;;;   (agora-redesign #2860)。置き場はどの Program よりも前に決まるので Ask で受ける入口が無い。module は変数の素の値と XDG の
;;;   cache の根を返すだけで、どの置き場を使うかの判断は呼び手(static_cache)に残す。
;;;   - 外す規則: DOEFF004 — 名指しの module(doeff_hy.env_places)に限る。この package のほかの module は今までどおり当たる。
;;;   - 禁じる import: doeff の業務の module(doeff_hy を含む — この module は os と pathlib だけで足りる)。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり見張り(または env_places)に当たる。
(defarchitecture doeff-hy
  :root "doeff_hy"
  :layers [(layer startup
             :summary "Python の起動の時点(.pth)で入る Hy の bytecode の見張り — Program の外"
             :knows "Python の import の仕組み・Hy の bytecode・共有の code の置き場の dir"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_hy_bytecode_guard]
             :exempt [(rule DOEFF004 "venv の Python が起動する時に .pth から入り、どの Program・handler よりも前に走るので、設定を Ask で受ける入口が無い(置き場の dir と置き場を切る旗を環境変数で読む)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])
           (layer environment
             :summary "道具が使う置き場を環境変数から読む module — Program の外で置き場を決める"
             :knows "環境変数の名・XDG の cache の根"
             :does-not-know "doeff の Program・effect・handler・どの置き場を使うかの判断"
             :modules [doeff_hy.env_places]
             :exempt [(rule DOEFF004 "doeff-hy-check の CLI と pytest の収集が、どの Program よりも前に cache の置き場を決めるので、設定を Ask で受ける入口が無い(変数の素の値を返すだけで、判断は呼び手に残す)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])])
