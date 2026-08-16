;;; Executable ADR: agent 実行 = effects+handlers、agentd = Hy session host.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-004
  :title "agent 実行は effect 語彙 + kind 別 defhandler に分解し、agentd は『寿命の外部性』だけを提供する Hy 製 session host(session を resource とするミニ control plane)へ再実装する — conformance 先行で Rust を oracle に交代"
  :status "accepted"
  :scope ["packages/doeff-agents"
          "packages/doeff-agentd(退役 Rust — rollback 座標 = git tag agentd-rust-final。in-tree 凍結コピーは持たない: issue #575 M3 で削除)"
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
     (rule R5 "host は kinds.list で {kind, apiVersion, スキーマ} を広告し、ACP は宣言時に照合して未知 kind/version を fail-loud 拒否する。wire は有限の versioned 語彙に限る(任意 effect のリモート転送は禁止)。【2026-07-08 縮小裁定(plan 裁定 9)で landed: 広告は {kind, agent_type, required_field, api_version} の有限表のみ — alias 解決や registry 転送は host に持たせない。表と広告関数(policy.hy BINDING-KIND-* 表 + binding-kind-advertisement)がスキーマの単一の家で、host.hy の kinds.list dispatch は純粋・store 非依存。『宣言時に照合』は精密化: ACP 自身の語彙への宣言時 fail-loud は ACP admission(0044 R3)が担い、host 広告とのクロスチェックは ACP daemon の level-triggered 周期照合(verifyBindingKindsOnce → BindingKindUnsupported condition)が担う — 登録と host liveness は結合せず、CLI `doeff-agents agentd kinds` は ensure/spawn しない純 read で host 不達 = 観測なし ≠ 違反。照合 law の家は ACP 0044 kind-verification-is-level-triggered。deftest: sessionhost_host_deftests.hy test-dispatch-kinds-list(表と広告の乖離を red 化)。】【同日 #15 で版が per-kind 化: api_version は BINDING-KIND-API-VERSION 表(claude-code = v1 / codex = v2)。codex v2 = 受理形の拡張({codex_home} XOR {auth_file, profile_dir}、BINDING-KIND-SHAPES)であり、required_field は人間可読ラベルへ(照合の機械面は kind+api_version のみ — shapes DSL は機械消費者不在の YAGNI 棄却)。スキーマを変えながら版を据え置くのは versioned 語彙の形骸化であり本 rule 違反。】")
     (rule R6 "デプロイは frozen 環境から(pin 済み専用 env)。dev venv / target-debug 依存の自己参照(子守りが子守られる開発環境に依存する)を禁止する。")
     (rule R7 "退役後の正典 executor は doeff-sessionhost: ensure_agentd の spawn 解決は DOEFF_AGENTD_BIN(明示 seam)→ 実行中 interpreter 隣接の console script → PATH の doeff-sessionhost で、退役 Rust binary は解決対象に含めない(silent rollback の根絶、ACP ADR 0045 R5)。rollback 可用性は git 履歴だけが担う: 退役 Rust の in-tree 凍結コピーは保持せず、削除直前 commit へ打つ tag agentd-rust-final を唯一の rollback 座標とする【2026-07-30 改訂(issue #575 M0): 旧条項『Rust binary/source の保存理由は rollback 可用性のみ』は in-tree 保存を含意し、(a) 素の pytest の退役 gate 束縛(27 件既知 red、#556)、(b) 欠陥修理の二重メンテ(#573/PR #574 — 同一欠陥を Rust/Hy 両実装へ出荷)、(c) 修理が届かず古い欠陥を抱えたままの rollback 先、を生んだため廃止】。正しさの基準として参照する禁止は不変(U1: それは一度も oracle ではなく partial-unreliable-impl だった)。移植出典表記(『oracle = main.rs:行番号』型 docstring)は tag 経由で git 解決可能な agentd-rust-final:src/main.rs:行番号 形式へ再アンカーし(issue #575 M3)、挙動契約の正本は conformance suite(README の F-* 表)に置く。")
     (rule R8 "result-contract 検証の意味論は JSON Schema 仕様が唯一の正(U1 裁定): 検証器は準拠参照実装(jsonschema)の輸入であり、subset を自前実装しない。仕様適合は公式 JSON-Schema-Test-Suite を repo 内に vendor して adapter に直接通すことで検証する(draft2020-12 required 全 1260 case green。skip 21 は全て裁定記録付き: remote レジストリ依存 = 契約は自己完結前提で unresolvable $ref は fail-loud / ECMA \\p regex = admission が launch 時 fail-closed で拒否 — 対 deftest あり)。schema 自体は launch 時に meta-schema で fail-closed 検証(壊れた契約で session を作らない)。旧 Rust 実装の fail-open 挙動を expected に固定するテストは歴史ピンとしても置かない。")
     (rule R9 "公開 launch 面(effect 語彙と wire launch の両方)は auth-blind: auth/profile 物理(CODEX_HOME / CLAUDE_CONFIG_DIR / 生鍵)は typed `binding`(束縛時構成の serialize、kind 判別スキーマ: codex {codex_home} / claude-code {config_dir})でのみ運ぶ。session_env は非 auth overlay に縮む — binding 所有キーの混入は全副作用より前に typed reject(所有権ベース: 既知の悪いキーの列挙は腐るが所有権は腐らない。provider API キーの FORBIDDEN blocklist は substrate 境界の防御として併存)。非 auth の per-launch env(観測フラグ・result channel 配線値など)は overlay として正当であり、handler 構成へ押し込まない(2026-07-07 裁定: env には auth と run 意図の 2 住人が居て、家が違う)。kind 別 auth 材料スキーマは handler の束縛時構成 — ローカル束縛 = main、host 束縛 = binding registry を持つ control plane(ACP 0044 R2/R5 と同じ線)。binding admission は ADR 0044 R3 と同思想: 未知 kind / kind↔agent_type 不整合 / 必須 field 欠落 / 未知 field を typed reject。")
     (rule R10 "単一インスタンス排他の実体は socket bind であり、lease はその影(観測面)。この主従を順序と述語で守る: (a) host 起動順は bind → store open → lease 取得 → latch clear — bind に負けた競合者は store にも lease にも触れずに死ぬ。(b) ensure の spawn 述語は『socket に live listener が居ない』(path 不在 / ECONNREFUSED)のみ — 生きた listener が status probe に遅い場合は長い予算(AGENTD_BUSY_STATUS_TIMEOUT_SECONDS)で再試行し、それでも駄目なら spawn せず loud エラー(slow ≠ dead。証明されない死で競合 host を作らない)。(c) heartbeat は失効した他人名義 lease を再取得する(level-triggered 自己修復 — 盗んで死んだ競合者の残骸から bind 保持者が回復する)が、未失効の他人名義は loud エラーのまま(別 socket 同一 DB 誤構成 = 生きた二重 host の検出面)。判定と upsert は BEGIN IMMEDIATE で原子化。(d) 単一 supervisor 原則(2026-07-26、doeff#558 / ACP ADR 0024 改訂と一括): canonical socket が state dir の agentd.supervisor.json で supervisor 管理下と宣言されている場合、(b) の証明された死でも ensure は self-spawn しない — 起動・再起動の所有者は supervisor(launchd 等)であり、ensure は宣言された kick_command(構成注入 argv — doeff は launchd も label もハードコードしない)への委譲 + readiness 待ちのみを行い、kick_command 不在なら loud fail する。壊れた宣言は fail-closed(typed AgentdSupervisorConfigError)で self-spawn への silent fallback を持たない。宣言は呼び手 env ではなくマシン状態(state dir のファイル)に置く — env を継承しない任意の呼び手が supervisor 停止窓(bootout・crash throttle)に野良 host を bind してしまう穴を env 宣言は塞げない。")
     (rule R11 "store-of-record は wall-clock 稼働時間に比例して成長しない(2026-07-26/27 socket 応答不能 wedge の根治、oracle からの意図的乖離): (a) status 導出系の監査 event は status 遷移(cycle 開始時の行 status ≠ 導出 status)でのみ記録 — 静止 tick は upsert のみで journal に追記しない(oracle は毎 tick 記録: main.rs:4128)。(b) 監査履歴(agent_session_events / agent_session_commands — runtime に読者なし)は retention 境界(DOEFF_AGENTD_HISTORY_RETENTION_DAYS、既定 14 日)で bounded: 起動時 + 毎時に batched prune(batch 毎に別 actor op — client op が割り込める)、物理回収(VACUUM)は freelist が支配的なときの起動時(accept 開始前)のみで serve 中は禁止。(c) hot path の一覧(毎 tick の ListActive)は status filter を SQL 側(idx_agent_sessions_status)で適用し、terminal 履歴行を読まない。実測根拠: 実 incident DB 1.54GB(session_blocked 251k 行 / 1.16GB = 95%)で無 filter 一覧 165ms/回 → filter 化 0.7ms、単一 StoreActor の FIFO queue 遅延が client timeout(probe 1s)を超え agent 起動が全滅した。")
     (rule R12 "herdr substrate(第二 substrate)の同一性と protocol 追随(2026-08-17 改訂・herdr 0.7.5 / protocol 17 実測): (a) doeff 所有 session の同一性アンカーは workspace label(doeff が workspace.create {label, cwd, env} で所有 — protocol 17 は cwd/env を直接受け root pane がそのまま session pane)。生死 = label holder 集合の非空、帰属 = 全 holder の pane 和、kill = 全 holder の閉鎖、重複拒否 = 事前照会 + 同時作成の合意(herdr-new-session-verdict)。holder 集合は順序を持たない(workspace_id は創出順に非単調 — 実測 w3NZ → w3N0)。(b) herdr の agent.start は protocol 17 で {name, kind, pane_id} 必須の『既存 pane で herdr 自身が claude/codex 等を起動して検出を待つ』API に改形された — kind の語彙は herdr が起動する agent 種別で、TmuxNewSession(名前付き shell pane を作り、起動コマンドは doeff が後から送る)には意味論が合わない。よって『kind を送る』のではなく agent.start を呼ばない形が正しい追随(旧 payload + kind でも missing field pane_id — 実測)。(c) herdr の agent 名簿は session 同一性を担えない: pane 内で実 agent が起動すると ~2 秒で herdr の検出が外部の付けた名札を消す(実測 n=4)。名簿を読んでよいのは**外部命名席の実在確認**のただ 1 点 — `ai` 等が名簿に付けた席名(koine session.adopt の session_name)は doeff 所有 workspace を持たず label は repo 名(実測: 自席 = agent 名 s-7bfc5d5028 / label 'doeff')ため、label holder が無い名前に限り agent.get {target: name}(応答の name 完全一致)で has-session / session-pane-ids を答える(herdr-external-agent-pane-id-io — semgrep waiver marker 直下の 1 呼び出し)。kill・重複拒否・doeff 所有の同一性には決して使わず、外部命名席は名前で閉じない(doeff が作っていない席は観測のみ)。実測記録の正本は conformance/herdr-physics.md。")]
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
     (law store-growth-is-bounded-by-recent-activity
       :statement "store_size => f(activity_within_retention_window) not f(wall_clock_uptime); quiescent_tick => upsert_only_never_journal_append; hot_path_list => sql_side_status_filter"
       :facts
         [(fact
            "R11 実装 landed(2026-07-27): finalize の event 記録を遷移 guard 化(policy.hy entry-status)、events/commands の retention prune(store.hy db-prune-history / actor-prune-history、時刻列 index)+ 起動時条件付き VACUUM(db-vacuum-if-bloated)、db-session-list の status SQL 押し下げ。deftest: sessionhost_policy_deftests test-blocked-quiescent-tick-records-no-event / test-blocked-transition-records-single-event-across-cycles / test-running-quiescent-tick-records-no-observed-event、sessionhost_store_deftests test-session-list-status-filter-applied-in-sql / test-prune-history-retention-and-batching / test-actor-prune-history-converges / test-vacuum-if-bloated-threshold。"
            :evidence "2026-07-27 実 incident: agentd.sqlite 1.54GB(session_blocked 251k 行 / 1.16GB)・process sample = 全 handle_stream thread がロック待ち・store thread が外部ソート spill 中(~/.local/state/doeff/sessionhost-wedge-sample-20260727.txt)")]
       :counterexamples
         [(counterexample "blocked のまま静止する行へ毎 tick full-snapshot event を INSERT し続ける(2026-07-27 実 incident の機構)")
          (counterexample "ListActive が terminal 含む全履歴行を full scan + 外部ソート + 全行 JSON decode してから Python 側で filter する")
          (counterexample "serve 中の actor op で VACUUM を実行し、client probe(1s)を数秒飢えさせる")])
     (law effect-user-is-auth-blind
       :statement "public_launch_surface_args => intent_plus_nonauth_overlay_only; auth_material_rides_typed_binding_owned_by_handler_binder"
       :facts
         [(fact
            "R9 実装 landed(2026-07-07): sessionhost の launch admission が binding 検査 + overlay 所有キー拒否を全副作用より前に実施し、tmux env = overlay ∪ binding 由来 auth env(合成の唯一の源は per-kind impl の identity — trust 書き先と agent の読みが構造的に一致)。ACP engine は bindingWireValue を typed field で送り、launch env の auth 直詰めを退役。live 検証: auth-in-env は loud reject / binding 由来 CLAUDE_CONFIG_DIR と非 auth overlay が tmux session env に並んで届く。"
            :evidence "doeff agentd-retire-rust 0ce87c84(sessionhost/policy.hy BINDING-OWNED-ENV-KEYS + binding-admission-error / launch.hy R7 admission / sessionhost_launch_deftests.hy test-launch-allows-non-auth-overlay-env・test-launch-rejects-auth-in-session-env)+ doeff-agent-haskell 789e3a8 + ACP 7b7cac6")
          (fact
            "R9 のローカル束縛(in-process)面も完結(2026-07-08): auth の家 = 束縛時構成として CodexRuntimePolicy(codex_home)を新設(ClaudeRuntimePolicy の codex 対)し、TmuxAgentHandler / DaemonAgentHandler / 全 factory / tmux-agent-defhandler / codex-handler(Hy、`codex-home` param)が constructor 注入を受ける。全ローカル launch 入口(production / daemon / session.py / claude.hy / codex.hy)に overlay ガード assert_session_env_is_non_auth_overlay を敷設 — 所有集合は sessionhost/policy.hy の overlay-env-offenders を import する単一実装で、ローカル guard と host admission は drift できない。DaemonAgentHandler は codex auth を typed wire binding(policy > binder process env)として転送し session_env 直詰めを退役 — R9 host admission に対して live で壊れていた経路(conductor の codex 起動が踏む地雷)の修理。daemon 経路の局所 codex trust 書きも退役(pre-launch 物理は host per-kind impl の単一所有)。mutation 検証済(_wire_binding 殺し → red)。"
            :evidence "packages/doeff-agents: runtime.py CodexRuntimePolicy / shell.py assert_session_env_is_non_auth_overlay / handlers/{production,daemon}.py / handlers/{codex,claude,effectful}.hy / tests/test_agentd_client.py test_daemon_agent_handler_sends_codex_binding_from_policy・test_daemon_agent_handler_rejects_codex_home_in_session_env / tests/test_session_backend.py test_tmux_agent_handler_rejects_codex_home_in_session_env")
          (fact
            "codex binding の受理形は #15(2026-07-08、v2)で二形の XOR に拡張: {codex_home}(native home — daemon ローカル束縛 CodexRuntimePolicy・conformance harness・CODEX_HOME= escape hatch の恒久住人)か {auth_file, profile_dir}(control plane の二軸宣言 — host の per-kind impl が FsComposeHomeView で $XDG_STATE_HOME/doeff/agent-homes/ に view を合成して native 形へ合流)。混在・部分・未知 field はどの shape にも一致せず typed reject(BINDING-KIND-SHAPES への完全一致)。binding が在れば process env fallback には決して到達しない(deftest で decoy pin)。宣言パスの実在検証は合成の冒頭で typed fail — ACP 側 ensure-agent-home(登録時検証)の退役の受け皿(ACP 0040 R2 改訂)。"
            :evidence "packages/doeff-agents: sessionhost/policy.hy BINDING-KIND-SHAPES/binding-admission-error / sessionhost/impls/codex.hy codex-view-root・codex-pre-launch / sessionhost/effects.hy FsComposeHomeView / sessionhost/substrate.hy compose-home-view / tests/sessionhost_launch_deftests.hy test-launch-composes-view-for-two-axis-codex-binding・test-launch-rejects-malformed-binding(XOR mutation → red)")]
       :counterexamples
         [(counterexample "session_env 経由で合成 CODEX_HOME を effect user が組んで渡す(旧形 — launch admission が typed reject する)")
          (counterexample "LaunchAgent / wire launch に authFile 引数を足し、user program がアカウント物理を知る")
          (counterexample "非 auth の per-launch env が必要になったからと session_env の所有権ガードを外す(overlay は非 auth 専用のまま、auth は binding へ)")
          (counterexample "テストと本番で同じ program が走らない(auth が effect 引数に居るため差し替え点が構成でなく呼び手コードになる)")])
     (law liveness-authority-is-the-socket
       :statement "exclusivity => socket_bind_primary AND lease_is_shadow; ensure_spawn_predicate => absence_of_live_listener_only AND socket_not_supervisor_declared; slow_status_never_spawns_competitor; supervised_socket => delegate_to_kick_command_or_loud_fail_never_self_spawn"
       :facts
         [(fact
            "R10 の起源 = 2026-07-07 ensure spawn スパイラル(live 障害): ensure_agentd が daemon.status の 1s timeout を『死』と誤診 → 同一 DB に競合 host を spawn → 子が(旧起動順 store→lease→bind のため)失効 lease を先に盗んでから socket 衝突で死亡 → 本物の heartbeat が『lease owner changed: expected 68021 got 88831』を無限連発し lease が死 pid 名義で腐る → attend launch が 11 分遅延し orphan 誤裁定。根治 3 層 = R10 (a)(b)(c)。mutation 検証済み(述語反転 / 起動順逆転 / 失効チェック除去 → 各ピン red)。"
            :evidence "packages/doeff-agents/tests/test_agentd_client.py test_ensure_agentd_never_spawns_against_live_but_slow_listener・test_ensure_agentd_waits_out_slow_status_from_live_listener・test_ensure_agentd_spawns_when_socket_file_is_stale / tests/sessionhost_host_deftests.hy test-main-loser-dies-at-bind-before-touching-store / tests/sessionhost_store_deftests.hy test-lease-acquire-and-heartbeat(失効他人名義の heartbeat 再取得 phase)")
          (fact
            "R10(d) の起源 = doeff#558(2026-07-26): live deploy は sessionhost を launchd(KeepAlive=true, ThrottleInterval=5)管理下に置いたが、ensure の (b) 述語は『listener 不在の証明』だけで self-spawn を正当化したままで、同一 socket/store に監督者が 2 人いた。launchd 停止窓(bootout メンテ・crash 後 throttle・plist 差し替え)に ensure が走ると launchd 非管理の野良 host が bind を先取りし、launchd 側 restart は bind-first で敗死を繰り返し(KeepAlive churn)、store は launchctl から不可視のプロセスに保持されて次のメンテ操作が split-brain する(2026-07-23 migration split-brain incident と同型)。根治 = supervision 所有の宣言(agentd.supervisor.json)+ ensure の委譲/loud-fail(self-spawn 禁止)+ .semgrep.yaml doeff-agents-agentd-spawn-only-via-ensure-boundary(spawn 経路の単一所有)。ACP 側 ADR 0024 の同時改訂と一括出荷。"
            :evidence "packages/doeff-agents/tests/test_agentd_client.py test_ensure_agentd_supervised_socket_refuses_self_spawn_without_kick・test_ensure_agentd_supervised_socket_delegates_to_kick_command・test_ensure_agentd_supervised_kick_failure_is_loud・test_ensure_agentd_supervised_kick_then_still_dead_is_loud・test_ensure_agentd_supervisor_declaration_for_other_socket_is_inert・test_ensure_agentd_supervised_ready_daemon_needs_no_kick・test_ensure_agentd_malformed_supervisor_declaration_is_loud")]
       :counterexamples
         [(counterexample "status probe の timeout を死と同一視して spawn する(遅い ≠ 死。busy writer actor の実測 class)")
          (counterexample "host が lease を取ってから socket を bind する(敗者が lease/latch に触れてから死ぬ)")
          (counterexample "heartbeat が owner 交代を無条件 raise し続け、失効残骸から永久に回復しない")
          (counterexample "heartbeat が未失効の他人名義 lease まで奪い返す(生きた二重 host の検出面が消える)")
          (counterexample "launchd(KeepAlive)配備の socket に対し、bootout / throttle の停止窓で ensure が listener 不在を証明して非監督 host を直 spawn する(doeff#558 — 監督者 2 人。野良 host が bind に勝つと store が launchctl 不可視のプロセスに渡り split-brain)")
          (counterexample "supervisor 宣言を env var で運び、env を継承しない呼び手の self-spawn 経路が開いたままになる")
          (counterexample "壊れた宣言ファイルを警告だけで無視して self-spawn に fallback する(fail-open — typo が委譲を黙って無効化する)")])
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
            "上記『Rust は binary/source とも保存』は 2026-07-30(issue #575 M0)に改訂: 保存の家は in-tree から git 履歴(削除直前 commit への tag agentd-rust-final)へ移った。現行法は R7 改訂と retired-impl-lives-only-in-git-history law が正本。"
            :evidence "doeff issue #575 / 本 ADR R7 改訂(2026-07-30)")
          (fact
            "同日 U1 裁定: Rust 実装の schema 検証は無裁可 subset(items/enum/additionalProperties 等を黙殺 = fail-open)で、parity 移植がこの省略を契約に洗浄していた(ACP steward 実障害で露呈)。修正 = 検証器を jsonschema(参照実装)の輸入に置換し、S20 が復元契約(items 違反の in-session reject→fix / malformed schema の launch 拒否)を凍結。教訓: 契約 enforcement 境界(結合核)の正解定義を sub-frontier 産実装の実測に接地してはならない — 仕様が存在するなら仕様が oracle。"
            :evidence "doeff#482 / conformance test_s20_schema_vocabulary.py / sessionhost/schema.hy / ACP sandbox invocation inv_wi_57cbac033483bed5_a1")]
       :counterexamples
         [(counterexample "conformance 無しで Hy 版に切り替え、solicitation/turn-end の hardening が退行する")])
     (law retired-impl-lives-only-in-git-history
       :statement "retired_implementation => git_history_only(rollback_tag agentd-rust-final); in_tree_frozen_copy_forbidden AND new_code_reference_forbidden(packages/doeff-agentd, doeff_agentd); porting_provenance => tag_qualified_form; behavior_contract_authority => conformance_suite_F_table"
       :facts
         [(fact
            "退役 GO(2026-07-06)後も crate を in-tree 凍結保存した実測コスト: (a) 素の pytest が退役 Rust gate に束縛され 27 件既知 red(#556)、(b) 同一欠陥の二重メンテ — #573 の claude spinner 誤検知修理は Rust/Hy 両実装への二重出荷になった(PR #574)、(c) rollback 先が修理の届かない古い欠陥を抱え続ける地雷。2026-07-30 改訂(issue #575 M0)で in-tree 保存条項を廃し、rollback 座標を削除直前 commit への git tag agentd-rust-final に一本化。契約語彙(agentd_client・agentd.sqlite・socket 名・CONFORMANCE_AGENTD_BIN・wire メソッド名)の改名は issue #575 の射程外で据え置き — 禁止対象は crate path packages/doeff-agentd と module 名 doeff_agentd の 2 形のみ。"
            :evidence "doeff issue #575(初期棚卸し 2026-07-30)/ #556 / #573 / PR #574")]
       :counterexamples
         [(counterexample "退役実装の凍結コピーを in-tree に保持し、現役 sessionhost と退役 Rust の両方へ同一欠陥修理を出荷し続ける(#573/PR #574 の二重メンテ)")
          (counterexample "conformance / 素の pytest の既定経路が退役実装のビルド(cargo)に束縛され、既知 red を常態化させる(#556)")
          (counterexample "新規コードが packages/doeff-agentd / doeff_agentd を参照して退役実装への依存を復活させる")
          (counterexample "rollback tag を打たずに crate を削除し、rollback 可用性を口約束にする")])
     (law capacity-counts-only-launch-owned-rows
       :statement "launch_admission_denominator => active_and_not_adopted_rows_only; adopted_rows_are_observations_not_capacity_consumers; ownership_predicate_not_lifecycle_predicate; cap_breach_remedy_is_never_reaping_exempt_rows_nor_raising_the_cap"
       :facts
         [(fact
            "起点実測(2026-08-17・波 1-S1 席の read-only 診断): 対話席用 SessionHost 台帳(agentd-herdr.sqlite)は active 全 32 行が adopted=1・lifecycle=interactive(登録 = 07-21 の手動一発)で、launch の入場検査(launch.hy の max_running 判定)が active 状態の全行を数えるため 32 ≥ 上限 10 で session.launch が恒久 100% 拒否。根因 = adopted/interactive の行には終端遷移の書き手が居らず(ADR-007 interactive-rows-are-never-reaped が正しく禁止)active 数は単調増加する一方、容量ガードは観測として登記した行を容量消費として数えていた — 母数の型違い。上限値をいくら上げても再発する。根治 = 母数の述語を『launch 所有の行 = active ∧ 非 adopted』へ(policy.hy counts-toward-launch-capacity — reap-exempt と同じ policy 所有の純述語)。lifecycle では絞らない: launch 起点の行は interactive でも substrate を実際に消費する。波 1-S1(adopt の会話別 3 分岐 — 下地再利用で新行が増える)・波 1-S2(登録の定期化 — adopt 行が約 90 枠へ)の両便とも adopted 行を増やす向きで、本 law はその前提(観測の登記は容量に混ざらない)を固定する。"
            :evidence "波 1-S1 席の実測(会話 ac0301fa-228a-41ad-ae89-2122aec051f8・2026-08-17)/ agentd-herdr.sqlite 32 行断面 / packages/doeff-agents/tests/sessionhost_launch_deftests.hy test-launch-capacity-ignores-adopted-rows・test-launch-capacity-counts-launch-owned-rows")]
       :counterexamples
         [(counterexample "入場検査が active 状態の全行(adopted 含む)を数え、観測の登記が launch の容量を食い潰す(2026-08-17 実測: 32 ≥ 10 で session.launch 恒久 100% 拒否)")
          (counterexample "母数はそのままに max_running の値だけ引き上げて対症する(adopted 行は単調増加 — 必ず再発する)")
          (counterexample "容量を空けるために interactive / adopted 行の刈り取りを再導入する(ADR-007 interactive-rows-are-never-reaped 違反)")
          (counterexample "母数を lifecycle(run_to_completion のみ)で絞る(launch 起点の interactive 行は substrate を実際に消費する — 絞るのは所有であって寿命ではない)")
          (counterexample "拒否文言から先頭逐語 'max running agent sessions reached' を落とす(ACP Scheduler の infix 照合が throttle 分類 — 席の解放と再試行 — に消費している凍結面)")])
     (law herdr-session-identity-is-workspace-label
       :statement "herdr_session_identity(doeff_owned) => workspace_label_holders_as_a_set; agent_start_protocol_17 => start_managed_agent_in_existing_pane not_a_named_shell_pane_factory => never_called_by_TmuxNewSession(no_kind_value_is_right); agent_name_registry => existence_probe_only(has_session_disjunct, session_pane_ids_when_no_label_holder) never_identity_never_kill_never_duplicate_gate; externally_named_seats => observed_never_closed_by_name"
       :facts
         [(fact
            "protocol 17(herdr 0.7.5)の agent.start は {name, kind, pane_id} 必須の『既存 pane で herdr 自身が agent を起動して検出を待つ』API(bundled schema $defs.AgentStartParams・稼働 server 実射 2026-07-29 / 08-17)。現 main(protocol 14 形 {name,cwd,argv,env,workspace_id,focus})は missing field `kind`、kind を足しても missing field `pane_id`。kind の語彙は herdr が起動する agent 種別(claude/codex/…)で TmuxNewSession の意味論(名前付き shell pane 生成・起動コマンドは doeff が後送)に合わない。追随 = workspace.create {label, cwd, env}(cwd/env 直受け・root pane = session pane、実射)。"
            :evidence "conformance/herdr-physics.md 追補(2026-07-29 / 08-14 / 08-17)/ 実 DB 回復確認 2026-08-16 20:19 の launch 拒否文言 `missing field kind at line 1 column 226` / socket 実射 2026-08-17(実在しない名前・無副作用): A 旧 payload → kind 欠落・B +kind → pane_id 欠落・C p17 形 + 実在しない pane → agent_pane_not_found")
          (fact
            "外部が付けた agent 名札は pane 内で実 agent が起動すると ~2 秒で herdr の検出に消される(通算 n=4)。workspace label は残る。herdr は label の一意性を強制しない(同 label の 2 つ目も成功)ため重複拒否は doeff 側。workspace_id は創出順に非単調(w3NZ → w3N0)。"
            :evidence "PR #569 review 2026-08-14 実測(n=4)/ PR #587 改訂 2(w3NZ → w3N0・w3ZZ → w303)/ deftest test-herdr-identity-survives-agent-name-loss・test-herdr-new-session-verdict-ignores-id-order")
          (fact
            "`ai` 等が herdr の名簿に付けた席名(koine session.adopt の session_name — dotfiles koine_adopt.py が `herdr agent list` の name をそのまま運ぶ)は doeff 所有の workspace を持たず、workspace label は repo 名(自席 w4XN:p9 = agent 名 s-7bfc5d5028 / label 'doeff'。対話席用 host の台帳は 134 行すべて adopted・session_name = 名簿名)。label だけの has-session は adopt を全拒否(adopt_target_not_found)し既存 adopted 行の substrate_present を一斉 false に倒す(PR #587 初版の未検出回帰 — conformance の adopt fixture は label = 名前の doeff 所有形しか作らなかった)。名簿の agent.get は AgentTarget として pane_id も受けるため応答の name 完全一致で絞る。"
            :evidence "2026-08-17 実測(herdr agent list / pane list / workspace list の突合・agent.get {target: 'w4XN:p9'} 解決)/ deftest test-herdr-external-seat-visible-to-has-session-but-not-killable・test-herdr-registry-agent-pane-id-requires-exact-name / conformance S29")]
       :counterexamples
         [(counterexample "agent.start に kind を 1 field 足して protocol 17 へ『追随』する(missing field pane_id で拒否され、pane_id を足せば herdr が勝手に agent を起動する — TmuxNewSession の意味論違反)")
          (counterexample "pane.report_agent → agent.rename の名札を session 同一性にする(実 agent 起動 ~2 秒で消える — 生死・帰属・kill が全滅)")
          (counterexample "has-session を label holder だけで答える(外部命名席が全部『不在』— adopt 全拒否・substrate_present 一斉 false)")
          (counterexample "外部命名席を名前で kill / 重複 gate の根拠にする(doeff が作っていない席を閉じる・agent.get を waiver marker 外で呼ぶ)")
          (counterexample "label holder 集合を添字で選ぶ・id 順で先行を決める(workspace_id は創出順に非単調)")])]
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
         "source" "# substrate handler 側(impls/ の外)は生 IO を持ってよい\nimport sqlite3\nimport subprocess\n"}])
     ;; R7 改訂(2026-07-30、issue #575 M0): 退役 crate への参照の逆流防止。
     ;; 禁止は crate path packages/doeff-agentd と module 名 doeff_agentd の
     ;; 2 形のみ — 契約語彙(agentd_client / DOEFF_AGENTD_BIN / agentd.sqlite /
     ;; doeff-agentd-<user>.sock 等)は issue #575 の射程外で無罪。散文(.md)と
     ;; ADR 法文書(defadr_*.hy)は歴史記述として対象外。installed rule 側の
     ;; paths.exclude には M1(conformance harness)/ M2(semgrep fixtures)/
     ;; M3(移植出典 docstring 再アンカー)で縮小・撤去する移行 allowlist を持つ。
     (defsemgrep retired-crate-reference
       "doeff-agentd-retired-crate-reference-forbidden"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_revival.py"
         "source" "# 退役 crate を cargo build する逆流\nimport subprocess\nsubprocess.run(['cargo', 'build'], cwd='packages/doeff-agentd')\n"}
        {"relative-path" "packages/doeff-agents/tests/test_retired_module_import.py"
         "source" "from doeff_agentd import serve\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/contract_vocab_ok.py"
         "source" "# agentd 契約語彙は退役対象外(issue #575 射程限定)\nfrom doeff_agents.agentd_client import ensure_agentd\nSOCKET_SUFFIX = 'doeff-agentd-user.sock'\nBIN_ENV = 'DOEFF_AGENTD_BIN'\nDB_NAME = 'agentd.sqlite'\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/provenance_ok.py"
         "source" "# 移植出典: agentd-rust-final:src/main.rs:2775(rollback 専用・正しさの基準ではない)\n"}
        {"relative-path" "docs/history-note.md"
         "source" "Rust 参照実装は packages/doeff-agentd に住んでいた(退役済み・tag agentd-rust-final)。\n"}])
     (deftest test-adr-doe-agents-004-capacity-denominator-is-launch-owned
       ;; capacity-counts-only-launch-owned-rows の機械面: 母数の述語は
       ;; 所有(adopted)で絞り、寿命(lifecycle)では絞らない。
       ;; adopt = 観測 — lifecycle を問わず容量に数えない。
       ;; launch 所有 — interactive でも substrate を消費するので数える。
       (import doeff_agents.sessionhost.effects [SessionRow])
       (import doeff_agents.sessionhost.policy [counts-toward-launch-capacity])
       (defn mk [lifecycle adopted]
         (SessionRow :session-id "adr4cap" :session-name "adr4cap" :pane-id "%0"
                     :agent-type "claude" :lifecycle lifecycle :status "running"
                     :started-at "2026-08-17T00:00:00+00:00" :adopted adopted))
       (assert (is (counts-toward-launch-capacity (mk "interactive" True)) False))
       (assert (is (counts-toward-launch-capacity (mk "run_to_completion" True)) False))
       (assert (is (counts-toward-launch-capacity (mk "interactive" False)) True))
       (assert (is (counts-toward-launch-capacity (mk "run_to_completion" False)) True)))
     ;; R12 / law herdr-session-identity-is-workspace-label: installed rule
     ;; doeff-agents-herdr-session-identity-not-agent-name(.semgrep.yaml)の
     ;; hit / clean 断面を ADR 側でも pin する。hit = 名簿で同一性・kill を
     ;; 解決する形(PR #569 の旧形)/ waiver marker の無い agent.get。clean =
     ;; label 解決 + waiver marker 直下の唯一の実在確認 probe。
     (defsemgrep herdr-session-identity-not-agent-name
       "doeff-agents-herdr-session-identity-not-agent-name"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr_name_identity_bad.hy"
         "source" ";; 名札(agent 名簿)で session 同一性・kill を解決する旧形(PR #569)\n(deff herdr-agent-pane-id-io [socket-path session-name]\n  (get (get (herdr-call socket-path \"agent.get\" {\"target\" session-name}) \"agent\") \"pane_id\"))\n(deff herdr-kill-session-io [socket-path session-name]\n  (herdr-call socket-path \"pane.close\" {\"pane_id\" (herdr-agent-pane-id-io socket-path session-name)}))\n"}
        {"relative-path" "packages/doeff-agents/conformance/harness_name_identity_bad.py"
         "source" "def session_exists_out_of_band(session_id: str) -> bool:\n    return 'error' not in _herdr_call(\"agent.get\", {\"target\": session_id})\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr_label_ok.hy"
         "source" ";; 同一性は workspace label(集合)— kill は全 holder の閉鎖\n(deff herdr-kill-session-io [socket-path session-name]\n  (for [ws-id (herdr-label-workspace-ids-io socket-path session-name)]\n    (herdr-call socket-path \"workspace.close\" {\"workspace_id\" ws-id})))\n;; 唯一の実在確認 probe(waiver marker 直下)\n(deff herdr-external-agent-pane-id-io [socket-path name]\n  (try\n    ;; registry-existence-probe: 外部命名席の実在確認だけに許す(同一性・帰属・kill は label)\n    (setv result (herdr-call socket-path \"agent.get\" {\"target\" name}))\n    (except [e HerdrApiError]\n      (when (= e.code \"agent_not_found\") (return None))\n      (raise)))\n  (herdr-registry-agent-pane-id result name))\n"}])
     (deftest test-adr-doe-agents-004-herdr-identity-pure-rules
       ;; law herdr-session-identity-is-workspace-label の純関数面(実 herdr
       ;; 不要): (a) 名簿の実在確認は応答 name の完全一致だけを採る(pane_id
       ;; target を「その名前の席」と誤認しない)。(b) 重複判定は id の順序に
       ;; 依存しない — 事前照会に holder が居れば順序に関わらず duplicate、
       ;; 作成後に自分が居なければ vanished。(c) label holder は集合(listing 順)。
       (import doeff_agents.sessionhost.substrate_herdr [
         herdr-registry-agent-pane-id herdr-new-session-verdict herdr-label-holders])
       (setv reply {"agent" {"name" "s-7bfc5d5028" "pane_id" "w4XN:p9"}})
       (assert (= (herdr-registry-agent-pane-id reply "s-7bfc5d5028") "w4XN:p9"))
       (assert (is (herdr-registry-agent-pane-id reply "w4XN:p9") None))
       (assert (is (herdr-registry-agent-pane-id reply "coupling-core-review") None))
       ;; 先行 w3NZ・後発 w3N0(shortlex で後発が先) — どちらの並びでも duplicate。
       (assert (= (herdr-new-session-verdict "w3N0" ["w3NZ"] ["w3NZ" "w3N0"]) "duplicate"))
       (assert (= (herdr-new-session-verdict "w3N0" ["w3NZ"] ["w3N0" "w3NZ"]) "duplicate"))
       (assert (= (herdr-new-session-verdict "w3N0" [] []) "vanished"))
       (assert (= (herdr-new-session-verdict "w3N0" [] ["w3N0"]) "ok"))
       (assert (= (herdr-label-holders
                    {"workspaces" [{"workspace_id" "w1" "label" "x"}
                                   {"workspace_id" "w2" "label" "y"}
                                   {"workspace_id" "w3" "label" "x"}]}
                    "x")
                  ["w1" "w3"])))]
  :plans ["../agent-control-plane 側 master plan: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"
          "doeff issue #575 — agentd 退役マイルストーン(M0 法改訂+逆流防止 / M1 conformance 既定反転 / M2 テスト再束縛 / M3 crate 削除+rollback tag / M4 全量検証)"])
