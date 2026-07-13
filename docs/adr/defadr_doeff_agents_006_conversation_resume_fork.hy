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
       :evidence "orch repo docs/adr/defadr_0005_run_session_lifecycle.hy; orch PR #532")]
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
     (rule R4 "RPC 語彙: session.resume {session_id, prompt?} = 同一会話の新 incarnation 行(新 session_id、generation + 1、lineage 記録)を作る。session.fork {session_id, prompt?} = 新会話の新行(generation = 1、fork 親を lineage 記録)。同一会話に non-terminal な incarnation が既在する resume は typed reject(one-live-incarnation)。fork の親生死非依存性(親が生きたまま fork できるか)は CLI 物理に依存するため、Phase 0 プローブの実測で受理形を確定してから広告する。")
     (rule R5 "capability 広告: kinds.list の per-kind 広告に {resumable, forkable} を追加し、該当 kind の api_version を進める(ADR-DOE-AGENTS-004 R5 — スキーマを変えながら版を据え置くのは versioned 語彙の形骸化)。未対応 kind / identity-unknown 行への resume / fork は admission で typed reject する。")
     (rule R6 "conformance 先行(ADR-DOE-AGENTS-004 R4 の続き): 偽 CLI に resume / fork 契約(transcript の継承・fork 時の新 identity 鋳造)を足し、シナリオを先に green にしてから実 CLI 物理を書く。最低ライン: (a) kill → resume で会話文脈が保持される、(b) fork の系譜記録と独立性(親を kill しても fork 会話は生存)、(c) identity-unknown 行への resume は typed 失敗、(d) 並行 incarnation の reject、(e) generation の単調増加と await_result / report_result の世代整合(旧 incarnation の遅延 report が新 incarnation の結果を汚さない)。実 CLI 物理(特に codex fork の受理形と rollout 継承、claude --fork-session の transcript 意味論)は conformance/herdr-physics.md 前例の Phase 0 プローブ文書に実測記録してから impl を書く。")]
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
       :statement "resume_fork_argv => single_kind_impl_module_in_sessionhost_impls"
       :counterexamples
         [(counterexample "conductor / orch / ハンドラ層が '--fork-session' や 'codex resume' の argv リテラルを自前で組む")])]
  :enforcement
    [(defsemgrep no-resume-fork-argv-outside-kind-impl
       :languages ["generic"]
       :pattern "--fork-session"
       :message "resume / fork の argv 物理は sessionhost impls/ の kind モジュール単一所有(ADR-DOE-AGENTS-006 R3 / law resume-physics-has-one-home)。この flag リテラルを他所で組まず、BuildResume effect を yield する。impls/ 内の正当な出現は installed rule の paths 除外で扱う。"
       :bad ["cmd = [\"claude\", \"--resume\", session_id, \"--fork-session\"]"]
       :good ["(<- argv (build-resume agent-type params))"])]
  :plans ["docs/adr/defadr_doeff_agents_006_conversation_resume_fork.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(conversation_json / generation / lineage 列とterminal 不可逆の機械面)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy(BuildResume)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy(--session-id 鋳造は現行、+ resume / fork argv)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/codex.hy(rollout 発見による identity 捕獲 + resume / fork argv)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(session.resume / session.fork dispatch)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(capability 広告 + admission + 共有 incarnation program)"
          "packages/doeff-agents/conformance(偽 CLI の resume / fork 契約 + シナリオ追加 + resume-physics Phase 0 プローブ文書)"])
