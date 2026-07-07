;;; Executable ADR: agent 実行 = effects+handlers、agentd = Hy session host.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-004
  :title "agent 実行は effect 語彙 + kind 別 defhandler に分解し、agentd は『寿命の外部性』だけを提供する Hy 製 session host(session を resource とするミニ control plane)へ再実装する — conformance 先行で Rust を oracle に交代"
  :status "proposed"
  :scope ["packages/doeff-agents"
          "packages/doeff-agentd(現 Rust = oracle)"
          "docs/adr/defadr_doeff_agents_004_effects_session_host.hy"]
  :problem
    [(fact
       "agentd は god daemon 化し(argv 組立・trust・monitor・solicitation・judge・taxonomy・DB・socket)、protocol 物理が Rust builder と Python adapter に二重実装されて claude 側だけ腐った(trust ダイアログ永久ハング・hooks 継承死は Rust 側のみ修正が届いた)。"
       :evidence "doeff 42fb28fa / 49b3549b(2026-07-05 live 傷跡)")
     (fact
       "ACP は doeff-agents でなく agentd の暗黙 wire に結合しており、新 CLI(opencode/kimi/AWS 系)追加の経路が『Rust の god daemon を編集する』しかない。"
       :evidence "ACP src/Acp/App/Agentd*.hs; ユーザー討議 2026-07-05 深夜")
     (fact
       "公開 launch effect の現署名は session-env(汎用 env dict)を引数に持ち、auth 物理(合成 CODEX_HOME / CLAUDE_CONFIG_DIR)が effect user から流れ込む構造裏口になっている — 呼び手が credentials の存在と形を知らないと起動できない。effect API レビュー(2026-07-07)で『the effect user should not know the auth specifics』と裁定。"
       :evidence "packages/doeff-agents/src/doeff_agents/handlers/codex.hy LaunchEffect [... session-env]; ユーザー討議 2026-07-07")]
  :context
    [(interpretation
       "agentd の存在理由は効果の解釈ではなく寿命の外部性(セッションが呼び手より長生きする・複数 user 間で真実が一つ)。意味論は doeff-native であり、daemon とは handler stack を寿命の外側でホストする場所にすぎない(ユーザー裁定)。多重化点は host の内側 = kubelet/CRI 同型で、ACP は multi-backend を持たない。")
     (interpretation
       "実装言語は Hy + doeff(ユーザー裁定)。monitor は常駐 continuation ではなく、session 行(store-as-truth)から毎 cycle 再導出する level-triggered reconciler program — agentd は ACP と同じ存在論のミニ control plane になり、新しい理論を要さない。")
     (interpretation
       "auth の家(2026-07-07 レビュー裁定): auth は effect user にも handler コードにも属さず、handler を束縛する者の構成に属する(Ask/Reader の注入と同型)。ローカル束縛では main が、host 束縛では binding registry を持つ control plane が構成を注入する。auth×profile の組合せ表現(registry)を host に持たせることは棄却 — 組合せの真実は束縛側に 1 箇所で、host/adapter は kind 別の材料スキーマの形しか知らない。")]
  :decision
    [(rule R1 "effect 語彙が interface: BuildLaunch / PreLaunchSetup / ClassifyPane / DeliverMessage / WireResultChannel(+substrate: SessionStore / Tmux / Clock / Proc)。共有 policy program(monitor・bounded solicitation・judge・taxonomy・result 検証)は 1 本で、impl は書かない。")
     (rule R2 "kind 追加 = defhandler モジュール 1 個 + kind スキーマ + conformance green。同一モジュールが直接束縛(呼び手 process 内)と host 束縛(RPC 転送)の両方で動く — impl は substrate-clean(生 IO 禁止、substrate effect のみ yield)。")
     (rule R3 "agentd(Hy)は外部性の 4 点だけを所有する: socket・単一 writer actor(SQLite session 行)・毎 cycle の reconciler 起動・lease。RPC method は program に写像され、handler stack が解釈する。continuation は永続化しない — 真実は行のみ。")
     (rule R4 "conformance 先行: Rust agentd を oracle に black-box 契約 suite(mini_conformance 前例)+ 台本駆動の conformance-agent(偽 CLI、実クォータ非消費)を先に整備し、Hy 実装は parity 到達で交代。cargo 93 tests + 2026-07-05 の trust/hooks 傷跡を挙動として結晶化してから Rust を退役する。")
     (rule R5 "host は kinds.list で {kind, apiVersion, スキーマ} を広告し、ACP は宣言時に照合して未知 kind/version を fail-loud 拒否する。wire は有限の versioned 語彙に限る(任意 effect のリモート転送は禁止)。")
     (rule R6 "デプロイは frozen 環境から(pin 済み専用 env)。dev venv / target-debug 依存の自己参照(子守りが子守られる開発環境に依存する)を禁止する。")
     (rule R7 "公開 launch effect は auth-blind: 引数は意図のみ(kind・binding 名(alias、省略可)・work-dir・prompt・model・effort・result-contract・session 名)。auth 物理(生パス・生鍵・合成 env)と汎用 env 注入(session-env — auth の構造裏口)は公開 effect から退役し、kind 別の auth 材料スキーマ(codex = authFile+profileDir、claude = configDir)は handler の束縛時構成として注入する。host 束縛(RPC)では構成の serialize として運び、effect 引数としては運ばない。非 auth の env 注入が将来必要になっても handler 構成として導入し、effect 引数に戻さない。ACP 側の同原則(0044 R2/R5: payload override は alias 参照のみ・秘密は参照のみ)と同じ線を doeff の effect 語彙に引く。")]
  :laws
    [(law protocol-physics-has-one-home
       :statement "protocol_physics(kind) => single_defhandler_module never_duplicated_across_languages"
       :counterexamples
         [(counterexample "Rust builder と Python adapter に同じ CLI の物理を二重実装し、片方だけ修正する")])
     (law daemon-owns-only-exteriority
       :statement "session_host => socket + writer_actor + reconciler_schedule + lease; semantics_lives_in_programs_and_handlers"
       :counterexamples
         [(counterexample "solicitation 回数や taxonomy を host 固有コードに焼き付ける")])
     (law truth-is-rows-not-continuations
       :statement "monitor => level_triggered_rederivation_from_session_rows; continuations_never_persisted"
       :counterexamples
         [(counterexample "中断した program の continuation を直列化して再起動時に復元する")])
     (law effect-user-is-auth-blind
       :statement "public_launch_effect_args => intent_only; auth_material_and_env_live_in_handler_binding_configuration"
       :counterexamples
         [(counterexample "session-env 経由で合成 CODEX_HOME を effect user が組んで渡す(現行 LaunchEffect の形 — R7 が退役対象と宣言)")
          (counterexample "LaunchAgent に authFile 引数を足し、user program がアカウント物理を知る")
          (counterexample "テストと本番で同じ program が走らない(auth が effect 引数に居るため差し替え点が構成でなく呼び手コードになる)")])
     (law conformance-before-cutover
       :statement "rust_retirement => hy_impl_passes_oracle_conformance including_trust_and_hooks_scars"
       :counterexamples
         [(counterexample "conformance 無しで Hy 版に切り替え、solicitation/turn-end の hardening が退行する")])]
  :enforcement
    ;; proposed(実装前): 実 enforcement(conformance suite・substrate-clean
    ;; 検査)は実装と同一チェンジセットで足す。現段階は設計 ADR の存在ピン。
    [(defsemgrep no-auth-material-in-launch-effect-args
       :languages ["generic"]
       :pattern "LaunchAgent :auth-file"
       :message "公開 launch effect の引数に auth 物理を置くのは ADR-DOE-AGENTS-004 R7 違反。auth 材料は handler の束縛時構成(ローカル = main、host 束縛 = control plane の registry)として注入する。"
       :bad ["(LaunchAgent :auth-file \"~/.codex/auth.json\" :prompt p)"]
       :good ["(LaunchAgent :kind \"codex\" :binding \"company\" :prompt p)"])
     (defsemgrep no-new-agent-physics-in-rust-agentd
       :languages ["generic"]
       :pattern "fn build_kimi_argv"
       :message "新 CLI の protocol 物理を Rust agentd に足すのは ADR-DOE-AGENTS-004 R2 違反。kind 追加は doeff-agents の defhandler モジュール + conformance。"
       :bad ["fn build_kimi_argv(params: &LaunchParams) -> Vec<String> {"]
       :good ["; doeff-agents/impls/kimi.hy に defhandler を書く"])]
  :plans ["../agent-control-plane 側 master plan: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"])
