;;; doeff-agents の層の宣言(agora-redesign #2861)。
;;;
;;; 今は、層 1 つ(fake-agent)だけを置く — 規則の母集団から外す物は無い(以前の外し 2 つは #3012 で消した: fake-agent の DOEFF004 と、
;;; 層 runner-env そのもの)。doeff-linter は Python の文ごとの規則の母集団を、file から上へ最も近い
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
;;; 戻し方: この file を消すと、この package の file の母集団は根の設定へ戻る。
(defarchitecture doeff-agents
  :root "."
  :layers [(layer fake-agent
             :summary "conformance の偽の agent — agentd が本物の CLI の代わりに起こし、材料を環境変数で受ける(起動の時に 1 度 ReadEnvironment で問う)"
             :knows "本物の CLI の起こされ方・conformance の手順と記録の形・環境変数の名・環境を問う effect(ReadEnvironment)と答える handler"
             :does-not-know "doeff_agents の業務の Program・handler"
             :modules [conformance_agent scripted_judge]
             :forbid-modules [doeff_agents doeff_hy doeff_vm])])
