;;; doeff-agents の層の宣言(agora-redesign #2861)。
;;;
;;; 今は、規則の母集団から外す層 2 つだけを置く。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module だけで、この package のほかの file の母集団は
;;; 変わらない(今までどおり DOEFF004 が当たる)。module の名は file の名(conformance・tests の dir には __init__.py が無い)。
;;;
;;; fake-agent = conformance の偽の agent(conformance/conformance_agent.py)。agentd が codex / claude の代わりに起こす、本物の
;;;   CLI の代役の script。本物の CLI と同じく、走らせ方の材料(手順の file・記録の file・結果の通り道・CLAUDE_CONFIG_DIR・CODEX_HOME)
;;;   を環境変数で受ける — README の Env contract がこの script の口で、受けた環境を記録する(record_env)のも検の中身。標準の
;;;   library だけで書き、doeff の Program の外で走る。
;;; runner-env = 検を走らせる人が環境変数で選ぶ材料(検の相手の daemon・本物の Claude の設定の dir と口座)を読む module
;;;   (tests/runner_env.py)。変数の素の値を返すだけで、どれを使うか・既定の値は呼び手(sessionhost_bin・
;;;   agentd_real_agent_result_retry_e2e_support)に残す。
;;;   - 外す規則: DOEFF004(os.environ を直に読む)— 名指しの module に限る。
;;;   - 禁じる import: doeff の業務の module。外した層に業務の code が入ると DOEFF032 が赤にする。
;;; 戻し方: :exempt の行を消せば、DOEFF004 が元どおり名指しの module に当たる。この file を消すと、この package の file の母集団は
;;;   根の設定へ戻る。
(defarchitecture doeff-agents
  :root "."
  :layers [(layer fake-agent
             :summary "conformance の偽の agent — agentd が本物の CLI の代わりに起こし、材料を環境変数で受ける"
             :knows "本物の CLI の起こされ方・conformance の手順と記録の形・環境変数の名"
             :does-not-know "doeff の Program・effect・handler"
             :modules [conformance_agent]
             :exempt [(rule DOEFF004 "本物の codex / claude の CLI の代役として agentd に起こされ、本物と同じく走らせ方の材料を環境変数で受ける(README の Env contract)— 受けた環境を記録するのも検の中身で、Program の外で走るので Ask で受ける入口が無い")]
             :forbid-modules [doeff doeff_agents doeff_core_effects doeff_hy doeff_vm])
           (layer runner-env
             :summary "検を走らせる人が環境変数で選ぶ材料を読む module — 変数の素の値を返すだけ"
             :knows "環境変数の名"
             :does-not-know "doeff の Program・effect・handler・どの値を使うかの判断"
             :modules [runner_env]
             :exempt [(rule DOEFF004 "pytest は Program の外で走り、e2e の相手(daemon・本物の Claude の設定の dir と口座)は走らせる人が環境変数で選ぶので、Ask で受ける入口が無い(変数の素の値を返すだけで、判断は呼び手に残す)")]
             :forbid-modules [doeff doeff_agents doeff_core_effects doeff_hy doeff_vm])])
