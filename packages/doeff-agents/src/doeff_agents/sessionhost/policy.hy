;;; 共有 session-host policy program(ADR-DOE-AGENTS-004 C1、Hy 1 本)。
;;;
;;; monitor cycle = session 行からの level-triggered 再導出(R1/R3)。
;;; continuation は永続化しない — 真実は行のみ(truth-is-rows-not-continuations)。
;;; program は effect を yield するのみで IO を直接呼ばない(substrate-clean)。
;;;
;;; 分岐は C0 契約の凍結物理そのもの(oracle: main.rs monitor_once、契約:
;;; packages/doeff-agents/conformance/README.md)。分岐の順序・文言・taxonomy は
;;; conformance suite(S1-S19)が oracle に対して green 済みの挙動を写しており、
;;; 変更は ADR 改訂が先(黙った変更は conformance red)。

(require doeff-hy.macros [defk deff <-])

(import dataclasses [replace])
(import datetime [datetime])
(import json)
(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  PaneObservation
  JudgeVerdict
  ProcResult
  MonitorKnobs
  classify-pane
  deliver-message
  discover-conversation
  session-store-list-active
  session-store-result-payload
  session-store-upsert
  session-store-record-event
  session-store-known-conversation-ids
  tmux-has-session
  tmux-pane-current-command
  tmux-capture
  tmux-send-keys
  tmux-kill-session
  clock-now
  proc-run])


;; ===========================================================================
;; 凍結定数(契約所有)
;; ===========================================================================

;; 非終端 status(main.rs:1931 active_statuses)。blocked_api は非終端が正
;; (S8a: level-triggered — pane が変われば回復し得る)。
(setv ACTIVE-STATUSES #{"pending" "booting" "running" "blocked" "blocked_api"})

;; 終端 status(main.rs is_terminal_status)。
(setv TERMINAL-STATUSES #{"done" "failed" "exited" "stopped" "cancelled"})

;; turn-end 無結果への是正メッセージ(main.rs RESULT_SOLICITATION_MESSAGE、
;; S2 が文言接頭 `AGENTD RESULT CONTRACT: ` を wire で assert する — verbatim)。
(setv RESULT-SOLICITATION-MESSAGE
      (+ "AGENTD RESULT CONTRACT: your turn ended without a report_result call. "
         "Call the report_result MCP tool now with a payload that satisfies the "
         "declared result schema. Do only that — no other actions, no files."))

;; TerminalCause 凍結表(conformance README、category → retryable。契約所有)。
;; category 文字列は **wire / 行に載る serde snake_case 値そのもの**
;; (oracle TerminalCauseCategory は rename_all = "snake_case" —
;; S3 が cause["category"] == "run_failed" を wire で assert する。README 表の
;; CamelCase は enum variant のラベルであって wire 値ではない。C1 がラベルを
;; 転記していた parity バグを C3 で是正)。
(setv TERMINAL-CAUSE-RETRYABLE
      {"rate_limited" True
       "timed_out" True
       "lost" True
       "runner_unavailable" False
       "protocol_error" False
       "run_failed" False
       "interactive_prompt_blocked" False
       ;; host RPC(session.cancel / session.cleanup)所有 — oracle は
       ;; retryable=false を明示で渡す(:1985-1991)。
       "cancelled" False})

(setv TERMINAL-CAUSE-CATEGORIES (frozenset (.keys TERMINAL-CAUSE-RETRYABLE)))


;; ===========================================================================
;; wire binding(ADR-DOE-AGENTS-004 R7: launch effect は auth-blind)
;; ===========================================================================
;;
;; auth/profile 物理は typed `binding` field で運ぶ(束縛時構成の serialize —
;; ACP AgentBindingDefinition agent-binding/v1 と同写像)。session_env は
;; 非 auth overlay に縮む: binding 所有キーが overlay に居たら typed reject
;; (それが 2026-07 まで生きていた構造裏口 — 合成 CODEX_HOME が汎用 env dict
;; 経由で effect user から流れ込んでいた)。

;; binding 所有 env キー: per-kind impl が binding から合成する auth/profile
;; env。真実の家: impls/codex.hy(CODEX_HOME)/ impls/claude_code.hy
;; (CLAUDE_CONFIG_DIR)。kind を問わず overlay から全キーを締め出す
;; (所有権ベース — 既知の悪いキーの列挙は腐るが所有権は腐らない)。
(setv BINDING-OWNED-ENV-KEYS #{"CODEX_HOME" "CLAUDE_CONFIG_DIR"})

;; wire binding kind → agent_type(ACP bindingAgentType と同写像)。
(setv BINDING-KIND-AGENT-TYPE {"codex" "codex" "claude-code" "claude"})

;; kind ごとの受理形(shape = kind 以外の field 名集合。宣言順に列挙)。
;; codex v2(#15)は「受理形の拡張」: {codex_home}(native home — daemon
;; ローカル束縛・CODEX_HOME= escape hatch の恒久住人)XOR {auth_file,
;; profile_dir}(control plane の二軸宣言 — host が FsComposeHomeView で
;; view を合成)。混在・部分・未知 field はどの shape にも一致せず reject。
(setv BINDING-KIND-SHAPES
      {"codex" [#{"codex_home"} #{"auth_file" "profile_dir"}]
       "claude-code" [#{"config_dir"}]})

;; per-kind の契約版。ACP 側の kind→期待版表(Definition.hs)と同写像 —
;; 二枚の表の drift は ACP の verifyBindingKindsOnce(kinds.list 照合)が
;; BindingKindUnsupported として検出する。codex は #15(受理形拡張)で v2、
;; claude-code は v1 のまま。
(setv BINDING-KIND-API-VERSION
      {"codex" "acp.dev/agent-binding/v2"
       "claude-code" "acp.dev/agent-binding/v1"})

(deff binding-kind-shape-label [kind]
  {:pre [(: kind str)]
   :post [(: % str)]}
  "kind の受理形の人間可読ラベル(広告と admission エラーの共有語彙)。
   shape 内は field 名の昇順を `+`、shape 間は宣言順を ` | ` で結ぶ。"
  (.join " | " (lfor shape (get BINDING-KIND-SHAPES kind)
                     (.join "+" (sorted shape)))))

;; kinds.list 広告(DOE-004 R5 縮小版、2026-07-08): host は自分の binding
;; kind 語彙を広告し、control plane の reconciler が登録済み binding と定期
;; 照合する(登録時結合はしない — host liveness と registration を結合しない)。
;; 照合の機械面は (kind, api_version) のみ。required_field は人間可読ラベル
;; (shapes DSL は導入しない — 機械消費者不在の YAGNI 裁定、#15)。
;; ADR-DOE-AGENTS-006 R5: resume / fork capability の広告。api_version
;; (BINDING-KIND-API-VERSION)は binding 受理形の契約版なので据え置き —
;; capability は別軸の additive field(受理形が変わらないのに版を進めると
;; ACP の verifyBindingKindsOnce が偽の BindingKindUnsupported を報じる)。
(setv BINDING-KIND-RESUMABLE {"codex" True "claude-code" True})
(setv BINDING-KIND-FORKABLE {"codex" True "claude-code" True})

(deff binding-kind-advertisement []
  {:pre []
   :post [(: % list)]}
  "kinds.list の result 本体: kind 表から導出した
   [{kind agent_type required_field api_version resumable forkable}]
   (kind 昇順)。"
  (lfor kind (sorted (.keys BINDING-KIND-AGENT-TYPE))
        {"kind" kind
         "agent_type" (get BINDING-KIND-AGENT-TYPE kind)
         "required_field" (binding-kind-shape-label kind)
         "api_version" (get BINDING-KIND-API-VERSION kind)
         "resumable" (get BINDING-KIND-RESUMABLE kind)
         "forkable" (get BINDING-KIND-FORKABLE kind)}))

(deff policy-normalized-env-key [key]
  {:pre [(: key str)]
   :post [(: % str)]}
  "env key の正規化(substrate normalized-env-key と同規約: `-`→`_`・大文字化)。"
  (.upper (.replace key "-" "_")))

(deff overlay-env-offenders [session-env]
  {:pre [(: session-env dict)]
   :post [(: % list)]}
  "session_env(非 auth overlay)に居てはならない binding 所有キーの列挙。"
  (sorted (lfor key (.keys session-env)
                :if (in (policy-normalized-env-key key) BINDING-OWNED-ENV-KEYS)
                key)))

(deff binding-admission-error [binding agent-type]
  {:pre [(: binding (| dict None)) (: agent-type str)]
   :post [(: % (| str None))]}
  "wire binding の admission(ADR 0044 R3 と同思想: parse できた binding だけが
   launch に到達する)。None = 適合。文字列 = reject 理由。検査: object 形・
   既知 kind・kind↔agent_type 整合・受理形への完全一致(XOR — 混在・部分・
   未知 field はどの shape にも一致しない)・shape の全 field が非空 str。"
  (when (is binding None)
    (return None))
  (when (not (isinstance binding dict))
    (return "binding must be an object"))
  (setv kind (.get binding "kind"))
  (when (not-in kind BINDING-KIND-AGENT-TYPE)
    (return (+ f"unknown binding kind: {kind !r} "
               f"(known: {(.join ", " (sorted (.keys BINDING-KIND-AGENT-TYPE))) })")))
  (setv expected-agent-type (get BINDING-KIND-AGENT-TYPE kind))
  (when (!= agent-type expected-agent-type)
    (return (+ f"binding kind {kind !r} drives agent_type "
               f"{expected-agent-type !r}, not {agent-type !r}")))
  (setv present (- (set (.keys binding)) #{"kind"}))
  (setv matched (lfor shape (get BINDING-KIND-SHAPES kind)
                      :if (= present shape)
                      shape))
  (when (not matched)
    (setv got (if present (.join ", " (sorted present)) "none"))
    (return (+ f"binding kind {kind !r} requires exactly one field set of: "
               f"{(binding-kind-shape-label kind) } (got: {got})")))
  (for [field (sorted (get matched 0))]
    (setv value (.get binding field))
    (when (or (not (isinstance value str)) (not (.strip value)))
      (return f"binding kind {kind !r} requires a non-empty string field `{field}`")))
  None)

;; judge verdict の keys whitelist(main.rs is_allowed_unblock_key):
;; 単一英数字 or 以下の名前付きキーのみ — 制御シーケンスは決して送らない。
(setv ALLOWED-UNBLOCK-KEY-NAMES
      #{"Up" "Down" "Left" "Right" "Enter" "Escape" "Tab" "Space" "BSpace"
        "Home" "End"})

;; 1 verdict が送ってよいキー数の上限(main.rs PROMPT_JUDGE_MAX_KEYS)。
(setv PROMPT-JUDGE-MAX-KEYS 8)

;; pane の foreground がこれらへ戻ったら「agent はもう居ない」(zombie、
;; main.rs IDLE_SHELL_COMMANDS)。shell を列挙する — agent が fork する短命の
;; helper(git/gh/jq 等)を誤分類しないため、agent 側の blacklist にはしない。
(setv IDLE-SHELL-COMMANDS #{"zsh" "bash" "sh" "dash" "fish" "ksh"})


;; ===========================================================================
;; 純粋 helper(text 物理・taxonomy)
;; ===========================================================================

(deff tail-chars [text n]
  {:pre [(: text str) (: n int) (> n 0)]
   :post [(: % str)]}
  "末尾 n 文字(oracle tail_chars)。turn-end の stable 判定は 500 字 tail。"
  (cut text (- n) None))

(deff tail-lower [text max-lines]
  {:pre [(: text str) (: max-lines int) (> max-lines 0)]
   :post [(: % str)]}
  "末尾 max-lines 行を小文字連結(oracle output_tail_lower)。
   failure marker は tail 10 行・api-limit / output 写像は tail 30 行の窓。"
  (setv lines (.splitlines text))
  (.lower (.join "\n" (cut lines (max 0 (- (len lines) max-lines)) None))))

(deff parse-iso [value]
  {:pre [(: value (| str None))]
   :post [(: % "datetime | None")]}
  "ISO8601 → datetime(None-safe)。"
  (if (is None value) None (datetime.fromisoformat value)))

(deff iso-format [dt]
  {:pre [(: dt datetime)]
   :post [(: % str)]}
  "datetime → ISO8601 文字列(行に格納する時刻表現)。"
  (.isoformat dt))

(deff seconds-since [now value]
  {:pre [(: now datetime) (: value (| str None))]
   :post [(: % "float | None")]}
  "now - parse(value) の経過秒(value 無しは None)。"
  (setv then (parse-iso value))
  (if (is None then) None (.total-seconds (- now then))))

(deff is-terminal-status [status]
  {:pre [(: status str)]
   :post [(: % bool)]}
  "done / failed / exited / stopped / cancelled(main.rs is_terminal_status)。"
  (in status TERMINAL-STATUSES))

(deff is-run-to-completion [lifecycle]
  {:pre [(: lifecycle str)]
   :post [(: % bool)]}
  "RunToCompletion lifecycle か(Kind 2)。"
  (= lifecycle "run_to_completion"))

(deff reap-exempt [row]
  {:pre [(: row SessionRow)]
   :post [(: % bool)]}
  "刈り取り免除(koine session surface v0 安全条項 1 / ADR-DOE-AGENTS-007 R3)。
   reap は run_to_completion 行だけの opt-in — adopted 行(ownership marker)と
   非 run_to_completion 行(interactive、および未知 lifecycle: fail-closed —
   markerless/foreign を刈らない条文の機械面)は監督権裁定(pavo ADR 0003
   stage 3)まで無条件に刈り取り対象外。"
  (or (bool row.adopted) (not (is-run-to-completion row.lifecycle))))

(deff turn-stalled [turn-holder turn-since now threshold-seconds]
  {:pre [(: turn-holder (| str None)) (: turn-since (| str None))
         (: now datetime) (: threshold-seconds int) (> threshold-seconds 0)]
   :post [(: % bool)]}
  "liveness 導出(koine 条項 4 / ADR-DOE-AGENTS-007 R4)。stalled =
   open turn(holder='agent')のまま threshold 超過、のみ。close 済み
   (holder=user/work = WAIT 待ち)は経過時間によらず False — 待つのは
   agora の正常状態。open 打刻の欠落(holder None)も False — turn-open の
   被覆は部分的(turn-stamp-path 所見 3)なので、open/close の対を前提に
   した edge-triggered 実装は禁止。level-triggered: wire 出力のたびに
   ここで再導出し、store には決して書かない。signal only — status 不変。"
  (when (!= turn-holder "agent")
    (return False))
  (setv age (seconds-since now turn-since))
  (and (is-not age None) (> age threshold-seconds)))

(deff observed-status-from-markers [obs]
  {:pre [(: obs PaneObservation)]
   :post [(: % str) (in % #{"failed" "blocked_api" "blocked" "running"})]}
  "凍結分類順: failure → api-limit → waiting → running
   (oracle observed_status_for_snapshot)。done はここからは決して出ない —
   work-end は result-first / turn-end 検証だけが決める。"
  (cond
    obs.has-failure-marker "failed"
    obs.has-api-limit-marker "blocked_api"
    obs.has-waiting-marker "blocked"
    True "running"))

(deff event-type-for-status [status]
  {:pre [(: status str)]
   :post [(: % str)]}
  "status → 監査 event 名(oracle event_type_for_observed_status)。"
  (cond
    (= status "done") "session_done"
    (= status "failed") "session_failed"
    (in status #{"blocked" "blocked_api"}) "session_blocked"
    True "session_observed"))

(deff make-cause [category reason observed-at]
  {:pre [(: category str) (in category TERMINAL-CAUSE-CATEGORIES)
         (: reason str) (: observed-at str)]
   :post [(: % TerminalCause)]}
  "TerminalCause を凍結表から構築する — retryable は category から一意に導出。"
  (TerminalCause :category category
                 :reason reason
                 :retryable (get TERMINAL-CAUSE-RETRYABLE category)
                 :observed-at observed-at))

(deff cause-if-absent [row cause]
  {:pre [(: row SessionRow) (: cause TerminalCause)]
   :post [(: % SessionRow)]}
  "first-write-wins(oracle set_terminal_cause_if_absent + DB COALESCE)。"
  (if (is-not row.terminal-cause None)
      row
      (replace row :terminal-cause cause)))

(deff failed-output-cause [obs output observed-at]
  {:pre [(: obs PaneObservation) (: output str) (: observed-at str)]
   :post [(: % TerminalCause)]}
  "reason 無し failed 限定の output 写像(oracle set_failed_output_cause_if_absent、
   ハザード 2: last_validation_error が立つ経路では走らない)。凍結表:
   api-limit → rate_limited / tail30 に timeout・timed out・deadline → timed_out /
   authentication failed → runner_unavailable / invalid json・protocol error →
   protocol_error / その他 → run_failed。"
  (setv lower (tail-lower output 30))
  (setv category
        (cond
          obs.has-api-limit-marker "rate_limited"
          (or (in "timeout" lower) (in "timed out" lower) (in "deadline" lower))
            "timed_out"
          (in "authentication failed" lower) "runner_unavailable"
          (or (in "invalid json" lower) (in "protocol error" lower))
            "protocol_error"
          True "run_failed"))
  (setv reason (tail-chars (or (.strip output) " ") 500))
  (make-cause category
              (if (.strip reason) reason "agent output indicated failure")
              observed-at))

(deff is-allowed-unblock-key [key]
  {:pre [(: key str)]
   :post [(: % bool)]}
  "whitelist(oracle is_allowed_unblock_key): 単一英数字 or 名前付きキーのみ。"
  (if (= (len key) 1)
      (and (.isascii key) (.isalnum key))
      (in key ALLOWED-UNBLOCK-KEY-NAMES)))

(deff parse-judge-verdict [raw]
  {:pre [(: raw str)]
   :post [(: % JudgeVerdict)]}
  "strict JSON verdict {blocked, keys, reason} の parse + whitelist 検証
   (oracle parse_prompt_judge_verdict)。単一 JSON object 以外・whitelist 外の
   key・9 個以上の keys・blocked なのに keys 空、はすべて judge failure
   (ValueError)— 呼び手が R7 の点別処遇(turn-end = solicitation へ degrade /
   stall = typed failure)を決める。"
  (setv start (.find raw "{"))
  (setv end (.rfind raw "}"))
  (when (or (< start 0) (< end start))
    (raise (ValueError f"prompt judge reply contains no JSON object: {raw !r}")))
  (setv payload (json.loads (cut raw start (+ end 1))))
  (when (not (isinstance payload dict))
    (raise (ValueError "prompt judge reply is not a JSON object")))
  (setv blocked (bool (.get payload "blocked" False)))
  (setv keys (list (.get payload "keys" [])))
  (setv reason (str (.get payload "reason" "")))
  (when blocked
    (when (not keys)
      (raise (ValueError "prompt judge verdict is blocked but carries no keys")))
    (when (> (len keys) PROMPT-JUDGE-MAX-KEYS)
      (raise (ValueError
               f"prompt judge verdict carries {(len keys)} keys (max {PROMPT-JUDGE-MAX-KEYS})")))
    (for [key keys]
      (when (not (is-allowed-unblock-key key))
        (raise (ValueError f"prompt judge verdict uses disallowed key {key !r}")))))
  (JudgeVerdict :blocked blocked :keys (tuple keys) :reason reason))

(deff prompt-judge-instructions [pane]
  {:pre [(: pane str)]
   :post [(: % str)]}
  "judge へ stdin で渡す指示 + pane capture(oracle prompt_judge_instructions)。
   framing は policy 所有 — 設定される judge command は素の stdin/stdout adapter
   のままにする。"
  (+ "You are a terminal-UI judge inside an agent supervisor. Below is a tmux "
     "pane capture of a coding-agent CLI session whose screen has stopped "
     "changing. Decide whether the pane is BLOCKED on an interactive prompt "
     "(menu, confirmation dialog, pager, login prompt) that is waiting for "
     "keyboard input. If it is, produce the shortest safe key sequence that "
     "dismisses the prompt while PRESERVING current behaviour — prefer options "
     "like 'Keep current model', 'Skip', 'Not now', 'No'. A normal idle REPL "
     "prompt or ordinary scrolled output is NOT blocked.\n"
     "Respond with ONLY one JSON object, no prose:\n"
     "{\"blocked\": true, \"keys\": [\"Down\", \"Enter\"], \"reason\": \"...\"}\n"
     "Allowed key names: single letters/digits, Up, Down, Left, Right, Enter, "
     "Escape, Tab, Space, BSpace, Home, End.\n"
     "PANE CAPTURE:\n"
     pane))


;; ===========================================================================
;; kleisli helper
;; ===========================================================================

(defk run-judge [knobs output]
  {:pre [(: knobs MonitorKnobs) (: output str)]
   :post [(: % JudgeVerdict)]}
  "設定済み judge に pane capture を判定させる(oracle judge_interactive_prompt)。
   judge 未設定・非 0 終了・verdict 不正は RuntimeError = judge failure。"
  (when (not knobs.judge-cmd)
    (raise (RuntimeError "no prompt judge configured")))
  (<- res (proc-run knobs.judge-cmd (prompt-judge-instructions output)))
  (when (!= res.exit-code 0)
    (raise (RuntimeError
             f"prompt judge failed with exit {res.exit-code}: {res.stderr}")))
  (try
    (setv verdict (parse-judge-verdict res.stdout))
    (except [e ValueError]
      (raise (RuntimeError f"prompt judge verdict invalid: {e}"))))
  verdict)

(defk send-unblock-keys [pane-id keys]
  {:pre [(: pane-id str) (: keys (| list tuple))]
   :post [(: % int)]}
  "検証済み unblock keys を 1 つずつ送出する(oracle send_unblock_keys —
   キー間 pacing は substrate 所有)。"
  (for [key keys]
    (<- _ (tmux-send-keys pane-id key False False)))
  (len keys))

(defk finalize [row observed-status obs output observed-at]
  {:pre [(: row SessionRow) (: observed-status str)
         (: obs PaneObservation) (: output str) (: observed-at str)]
   :post [(: % SessionRow)]}
  "status 書き戻し + 終端処理(taxonomy first-write-wins・finished_at)+
   RTC 終端 cleanup + upsert + event(oracle monitor_once 末尾)。"
  (setv row (replace row :status observed-status))
  (when (is-terminal-status observed-status)
    (when (= observed-status "failed")
      (setv row
            (if (is-not row.last-validation-error None)
                ;; 明示カテゴリ(solicitation 超過・stall)は reason が先に
                ;; 書かれている — ここは既書き cause を尊重する保険写像のみ
                (cause-if-absent row (make-cause "run_failed"
                                                 row.last-validation-error
                                                 observed-at))
                ;; reason 無し failed のみ output 写像(ハザード 2)
                (cause-if-absent row (failed-output-cause obs output observed-at)))))
    (setv row (replace row :finished-at (or row.finished-at observed-at))))
  (when (and (is-run-to-completion row.lifecycle)
             (in observed-status #{"done" "failed"}))
    (<- still-alive (tmux-has-session row.session-name))
    (when still-alive
      (<- _ (tmux-kill-session row.session-name))
      (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event row.session-id
                                    (event-type-for-status observed-status)
                                    row))
  row)


;; ===========================================================================
;; policy program 本体
;; ===========================================================================

(defk monitor-session-once [row knobs]
  {:pre [(: row SessionRow) (: knobs MonitorKnobs)]
   :post [(: % SessionRow)]}
  "1 session の level-triggered 再導出(oracle: main.rs monitor_once の 1 行分)。
   分岐順は凍結物理: booting 所有権(launch pipeline 所有 — boot watchdog のみ)→
   stale reap → launch timeout → tmux 生存(result-first)→
   zombie → capture/classify → paste 再送 → managed dialog fast-path →
   latch clear → 分類 → turn-end → result-first → judge-before-solicitation →
   bounded solicitation → stall watchdog → 終端 taxonomy。"
  (<- now (clock-now))
  (setv observed-at (iso-format now))

  ;; --- 刈り取り免除 arm(koine 安全条項 1 / ADR-DOE-AGENTS-007 R3)。
  ;; どの terminal 化 arm よりも前(booting arm 含む)が契約 — semgrep
  ;; doeff-agents-interactive-must-not-be-terminalized が「免除判定より前に
  ;; :status terminal を書く形」を禁止する。免除行に monitor がしてよいのは
  ;; 観測の記帳のみ: last_observed_at を進めて返す(=「monitor は生きて
  ;; 評価した上で刈らなかった」の witness — S26 が assert する)。
  ;; status 遷移 / finished_at・terminal_cause 書き込み / pane kill /
  ;; solicitation はすべて禁止。pane 消滅との突合(鏡原則・条項 3)は
  ;; wire 出力時の導出(host augment-wire-snapshot)が担い、台帳は現実を
  ;; 上書き裁定しない。
  (when (reap-exempt row)
    (setv row (replace row :last-observed-at observed-at))
    (<- _ (session-store-upsert row))
    (return row))

  ;; --- booting 所有権 arm(issue agentd-session-registration-after-ready-gate):
  ;; BOOTING 行は in-flight の launch pipeline が所有する。登録が ready gate より
  ;; 前になったため、配送中の pane は launch transport の中間状態(素の shell・
  ;; 貼り付け途中の prompt)であり、以降の観測 arm は全て誤読する — zombie
  ;; reaper が「command 送出前の zsh」を exited と誤判定し、turn-end が
  ;; 半起動 agent へ solicitation を paste した(2026-07-17 e2e 実障害)。
  ;; monitor の責務は boot watchdog のみ: launch pipeline が死んで BOOTING が
  ;; 残置されたら terminal へ(daemon crash mid-launch の受け皿)。予算は
  ;; launch timeout + repl-idle 予算 — ready gate は正規に repl-idle 予算まで
  ;; 待つため、それより短いと健全な cold start を reap してしまう。
  ;; launch 完了時の手渡しは launch-session の running upsert(launch.hy)。
  (when (= row.status "booting")
    (setv boot-secs (+ knobs.launch-timeout-seconds
                       knobs.repl-idle-max-wait-seconds))
    (setv boot-age (seconds-since now row.started-at))
    (when (and (is-not boot-age None) (> boot-age boot-secs))
      (setv reason (+ f"launch timeout: launch pipeline did not complete within "
                      f"{boot-secs}s (BOOTING row left behind — launcher died "
                      "mid-launch?)"))
      (setv row (replace row
                         :status "failed"
                         :last-observed-at observed-at
                         :finished-at (or row.finished-at observed-at)
                         :last-validation-error reason))
      (setv row (cause-if-absent row (make-cause "timed_out" reason observed-at)))
      (<- _ (session-store-upsert row))
      (<- _ (session-store-record-event row.session-id "session_launch_timeout" row)))
    (return row))

  ;; --- stale-observation watchdog(S19)。どの tmux probe よりも前 —
  ;; tmux が hang しても reap は進む(oracle 実障害: 11h 沈黙の monitor)。
  (setv stale-secs knobs.stale-observation-seconds)
  (setv observation-age (seconds-since now row.last-observed-at))
  (when (and (is-not observation-age None) (> observation-age stale-secs))
    (setv row (replace row
                       :status "exited"
                       :last-observed-at observed-at
                       :finished-at (or row.finished-at observed-at)))
    (setv row (cause-if-absent
                row (make-cause "lost"
                                f"no monitor observation for more than {stale-secs}s"
                                observed-at)))
    (<- _ (session-store-upsert row))
    (<- _ (session-store-record-event row.session-id "session_stale_reaped" row))
    (return row))

  ;; --- launch-timeout watchdog(S19)。startup 完了マーカー
  ;; (observed_active_at)を一度も見ていない running だけが対象 —
  ;; startup spinner は capture を変え続けるので stale では捕まらない。
  (setv launch-secs knobs.launch-timeout-seconds)
  (when (and (= row.status "running") (is None row.observed-active-at))
    (setv startup-age (seconds-since now row.started-at))
    (when (and (is-not startup-age None) (> startup-age launch-secs))
      (setv reason (+ f"launch timeout: never reached active state within {launch-secs}s"
                      " (stuck in startup — likely a hung MCP server)"))
      (setv row (replace row
                         :status "failed"
                         :last-observed-at observed-at
                         :finished-at (or row.finished-at observed-at)
                         :last-validation-error reason))
      (setv row (cause-if-absent row (make-cause "timed_out" reason observed-at)))
      (<- _ (session-store-upsert row))
      (<- _ (session-store-record-event row.session-id "session_launch_timeout" row))
      (return row)))

  ;; --- tmux 生存確認。消えていれば result-first で done / Lost(S9)。
  (<- session-exists (tmux-has-session row.session-name))
  (when (not session-exists)
    (setv row (replace row
                       :last-observed-at observed-at
                       :finished-at observed-at))
    (setv reported None)
    (when (is-not row.expected-result None)
      (<- fresh (session-store-result-payload row.session-id))
      (setv reported fresh))
    (if (is-not reported None)
        (do
          (setv row (replace row
                             :status "done"
                             :result-payload reported
                             :last-validation-error None))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id "session_done" row)))
        (do
          (setv row (replace row :status "exited"))
          (setv row (cause-if-absent
                      row (make-cause "lost" "tmux session disappeared" observed-at)))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id "session_exited" row))))
    (return row))

  ;; --- zombie reaper(S19): pane の foreground が idle shell へ戻った。
  ;; 早期 boot の shell 瞬間と race しないよう running のみ対象。
  (when (= row.status "running")
    (<- current-command (tmux-pane-current-command row.pane-id))
    (when (and (is-not current-command None)
               (in current-command IDLE-SHELL-COMMANDS))
      (setv row (replace row
                         :status "exited"
                         :last-observed-at observed-at
                         :finished-at (or row.finished-at observed-at)))
      (setv row (cause-if-absent
                  row (make-cause "lost"
                                  f"tmux pane returned to idle shell: {current-command}"
                                  observed-at)))
      (<- _ (session-store-upsert row))
      (<- _ (session-store-record-event row.session-id "session_exited" row))
      (return row)))

  ;; --- ADR-006 R1: 会話 identity の事後発見(codex の fresh launch と
  ;; 両 kind の fork は CLI 側が identity を鋳造する)。level-triggered:
  ;; conversation 未確定の非終端行へ毎 cycle 試みる。除外集合 = store が知る
  ;; 全会話(terminal 含む — 既知の会話を新会話と誤認しない)。未発見は
  ;; そのまま続行(次 cycle 再試行)— この arm は status を変えない。
  (when (and (is None row.conversation)
             (in row.agent-type #{"claude" "codex"}))
    (<- known-ids (session-store-known-conversation-ids))
    (<- found (discover-conversation
                row.agent-type
                {"work_dir" row.work-dir
                 "effective_identity" row.effective-identity
                 "exclude_session_ids" known-ids}))
    (when (is-not found None)
      (setv row (replace row :conversation found))
      (<- _ (session-store-upsert row))
      (<- _ (session-store-record-event row.session-id
                                        "session_conversation_discovered" row))))

  ;; --- 観測: capture(100 行窓)+ kind 別 marker 事実。
  (<- output (tmux-capture row.pane-id 100))
  (<- obs (classify-pane row.agent-type output))

  ;; --- paste 残留の Enter 再送(ハザード 4 付随物理)。latch は保持。
  (when (and row.awaiting-response obs.has-unsubmitted-paste)
    (<- _ (tmux-send-keys row.pane-id "Enter" False False))
    (setv row (replace row
                       :last-observed-at observed-at
                       :output-snippet (tail-chars output 500)))
    (<- _ (session-store-upsert row))
    (<- _ (session-store-record-event row.session-id
                                      "session_unsubmitted_paste_resubmitted" row))
    (return row))

  ;; --- managed-settings dialog fast-path(R9・S18: monitor loop で発火するのは
  ;; managed のみ)。dismissal キー列は per-kind impl 所有(C2 で
  ;; PaneObservation.dialog-dismiss-keys に移設 — policy はキー物理を知らない)。
  ;; accept 後に observed_active_at を立てる。
  (when (= obs.dialog "managed")
    (assert obs.dialog-dismiss-keys
            f"ClassifyPane returned dialog={obs.dialog !r} without dismiss keys")
    (for [key obs.dialog-dismiss-keys]
      (<- _ (tmux-send-keys row.pane-id key False False)))
    (setv row (replace row
                       :last-observed-at observed-at
                       :output-snippet (tail-chars output 500)
                       :observed-active-at (or row.observed-active-at observed-at)))
    (<- _ (session-store-upsert row))
    (<- _ (session-store-record-event row.session-id "session_observed" row))
    (return row))

  ;; --- awaiting_response latch は POSITIVE work evidence(active marker /
  ;; turn-activity)でのみ clear(ハザード 4: pane 不安定では clear しない —
  ;; submit→spinner の隙間で turn-end が再武装して budget を焼いた実障害)。
  (when (and row.awaiting-response
             (or obs.has-active-marker obs.has-turn-activity))
    (setv row (replace row :awaiting-response False)))

  ;; --- startup 完了の初回観測(launch watchdog の解除信号)。
  (when (and (is None row.observed-active-at) obs.startup-finished)
    (setv row (replace row :observed-active-at observed-at)))

  ;; --- 凍結分類順: failure → api-limit → waiting → running。
  (setv raw-status (observed-status-from-markers obs))

  ;; --- turn-end 判定は snippet 書き戻しの前に(stable = 前回 500 字 tail 一致。
  ;; 後に書くと current == current に退化して毎観測 stable になる)。
  (setv stable (and (is-not row.output-snippet None)
                    (= row.output-snippet (tail-chars output 500))))
  (setv output-changed (not stable))
  (setv turn-ended (and (not row.awaiting-response)
                        obs.has-idle-prompt
                        (not obs.has-active-marker)
                        stable))
  (setv row (replace row
                     :last-observed-at observed-at
                     :output-snippet (tail-chars output 500)))
  (when (or output-changed (is None row.last-output-change-at))
    (setv row (replace row :last-output-change-at observed-at)))

  (setv observed-status raw-status)

  ;; --- result-first 終端(ADR 0035): 結果は report_result データチャネルのみ。
  ;; fresh read — 手元の行を信じない(別 connection が書く)。
  (if (and (is-run-to-completion row.lifecycle)
           (is-not row.expected-result None))
      (do
        (<- fresh (session-store-result-payload row.session-id))
        (if (is-not fresh None)
            (do
              (setv row (replace row
                                 :result-payload fresh
                                 :last-validation-error None))
              (setv observed-status "done"))
            (when turn-ended
              ;; turn-end の直前 landing を閉じる再読(oracle: sub-tick 窓)。
              (<- fresh2 (session-store-result-payload row.session-id))
              (if (is-not fresh2 None)
                  (do
                    (setv row (replace row
                                       :result-payload fresh2
                                       :last-validation-error None))
                    (setv observed-status "done"))
                  (do
                    ;; --- judge-before-solicitation(R6): codex メニューは idle
                    ;; glyph で描画される — メニューへ solicitation を貼ると Enter
                    ;; が任意の選択肢を確定する。judge failure はここでは
                    ;; solicitation へ degrade(bounded、R7)。
                    (setv unblocked False)
                    (when (and knobs.judge-cmd
                               (< row.prompt-unblock-attempts
                                  knobs.prompt-unblock-limit))
                      (setv row (replace row
                                         :prompt-unblock-attempts
                                         (+ row.prompt-unblock-attempts 1)))
                      (setv verdict None)
                      (try
                        (<- v (run-judge knobs output))
                        (setv verdict v)
                        (except [e RuntimeError]
                          ;; oracle: eprintln して solicitation へ fall through
                          (setv verdict None)))
                      (when (and (is-not verdict None) verdict.blocked)
                        (<- _ (send-unblock-keys row.pane-id verdict.keys))
                        (<- _ (session-store-upsert row))
                        (<- _ (session-store-record-event
                                row.session-id "session_prompt_unblocked" row))
                        (setv unblocked True)))
                    (when unblocked
                      (return row))
                    (if (< row.result-solicitations-used
                           knobs.result-solicitation-limit)
                        (do
                          ;; --- bounded solicitation(R1/R2): durable counter を
                          ;; 進め、latch を再武装し、non-terminal のまま次の
                          ;; turn-end を再観測する(R4: report_result が
                          ;; いつ着地しても result-first で勝つ)。
                          (setv row (replace row
                                             :result-solicitations-used
                                             (+ row.result-solicitations-used 1)))
                          (<- _ (deliver-message row.pane-id
                                                 RESULT-SOLICITATION-MESSAGE))
                          (setv row (replace row :awaiting-response True))
                          (<- _ (session-store-upsert row))
                          (<- _ (session-store-record-event
                                  row.session-id "session_result_solicited" row))
                          (return row))
                        (do
                          ;; budget 超過 → 型付き終端(S3 文言 verbatim)。
                          (setv observed-status "failed")
                          (setv reason
                                (if (= row.result-solicitations-used 0)
                                    "session reached turn-end without reporting a result via report_result"
                                    (+ "session reached turn-end without reporting a result via report_result"
                                       f" (after {row.result-solicitations-used} solicitation(s))")))
                          (setv row (replace row :last-validation-error reason))
                          (setv row (cause-if-absent
                                      row (make-cause "run_failed" reason
                                                      observed-at))))))))))
      ;; contract 無し RunToCompletion: turn-end 信号を work-end として信頼。
      (when (and turn-ended (is-run-to-completion row.lifecycle))
        (setv observed-status "done")))

  ;; --- interactive-prompt stall watchdog(R5/R7、S6/S6b): stall T 超えの
  ;; 凍結 pane(active でも idle でもない)は turn-end 検出が永遠に見えない —
  ;; bounded judge、超過/judge 不能は型付き loud failure。無限待ちは禁止。
  (setv stall-secs knobs.prompt-stall-seconds)
  (setv change-age (seconds-since now row.last-output-change-at))
  (when (and (= observed-status "running")
             (is-run-to-completion row.lifecycle)
             (not row.awaiting-response)
             (is-not row.observed-active-at None)
             (not obs.has-active-marker)
             (not obs.has-idle-prompt)
             (is-not change-age None)
             (> change-age stall-secs))
    (setv blocked-failure None)
    (if (>= row.prompt-unblock-attempts knobs.prompt-unblock-limit)
        (setv blocked-failure
              (+ f"interactive-prompt-blocked: pane unchanged for over {stall-secs}s"
                 f" and {row.prompt-unblock-attempts} unblock attempt(s) exhausted"))
        (if (not knobs.judge-cmd)
            (setv blocked-failure
                  (+ f"interactive-prompt-blocked: pane unchanged for over {stall-secs}s"
                     " and no prompt judge configured"))
            (do
              (setv row (replace row
                                 :prompt-unblock-attempts
                                 (+ row.prompt-unblock-attempts 1)))
              (setv verdict None)
              (setv judge-error None)
              (try
                (<- v (run-judge knobs output))
                (setv verdict v)
                (except [e RuntimeError]
                  (setv judge-error e)))
              (if (is-not judge-error None)
                  ;; stall 点の judge 不能: ほかに pane を動かせる経路が無い —
                  ;; 永遠に待たず型付きで failed(R7)。
                  (setv blocked-failure
                        (+ f"interactive-prompt-blocked: pane unchanged for over {stall-secs}s"
                           f" and prompt judge failed: {judge-error}"))
                  (if verdict.blocked
                      (do
                        (<- _ (send-unblock-keys row.pane-id verdict.keys))
                        (<- _ (session-store-upsert row))
                        (<- _ (session-store-record-event
                                row.session-id "session_prompt_unblocked" row))
                        (return row))
                      (do
                        ;; inconclusive も budget を消費して監視継続(R7 —
                        ;; 「blocker 無し」の verdict で永遠に park しない)。
                        (<- _ (session-store-upsert row))
                        (<- _ (session-store-record-event
                                row.session-id "session_prompt_judge_inconclusive"
                                row))
                        (return row)))))))
    (when (is-not blocked-failure None)
      (setv observed-status "failed")
      (setv row (replace row :last-validation-error blocked-failure))
      (setv row (cause-if-absent
                  row (make-cause "interactive_prompt_blocked" blocked-failure
                                  observed-at)))))

  ;; --- 書き戻し + 終端 taxonomy + cleanup + event。
  (<- final-row (finalize row observed-status obs output observed-at))
  final-row)


(defk monitor-cycle [knobs]
  {:pre [(: knobs MonitorKnobs)]
   :post [(: % dict)]}
  "monitor cycle = 非終端 session 行の一覧からの level-triggered 再導出(R1/R3)。
   per-session 隔離(S16 / DOE-004 R3): 1 session の例外は捕捉して次へ進む —
   oracle の tick 単位隔離(run_worker_tick)より細かい session 単位隔離。
   戻り値: {session-id: 処理後 status | \"error:<ExceptionType>\"}。"
  (<- rows (session-store-list-active))
  (setv outcomes {})
  (for [row (sorted rows :key (fn [r] r.session-id))]
    (try
      (<- updated (monitor-session-once row knobs))
      (setv (get outcomes row.session-id) updated.status)
      (except [e Exception]
        (setv (get outcomes row.session-id)
              f"error:{(. (type e) __name__)}"))))
  outcomes)
