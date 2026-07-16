;;; 共有 session-launch program(ADR-DOE-AGENTS-004 C2)。
;;;
;;; oracle: packages/doeff-agentd/src/main.rs session_launch + wait_for_repl_idle。
;;; kind 別の protocol 物理(argv・trust・gate・marker・dialog キー)は
;;; interface effect 越しに per-kind defhandler(impls/)が所有し、この program は
;;; 凍結された起動の「順序と方針」だけを持つ:
;;;   admission(重複 / 既存 tmux)→ per-kind PreLaunchSetup(S11 gate + trust)
;;;   → result channel 配線(ADR 0035 reject-at-launch)→ argv 構築 →
;;;   TmuxNewSession → booting 行 upsert + session_started event → 起動 command
;;;   送出 → wait-for-repl-idle(R9 launch dialog の決定的 dismissal)→ prompt の
;;;   live REPL 配送(result-protocol instruction 追記)。ready timeout は先行登録行を
;;;   failed へ終端化してから tmux を清掃する。
;;;
;;; program は effect を yield するのみで IO を直接呼ばない(substrate-clean)。
;;; 呼び手より長生きする部分(socket・writer actor・lease・cycle 起動)は
;;; C3 の host 所有 — ここには置かない(daemon-owns-only-exteriority)。

(require doeff-hy.macros [defk deff <-])

(import re)
(import dataclasses [replace])

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  PaneObservation
  classify-pane
  deliver-message
  build-launch
  build-resume
  pre-launch-setup
  wire-result-channel
  session-store-get
  session-store-list-active
  session-store-upsert
  session-store-record-event
  tmux-has-session
  tmux-new-session
  tmux-send-keys
  tmux-capture
  tmux-kill-session
  clock-now
  clock-sleep])
(import doeff_agents.sessionhost.policy [
  BINDING-OWNED-ENV-KEYS
  binding-admission-error
  iso-format
  overlay-env-offenders
  seconds-since])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle 定数と文言)
;; ---------------------------------------------------------------------------

(setv LIFECYCLE-RUN-TO-COMPLETION "run_to_completion")
(setv LIFECYCLE-INTERACTIVE "interactive")

;; agentd が argv を組める(= interface effect の per-kind impl が存在すると
;; 契約上約束されている)kind(oracle is_interactive_agent_type)。
(setv INTERACTIVE-AGENT-TYPES #{"codex" "claude"})

;; expected_result 付き launch の prompt へ追記する結果搬送契約
;; (oracle result_protocol_instruction — 文言 verbatim。ADR 0035:
;; 結果は byte-faithful データチャネルで回収し、決して画面から scrape しない)。
(setv RESULT-PROTOCOL-INSTRUCTION
      (+ " Result channel: when you have finished the task, call the "
         "`report_result` MCP tool exactly once, passing your result as the "
         "`payload` argument — a JSON object that satisfies the result schema. "
         "Do not print the result to the terminal and do not create JSON "
         "result files; agentd only accepts the result through the "
         "`report_result` tool. If the tool responds with a validation error, "
         "fix the payload and call `report_result` again in the same session."))

;; wait-for-repl-idle の上限(oracle: Duration::from_secs(120) 定数)と
;; poll / 再描画待ち(oracle: 300ms / 800ms)。
(setv REPL-IDLE-MAX-WAIT-SECONDS 120)
(setv REPL-IDLE-POLL-SECONDS 0.3)
(setv DIALOG-REDRAW-SECONDS 0.8)


;; ---------------------------------------------------------------------------
;; 純粋 helper(oracle shell_quote / shell_join / command_mentions_codex)
;; ---------------------------------------------------------------------------

(deff shell-quote [value]
  {:pre [(: value str)]
   :post [(: % str)]}
  "oracle shell_quote: 空は ''、安全文字([a-zA-Z0-9] と -_./:=@,%+)のみは
   素通し、それ以外は single-quote(埋め込み ' は '\\'' でエスケープ)。"
  (if (= value "")
      "''"
      (do
        (setv safe (all (gfor c value
                              (or (.isalnum c) (in c "-_./:=@,%+")))))
        (if safe
            value
            (+ "'" (.replace value "'" "'\\''") "'")))))

(deff shell-join [args]
  {:pre [(: args list)]
   :post [(: % str)]}
  "argv → tmux pane に流す 1 行 shell command(oracle shell_join)。"
  (.join " " (lfor a args (shell-quote a))))

(deff command-mentions-codex [command]
  {:pre [(: command str)]
   :post [(: % bool)]}
  "明示 command が codex を起動するか(oracle command_mentions_codex:
   whitespace token が `codex` そのもの、または `/codex` で終わる。
   `codexify` のような部分文字列は数えない)。"
  (bool (any (gfor token (.split command)
                   (or (= token "codex") (.endswith token "/codex"))))))


;; ---------------------------------------------------------------------------
;; wait-for-repl-idle(oracle wait_for_repl_idle — R9 launch dialog 込み)
;; ---------------------------------------------------------------------------

(defk wait-for-repl-idle [agent-type pane-id max-wait-seconds]
  {:pre [(: agent-type str) (: pane-id str)
         (: max-wait-seconds (| int float)) (> max-wait-seconds 0)]
   :post [(: % bool)]}
  "REPL が入力を受けられる状態(idle prompt 可視)まで poll する。codex は
   banner + MCP ロードの後にしか input loop が配線されない — その窓に keys を
   送ると Enter がロード画面に食われ、prompt が入力箱に座ったまま submit
   されない(oracle 実障害)。R9 launch dialog(codex-update / bypass /
   fullscreen / trust / managed)は idle 判定より先に検出して決定的 keys で
   dismiss する(update dialog は `›` 選択 marker を描くため idle と誤認
   される)。上限到達は False を返すだけ — 呼び手(launch-session)はこれを
   fail-closed の typed error にする(2026-07-07 契約修正。旧 oracle は
   構わず送出していたが、R9 外の未知 dialog に prompt が送出されて
   silent hang になる実障害 — trust dialog — がそれで隠れた)。"
  (<- start (clock-now))
  (setv idle-seen False)
  (setv looping True)
  (while looping
    (<- now (clock-now))
    (setv elapsed (- now start))
    (if (>= (.total-seconds elapsed) max-wait-seconds)
        (setv looping False)
        (do
          (<- output (tmux-capture pane-id 60))
          (<- obs (classify-pane agent-type output))
          (cond
            (is-not obs.dialog None)
              (do
                (for [key obs.dialog-dismiss-keys]
                  (<- _ (tmux-send-keys pane-id key False False)))
                (<- _ (clock-sleep DIALOG-REDRAW-SECONDS)))
            obs.has-idle-prompt
              (do
                (setv idle-seen True)
                (setv looping False))
            True
              (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS))))))
  idle-seen)


;; ---------------------------------------------------------------------------
;; launch program 本体(oracle session_launch の凍結順序)
;; ---------------------------------------------------------------------------

(defk launch-session [params]
  {:pre [(: params dict)]
   :post [(: % SessionRow)]}
  "1 session の launch。params(oracle LaunchParams + R7 binding):
   session_id / session_name / agent_type / work_dir / lifecycle /
   binding(typed auth/profile 構成 — ADR-DOE-AGENTS-004 R7)/
   session_env(非 auth overlay)/ prompt / command(明示 override、
   escape hatch)/ expected_result / model / effort / mcp_servers /
   socket_path / skip_trust_setup。戻り値: 永続化済みの booting SessionRow。"
  (setv session-id (get params "session_id"))
  (setv session-name (get params "session_name"))
  (setv agent-type (get params "agent_type"))
  (setv lifecycle (get params "lifecycle"))
  (setv binding (.get params "binding"))
  (setv session-env (.get params "session_env" {}))
  (setv command-override (or (.get params "command") ""))
  (setv has-override (bool (.strip command-override)))
  (setv expected-result (.get params "expected_result"))

  ;; --- R7 admission(純粋検査 — 全副作用より前): auth は typed binding で
  ;; 運び、session_env は非 auth overlay。binding 所有キーの overlay 混入は
  ;; 裏口(2026-07 まで合成 CODEX_HOME がここを通っていた)なので typed reject。
  (setv binding-error (binding-admission-error binding agent-type))
  (when (is-not binding-error None)
    (raise (RuntimeError f"session.launch: invalid binding — {binding-error}")))
  (setv offenders (overlay-env-offenders session-env))
  (when offenders
    (raise (RuntimeError
             (+ "session.launch: session_env is a non-auth overlay and may not "
                f"carry binding-owned auth env (offending: {(.join ", " offenders) }). "
                "Declare the auth profile through the typed `binding` field "
                "(ADR-DOE-AGENTS-004 R7)."))))

  ;; --- admission(oracle 順序: lifecycle → 重複 → 既存 tmux)。
  (when (not-in lifecycle #{LIFECYCLE-RUN-TO-COMPLETION LIFECYCLE-INTERACTIVE})
    (raise (RuntimeError
             (+ f"unsupported session lifecycle: {lifecycle} "
                f"(expected {LIFECYCLE-RUN-TO-COMPLETION} or {LIFECYCLE-INTERACTIVE})"))))
  (<- existing (session-store-get session-id))
  (when (is-not existing None)
    (raise (RuntimeError f"session is already registered: {session-id}")))
  ;; max_running admission(oracle :1679-1685 — 重複 check の後・tmux check の
  ;; 前)。host が params["max_running"] に運用上限を注入する。直接束縛では
  ;; 省略 = 無制限(config を持たない)。
  (setv max-running (.get params "max_running"))
  (when (is-not max-running None)
    (<- active-rows (session-store-list-active))
    (setv active-count (len active-rows))
    (when (>= active-count max-running)
      (raise (RuntimeError
               f"max running agent sessions reached: {active-count}/{max-running}"))))
  (<- tmux-exists (tmux-has-session session-name))
  (when tmux-exists
    (raise (RuntimeError f"tmux session already exists: {session-name}")))

  ;; --- ADR 0035 reject-at-launch: result channel を配線できない agent が
  ;; result contract を持つことは受けない(silent timeout の予約になる)。
  (when (and (is-not expected-result None)
             (not has-override)
             (not-in agent-type INTERACTIVE-AGENT-TYPES))
    (raise (RuntimeError
             (+ f"session.launch: agent_type '{agent-type}' cannot deliver a "
                "result over the report_result channel; a result contract "
                "requires agent_type 'codex' or 'claude' (or an explicit "
                "`command` that reports results itself)"))))

  ;; --- per-kind PreLaunchSetup(S11 auth gate + trust、必ず tmux より前)。
  ;; 明示 command が codex を起動する場合も codex の gate/trust が効く
  ;; (oracle command_mentions_codex — 暗黙 ~/.codex が個人クォータを焼いた
  ;; 実障害)。
  (setv prelaunch-kind
        (cond
          (in agent-type INTERACTIVE-AGENT-TYPES) agent-type
          (command-mentions-codex command-override) "codex"
          True None))
  (setv identity None)
  (setv minted-conversation None)
  (when (is-not prelaunch-kind None)
    (<- resolved (pre-launch-setup prelaunch-kind params))
    ;; warnings は運用ログ向けの副産物(host が stderr へ出す)— 永続する
    ;; identity(S14 の effective_identity 列)は auth home の解決結果のみ。
    ;; conversation(ADR-006: claude が launch 時に鋳造する会話 identity)は
    ;; identity 列ではなく conversation 列の住人なのでここで分離する。
    (setv identity (dict resolved))
    (.pop identity "warnings" None)
    (setv minted-conversation (.pop identity "conversation" None)))
  (setv resume-context (.get params "resume_context"))

  ;; --- result channel 配線 + 起動 command(oracle resolve_launch_command:
  ;; override は verbatim、それ以外は per-kind argv builder)。
  (setv command-line command-override)
  (when (not has-override)
    (setv effective-params (dict params))
    (when (and (is-not expected-result None)
               (in agent-type INTERACTIVE-AGENT-TYPES))
      (<- channel (wire-result-channel agent-type session-id
                                       (.get params "socket_path" "")))
      (setv (get effective-params "result_channel") channel))
    ;; ADR-006 R3: incarnation の宿し(この program)は 1 本のまま、argv 構築
    ;; だけを fresh launch / resume / fork で分岐する。
    (if (is resume-context None)
        (do
          (when (and (= agent-type "claude") (is-not minted-conversation None))
            (setv (get effective-params "conversation") minted-conversation))
          (<- argv (build-launch agent-type effective-params)))
        (do
          (setv (get effective-params "resume_mode") (get resume-context "mode"))
          (setv (get effective-params "conversation")
                (get resume-context "conversation"))
          (<- argv (build-resume agent-type effective-params))))
    (setv command-line (shell-join argv)))

  ;; --- tmux session 作成(禁止 env reject は substrate 所有)+ 起動。
  ;; 実効 env = 非 auth overlay ∪ binding 由来 auth env(R7: auth の合成は
  ;; per-kind impl の解決した identity が唯一の源 — overlay は admission で
  ;; 所有キーを締め出し済みなので衝突は構造的に無い)。
  (setv binding-env
        (if (is identity None)
            {}
            (dfor [k v] (.items identity)
                  :if (and (in k BINDING-OWNED-ENV-KEYS) (isinstance v str))
                  k v)))
  (setv effective-env {#** session-env #** binding-env})
  (<- pane-id (tmux-new-session session-name (get params "work_dir") effective-env))

  ;; --- 物理 session の簿記は TUI readiness と独立。外部 reconciler が
  ;; ready 待ち(最大 120s)を orphan と誤認しないよう、new-session 直後に
  ;; BOOTING 行を公開する。awaiting-response は prompt 配送成功後にのみ武装。
  (setv awaiting False)
  (setv row-conversation
        (cond
          (is-not resume-context None)
            (if (= (get resume-context "mode") "resume")
                (get resume-context "conversation")
                None)
          (and (= agent-type "claude") (not has-override)) minted-conversation
          True None))
  (<- now (clock-now))
  (setv row (SessionRow
              :session-id session-id
              :session-name session-name
              :pane-id pane-id
              :agent-type agent-type
              :lifecycle lifecycle
              :status "booting"
              :started-at (iso-format now)
              :awaiting-response awaiting
              :expected-result expected-result
              :effective-identity identity
              :work-dir (get params "work_dir")
              :backend-kind (.get params "backend_kind" "tmux")
              :backend-ref {"session_name" session-name
                            "pane_id" pane-id
                            "command" command-line}
              ;; ADR-006: 非 auth の launch 意図を行に永続化(resume の復元源)。
              :launch-overlay {"session_env" session-env
                               "model" (.get params "model")
                               "effort" (.get params "effort")
                               "mcp_servers" (or (.get params "mcp_servers") {})}
              :conversation row-conversation
              :generation (if (is resume-context None)
                              1
                              (get resume-context "generation"))
              :resumed-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "resumed_from_session_id"))
              :forked-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "forked_from_session_id"))))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id "session_started" row))

  (when (.strip command-line)
    (<- _ (tmux-send-keys pane-id command-line True True)))

  ;; --- prompt の live REPL 配送(argv / print-mode 禁止 — session が task
  ;; 完了後も生き、monitor が validate / 再促せるように)。
  (setv prompt (or (.get params "prompt") ""))
  (when (.strip prompt)
    (setv full-prompt
          (if (is-not expected-result None)
              (+ prompt RESULT-PROTOCOL-INSTRUCTION)
              prompt))
    (when (and (not has-override) (in agent-type INTERACTIVE-AGENT-TYPES))
      ;; 予算は host 注入 knob(DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS、
      ;; max_running と同じ注入パターン)が優先、無ければ oracle 定数 120s。
      (setv repl-idle-max-wait
            (or (.get params "repl_idle_max_wait_seconds")
                REPL-IDLE-MAX-WAIT-SECONDS))
      (<- repl-ready (wait-for-repl-idle agent-type pane-id repl-idle-max-wait))
      (when (not repl-ready)
        ;; fail-closed(2026-07-07 契約修正): idle 未達のまま paste すると
        ;; prompt が R9 外の未知 dialog に送出され session は silent hang に
        ;; なる(trust dialog 実障害)。paste せず、画面 tail を証拠として
        ;; 積んだ typed error で launch を fail させ、作った session は
        ;; 片付ける。先行登録した行は failed へ終端化し BOOTING を残さない。
        (<- final-frame (tmux-capture pane-id 40))
        (setv tail-lines (cut (.splitlines final-frame) -15 None))
        (setv screen-tail (.join "\n" tail-lines))
        (setv reason
              (+ f"session.launch: {agent-type} REPL did not become ready "
                 f"within {repl-idle-max-wait}s — startup is blocked by an "
                 "unrecognized screen (a dialog outside the R9 fast-path "
                 "set?). The prompt was NOT delivered and the created "
                 "session cleanup was requested. Last screen tail:\n"
                 screen-tail))
        (<- failed-at (clock-now))
        (setv failed-at-iso (iso-format failed-at))
        (setv row
              (replace row
                       :status "failed"
                       :last-observed-at failed-at-iso
                       :finished-at failed-at-iso
                       :output-snippet screen-tail
                       :last-validation-error reason
                       :terminal-cause
                         (TerminalCause :category "timed_out"
                                        :reason reason
                                        :retryable True
                                        :observed-at failed-at-iso)))
        (<- _ (session-store-upsert row))
        (<- _ (session-store-record-event session-id "session_launch_timeout" row))
        (<- _ (tmux-kill-session session-name))
        (setv row (replace row :cleaned-at failed-at-iso))
        (<- _ (session-store-upsert row))
        (raise (RuntimeError reason))))
    (if (in agent-type INTERACTIVE-AGENT-TYPES)
        (<- _ (deliver-message pane-id full-prompt))
        (<- _ (tmux-send-keys pane-id full-prompt True True)))
    (setv awaiting True)
    (setv row (replace row :awaiting-response awaiting))
    (<- _ (session-store-upsert row)))
  (when (not (.strip prompt))
    (setv row (replace row :status "running"))
    (<- _ (session-store-upsert row)))
  row)


;; ---------------------------------------------------------------------------
;; resume / fork program(ADR-DOE-AGENTS-006 R4)
;; ---------------------------------------------------------------------------

(deff strip-incarnation-suffix [value]
  {:pre [(: value str)]
   :post [(: % str)]}
  "incarnation 命名の基底を得る(末尾の `~g<N>` / `~fork<N>` を 1 つ剥がす)。"
  (re.sub "~(g|fork)[0-9]+$" "" value))


(defk resume-session [params]
  {:pre [(: params dict)
         (in (.get params "mode") #{"resume" "fork"})]
   :post [(: % SessionRow)]}
  "会話の新しい incarnation を宿す(ADR-006 R4)。resume = 同一会話 ref の
   新行(generation + 1)/ fork = 新会話の新行(generation 1、CLI が新 ID を
   鋳造)。admission: 蘇生元の実在 → kind capability → identity-unknown の
   typed 失敗(R1)→ one-live-incarnation(resume のみ)。宿し自体は
   launch-session を再利用する — 並行実装を作らない(R3)。auth は蘇生元行の
   effective_identity から typed binding を再構成する(行が auth の家)。
   params: session_id(蘇生元)/ mode / prompt? / model? / effort? /
   mcp_servers? + host 注入(socket_path / max_running / backend_kind /
   repl_idle_max_wait_seconds)。"
  (setv source-sid (get params "session_id"))
  (setv mode (get params "mode"))
  (<- source (session-store-get source-sid))
  (when (is source None)
    (raise (RuntimeError f"session is not registered: {source-sid}")))
  (when (not-in source.agent-type INTERACTIVE-AGENT-TYPES)
    (raise (RuntimeError
             (+ f"session.{mode}: agent_type '{source.agent-type}' does not "
                f"support {mode} (supported: codex, claude)"))))
  (setv conv source.conversation)
  (when (is conv None)
    (raise (RuntimeError
             (+ f"session.{mode}: session '{source-sid}' has no stored "
                "conversation identity (identity-unknown) — it cannot be "
                "resumed or forked (ADR-DOE-AGENTS-006 R1: revivability is a "
                "stored fact)"))))
  (setv conv-id (get conv "session_id"))
  (when (= mode "resume")
    (<- active-rows (session-store-list-active))
    (for [row active-rows]
      (when (and (is-not row.conversation None)
                 (= (.get row.conversation "session_id") conv-id))
        (raise (RuntimeError
                 (+ f"session.resume: conversation '{conv-id}' already has a "
                    f"live incarnation '{row.session-id}' "
                    f"(status {row.status}) — "
                    "one-live-incarnation-per-conversation "
                    "(ADR-DOE-AGENTS-006 R4)"))))))

  ;; 新 incarnation の id / name(~g<N> / ~fork<N> 系列。既使用は前進で回避 —
  ;; 古い incarnation からの resume でも序数が単調に進む)。
  (setv base-sid (strip-incarnation-suffix source-sid))
  (setv base-name (strip-incarnation-suffix source.session-name))
  (setv gen (if (= mode "resume") (+ source.generation 1) 1))
  (setv counter (if (= mode "resume") gen 1))
  (setv suffix-kind (if (= mode "resume") "g" "fork"))
  (setv new-sid f"{base-sid}~{suffix-kind}{counter}")
  (<- clash (session-store-get new-sid))
  (while (is-not clash None)
    (setv counter (+ counter 1))
    (setv new-sid f"{base-sid}~{suffix-kind}{counter}")
    (<- next-clash (session-store-get new-sid))
    (setv clash next-clash))
  (when (= mode "resume")
    (setv gen counter))
  (setv new-name f"{base-name}~{suffix-kind}{counter}")

  ;; auth binding の再構成(行の effective_identity が auth の家)。
  (setv identity (or source.effective-identity {}))
  (setv binding
        (cond
          (and (= source.agent-type "claude")
               (is-not (.get identity "CLAUDE_CONFIG_DIR") None))
            {"kind" "claude-code"
             "config_dir" (get identity "CLAUDE_CONFIG_DIR")}
          (and (= source.agent-type "codex")
               (is-not (.get identity "CODEX_HOME") None))
            {"kind" "codex"
             "codex_home" (get identity "CODEX_HOME")}
          True None))

  ;; 未達成の result contract は resume が引き継ぐ(会話の続きなので)。
  ;; fork は新しい仕事 — contract は引き継がない。
  (setv carried-expected
        (if (and (= mode "resume")
                 (is-not source.expected-result None)
                 (is source.result-payload None))
            source.expected-result
            None))

  ;; 非 auth の launch 意図は蘇生元行の launch_overlay から復元し、呼び手の
  ;; params が per-key で上書きする(行が意図の家 — 呼び手は差分だけ言う)。
  (setv overlay (or source.launch-overlay {}))
  (setv overlay-env (dict (or (.get overlay "session_env") {})))
  (.update overlay-env (or (.get params "session_env") {}))

  (setv launch-params
        {"session_id" new-sid
         "session_name" new-name
         "agent_type" source.agent-type
         "work_dir" source.work-dir
         "command" None
         "prompt" (.get params "prompt")
         "model" (or (.get params "model") (.get overlay "model"))
         "effort" (or (.get params "effort") (.get overlay "effort"))
         "mcp_servers" (or (.get params "mcp_servers")
                           (.get overlay "mcp_servers")
                           {})
         "skip_trust_setup" False
         "lifecycle" source.lifecycle
         "binding" binding
         "session_env" overlay-env
         "expected_result" carried-expected
         "socket_path" (.get params "socket_path" "")
         "max_running" (.get params "max_running")
         "repl_idle_max_wait_seconds" (.get params "repl_idle_max_wait_seconds")
         "backend_kind" (.get params "backend_kind" "tmux")
         "resume_context" {"mode" mode
                           "conversation" conv
                           "generation" gen
                           "resumed_from_session_id"
                             (when (= mode "resume") source-sid)
                           "forked_from_session_id"
                             (when (= mode "fork") source-sid)}})
  (<- row (launch-session launch-params))
  (<- _ (session-store-record-event row.session-id
                                    (if (= mode "resume")
                                        "session_resumed"
                                        "session_forked")
                                    row))
  row)
