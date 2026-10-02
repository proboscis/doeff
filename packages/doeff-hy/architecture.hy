;;; doeff-hy の層の宣言(agora-redesign #2811)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い architecture.hy
;;; (この file)から決める — doeff の根の実行(pre-commit・make lint-doeff)も、この宣言の除外を読む。
;;;
;;; startup = Python の起動の時点で `.pth`(src/doeff_hy_bytecode_guard.pth)から入る、Hy の bytecode の見張り
;;;   (doeff_hy_bytecode_guard)。venv の Python が起動する時、どの Program・どの handler よりも前に走るので、設定を依存の注入
;;;   (doeff の Ask)で受ける入口が無い。共有の code の置き場の dir と置き場を切る旗は環境変数(DOEFF_HY_CODE_STORE・XDG_CACHE_HOME)で
;;;   読む(置き場を切る手 DOEFF_HY_CODE_STORE=off は #2799 の当座の手として要る)。
;;;   #3012 で見直して免除のまま置く: ここで doeff の handler を被せると venv のすべての Python の起動が doeff を読み込む(build の入口 PEP 517 と同じ種類)。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module(doeff_hy_bytecode_guard とその下)に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする(外した規則が業務の code にも
;;;     当たらなくなる形を作らない)。
;;; 道具が使う置き場を環境変数から読む module(doeff_hy.env_places)を外していた層 environment は消した — 環境変数を ReadEnvironment の
;;;   効果で本物の答え手 subprocess_handler の下で問う形にしたので、DOEFF004 が当たらない(agora-redesign #3012)。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり見張りに当たる。
(defarchitecture doeff-hy
  :root "doeff_hy"
  :layers [(layer startup
             :summary "Python の起動の時点(.pth)で入る Hy の bytecode の見張り — Program の外"
             :knows "Python の import の仕組み・Hy の bytecode・共有の code の置き場の dir"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_hy_bytecode_guard]
             :exempt [(rule DOEFF004 "venv の Python が起動する時に .pth から入り、どの Program・handler よりも前に走るので、設定を Ask で受ける入口が無い(置き場の dir と置き場を切る旗を環境変数で読む)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])])
