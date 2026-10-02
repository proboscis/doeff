;;; doeff-openrouter の層の宣言(agora-redesign #2861)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module だけで、この package のほかの file の母集団は
;;; 変わらない(今までどおり DOEFF004 が当たる)。module の名は file の名(tests の dir には __init__.py が無い)。
;;;
;;; local-dotenv = live の検が使う OpenRouter のキーを、走らせる人の手元の .env(packages/doeff-openrouter/.env)から検の process の
;;;   環境へ渡す module(tests/local_dotenv.py)。pytest は Program の外で走るので、キーは検の process の環境変数として渡す。
;;;   既に在る変数は上書きしない。
;;;   - 外す規則: DOEFF004(os.environ を直に読み書きする)— 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり local_dotenv に当たる。この file を消すと、この package の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-openrouter
  :root "."
  :layers [(layer local-dotenv
             :summary "走らせる人の手元の .env を検の process の環境へ渡す module"
             :knows ".env の形・環境変数"
             :does-not-know "doeff の Program・effect・handler"
             :modules [local_dotenv]
             :exempt [(rule DOEFF004 "pytest は Program の外で走り、live の検の OpenRouter のキーは走らせる人の手元の .env から検の process の環境変数として渡すしか入口が無い(既に在る変数は上書きしない)")]
             :forbid-modules [doeff doeff_openrouter doeff_core_effects doeff_hy doeff_vm])])
