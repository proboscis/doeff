;;; doeff-openrouter の層の宣言(agora-redesign #2861)。
;;;
;;; 今は、層 1 つだけを置く(規則の母集団から外す物は無い — 以前の DOEFF004 の外しは #3012 で消した)。doeff-linter は Python の文ごとの
;;; 規則の母集団を、file から上へ最も近い architecture.hy(この file)から決める(#2811)。module の名は file の名(tests の dir には
;;; __init__.py が無い)。
;;;
;;; local-dotenv = live の検が使う OpenRouter のキーを、走らせる人の手元の .env(packages/doeff-openrouter/.env)から読んで値を返す
;;;   module(tests/local_dotenv.py)。検の process の環境には書かない — キーは live の検の fixture が、環境変数(ReadEnvironment と
;;;   本物の答え手)を先に・無ければこの値を読む。以前は .env を os.environ へ写していたので DOEFF004 から外していたが、その外しは消した
;;;   (agora-redesign #3012)。
;;;   - 禁じる import: doeff の業務の module。この層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: この file を消すと、この package の file の母集団は根の設定へ戻る。
(defarchitecture doeff-openrouter
  :root "."
  :layers [(layer local-dotenv
             :summary "走らせる人の手元の .env を読んで値を返す module(検の process の環境には書かない)"
             :knows ".env の形"
             :does-not-know "doeff の Program・effect・handler・環境変数"
             :modules [local_dotenv]
             :forbid-modules [doeff doeff_openrouter doeff_core_effects doeff_hy doeff_vm])])
