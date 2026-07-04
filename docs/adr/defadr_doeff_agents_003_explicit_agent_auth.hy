;;; Executable ADR for agentd's explicit agent-auth-profile launch gate.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-003
  :title "agent auth profile は起動時に明示必須 — agentd は暗黙の ~/.codex フォールバックを拒否する"
  :status "accepted"
  :scope ["doeff-agentd" "agentd.session-launch" "agent-auth-profile"]
  :problem
    [(fact
       "codex は CODEX_HOME 未指定なら ~/.codex に暗黙フォールバックする。共有マシンではそこに personal アカウントが住んでおり、どのアカウントで agent が走るか(課金・権限の決定)が設定の欠落で決まっていた。"
       :evidence "~/.codex/auth.json vs ~/.codex/profiles/company/auth.json(実測 2026-07-04)")
     (fact
       "2026-07-04、ACP hypha runtime のワイヤ登録カタログが裸の codex コマンドを登録しており、無人 attend session が personal アカウントで走って rate-limit メニューで死亡、personal 週次クォータは 100% に達した。"
       :evidence "agent-control-plane ADR 0039 problem facts; ~/.codex/sessions rollout telemetry")
     (fact
       "呼び手側(ACP)の宣言修正だけでは、ACP 外の呼び手・将来の宣言漏れを塞げない。全 agent session は agentd の session.launch を通る — 単一の強制点はそこにある。"
       :evidence "packages/doeff-agentd/src/main.rs session_launch")]
  :context
    [(interpretation
       "auth profile は project/namespace の属性であり、デフォルトは存在しない。欠落は『どこかの都合のいい値で走る』ではなく『明確なエラーで止まる』でなければならない。")
     (interpretation
       "claude にも同じ原理(CLAUDE_CONFIG_DIR)が適用されるが、既存の claude 呼び手(取引運用を含む)が移行するまで launch 拒否は codex のみ・claude は警告に留める(段階強制)。")]
  :decision
    [(rule R1 "codex を起動する session.launch(agent_type=codex または command が codex を含む)は、session_env の CODEX_HOME か command 内の CODEX_HOME= を必須とする。無ければ tmux 作業前に明確なエラーで拒否する。")
     (rule R2 "エラーメッセージは欠落した宣言(CODEX_HOME)と対処(session_env / command で明示)と根拠(デフォルト不在)を名指しする。")
     (rule R3 "claude session は CLAUDE_CONFIG_DIR 未指定を警告する。呼び手の移行完了後、R1 と同じ拒否に昇格する(昇格はこの ADR の改訂として記録する)。")
     (rule R4 "agentd は profile の中身(どのアカウントか)を検証しない — それは呼び手(project/namespace の宣言、ACP では ADR 0039)の責務。agentd が強制するのは『明示されていること』のみ。")]
  :laws
    [(law codex-launch-requires-explicit-auth
       :statement "codex_launch => explicit_CODEX_HOME (session_env or command) else reject_before_tmux"
       :counterexamples
         [(counterexample "CODEX_HOME 未指定の codex launch が ~/.codex で走り出す")
          (counterexample "拒否の代わりに warning ログだけ出して起動を続行する")])
     (law no-default-auth-profile
       :statement "missing_auth_declaration => clear_error not_fallback"
       :counterexamples
         [(counterexample "agentd が『妥当そうな』プロファイルを推測して補完する")
          (counterexample "会社プロファイルのパスを agentd にハードコードしてデフォルトにする")])]
  :enforcement
    [(defsemgrep no-default-codex-home-in-agentd
       :languages ["generic"]
       :pattern "DEFAULT_CODEX_HOME"
       :message "agentd に CODEX_HOME のデフォルトを持たせない(ADR-DOE-AGENTS-003 R4: agentd は明示の強制のみ、値の決定は呼び手)。"
       :bad ["const DEFAULT_CODEX_HOME: &str = \"/Users/x/.codex/profiles/agent\";"]
       :good ["// auth profile must arrive explicitly via session_env or the launch command"])]
  :plans ["docs/adr/defadr_doeff_agents_003_explicit_agent_auth.hy"
          "packages/doeff-agentd/src/main.rs (session_launch auth gate; cargo test)"])
