;;; Executable ADR: agent 実行 = effects+handlers、agentd = Hy session host.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-004
  :title "agent 実行は effect 語彙 + kind 別 defhandler に分解し、agentd は『寿命の外部性』だけを提供する Hy 製 session host(session を resource とするミニ control plane)へ再実装する — conformance 先行で Rust を oracle に交代"
  :status "accepted"
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
       "実装言語は Hy + doeff(ユーザー裁定)。monitor は常駐 continuation ではなく、session 行(store-as-truth)から毎 cycle 再導出する level-triggered reconciler program — agentd は ACP と同じ存在論のミニ control plane になり、新しい理論を要さない。")]
  :decision
    [(rule R1 "effect 語彙が interface: BuildLaunch / PreLaunchSetup / ClassifyPane / DeliverMessage / WireResultChannel(+substrate: SessionStore / Tmux / Fs / Env / Clock / Proc)。共有 policy program(monitor・bounded solicitation・judge・taxonomy・result 検証)は 1 本で、impl は書かない。substrate の Fs / Env は C2 で追加 — per-kind trust 物理(S11/S12: claude .claude.json temp+rename・codex config.toml・process env fallback)は kind 所有だが、impls/ は substrate-clean で生 IO を持てないため、FS / env 読みも effect 境界を通る。")
     (rule R2 "kind 追加 = defhandler モジュール 1 個 + kind スキーマ + conformance green。同一モジュールが直接束縛(呼び手 process 内)と host 束縛(RPC 転送)の両方で動く — impl は substrate-clean(生 IO 禁止、substrate effect のみ yield)。")
     (rule R3 "agentd(Hy)は外部性の 4 点だけを所有する: socket・単一 writer actor(SQLite session 行)・毎 cycle の reconciler 起動・lease。RPC method は program に写像され、handler stack が解釈する。continuation は永続化しない — 真実は行のみ。")
     (rule R4 "conformance 先行: Rust agentd を oracle に black-box 契約 suite(mini_conformance 前例)+ 台本駆動の conformance-agent(偽 CLI、実クォータ非消費)を先に整備し、Hy 実装は parity 到達で交代。cargo 93 tests + 2026-07-05 の trust/hooks 傷跡を挙動として結晶化してから Rust を退役する。")
     (rule R5 "host は kinds.list で {kind, apiVersion, スキーマ} を広告し、ACP は宣言時に照合して未知 kind/version を fail-loud 拒否する。wire は有限の versioned 語彙に限る(任意 effect のリモート転送は禁止)。【2026-07-08 縮小裁定(plan 裁定 9)で landed: 広告は {kind, agent_type, required_field, api_version} の有限表のみ — alias 解決や registry 転送は host に持たせない。表と広告関数(policy.hy BINDING-KIND-* 表 + binding-kind-advertisement)がスキーマの単一の家で、host.hy の kinds.list dispatch は純粋・store 非依存。api_version は ACP agentBindingApiVersionV1(acp.dev/agent-binding/v1)と同値。『宣言時に照合』は精密化: ACP 自身の語彙への宣言時 fail-loud は ACP admission(0044 R3)が担い、host 広告とのクロスチェックは ACP daemon の level-triggered 周期照合(verifyBindingKindsOnce → BindingKindUnsupported condition)が担う — 登録と host liveness は結合せず、CLI `doeff-agents agentd kinds` は ensure/spawn しない純 read で host 不達 = 観測なし ≠ 違反。照合 law の家は ACP 0044 kind-verification-is-level-triggered。deftest: sessionhost_host_deftests.hy test-dispatch-kinds-list(表と広告の乖離を red 化)。】")
     (rule R6 "デプロイは frozen 環境から(pin 済み専用 env)。dev venv / target-debug 依存の自己参照(子守りが子守られる開発環境に依存する)を禁止する。")
     (rule R7 "退役後の正典 executor は doeff-sessionhost: ensure_agentd の spawn 解決は DOEFF_AGENTD_BIN(明示 seam)→ 実行中 interpreter 隣接の console script → PATH の doeff-sessionhost で、退役 Rust binary は解決対象に含めない(silent rollback の根絶、ACP ADR 0045 R5)。Rust binary/source の保存理由は rollback 可用性のみ — 正しさの基準として参照することを禁止する(U1: それは一度も oracle ではなく partial-unreliable-impl だった)。")
     (rule R8 "result-contract 検証の意味論は JSON Schema 仕様が唯一の正(U1 裁定): 検証器は準拠参照実装(jsonschema)の輸入であり、subset を自前実装しない。仕様適合は公式 JSON-Schema-Test-Suite を repo 内に vendor して adapter に直接通すことで検証する(draft2020-12 required 全 1260 case green。skip 21 は全て裁定記録付き: remote レジストリ依存 = 契約は自己完結前提で unresolvable $ref は fail-loud / ECMA \\p regex = admission が launch 時 fail-closed で拒否 — 対 deftest あり)。schema 自体は launch 時に meta-schema で fail-closed 検証(壊れた契約で session を作らない)。旧 Rust 実装の fail-open 挙動を expected に固定するテストは歴史ピンとしても置かない。")
     (rule R9 "公開 launch 面(effect 語彙と wire launch の両方)は auth-blind: auth/profile 物理(CODEX_HOME / CLAUDE_CONFIG_DIR / 生鍵)は typed `binding`(束縛時構成の serialize、kind 判別スキーマ: codex {codex_home} / claude-code {config_dir})でのみ運ぶ。session_env は非 auth overlay に縮む — binding 所有キーの混入は全副作用より前に typed reject(所有権ベース: 既知の悪いキーの列挙は腐るが所有権は腐らない。provider API キーの FORBIDDEN blocklist は substrate 境界の防御として併存)。非 auth の per-launch env(観測フラグ・result channel 配線値など)は overlay として正当であり、handler 構成へ押し込まない(2026-07-07 裁定: env には auth と run 意図の 2 住人が居て、家が違う)。kind 別 auth 材料スキーマは handler の束縛時構成 — ローカル束縛 = main、host 束縛 = binding registry を持つ control plane(ACP 0044 R2/R5 と同じ線)。binding admission は ADR 0044 R3 と同思想: 未知 kind / kind↔agent_type 不整合 / 必須 field 欠落 / 未知 field を typed reject。")
     (rule R10 "単一インスタンス排他の実体は socket bind であり、lease はその影(観測面)。この主従を順序と述語で守る: (a) host 起動順は bind → store open → lease 取得 → latch clear — bind に負けた競合者は store にも lease にも触れずに死ぬ。(b) ensure の spawn 述語は『socket に live listener が居ない』(path 不在 / ECONNREFUSED)のみ — 生きた listener が status probe に遅い場合は長い予算(AGENTD_BUSY_STATUS_TIMEOUT_SECONDS)で再試行し、それでも駄目なら spawn せず loud エラー(slow ≠ dead。証明されない死で競合 host を作らない)。(c) heartbeat は失効した他人名義 lease を再取得する(level-triggered 自己修復 — 盗んで死んだ競合者の残骸から bind 保持者が回復する)が、未失効の他人名義は loud エラーのまま(別 socket 同一 DB 誤構成 = 生きた二重 host の検出面)。判定と upsert は BEGIN IMMEDIATE で原子化。")]
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
            :evidence "doeff agentd-retire-rust 0ce87c84(sessionhost/policy.hy BINDING-OWNED-ENV-KEYS + binding-admission-error / launch.hy R7 admission / sessionhost_launch_deftests.hy test-launch-allows-non-auth-overlay-env・test-launch-rejects-auth-in-session-env)+ doeff-agent-haskell 789e3a8 + ACP 7b7cac6")
          (fact
            "R9 のローカル束縛(in-process)面も完結(2026-07-08): auth の家 = 束縛時構成として CodexRuntimePolicy(codex_home)を新設(ClaudeRuntimePolicy の codex 対)し、TmuxAgentHandler / DaemonAgentHandler / 全 factory / tmux-agent-defhandler / codex-handler(Hy、`codex-home` param)が constructor 注入を受ける。全ローカル launch 入口(production / daemon / session.py / claude.hy / codex.hy)に overlay ガード assert_session_env_is_non_auth_overlay を敷設 — 所有集合は sessionhost/policy.hy の overlay-env-offenders を import する単一実装で、ローカル guard と host admission は drift できない。DaemonAgentHandler は codex auth を typed wire binding(policy > binder process env)として転送し session_env 直詰めを退役 — R9 host admission に対して live で壊れていた経路(conductor の codex 起動が踏む地雷)の修理。daemon 経路の局所 codex trust 書きも退役(pre-launch 物理は host per-kind impl の単一所有)。mutation 検証済(_wire_binding 殺し → red)。"
            :evidence "packages/doeff-agents: runtime.py CodexRuntimePolicy / shell.py assert_session_env_is_non_auth_overlay / handlers/{production,daemon}.py / handlers/{codex,claude,effectful}.hy / tests/test_agentd_client.py test_daemon_agent_handler_sends_codex_binding_from_policy・test_daemon_agent_handler_rejects_codex_home_in_session_env / tests/test_session_backend.py test_tmux_agent_handler_rejects_codex_home_in_session_env")]
       :counterexamples
         [(counterexample "session_env 経由で合成 CODEX_HOME を effect user が組んで渡す(旧形 — launch admission が typed reject する)")
          (counterexample "LaunchAgent / wire launch に authFile 引数を足し、user program がアカウント物理を知る")
          (counterexample "非 auth の per-launch env が必要になったからと session_env の所有権ガードを外す(overlay は非 auth 専用のまま、auth は binding へ)")
          (counterexample "テストと本番で同じ program が走らない(auth が effect 引数に居るため差し替え点が構成でなく呼び手コードになる)")])
     (law liveness-authority-is-the-socket
       :statement "exclusivity => socket_bind_primary AND lease_is_shadow; ensure_spawn_predicate => absence_of_live_listener_only; slow_status_never_spawns_competitor"
       :facts
         [(fact
            "R10 の起源 = 2026-07-07 ensure spawn スパイラル(live 障害): ensure_agentd が daemon.status の 1s timeout を『死』と誤診 → 同一 DB に競合 host を spawn → 子が(旧起動順 store→lease→bind のため)失効 lease を先に盗んでから socket 衝突で死亡 → 本物の heartbeat が『lease owner changed: expected 68021 got 88831』を無限連発し lease が死 pid 名義で腐る → attend launch が 11 分遅延し orphan 誤裁定。根治 3 層 = R10 (a)(b)(c)。mutation 検証済み(述語反転 / 起動順逆転 / 失効チェック除去 → 各ピン red)。"
            :evidence "packages/doeff-agents/tests/test_agentd_client.py test_ensure_agentd_never_spawns_against_live_but_slow_listener・test_ensure_agentd_waits_out_slow_status_from_live_listener・test_ensure_agentd_spawns_when_socket_file_is_stale / tests/sessionhost_host_deftests.hy test-main-loser-dies-at-bind-before-touching-store / tests/sessionhost_store_deftests.hy test-lease-acquire-and-heartbeat(失効他人名義の heartbeat 再取得 phase)")]
       :counterexamples
         [(counterexample "status probe の timeout を死と同一視して spawn する(遅い ≠ 死。busy writer actor の実測 class)")
          (counterexample "host が lease を取ってから socket を bind する(敗者が lease/latch に触れてから死ぬ)")
          (counterexample "heartbeat が owner 交代を無条件 raise し続け、失効残骸から永久に回復しない")
          (counterexample "heartbeat が未失効の他人名義 lease まで奪い返す(生きた二重 host の検出面が消える)")])
     (law conformance-before-cutover
       :statement "rust_retirement => hy_impl_passes_oracle_conformance including_trust_and_hooks_scars"
       :facts
         [(fact
            "C0-2 交代ゲート前半は達成済み: conformance suite 31/31 green on Rust oracle(全 P green・S14 は X として expected-red 記録)+ cargo test -p doeff-agentd 94 passed / 0 failed。"
            :evidence "packages/doeff-agents/conformance @ doeff 0b67cd5c(2026-07-05): pytest 31/31・cargo 94/0")
          (fact
            "C3 交代ゲート後半も達成: Hy session host が hy gate で全 green(S14 は positive 側)となり、2026-07-06 に canary 交代(同一 socket・同一 4.6GB store)。交代後に実 steward attend・24KB 合成 session・launchd 常駐化・identity probe(ACP ADR 0045)まで検証済み。"
            :evidence "doeff agentd-c1-base fa41774d(C3-1 LANDED)+ ACP docs/acp-2026-07-06-executor-cutover-closure-architecture-plan.md F1-F7")
          (fact
            "Rust 退役はユーザー GO(2026-07-06)で実行: E.2 の 1 週間無退行窓はユーザー裁定で短縮。Rust は binary/source とも保存 — ただし rollback 可用性のためだけであり、正しさの基準ではない。"
            :evidence "ACP plan U1 / 裁定台帳 8(docs/acp-2026-07-05-agentd-hy-session-host-plan.md)")
          (fact
            "同日 U1 裁定: Rust 実装の schema 検証は無裁可 subset(items/enum/additionalProperties 等を黙殺 = fail-open)で、parity 移植がこの省略を契約に洗浄していた(ACP steward 実障害で露呈)。修正 = 検証器を jsonschema(参照実装)の輸入に置換し、S20 が復元契約(items 違反の in-session reject→fix / malformed schema の launch 拒否)を凍結。教訓: 契約 enforcement 境界(結合核)の正解定義を sub-frontier 産実装の実測に接地してはならない — 仕様が存在するなら仕様が oracle。"
            :evidence "doeff#482 / conformance test_s20_schema_vocabulary.py / sessionhost/schema.hy / ACP sandbox invocation inv_wi_57cbac033483bed5_a1")]
       :counterexamples
         [(counterexample "conformance 無しで Hy 版に切り替え、solicitation/turn-end の hardening が退行する")])]
  :enforcement
    ;; C1(effect 語彙 + policy program)と同一チェンジセットで substrate-clean
    ;; を実 enforcement 化。conformance suite ゲートは C0-2 で green 済み
    ;; (conformance-before-cutover law の :facts 参照)。
    [(defsemgrep no-new-agent-physics-in-rust-agentd
       :languages ["generic"]
       :pattern "fn build_kimi_argv"
       :message "新 CLI の protocol 物理を Rust agentd に足すのは ADR-DOE-AGENTS-004 R2 違反。kind 追加は doeff-agents の defhandler モジュール + conformance。"
       :bad ["fn build_kimi_argv(params: &LaunchParams) -> Vec<String> {"]
       :good ["; doeff-agents/impls/kimi.hy に defhandler を書く"])
     (defsemgrep no-auth-material-in-launch-effect-args
       :languages ["generic"]
       :pattern "LaunchAgent :auth-file"
       :message "公開 launch effect の引数に auth 物理を置くのは ADR-DOE-AGENTS-004 R9 違反。auth 材料は handler の束縛時構成(ローカル = main、host 束縛 = control plane の binding registry)として注入し、wire では typed binding で運ぶ。session_env は非 auth overlay 専用。"
       :bad ["(LaunchAgent :auth-file \"~/.codex/auth.json\" :prompt p)"]
       :good ["(LaunchAgent :kind \"codex\" :binding \"company\" :prompt p)"])
     ;; R2 substrate-clean: impls/(per-kind defhandler 置き場)は substrate
     ;; effect(SessionStore / Tmux / Fs / Env / Clock / Proc)を yield する
     ;; のみ — 生 IO(subprocess / sqlite3 / open / os.system)を直接呼ぶことを
     ;; 禁止する。glob は C1 で `packages/doeff-agents/impls/` を先行予約し、
     ;; C2 実装時に import 可能なパッケージ内
     ;; `packages/doeff-agents/src/doeff_agents/sessionhost/impls/` へ具体化
     ;; (doeff-agents は hatchling src-layout — src 外のモジュールは wheel に
     ;; 入らず import 不能)。installed rule は .semgrep.yaml の
     ;; doeff-agents-substrate-clean-impls。
     (defsemgrep substrate-clean
       "doeff-agents-substrate-clean-impls"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/kimi.hy"
         "source" ";; per-kind impl が subprocess を直接叩く違反\n(import subprocess)\n(defn launch [argv] (subprocess.run argv))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/opencode.hy"
         "source" ";; per-kind impl が sqlite3 を直接読む違反\n(import sqlite3 [connect])\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/awskind.hy"
         "source" ";; per-kind impl がファイル IO を直接行う違反\n(defn read-home [path] (with [f (open path)] (.read f)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/geminikind.hy"
         "source" ";; per-kind impl が shell を直接叩く違反\n(defn kick [cmd] (os.system cmd))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/cleankind.hy"
         "source" ";; substrate-clean な per-kind impl: substrate effect を yield するのみ\n(defhandler cleankind-handler\n  (ClassifyPane [agent-type output]\n    (resume (classify-frame output)))\n  (DeliverMessage [pane-id text]\n    (<- _ (TmuxSendKeys :pane-id pane-id :text text :literal True :submit True))\n    (resume None)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/session_store_sub.py"
         "source" "# substrate handler 側(impls/ の外)は生 IO を持ってよい\nimport sqlite3\nimport subprocess\n"}])]
  :plans ["../agent-control-plane 側 master plan: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"])
