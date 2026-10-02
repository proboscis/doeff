;;; doeff-effect-analyzer の層の宣言(agora-redesign #2860)。
;;;
;;; 今は、規則の母集団から外す層 1 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module だけで、この package のほかの file の母集団は
;;; 変わらない(今までどおり DOEFF004 が当たる)。
;;;
;;; environment = 解析の cache の置き場と入切を環境変数から読む module(doeff_effect_analyzer.env_places)。解析器は道具(CLI・
;;;   editor・pytest の収集)として Program の外で走り、置き場はどの Program よりも前に決まるので、Ask で受ける入口が無い。
;;;   module は変数の素の値(DOEFF_EFFECT_ANALYZER_CACHE・DOEFF_EFFECT_ANALYZER_RESULT_CACHE)と XDG の cache の根を返すだけで、
;;;   "off" で切る・どの置き場を使うかの判断は呼び手(program_effects・result_cache)に残す。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり env_places に当たる。この file を消すと、この package の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-effect-analyzer
  :root "."
  :layers [(layer environment
             :summary "解析の cache の置き場と入切を環境変数から読む module — Program の外で置き場を決める"
             :knows "環境変数の名・XDG の cache の根"
             :does-not-know "doeff の Program・effect・handler・どの置き場を使うかの判断"
             :modules [doeff_effect_analyzer.env_places]
             :exempt [(rule DOEFF004 "解析器は CLI・editor・pytest の収集として Program の外で走り、cache の置き場と入切がどの Program よりも前に決まるので、設定を Ask で受ける入口が無い(変数の素の値を返すだけで、判断は呼び手に残す)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_effect_analyzer])])
