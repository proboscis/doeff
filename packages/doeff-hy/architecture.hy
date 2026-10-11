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
;;;
;;; 不変条件(startup の層の .pyc の記録の照らし — doeff_hy_bytecode_guard.macro_use。Program の外なので defservice の :invariants に
;;; 置けず、条をここに 1 か所で書く):
;;;   B1 stale-expansion-is-never-current — 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の module の file の
;;;     中身・環境変数など file の外で決まる値)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。digest は
;;;     process をまたいで覚えない(生きた値を辿った file の状態だけで信じる形になる)。process をまたいで覚えてよいのは、中身で
;;;     決まる純な読み — 辿れない大域を覆う source の文の読み(bound_source — 鍵 = source の中身・名・読み方の code と版)— だけ。
;;;     失敗ケース = tests/test_macro_use_record.py の「process をまたぐ覚え」の条 1〜3(digest を file の状態で覚えて次の process が
;;;     信じる形で赤 — agora-redesign card ki-38508e6e56fd)。
(defarchitecture doeff-hy
  :root "doeff_hy"
  :layers [(layer startup
             :summary "Python の起動の時点(.pth)で入る Hy の bytecode の見張り — Program の外"
             :knows "Python の import の仕組み・Hy の bytecode・共有の code の置き場の dir"
             :does-not-know "doeff の Program・effect・handler"
             :modules [doeff_hy_bytecode_guard]
             :exempt [(rule DOEFF004 "venv の Python が起動する時に .pth から入り、どの Program・handler よりも前に走るので、設定を Ask で受ける入口が無い(置き場の dir と置き場を切る旗を環境変数で読む)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])])
