;;; Executable ADR: session の耐久エンティティは会話(conversation)である —
;;; conversation identity の stored-fact 化と resume / fork の一級語彙化。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-006
  :title "session の耐久エンティティは会話(agent-native transcript)である — conversation identity を session 行の stored fact に昇格し、resume / fork を effect 語彙・RPC・conformance の一級市民として追加する"
  :status "accepted"
  :scope ["packages/doeff-agents (sessionhost: store / effects / launch / host / policy / impls)"
          "packages/doeff-agents/conformance"]
  :problem
    [(fact
       "agent_sessions 行に agent ネイティブの会話 identity が存在しない。列は session_id(host 自前 ID)/ session_name / pane_id / agent_type / work_dir / status / backend_kind / backend_ref_json(mux 参照)/ 時刻群 / pr_url / output_snippet / terminal_cause_json のみで、claude の session UUID も codex の rollout も保存されない。したがって host は会話を蘇生する術を構造的に持たず、session kill = 会話の喪失が仕様になっている。"
       :evidence "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy:62-78(CREATE TABLE agent_sessions)")
     (fact
       "effect 語彙にも wire 語彙にも resume / fork が無い。kind 面は BuildLaunch / PreLaunchSetup / ClassifyPane / DeliverMessage / WireResultChannel の 5 語彙、RPC は session.launch / get / list / capture / send / cancel / cleanup / await_result / report_result のみで、lifecycle は launch → terminal → cleanup の一方通行。"
       :evidence "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy:172-215; host.hy の dispatch(daemon.status / kinds.list / session.*)")
     (fact
       "CLI ネイティブ物理は両 kind とも resume と fork を一級提供済みである: claude は --session-id <uuid>(起動前に identity を指定可能)/ --resume <id> / --fork-session(resume 時に新 session ID を鋳造)、codex は codex resume <SESSION_ID> / codex fork をサブコマンドとして持つ(2026-07-13 に --help 実測)。抽象の欠落は sessionhost 側だけにある。"
       :evidence "claude --help / codex --help / codex resume --help(2026-07-13 実測)")
     (fact
       "消費者側での実証が既にある: orch の ADR-0005(2026-07-13)は『mux session = 使い捨てキャッシュ、耐久状態 = agent transcript、蘇生可能性は stored fact』を法制化し(LS2 / LS5)、identity 捕獲の物理(claude = UUID 鋳造 + --session-id / codex = boot 後の rollout cwd-match 発見)を実装・検証済み(orch PR #532)。しかし identity の家が orch の event log にあり、session を所有する sessionhost に無いため、所有権が泣き別れている — ACP や conductor など他の control plane は同じ捕獲物理を各自再実装するしかない(protocol 物理の二重実装 = ADR-DOE-AGENTS-004 が Rust/Python 二重実装の腐敗として検死した構造の再演)。"
       :evidence "orch repo docs/adr/defadr_0005_run_session_lifecycle.hy; orch PR #532")
     (fact
       "【2026-08-11 改訂の駆動問題】ACP(agent-control-plane)の枠切れ failover は operator 裁定(決裁 decision-acp-ratelimit-attempt-loss-2026-08-11・案 A 採択)で『別アカウントでの同一会話 resume』を要求するが、session.resume は蘇生元行の effective_identity から binding を再構成する一択で、呼び手が別 auth home を指定する口が無い。また transcript の家(claude projects jsonl / codex rollout)は auth home 単位なので、home を替えるだけでは実 CLI が transcript を発見できない(claude は『No conversation found with session ID』rc=1、codex は『no rollout found for thread id』rc=1 — resume-physics.md 2026-08-11 プローブ (a)(c))。さらに resume の admission 失敗は素の RuntimeError → {\"ok\":false,\"error\":\"<msg>\"} で error_code が無く、機械消費者(ACP Haskell client)が message substring 照合に依存する形になっていた。"
       :evidence "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy(resume-session の binding 再構成); packages/doeff-agents/conformance/resume-physics.md(cross-binding 追加プローブ)")]
  :context
    [(interpretation
       "存在論(ユーザー裁定 2026-07-13): 耐久エンティティは会話(agent-native transcript: claude の projects jsonl / codex の rollout)であり、session 行はその 1 回の宿り(incarnation)である。resume = 同一会話 ref を持つ新しい incarnation 行(generation + 1)。fork = 親 transcript から派生した新しい会話 ref を持つ新行。terminal 行の finality は不可侵(truth-is-rows の帰結)— 行が蘇るのではなく、会話が行を乗り換える。")
     (interpretation
       "これは ADR-DOE-AGENTS-004 の『agentd = ACP と同じ存在論のミニ control plane、新しい理論を要さない』路線の継続である: session 行 = Pod(使い捨て・terminal で終わり・作り直す)、conversation = 永続 identity(StatefulSet ordinal / PVC 同型)。settled semantics の輸入であって発明ではない。")
     (interpretation
       "蘇生可能性は stored fact であり、kill / reap の判断時に FS や CLI を probe しない(orch ADR-0005 LS2 / LS5 の輸入)。identity が行に書かれていることが resumable の定義である。probe は TOCTOU と権威の分裂(行と FS のどちらが真実か)を生む。")]
  :decision
    [(rule R1 "conversation identity は launch の成果物である: 全 kind impl は起動時に会話 identity を確定し session 行へ書く。claude = host が UUID を鋳造し --session-id で注入(boot 前に確定)。codex = boot 後に rollout を発見(Fs / Clock effect 経由 — impls は substrate-clean のまま、work_dir の cwd-match で特定し、rollout ファイル名の UUID を identity とする)。捕獲は BuildLaunch / PreLaunchSetup の kind 所有物理であり、捕獲失敗を黙って無 identity のまま running にしない — typed の degraded 観測(identity-unknown)として行に現れ、その session への resume 要求は typed 失敗になる。")
     (rule R2 "schema: agent_sessions に conversation_json(kind 判別 union: claude {session_id} / codex {session_id, rollout_path})・generation(incarnation 序数、1 起点)・lineage(resume 元 session_id / fork 親 session_id)を追加する。terminal に達した行の status を active 系へ戻す UPDATE は禁止(存在論の機械面)。既存行は generation=1・conversation 不明として移行し、identity-unknown の意味論(R1)に従う。")
     (rule R3 "effect 語彙: BuildResume [agent-type params] を追加する(mode = resume | fork は params で判別 — 物理が同一面(argv 構築)で、能力広告も同じ軸のため別 effect にしない)。物理は claude: --resume <uuid>(fork は + --fork-session)/ codex: codex resume <uuid> / codex fork。argv 物理は sessionhost impls/ の kind モジュール単一所有であり、law protocol-physics-has-one-home(ADR-DOE-AGENTS-004)の適用対象に resume / fork を含める。incarnation の宿し(pane 作成・env 合成・result channel 配線)は launch と同一の共有 policy program を通る — resume 専用の並行実装を作らない。")
     (rule R4 "RPC 語彙: session.resume {session_id, prompt?} = 同一会話の新 incarnation 行(新 session_id、generation + 1、lineage 記録)を作る。session.fork {session_id, prompt?} = 新会話の新行(generation = 1、fork 親を lineage 記録)。同一会話に non-terminal な incarnation が既在する resume は typed reject(one-live-incarnation)。fork の親生死非依存性(親が生きたまま fork できるか)は CLI 物理に依存するため、Phase 0 プローブの実測で受理形を確定してから広告する。【2026-08-11 改訂(ACP 枠切れ failover — 決裁 decision-acp-ratelimit-attempt-loss-2026-08-11): session.resume は optional 3 param を受ける(すべて後方互換・resume 専用で fork への指定は fail-closed reject)。(1) binding — session.launch と同形の typed union。指定時は蘇生元行 effective_identity からの再構成を上書きする。admission は launch の R7 admission(policy binding-admission-error)と同一実装を共有し、複製実装を禁止する。home が異なるときは R7(transplant)が発火する。(2) new_session_id — 新 incarnation の session_id を呼び手が鋳造する(ACP は agent_<invocationId> 規約)。session_name も同値で立てる。既存 id との重複は launch と同語彙(session is already registered)で、全副作用より前に reject する。未指定は従来のサーバー鋳造 <base>~g<N> を維持。lineage の真実は常に resumed_from_session_id 列であり、命名からの導出はしない。(3) expected_result — launch と同じ payload_schema admission(host の admit-expected-result を共有)。key の実在で『指定』を判定し、指定時(明示 null = 契約なしを含む)は unfulfilled contract carry より優先、未指定は従来どおり carry。work_dir は引き続き受けない — source copy 固定が transcript 発見可能性(projects/<mangled cwd>/ 物理)の保証である。】【2026-08-17 改訂(R10 と対 — 決裁 decision-acp-resume-transplant-2026-08-17.html 裁定 A): 直前の一文『work_dir は引き続き受けない』を撤回する。source copy 固定は『前身の作業場が会話より長く生きる』という前提の上でだけ発見可能性の保証になり、その前提は本番で反証された(呼び手 = ACP は invocation の回収と一緒に作業場を消す)。session.resume は optional 4 param 目として work_dir を受ける(str・後方互換: 未指定は従来どおり source copy)。詳細と物理は R10。】")
     (rule R5 "capability 広告: kinds.list の per-kind 広告に {resumable, forkable} を追加し、該当 kind の api_version を進める(ADR-DOE-AGENTS-004 R5 — スキーマを変えながら版を据え置くのは versioned 語彙の形骸化)。未対応 kind / identity-unknown 行への resume / fork は admission で typed reject する。【2026-07-13 実装時精密化: api_version(BINDING-KIND-API-VERSION)は binding 受理形の契約版であり、capability 広告は別軸の additive field — 受理形が変わらない本変更では版を据え置く。進めると ACP の verifyBindingKindsOnce が偽の BindingKindUnsupported を報じ、登録済み binding が不当に落ちる。004 R5 の『形骸化』に当たるのは受理形を変えつつ版を据え置く場合であり、本件は受理形不変。機械面: policy.hy BINDING-KIND-RESUMABLE / BINDING-KIND-FORKABLE 表 + sessionhost_host_deftests.hy test-dispatch-kinds-list(表と広告の乖離を red 化)。】")
     (rule R6 "conformance 先行(ADR-DOE-AGENTS-004 R4 の続き): 偽 CLI に resume / fork 契約(transcript の継承・fork 時の新 identity 鋳造)を足し、シナリオを先に green にしてから実 CLI 物理を書く。最低ライン: (a) kill → resume で会話文脈が保持される、(b) fork の系譜記録と独立性(親を kill しても fork 会話は生存)、(c) identity-unknown 行への resume は typed 失敗、(d) 並行 incarnation の reject、(e) generation の単調増加と await_result / report_result の世代整合(旧 incarnation の遅延 report が新 incarnation の結果を汚さない)。実 CLI 物理(特に codex fork の受理形と rollout 継承、claude --fork-session の transcript 意味論)は conformance/herdr-physics.md 前例の Phase 0 プローブ文書に実測記録してから impl を書く。【2026-08-11 改訂: 偽 CLI は transcript / rollout 不在の resume を実 CLI どおり loud に失敗させる(claude『No conversation found with session ID』/ codex『no rollout found for thread id』・rc=1、silent 新規化なし — resume-physics.md プローブ (a)(c) の鏡映)。S21 は cross-binding resume の golden(transplant link 越しの文脈継承・新 binding の effective_identity 記帳・new_session_id)と transcript-not-discoverable の typed reject(row 不生成)を wire で検証する。】")
     (rule R7 "transcript transplant 前処理(2026-08-11 追加): cross-binding resume(binding 指定 ∧ source 行の effective_identity と異なる home)は、launch より前に per-kind impl が source home → binding home へ transcript を symlink で敷設する。物理は resume-physics.md 2026-08-11 プローブが凍結する: claude = projects/<mangle(canonical work_dir)>/<conversation.session_id>.jsonl + 周辺 artifact 3 対(sessions-index.json / session-env/<sid> / file-history/<sid> — dotfiles agentcli share.py の 4 対と同型。transcript のみ必須、周辺は best-effort)。codex = conversation_json の rollout_path を <target sessions root>/<sessions/ 以下同相対 path> へ link(native 形 = codex_home/sessions、二軸形 = profile_dir/sessions)— codex の resume 発見は常に自 home の sessions/ 走査であり rollout_path 絶対 path を CLI は辿らない。source transcript が実体としても symlink 解決先としても不在なら typed reject(transcript_not_discoverable)— 実 CLI の loud 失敗を row 生成前に前倒しする。transplant の path 物理は sessionhost impls/ の kind モジュール単一所有(law resume-physics-has-one-home の延長)であり、substrate プリミティブは FsLinkArtifact(share.py link_session_artifact の意味移植: source-missing / same-entity no-op / target-conflict no-op / linked)のみ。所有 profile は source 行の記帳事実(effective_identity)で既知 — registry 走査は持ち込まない。transplant は会話(transcript)だけを運び、auth は運ばない(auth は binding の責務 — claude の credential は CLAUDE_CONFIG_DIR ごとに Keychain 分離、と実測済み)。なお transplant は蘇生可能性の判定ではない(判定は R1 のとおり stored fact のみ)— 宿しの物理前処理であり、law revivability-is-stored-fact の kill / reap 決定面とは別層。")
     (rule R8 "境界検証は呼び手責務(2026-08-11 追加): sessionhost は binding の資格(会社/個人アカウントの適合・利用可否)を検証しない。boundary rules は ACP engine の管轄(ACP ADR 0068 R3)であり、呼び手は資格判定済みの binding だけを渡す契約。sessionhost に境界検査を足すことは二重実装(判定の家の分裂)であり禁止 — sessionhost が検査するのは binding の形(admission)と transcript の物理(R7)のみ。")
     (rule R9 "typed error_code 語彙(2026-08-11 追加): resume / fork の admission reject は RpcHostError 系に整列した安定 error_code を wire に載せる — one_live_incarnation / identity_unknown / transcript_not_discoverable / kind_not_supported(effects.hy RESUME-ERR-* が語彙の家。koine の typed 文字列 code — adopt_target_not_found — と同系)。機械消費者(ACP Haskell client)は message substring ではなく code を照合する。既存 message 文言は後方互換で不変。program 層は ResumeRejected(code, message)で raise し、host dispatch が封筒の error_code へ写す。")
     (rule R10 "resume は実在する作業場で走る(2026-08-17 追加 — 決裁 decision-acp-resume-transplant-2026-08-17.html 裁定 A・ACP ADR a64d9d の受け口側)。3 点で一体: (1) 作業場は呼び手指定が優先。session.resume は optional param work_dir を受け、指定時はそこで incarnation を起こす。未指定は従来どおり蘇生元行の work_dir を継ぐ(後方互換)。(2) transcript の家は auth home × 作業場 の 2 軸。R7 の transplant は source(= 蘇生元の作業場)と target(= これから走る作業場)を別に取り、no-op は 2 軸とも一致するときだけ。同一 home でも作業場が動けば敷設が要る。(3) 作業場の実在は launch の precondition。tmux / transplant を含む全副作用より前に FsDirExists で問い、不在は typed reject work_dir_missing(確定的失敗 = 呼び手側の degrade-to-fresh の対象)。【なぜ 3 点が一体か】単独では穴が残る: (1) だけなら移植先が索き先とずれ、(2) だけなら消えた作業場で起こし、(3) だけなら止まるが継げない。【実弾】2026-08-16 の本番で resume が 43/43 全滅した。鎖は ①呼び手が前身の作業場を回収で消す ②resume が前身の作業場で tmux session を起こす ③`tmux new-session -c <不在>` が黙って $HOME へ落ちる ④claude は会話を cwd で索くので移植済み transcript に構造的に届かない ⑤『会話が無い』のまま ready gate の 120 秒を空費して縮退する。③ が沈黙であるため ②〜⑤ は 1 つも赤くならず、時間だけが消えた。【kind 差】この軸は claude 固有 — codex の resume 発見は自 home の sessions/ 走査であり cwd に依存しないので、codex 側の no-op 判定は home 一致のみで正しい(R7 のまま)。")]
  :laws
    [(law conversation-outlives-incarnation
       :statement "durable_identity => conversation_ref_stored_in_session_rows; terminal_session_rows_never_reactivated"
       :counterexamples
         [(counterexample "terminal 行の status を running へ UPDATE して session を『復活』させる")
          (counterexample "conversation identity を行に保存せず、必要時に pane / tmux session 名 / FS から都度導出する")])
     (law one-live-incarnation-per-conversation
       :statement "resume => reject_when_nonterminal_incarnation_exists_for_same_conversation"
       :counterexamples
         [(counterexample "同一会話に 2 つの live incarnation を許し、両方が同じ transcript に書き込む")])
     (law revivability-is-stored-fact
       :statement "kill_or_reap_decision => reads_session_rows_only; never_probes_fs_or_cli_at_decision_time"
       :counterexamples
         [(counterexample "kill 直前に rollout ファイルの存在を FS へ照会して蘇生可能性を判断する")])
     (law resume-physics-has-one-home
       :statement "resume_fork_argv_and_transplant_paths => single_kind_impl_module_in_sessionhost_impls"
       :counterexamples
         [(counterexample "conductor / orch / ハンドラ層が '--fork-session' や 'codex resume' の argv リテラルを自前で組む")
          (counterexample "control plane や host dispatch が projects/<mangled key>/ や sessions/<Y/M/D>/ の transplant symlink を kind impl を通さず自前で張る")])
     (law resume-runs-in-a-directory-that-exists
       :statement "resume_launch => work_dir_existence_checked_before_any_side_effect; missing_work_dir_is_typed_reject(work_dir_missing); never_delegated_to_tmux_fallback"
       :counterexamples
         [(counterexample "work_dir を tmux new-session -c へそのまま渡し、不在なら $HOME で起動する暗黙の読み替えに委ねる(失敗ではなく『違う場所での成功』になり、どの検査も赤くならない)")
          (counterexample "実在検査を transplant の後ろに置く — 移植だけ済んで起動は $HOME、という半端な状態が残る")
          (counterexample "FsListDir の空 list を『不在』と読む — 空ディレクトリと弁別できず、実在する空の作業場を不在と誤判定する")])
     (law transcript-home-is-auth-times-workdir
       :statement "claude_transplant_target => keyed_by(target_auth_home, work_dir_the_incarnation_will_run_in); no_op_only_when_both_axes_match"
       :counterexamples
         [(counterexample "source と target の project key を同じ work_dir から組む — 作業場が動く resume で移植先が CLI の索き先とずれ、transcript は在るのに『会話が無い』になる")
          (counterexample "auth home が一致した時点で no-op と判定する — 同一 home で作業場だけ動く resume が素通りする")])
     (law transplant-carries-conversation-not-auth
       :statement "cross_binding_resume => transplants_transcript_artifacts_only; auth_material_never_copied_or_linked"
       :counterexamples
         [(counterexample "transplant が .credentials.json / auth.json / Keychain 素材を target home へ symlink または copy する")
          (counterexample "binding の代わりに transcript と同時に auth home 全体を link して『ログイン済みに見える』resume を作る")])
     (law boundary-checks-live-in-the-caller
       :statement "binding_eligibility_verdict => acp_engine_owned_adr0068_r3; sessionhost_never_reimplements_boundary_rules"
       :counterexamples
         [(counterexample "sessionhost が binding config_dir / codex_home の会社/個人適合を判定して reject する(判定の家の分裂 — ACP ADR 0068 R3 の二重実装)")])
     (law resume-rejects-are-machine-readable
       :statement "resume_admission_reject => stable_error_code_on_wire(one_live_incarnation | identity_unknown | transcript_not_discoverable | kind_not_supported | work_dir_missing); consumers_never_match_message_substrings"
       :counterexamples
         [(counterexample "ACP client が error message の substring('one-live-incarnation')で失敗分類する")
          (counterexample "reject の message 文言変更が機械消費者を壊す(= code でなく文言が契約になっている)")])]
  :enforcement
    [(defsemgrep no-resume-fork-argv-outside-kind-impl
       :languages ["generic"]
       :pattern "--fork-session"
       :message "resume / fork の argv 物理は sessionhost impls/ の kind モジュール単一所有(ADR-DOE-AGENTS-006 R3 / law resume-physics-has-one-home)。この flag リテラルを他所で組まず、BuildResume effect を yield する。impls/ 内の正当な出現は installed rule の paths 除外で扱う。"
       :bad ["cmd = [\"claude\", \"--resume\", session_id, \"--fork-session\"]"]
       :good ["(<- argv (build-resume agent-type params))"])
     (defsemgrep no-transplant-path-literals-outside-kind-impl
       :languages ["generic"]
       :pattern "file-history"
       :message "cross-binding transplant の path 物理(claude 4 対: transcript / sessions-index.json / session-env / file-history、codex rollout 相対 path)は sessionhost impls/ の kind モジュール単一所有(ADR-DOE-AGENTS-006 R7 / law resume-physics-has-one-home)。この path リテラルを他所で組まず、TransplantConversation effect を yield する。impls/ 内の正当な出現は installed rule の paths 除外で扱う。"
       :bad ["target = f\"{config_dir}/file-history/{sid}\""]
       :good ["(<- transplanted (transplant-conversation agent-type params))"])]
  :plans ["docs/adr/defadr_doeff_agents_006_conversation_resume_fork.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(conversation_json / generation / lineage 列とterminal 不可逆の機械面)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy(BuildResume + 2026-08-11: TransplantConversation / FsLinkArtifact / RESUME-ERR-* 語彙)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy(--session-id 鋳造は現行、+ resume / fork argv + claude-transplant-conversation)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/codex.hy(rollout 発見による identity 捕獲 + resume / fork argv + codex-transplant-conversation)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(session.resume / session.fork dispatch + admit-expected-result 共有 + ResumeRejected→error_code 写像)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(capability 広告 + admission + 共有 incarnation program)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy(resume-session の binding / new_session_id / expected_result 拡張 + ResumeRejected)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy(FsLinkArtifact — share.py link_session_artifact の意味移植)"
          "packages/doeff-agents/conformance(偽 CLI の resume / fork 契約 + loud 失敗整合 + S21 cross-binding シナリオ + resume-physics Phase 0 プローブ文書)"])
