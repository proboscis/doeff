;;; doeff-agents の層の宣言(agora-redesign #2861)。
;;;
;;; 層は 2 つ。規則の母集団から外すのは foundation の層の名指しの 2 module の DOEFF004 だけ(以前の外し 2 つは #3012 で消した:
;;; fake-agent の DOEFF004 と、層 runner-env そのもの)。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
;;; architecture.hy(この file)から決める(#2811)。ここで外すのは下の名指しの module だけで、この package のほかの file の母集団は
;;; 変わらない(今までどおり DOEFF004 が当たる)。module の名は file の名(conformance・tests の dir には __init__.py が無い)。
;;;
;;; fake-agent = conformance の偽の agent(conformance/conformance_agent.py)。agentd が codex / claude の代わりに起こす、本物の
;;;   CLI の代役の script。本物の CLI と同じく、走らせ方の材料(手順の file・記録の file・結果の通り道・CLAUDE_CONFIG_DIR・CODEX_HOME)
;;;   を環境変数で受ける — README の Env contract がこの script の口で、受けた環境を記録する(record_env)のも検の中身。
;;;   環境変数は os.environ を直に読まず、起動の時に 1 度 ReadEnvironment で問う(答えるのは doeff の foundation の handler
;;;   subprocess_handler)— そのために doeff と doeff_core_effects は import してよい(agora-redesign #3012・利用者 2026-10-02 23:0x
;;;   「known と印した critical も直す」)。本体は受けた写像を読む普通の Python のまま。
;;;   台本の判定器(conformance/scripted_judge.py・#2954)も同じ層: agentd が LLM の prompt judge の代わりに
;;;   `--prompt-judge-cmd` で起こす代役の script で、判定の表と記録の file を環境変数(CONFORMANCE_JUDGE_TABLE・
;;;   CONFORMANCE_JUDGE_JOURNAL)で受ける(README の契約)— 読み方は同じく ReadEnvironment。この層は DOEFF004 を外さない。
;;;   前の形へ戻すなら、2 つの代役の _received_environment を os.environ の読みへ戻し、DOEFF004 の :exempt の行と
;;;   forbid-modules の doeff・doeff_core_effects を足し直す。
;;; (以前の層 runner-env — tests/runner_env.py・conformance/conformance_env.py — は外した: 2 つは環境変数を ReadEnvironment と本物の
;;;   答え手で読むようになり、DOEFF004 から外す理由と doeff の import を禁じる理由が無くなった・agora-redesign #3012。)
;;; foundation = 環境変数の読みが許される foundation の handler の層(利用者 2026-10-02 22:2x "reading env var from outside foundation
;;;   handler is clear violation" — 環境変数の読みは foundation の handler の中だけ)。名指すのは、agent の起こしに答える handler の
;;;   2 module だけ: doeff_agents.handlers.daemon(daemon の handler が codex を繋ぐ時の CODEX_HOME の既定)・
;;;   doeff_agents.handlers.production(本番の handler が codex を起こす時の CODEX_HOME の既定)。どちらも policy が家を名指さない時だけ
;;;   本物の CLI と同じ既定(CODEX_HOME)を読む。ほかの file を足さない(doeff-core-effects の foundation の層と同じ形・cisco-c8 の決め)。
;;;   戻し方: この層を消せば、DOEFF004 が元どおり 2 module に当たる。
;;; 戻し方: この file を消すと、この package の file の母集団は根の設定へ戻る。
(defarchitecture doeff-agents
  :root "."
  :layers [(layer fake-agent
             :summary "conformance の偽の agent — agentd が本物の CLI の代わりに起こし、材料を環境変数で受ける(起動の時に 1 度 ReadEnvironment で問う)"
             :knows "本物の CLI の起こされ方・conformance の手順と記録の形・環境変数の名・環境を問う effect(ReadEnvironment)と答える handler"
             :does-not-know "doeff_agents の業務の Program・handler"
             :modules [conformance_agent scripted_judge]
             :forbid-modules [doeff_agents doeff_hy doeff_vm])
           (layer foundation
             :summary "環境変数の読みが許される foundation の handler の層 — agent の起こしに答える handler(daemon・production)"
             :knows "agent の CLI の家の環境変数の名(CODEX_HOME)と、その既定"
             :does-not-know "業務の Program・どの業務がどの agent を起こすか"
             :modules [doeff_agents.handlers.daemon doeff_agents.handlers.production]
             :exempt [(rule DOEFF004 "foundation の層 — agent の起こしに答える handler が、policy が家を名指さない時に本物の CLI と同じ既定(CODEX_HOME)を読む。環境変数の読みは foundation の handler の中だけ可(利用者 2026-10-02)")])])
