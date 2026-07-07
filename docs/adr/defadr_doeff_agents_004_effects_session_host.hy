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
       "launch 面の session_env(汎用 env dict)は auth 物理(合成 CODEX_HOME / CLAUDE_CONFIG_DIR)が effect user から流れ込む構造裏口だった — FORBIDDEN_AGENT_ENV_KEYS(provider API キーの blocklist)が既に『env に auth を運ばせない』意図を刻んでいたのに、profile ディレクトリ系キーは現行アーキテクチャ自身が必要とするためリストに載せられなかった(blocklist の腐敗そのもの)。『the effect user should not know the auth specifics』(2026-07-07 裁定)。"
       :evidence "doeff_agents/shell.py FORBIDDEN_AGENT_ENV_KEYS; 旧 impls の session_env 経由 CODEX_HOME 解決; ユーザー討議 2026-07-07")]
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
     (rule R9 "公開 launch 面(effect 語彙と wire launch の両方)は auth-blind: auth/profile 物理(CODEX_HOME / CLAUDE_CONFIG_DIR / 生鍵)は typed `binding`(束縛時構成の serialize、kind 判別スキーマ: codex {codex_home} / claude-code {config_dir})でのみ運ぶ。session_env は非 auth overlay に縮む — binding 所有キーの混入は全副作用より前に typed reject(所有権ベース: 既知の悪いキーの列挙は腐るが所有権は腐らない。provider API キーの FORBIDDEN blocklist は substrate 境界の防御として併存)。非 auth の per-launch env(観測フラグ・result channel 配線値など)は overlay として正当であり、handler 構成へ押し込まない(2026-07-07 裁定: env には auth と run 意図の 2 住人が居て、家が違う)。kind 別 auth 材料スキーマは handler の束縛時構成 — ローカル束縛 = main、host 束縛 = binding registry を持つ control plane(ACP 0044 R2/R5 と同じ線)。binding admission は ADR 0044 R3 と同思想: 未知 kind / kind↔agent_type 不整合 / 必須 field 欠落 / 未知 field を typed reject。")]
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
       :statement "public_launch_surface_args => intent_plus_nonauth_overlay_only; auth_material_rides_typed_binding_owned_by_handler_binder"
       :facts
         [(fact
            "R9 実装 landed(2026-07-07): sessionhost の launch admission が binding 検査 + overlay 所有キー拒否を全副作用より前に実施し、tmux env = overlay ∪ binding 由来 auth env(合成の唯一の源は per-kind impl の identity — trust 書き先と agent の読みが構造的に一致)。ACP engine は bindingWireValue を typed field で送り、launch env の auth 直詰めを退役。live 検証: auth-in-env は loud reject / binding 由来 CLAUDE_CONFIG_DIR と非 auth overlay が tmux session env に並んで届く。"
            :evidence "doeff agentd-retire-rust 0ce87c84(sessionhost/policy.hy BINDING-OWNED-ENV-KEYS + binding-admission-error / launch.hy R7 admission / sessionhost_launch_deftests.hy test-launch-allows-non-auth-overlay-env・test-launch-rejects-auth-in-session-env)+ doeff-agent-haskell 789e3a8 + ACP 7b7cac6")]
       :counterexamples
         [(counterexample "session_env 経由で合成 CODEX_HOME を effect user が組んで渡す(旧形 — launch admission が typed reject する)")
          (counterexample "LaunchAgent / wire launch に authFile 引数を足し、user program がアカウント物理を知る")
          (counterexample "非 auth の per-launch env が必要になったからと session_env の所有権ガードを外す(overlay は非 auth 専用のまま、auth は binding へ)")
          (counterexample "テストと本番で同じ program が走らない(auth が effect 引数に居るため差し替え点が構成でなく呼び手コードになる)")])
     (law conformance-before-cutover
       :statement "rust_retirement => hy_impl_passes_oracle_conformance including_trust_and_hooks_scars"
       :counterexamples
         [(counterexample "conformance 無しで Hy 版に切り替え、solicitation/turn-end の hardening が退行する")])]
  :enforcement
    ;; R9 実 enforcement(admission ガード + deftests)は sessionhost 実装と
    ;; 同一チェンジセット(agentd-retire-rust 0ce87c84)に landed。
    [(defsemgrep no-auth-material-in-launch-effect-args
       :languages ["generic"]
       :pattern "LaunchAgent :auth-file"
       :message "公開 launch effect の引数に auth 物理を置くのは ADR-DOE-AGENTS-004 R9 違反。auth 材料は handler の束縛時構成(ローカル = main、host 束縛 = control plane の binding registry)として注入し、wire では typed binding で運ぶ。session_env は非 auth overlay 専用。"
       :bad ["(LaunchAgent :auth-file \"~/.codex/auth.json\" :prompt p)"]
       :good ["(LaunchAgent :kind \"codex\" :binding \"company\" :prompt p)"])
     (defsemgrep no-new-agent-physics-in-rust-agentd
       :languages ["generic"]
       :pattern "fn build_kimi_argv"
       :message "新 CLI の protocol 物理を Rust agentd に足すのは ADR-DOE-AGENTS-004 R2 違反。kind 追加は doeff-agents の defhandler モジュール + conformance。"
       :bad ["fn build_kimi_argv(params: &LaunchParams) -> Vec<String> {"]
       :good ["; doeff-agents/impls/kimi.hy に defhandler を書く"])]
  :plans ["../agent-control-plane 側 master plan: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"])
