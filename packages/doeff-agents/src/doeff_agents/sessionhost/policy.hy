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
  probe-conversation-activity
  session-store-list-active
  session-store-list-cleanup-pending
  session-store-result-payload
  session-store-upsert
  session-store-record-event
  session-store-known-conversation-ids
  tmux-has-session
  tmux-pane-current-command
  tmux-session-pane-ids
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
;; ADR-DOE-AGENTS-009 R3(2026-07-28): lost は表から除去 — 観測断はもう
;; terminal cause として構築されない(make-cause の precondition が拒否する)。
;; 証拠つき死亡(tmux session disappeared / pane returned to idle shell)は
;; vanished へ分割: ACP decodeSnapshotStatus は category==lost だけを
;; StatusLost(AgentUnobserved hold — ACP ADR 0067)へ写すため、vanished は
;; StatusExited として deadman gate を待たず即時終端できる。retryable=true は
;; 維持(死亡が確証された attempt の再試行は二重実装にならない)。
;; ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8(2026-08-12): 未配達 =
;; 「prompt が agent に一度も届かないまま attempt が起動段で終わった」は
;; timed_out の一形ではなく独立の分類。timed_out に混ぜている間、下流
;; (ACP)は『走ったが時間切れ』と『1 turn も走っていない』を区別できず、
;; 受入条件 (d)(prompt 未達で終端した session を「走った」と数えない)を
;; 機械で表現できなかった。retryable=true は維持 — 別の機械・別の負荷での
;; 再試行は二重実装にならない(実測: 同一 issue の再宣言で成功する例が在る)。
;; 未知 category に対する下流の既定は「CommandNonZeroExit + causeRetryable
;; 由来の retry 意味論」なので、この追加は旧 engine に対しても無害
;; (ACP Observed.hs failureKindForCause の `_` 分岐)。
(setv TERMINAL-CAUSE-RETRYABLE
      {"rate_limited" True
       "timed_out" True
       "vanished" True
       "prompt_undelivered" True
       "runner_unavailable" False
       "protocol_error" False
       "run_failed" False
       "interactive_prompt_blocked" False
       ;; ACP ADR 0049 R9 第 3 改訂(2026-08-12): provider 由来の非上限終端。
       ;; auth_failed = 再認証要求 / 組織 access 剥奪。retryable=true —— この
       ;; identity が今 provider と話せないという事実であって席の判断ではなく、
       ;; 人手の再ログイン・admin 操作・binding rotation のいずれでも解ける。
       ;; 有界 budget を焼き切った先は従来どおり人の gate だが、そこに載る名前が
       ;; 「席が結果を報告しなかった」ではなく実際の死因になる。
       "auth_failed" True
       ;; transport_failed = provider 側 5xx / 応答中断。時間で解ける典型。
       "transport_failed" True
       ;; context_exhausted = 文脈枠の枯渇。**retryable=false のまま** ——
       ;; 同じ封筒での再試行は同じ地点で必ず死ぬ(hard rule 7)。ここで直るのは
       ;; 再試行可否ではなく名前で、下流は初めて「文脈枯渇」を run_failed から
       ;; 分離して読める(ACP issue argus-attend-00738c 要件 3 の弁別軸)。
       "context_exhausted" False
       ;; host RPC(session.cancel / session.cleanup)所有 — oracle は
       ;; retryable=false を明示で渡す(:1985-1991)。
       "cancelled" False})

(setv TERMINAL-CAUSE-CATEGORIES (frozenset (.keys TERMINAL-CAUSE-RETRYABLE)))

;; provider 失敗の族名 → terminal category(ACP ADR 0049 R9 第 3 改訂)。
;; 検出(どの逐語がどの族か)の家は markers.hy、意味(族が何を意味するか)の
;; 家はここ —— F-* 契約の「marker→検出は impl 所有 / 分類は policy 所有」を
;; 族の追加でも守る。新しい族は markers に 1 行 + この表に 1 行で閉じる。
(setv PROVIDER-FAILURE-TERMINAL-CATEGORY
      {"reauth-required" "auth_failed"
       "access-revoked" "auth_failed"
       "context-exhausted" "context_exhausted"
       "transport-failure" "transport_failed"})

;; 席に帰属する既定の category(「席が正しく終われなかった」の意味を持つ)。
;; provider 失敗を観測した attempt はここへ落ちてはならない —— それが本改訂の
;; 法(provider-failure-never-defaults-to-seat-attribution)。
(setv SEAT-ATTRIBUTED-DEFAULT-CATEGORIES
      (frozenset ["run_failed" "interactive_prompt_blocked"]))


;; ===========================================================================
;; 起動段の分類(ADR-DOE-AGENTS-011)
;; ===========================================================================
;;
;; 「起動画面が読めなかった」を分類つきで残す。旧実装は 120s の予算切れを
;; 単一の固定文言(「startup is blocked by an unrecognized screen (a dialog
;; outside the R9 fast-path set?)」)で終端しており、事後にどの状態だったかを
;; 分離できなかった。実測(2026-08-11 断面・launch ready gate 終端 321 件を
;; 実物 frame で分類)では、その文言が主張する「認識不能画面」は 0 件で、
;; 294 件が shell echo だけの未描画・24 件が空画面・3 件は最終 frame に
;; idle prompt が見えている予算競合だった — 文言は全件で誤りだった。

;; 閉語彙。gate は必ずこのどれかで不成立を自己記述する(語彙外は gate error)。
(setv LAUNCH-NOT-READY-CLASSES
      #("no-output"              ;; (B) 描画ゼロ — pane に文字が 1 つも無い
        "conversation-not-found" ;; CLI が resume の会話を解決できず loud 終了
                                 ;; (2026-08-18・ADR-006 R10 第 2 層。実測
                                 ;; 2026-08-16〜17 の 91 件が no-agent-frame に
                                 ;; 誤分類されていた形)
        "no-agent-frame"         ;; (B') agent 未描画 — shell echo だけが在る
        "provider-limit-screen"  ;; 起動直後に provider 上限告知(枠切れ)
        "dialog-not-dismissed"   ;; R9 既知 dialog が予算切れまで消えなかった
        "unknown-dialog"         ;; (A) R9 fast-path の外の dialog(loud)
        "unrecognized-screen"    ;; (A) 何か描かれているがどの marker にも無い
        "mcp-boot-window"        ;; 入力欄は描かれたが MCP boot 中(loop 未配線)
        "composer-occupied"      ;; 入力欄に未送信の内容(チップ)が座っている
        "paste-not-consumed"     ;; probe を貼ったが composer が消費しない
        "composer-not-clearable" ;; probe が消えない(消去キーが効かない)
        "deadline-race"))        ;; 最終 frame では ready — 予算と描画の競合


(deff launch-not-ready-class [obs output]
  {:pre [(: obs PaneObservation) (: output str)]
   :post [(: % str) (in % LAUNCH-NOT-READY-CLASSES)]}
  "予算切れ時の最終 frame から起動段の不成立を分類する(閉語彙・情報量の
   強い順)。probe 局面で判明する 3 類(composer-occupied /
   paste-not-consumed / composer-not-clearable)は gate が局面から直接
   宣言する — この関数は『予算が切れた』側の分類だけを担う。"
  (cond
    (not (.strip output)) "no-output"
    ;; 会話解決失敗の loud 出力は shell echo だけの画面(agent 未描画)に
    ;; 現れるため、no-agent-frame より先に名を与える(後に置くと実測 91 件が
    ;; 全件 no-agent-frame に吸われる — 2026-08-16〜17 の誤分類そのもの)。
    obs.has-conversation-not-found-marker "conversation-not-found"
    (not obs.has-agent-frame) "no-agent-frame"
    ;; provider 上限は再試行の話ではない(失敗の所有者が provider 側)—
    ;; ACP ADR 0049 の failover が引き取れるよう最優先で名を与える。
    ;; 実測: 「What do you want to do? / ❯ 1. Stop and wait for limit to
    ;; reset」形の告知 dialog は稼働席の画面 283 件中 16 件に実在する。
    obs.has-api-limit-marker "provider-limit-screen"
    (is-not obs.dialog None) "dialog-not-dismissed"
    obs.dialog-shaped "unknown-dialog"
    (and obs.input-loop-wired (not obs.composer-clear)) "composer-occupied"
    obs.input-loop-wired "deadline-race"
    ;; idle prompt は在るが MCP boot 中(input loop 未配線)= 起動が終わって
    ;; いない画面。旧実装はこの窓に prompt を貼っていた。
    obs.has-idle-prompt "mcp-boot-window"
    True "unrecognized-screen"))


;; 起動段の terminal reason の書式。DB 面の分類(受入条件 (e))はこの
;; 接頭辞と class token で行う — 散文の言い換えに依存しない。
;; 既存の述語 `reason like '%launch ready gate%'` は接頭辞の中に保存されて
;; いるので、landed 前後の日次件数が同じ predicate で連続して数えられる。
(setv LAUNCH-READY-GATE-REASON-PREFIX "launch ready gate")


(deff launch-not-ready-reason [agent-type budget-seconds failure-class]
  {:pre [(: agent-type str) (: budget-seconds (| int float))
         (: failure-class str) (in failure-class LAUNCH-NOT-READY-CLASSES)]
   :post [(: % str)]}
  "起動段の terminal reason(単一の家)。形:
   `launch ready gate [<class>]: <agent> REPL did not become ready within
   <budget>s; the prompt was never delivered`。
   class は閉語彙・prompt 未配達の事実は逐語で残す(受入条件 (a)/(d))。"
  (+ f"{LAUNCH-READY-GATE-REASON-PREFIX} [{failure-class}]: "
     f"{agent-type} REPL did not become ready within {budget-seconds}s"
     "; the prompt was never delivered"))


(deff launch-not-ready-category [failure-class]
  {:pre [(: failure-class str) (in failure-class LAUNCH-NOT-READY-CLASSES)]
   :post [(: % str) (in % TERMINAL-CAUSE-CATEGORIES)]}
  "起動段の不成立 → TerminalCause category(単一の家)。
   既定は prompt_undelivered(未配達は独立の分類 —
   R-undelivered-first-class-b5e8)。provider 上限告知の画面だけは
   rate_limited へ蒸留する(行動系終端は provider-limit 観測を先に見る、
   ACP ADR 0049 R9 / policy の S8c-S8e と同型 — 未配達で括ると failover が
   引き取れず、同じ枠切れへ再試行を積む)。"
  (if (= failure-class "provider-limit-screen")
      "rate_limited"
      "prompt_undelivered"))


;; sessionhost が産む timed_out / prompt_undelivered reason の閉語彙分類。
;; 受入条件 (e) の「timeout は 1 つでなく 3 種」— category=timed_out で
;; 数えると 3 種が混入する(発注席が現に汚染を出した)。3 種の弁別知識の
;; 家はこの表 1 つで、読み手(sensor / 集計 / 事後分析)は再実装しない。
(setv SESSIONHOST-TIMEOUT-KINDS
      #(#("launch-ready-gate" LAUNCH-READY-GATE-REASON-PREFIX)
        #("launch-never-active" "launch timeout: never reached active state")
        #("launch-pipeline-incomplete" "launch timeout: launch pipeline did not complete")
        #("unsubmitted-prompt" "unsubmitted-prompt:")
        #("awaiting-response" "awaiting-response timeout:")))


(deff sessionhost-timeout-kind [reason]
  {:pre [(: reason (| str None))] :post [(: % str)]}
  "reason → 終端の種別 token(閉語彙 + \"unclassified\")。
   sessionhost が産むどの reason も unclassified に落ちてはならない
   (ADR-DOE-AGENTS-011 の deftest が全 site の実物 reason を通して検定する)。"
  (setv text (or reason ""))
  (setv kind "unclassified")
  (for [[token prefix] SESSIONHOST-TIMEOUT-KINDS]
    (when (in prefix text)
      (setv kind token)
      (break)))
  kind)


(deff format-evidence-frames [frames]
  {:pre [(: frames "value")] :post [(: % str)]}
  "保持 frame 列 → output_snippet 用の逐語ブロック(受入条件 (g): 保持数 1
   からの増量)。各 frame に gate 開始からの経過秒を付す — 『どの局面の
   画面か』が事後に置けないと複数保持の価値が出ない。"
  (setv blocks [])
  (for [frame frames]
    (.append blocks (+ f"=== frame @{frame.at-seconds :.1f}s ===\n" frame.text)))
  (.join "\n" blocks))


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

(deff counts-toward-launch-capacity [row]
  {:pre [(: row SessionRow)]
   :post [(: % bool)]}
  "launch の入場検査(max_running)の母数に数える行か(ADR-DOE-AGENTS-004
   capacity-counts-only-launch-owned-rows)。母数 = launch 所有の行 = 非
   adopted。adopt(session.adopt)は観測の登記であって容量の消費ではない
   (ADR-007 R2 — SessionHost はその substrate を作っておらず、鏡原則で
   刈りもしない。終端遷移の書き手が居ないため adopted の active 行は単調
   増加する: 全行母数は launch の恒久拒否になる — 2026-08-17 実測 32 ≥ 10)。
   絞るのは所有であって寿命ではない: launch 起点の行は lifecycle が
   interactive でも substrate を実際に消費するので数える。"
  (not (bool row.adopted)))

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
  "凍結分類順: failure → api-limit → (waiting ∧ ¬active) → running
   (oracle observed_status_for_snapshot + 2026-08-12 の作業証拠腕)。done は
   ここからは決して出ない — work-end は result-first / turn-end 検証だけが決める。

   waiting 腕は作業証拠との連言である(ACP issue 55b1bd の根治)。has-waiting-marker
   は現行 TUI では『入力待ち』を意味しない: 判定材料 7 語のうち accept edits /
   bypass permissions / shift+tab to cycle は claude の permission-mode
   インジケータ = 作業中も常時描画される常設フッターで、しかもこの marker だけ
   capture 全文一致(他は tail 30 行窓)。結果、全席が起動直後から終了まで
   blocked と記録され、その状態を『指示未配達』と読む上流の有界 dwell
   (ACP ADR 9211b3 の 1800s ヒューズ)が働いている席を例外なく終端した —
   実測 2026-08-06..12 の壁帯 218 席のうち 142 席(65%)は死の 10 秒前まで
   画面が動いており(8〜9 万トークンの実作業)、真に静止していたのは 51 席のみ。
   live active marker(claude spinner / codex working 行)が見えている観測は
   『働いている』が確定事実なので、waiting 語の同時出現は blocked の根拠に
   ならない。

   has-turn-activity(⏺ / ⎿)は意図的に見ない: あれは idle 画面にも残留する
   痕跡であって live の作業証拠ではない(markers.hy の逐語)。連言に入れると
   一度でも作業した claude 席は二度と blocked にならず、本当に固まった席
   (2026-08-05..06 の 7 席 × 22h27m)が再び不可視になる — latch clear と
   startup watchdog の用途に限る、という marker 側の契約を status 導出へ
   持ち込まない。"
  (cond
    obs.has-failure-marker "failed"
    obs.has-api-limit-marker "blocked_api"
    (and obs.has-waiting-marker (not obs.has-active-marker)) "blocked"
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

(deff action-terminal-cause [row default-category reason observed-at]
  {:pre [(: row SessionRow) (: default-category str)
         (in default-category TERMINAL-CAUSE-CATEGORIES)
         (: reason str) (: observed-at str)]
   :post [(: % TerminalCause)]}
  "行動系終端(solicitation 超過 / paste budget / awaiting timeout / stall /
   reason 無し failed)の cause 蒸留 —— **単一の家**。

   ACP ADR 0049 R9 は『行動系の終端でも pane の provider marker を先に見る』を
   定めたが、実装はこの 4 行の if/else が 5 箇所へ複写された形で、見る対象も
   provider-limit 族 1 つに固定されていた。族が 1 つ増えるたび 5 箇所を直す
   必要がある構造そのものが穴の原因である(compensator proliferation)——
   本改訂は複写を畳んで、族の追加が『markers に 1 行 + 表に 1 行』で閉じる形に
   する。

   凍結順:
     1. api-limit latch  → rate_limited(既存 S8c/S8d の意味論は不変)
     2. provider 失敗 latch → 族の category(auth_failed / transport_failed /
        context_exhausted)。ただし **既定が席帰属(run_failed /
        interactive_prompt_blocked)の時だけ** 上書きする —— 既定が既に
        provider 側/転送側を名指す transient 分類(prompt_undelivered /
        timed_out)なら、その分類が別の会計(ADR-DOE-AGENTS-011 の未配達
        第一級化など)を担っているのでそちらを尊重する。法は『provider 失敗を
        観測した attempt が席帰属の既定へ落ちない』であって『全部 provider
        名にする』ではない。
     3. それ以外 → 既定"
  (cond
    (is-not row.api-limit-observed-at None)
      (make-cause "rate_limited"
                  (+ reason
                     "; api-limit marker observed during attempt at "
                     row.api-limit-observed-at)
                  observed-at)
    (and (is-not row.provider-failure-class None)
         (in default-category SEAT-ATTRIBUTED-DEFAULT-CATEGORIES))
      (make-cause (get PROVIDER-FAILURE-TERMINAL-CATEGORY
                       row.provider-failure-class)
                  (+ reason
                     f"; provider failure [{row.provider-failure-class}]"
                     " observed during attempt at "
                     (str row.provider-failure-observed-at))
                  observed-at)
    True (make-cause default-category reason observed-at)))

(deff failed-output-cause [obs output observed-at limit-latched provider-class]
  {:pre [(: obs PaneObservation) (: output str) (: observed-at str)
         (: limit-latched bool) (: provider-class (| str None))]
   :post [(: % TerminalCause)]}
  "reason 無し failed 限定の output 写像(oracle set_failed_output_cause_if_absent、
   ハザード 2: last_validation_error が立つ経路では走らない)。凍結表:
   api-limit(終端時 marker または attempt 中の durable latch — issue #557 S8d:
   終端時 tail は racy なので観測済み事実が優先)→ rate_limited /
   tail30 に timeout・timed out・deadline → timed_out /
   authentication failed → runner_unavailable / invalid json・protocol error →
   protocol_error / provider 失敗 latch → 族の category / その他 → run_failed。

   provider 族を **既存の連鎖の最後・run_failed の直前** に置くのは意図的:
   本改訂が直すのは『席に帰属しない失敗が run_failed に落ちる』ことだけで、
   既に名前を持っている分類(timed_out / runner_unavailable / protocol_error)の
   優先順は 1 つも動かさない —— 凍結表の改訂を最小の一手に留める。"
  (setv lower (tail-lower output 30))
  (setv category
        (cond
          (or obs.has-api-limit-marker limit-latched) "rate_limited"
          (or (in "timeout" lower) (in "timed out" lower) (in "deadline" lower))
            "timed_out"
          (in "authentication failed" lower) "runner_unavailable"
          (or (in "invalid json" lower) (in "protocol error" lower))
            "protocol_error"
          (is-not provider-class None)
            (get PROVIDER-FAILURE-TERMINAL-CATEGORY provider-class)
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

(defk send-unblock-keys [row keys]
  {:pre [(: row SessionRow) (: keys (| list tuple))]
   :post [(: % bool)]}
  "検証済み unblock keys を中断キー安全壁越しに送出する(oracle
   send_unblock_keys + issue #573 の busy veto。キー間 pacing は substrate
   所有)。judge verdict は stale になりうる capture への LLM の推測で、
   許可キーには Escape(走行中 turn の不可逆な中断)を含む — 2026-07-29
   実測で 1 日の invocation の約 1 割が走行中に中断キーを受け、実装作業が
   丸ごと失われた。verdict がどうであれ、送出直前の fresh capture が明白な
   busy 証拠を示すなら送らない:

   * live active-work marker(claude spinner / codex working 行)
   * claude の queued-messages 表示(未消費 queue = turn 走行中)

   本物の blocked pane(menu / dialog / pager / login)はどちらも示さないので
   unblock 経路そのものは生き続ける。壁なしの送出プリミティブは意図的に
   存在させない(迂回経路を作らない)。戻り値 = 送出したか(False = veto)。"
  (<- latest (tmux-capture row.pane-id 100))
  (<- obs (classify-pane row.agent-type latest))
  (if (or obs.has-active-marker obs.has-queued-messages)
      False
      (do
        (for [key keys]
          (<- _ (tmux-send-keys row.pane-id key False False)))
        True)))

(defk finalize [row entry-status observed-status obs output observed-at]
  {:pre [(: row SessionRow) (: entry-status str) (: observed-status str)
         (: obs PaneObservation) (: output str) (: observed-at str)]
   :post [(: % SessionRow)]}
  "status 書き戻し + 終端処理(taxonomy first-write-wins・finished_at)+
   upsert + 遷移 event(oracle monitor_once 末尾)。

   substrate cleanup(tmux kill + cleaned_at)はここには無い(issue #568 /
   ADR-DOE-AGENTS-010 R5): 経路内蔵の片付けは 5 終端経路中 4 経路を漏らし
   残骸 26 台・6.155 GiB を堆積させた実測の根因 — 片付けは monitor-cycle 末尾の
   単一掃き取り(cleanup-terminal-session-once)が終端 status を観測して行う。

   監査 event は status 遷移(entry-status ≠ observed-status)でのみ記録する
   — oracle の毎 tick 記録からの意図的乖離(2026-07-27 wedge 根治)。blocked
   のまま静止する行が毎秒 full-snapshot event を注いで agentd.sqlite を
   1.5GB(session_blocked 251k 行 / 1.16GB)へ肥大させ、単一 StoreActor の
   per-op コスト増から socket 応答不能に至った実 incident の再発防止。
   state の更新(upsert)は level-triggered のまま — 止めるのは journal
   追記のみ。conformance(S8 ほか)は event の存在を assert する — 遷移 tick
   が必ず 1 件書くので契約は保たれる。"
  (setv row (replace row :status observed-status))
  (when (is-terminal-status observed-status)
    (when (= observed-status "failed")
      (setv row
            (if (is-not row.last-validation-error None)
                ;; 明示カテゴリ(solicitation 超過・stall)は reason が先に
                ;; 書かれている — ここは既書き cause を尊重する保険写像のみ。
                ;; 保険写像も同じ蒸留の家を通す(ACP ADR 0049 R9 第 3 改訂):
                ;; ここだけ素の run_failed を残すと、cause 未書きの経路が
                ;; 席帰属の既定へ落ちる裏口として残ってしまう。
                (cause-if-absent row (action-terminal-cause
                                       row "run_failed"
                                       row.last-validation-error
                                       observed-at))
                ;; reason 無し failed のみ output 写像(ハザード 2)
                (cause-if-absent row (failed-output-cause
                                       obs output observed-at
                                       (is-not row.api-limit-observed-at None)
                                       row.provider-failure-class)))))
    (setv row (replace row :finished-at (or row.finished-at observed-at))))
  (<- _ (session-store-upsert row))
  (when (!= observed-status entry-status)
    (<- _ (session-store-record-event row.session-id
                                      (event-type-for-status observed-status)
                                      row)))
  row)


;; ===========================================================================
;; policy program 本体
;; ===========================================================================

(defk monitor-session-once [row knobs]
  {:pre [(: row SessionRow) (: knobs MonitorKnobs)]
   :post [(: % SessionRow)]}
  "1 session の level-triggered 再導出(oracle: main.rs monitor_once の 1 行分)。
   分岐順は凍結物理: booting 所有権(launch pipeline 所有 — boot watchdog のみ)→
   観測断記帳(terminal 化しない — ADR-DOE-AGENTS-009 R1)→ launch timeout
   (watch 窓基点 = max(started_at, observation_gap_at) — 同 R2)→
   tmux 生存(result-first)→ 宛先 pane の帰属検証(ADR-DOE-AGENTS-010 R4)→
   zombie → capture/classify → 有界 paste 再送(同 R2)→
   managed dialog fast-path → latch clear → awaiting 期限(同 R3)→ 分類 →
   turn-end → result-first → judge-before-solicitation →
   bounded solicitation → stall watchdog → 終端 taxonomy。"
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  ;; 監査 event の遷移判定基準 = この cycle が store から読んだ時点の status
  ;; (finalize が edge-triggered 記録に使う)。
  (setv entry-status row.status)

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

  ;; --- 観測断 watchdog(S19c 改・ADR-DOE-AGENTS-009 R1 — 2026-07-28 契約
  ;; 改訂)。観測の途絶は観測経路(supply)の命題であり被観測対象の死亡命題
  ;; ではない — terminal 化しない(旧挙動 exited/lost は 2026-07-27 wedge で
  ;; 供給断窓の全生存 agent を死亡刻印し、実装結果を result channel ごと
  ;; 失わせた実 incident の根因)。観測断は observation_gap_at へ刻印して
  ;; journal に 1 回記帳し(検出条件が gap_at 自身で再武装されるため event
  ;; 率は 1/stale-secs に有界 — PR #564 の journal 肥大を再発させない)、
  ;; 死亡裁定は直後の第 2 証拠 arm(tmux 生存 probe / zombie reaper)に
  ;; 委ねる。probe まで不能なら行は running のまま保持(unknown)— 有界性は
  ;; engine 側 deadman gate(ACP ADR 0059)が閉じる。
  (setv stale-secs knobs.stale-observation-seconds)
  (setv observation-age (seconds-since now row.last-observed-at))
  (setv gap-age (seconds-since now row.observation-gap-at))
  (setv supply-age (cond
                     (and (is-not observation-age None) (is-not gap-age None))
                       (min observation-age gap-age)
                     (is-not gap-age None) gap-age
                     True observation-age))
  (when (and (is-not supply-age None) (> supply-age stale-secs))
    (setv row (replace row :observation-gap-at observed-at))
    (<- _ (session-store-upsert row))
    (<- _ (session-store-record-event row.session-id "session_observation_gap" row)))

  ;; --- launch-timeout watchdog(S19)。startup 完了マーカー
  ;; (observed_active_at)を一度も見ていない running だけが対象 —
  ;; startup spinner は capture を変え続けるので stale では捕まらない。
  ;; watch 窓の基点は max(started_at, observation_gap_at)(ADR-DOE-AGENTS-009
  ;; R2): 「観測し続けたのに active を見ていない」premise は観測断で void に
  ;; なる(active marker は供給断の窓で流れ去る)ため、供給回復後に改めて
  ;; launch timeout 秒の連続観測で判定する — genuinely stuck な session は
  ;; 回復後に従来どおり reap される(有界)。
  (setv launch-secs knobs.launch-timeout-seconds)
  (when (and (= row.status "running") (is None row.observed-active-at))
    (setv startup-age (seconds-since now row.started-at))
    (setv gap-watch-age (seconds-since now row.observation-gap-at))
    (when (and (is-not gap-watch-age None)
               (or (is None startup-age) (< gap-watch-age startup-age)))
      (setv startup-age gap-watch-age))
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
          ;; 第 2 証拠つき死亡(ADR-DOE-AGENTS-009 R3): tmux が応答した上での
          ;; session 不在は死亡証拠 — vanished で即時終端(観測断の lost とは
          ;; 別語彙。下流は deadman gate を待たず回収できる)。
          (setv row (replace row :status "exited"))
          (setv row (cause-if-absent
                      row (make-cause "vanished" "tmux session disappeared" observed-at)))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id "session_exited" row))))
    (return row))

  ;; --- 宛先 pane の帰属検証(issue #568 / ADR-DOE-AGENTS-010 R4 — #582 穴
  ;; a/b の根治)。pane 番号は再利用される(台帳実測 1,483 衝突)ため、
  ;; row.pane_id が row.session_name の所有 pane 集合に属することを毎 cycle
  ;; ここで一括検証する — この cycle の全下流送出(Enter 再送・dialog dismiss・
  ;; 救援キー・督促配達)と観測(capture)は検証済みの宛先にのみ行われる。
  ;; 帰属の喪失は、直前の has-session probe に応答した substrate の積極観測 =
  ;; 第 2 証拠つき死亡(ADR-DOE-AGENTS-009 R3 と同クラス)— vanished で即時終端。
  (<- session-pane-ids (tmux-session-pane-ids row.session-name))
  (when (not-in row.pane-id session-pane-ids)
    (setv row (replace row
                       :status "exited"
                       :last-observed-at observed-at
                       :finished-at (or row.finished-at observed-at)))
    (setv current-panes (if session-pane-ids (.join "," session-pane-ids) "none"))
    (setv row (cause-if-absent
                row (make-cause "vanished"
                                (+ f"tmux pane {row.pane-id} no longer belongs to "
                                   f"session {row.session-name} "
                                   f"(current panes: {current-panes})")
                                observed-at)))
    (<- _ (session-store-upsert row))
    (<- _ (session-store-record-event row.session-id "session_exited" row))
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
      ;; 第 2 証拠つき死亡(ADR-DOE-AGENTS-009 R3): pane の foreground 観測は
      ;; tmux が応答した上での積極証拠 — vanished で即時終端。
      (setv row (cause-if-absent
                  row (make-cause "vanished"
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

  ;; --- issue #557: api-limit 観測の durable latch(first-write-wins)。
  ;; 終端時 snapshot は racy(上限文言は scroll out する)— 観測した事実は
  ;; その場で行へ固定し、以降のどの upsert 経路でも永続化される。終端分類
  ;; (turn-end budget 超過 / reason 無し failed の output 写像)がこれを
  ;; 参照して rate_limited/retryable=true へ蒸留する(S8c/S8d)。
  (when (and obs.has-api-limit-marker (is None row.api-limit-observed-at))
    (setv row (replace row :api-limit-observed-at observed-at)))

  ;; --- ACP ADR 0049 R9 第 3 改訂: 上限族の外の provider 失敗も同じ理由で
  ;; durable latch する。終端時 tail が racy なのは上限文言に限らない ——
  ;; 実測 2026-08-10 wi_86957315d9157a2c は transcript に組織 access 剥奪の
  ;; 逐語が在るのに、終端 snapshot は催促文だけで埋まっていた(告知は
  ;; solicitation の貼り付けに押し流される)。first-write-wins で族名と
  ;; 初回観測時刻を対にして固定する。
  (when (and (is-not obs.provider-failure-class None)
             (is None row.provider-failure-class))
    (setv row (replace row
                       :provider-failure-class obs.provider-failure-class
                       :provider-failure-observed-at observed-at)))

  ;; --- paste / 添付残留の Enter 再送(ハザード 4 付随物理 + issue #568 /
  ;; ADR-DOE-AGENTS-010 R2 の有界化)。latch は保持。busy 証拠(live active
  ;; marker / 未消費 queue)がある間は補償しない — 走行中 turn の composer は
  ;; agent 自身が消費する(#573 の中断キー安全壁と同じ根拠で、誤補償が働く
  ;; 席を budget 超過 terminal へ導く形を塞ぐ)。budget 超過は loud typed
  ;; terminal(沈黙 blocked の禁止): 実測 2026-08-06 で無上限の再送(32 回 /
  ;; 13 回)が無効のまま沈黙し、runner 7/7 が 7 日飽和した。
  (when (and row.awaiting-response obs.has-unsubmitted-paste
             (not obs.has-active-marker) (not obs.has-queued-messages))
    (if (< row.paste-resubmit-attempts knobs.paste-resubmit-limit)
        (do
          (setv row (replace row
                             :paste-resubmit-attempts
                             (+ row.paste-resubmit-attempts 1)))
          (<- _ (tmux-send-keys row.pane-id "Enter" False False))
          (setv row (replace row
                             :last-observed-at observed-at
                             :output-snippet (tail-chars output 500)))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id
                                            "session_unsubmitted_paste_resubmitted" row))
          (return row))
        (do
          (setv reason (+ "unsubmitted-prompt: composer still holds an "
                          "unsubmitted prompt/attachment after "
                          f"{row.paste-resubmit-attempts} Enter resubmit(s)"))
          (setv row (replace row
                             :last-observed-at observed-at
                             :last-validation-error reason))
          ;; 行動系終端は provider-limit 観測を先に見る(ACP ADR 0049 R9 の
          ;; 蒸留と同型)— attempt 中の blocked_api latch は rate_limited へ。
          ;; ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8(010 R2 改訂):
          ;; 非 api-limit 側の category は timed_out から prompt_undelivered へ。
          ;; この arm が終端させる行は turn が一度も始まっていない(実測 25 件
          ;; 全数で turn-activity marker 不在)— 起動段の gate 失敗と同じ
          ;; 命題であり、2 つの category に割れていると未配達の集計が割れる。
          ;; 第 3 改訂で蒸留は単一の家(action-terminal-cause)経由になった。
          ;; prompt_undelivered は席帰属でない transient なので provider 族に
          ;; 上書きされない —— 未配達の会計(011)はそのまま保たれる。
          (setv row
                (cause-if-absent
                  row (action-terminal-cause
                        row "prompt_undelivered" reason observed-at)))
          (<- failed-row (finalize row entry-status "failed" obs output observed-at))
          (return failed-row))))

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

  ;; --- 会話記録の鮮度 probe(ADR-DOE-AGENTS-002 R-conversation-evidence):
  ;; turn 生死のデータ層証拠。pane の描画は CLI 版更新で変わる非契約面で、
  ;; 走行中の turn を描かない実物(claude CLI 2.1.234、2026-08-18 に 22 件が
  ;; 走行中のまま約 20 秒で run_failed/deterministic に焼かれた)が観測されて
  ;; いる — record が書かれている限り turn は生きている。識別素材が揃う
  ;; kind の行のみ probe し(discover arm と同じ kind 集合)、None は従来の
  ;; 表示層物理へ fallback(退行ゼロ)。
  (setv conversation-activity-at None)
  (when (and (is-not row.conversation None)
             (in row.agent-type #{"claude" "codex"}))
    (<- probed (probe-conversation-activity
                 row.agent-type
                 {"work_dir" row.work-dir
                  "effective_identity" row.effective-identity
                  "conversation" row.conversation}))
    (setv conversation-activity-at probed))
  ;; 鮮度窓内の更新 = turn 走行中(turn-end の反証)。
  (setv conversation-fresh
        (and (is-not conversation-activity-at None)
             (< (- (.timestamp now) conversation-activity-at)
                knobs.conversation-quiescence-seconds)))
  ;; 配送後の進行 = turn が始まった証拠(awaiting 解除)。margin は配送
  ;; そのもの(prompt / solicitation の queue 書き込み)が記録の mtime を
  ;; 動かす分の余白 — 基点は awaiting 期限(R3)と同じ fallback。
  (setv awaiting-anchor (parse-iso (or row.awaiting-response-since
                                       row.started-at)))
  (setv conversation-progressed
        (and (is-not conversation-activity-at None)
             (is-not awaiting-anchor None)
             (> conversation-activity-at
                (+ (.timestamp awaiting-anchor)
                   knobs.conversation-progress-margin-seconds))))

  ;; --- awaiting_response latch は POSITIVE work evidence(active marker /
  ;; turn-activity / 会話記録の配送後進行)でのみ clear(ハザード 4: pane
  ;; 不安定では clear しない — submit→spinner の隙間で turn-end が再武装して
  ;; budget を焼いた実障害)。期限の基点(awaiting_response_since)も latch と
  ;; 同時に clear する(ADR-DOE-AGENTS-010 R3)。
  (when (and row.awaiting-response
             (or obs.has-active-marker obs.has-turn-activity
                 conversation-progressed))
    (setv row (replace row :awaiting-response False
                       :awaiting-response-since None)))

  ;; --- startup 完了の初回観測(launch watchdog の解除信号)。
  (when (and (is None row.observed-active-at) obs.startup-finished)
    (setv row (replace row :observed-active-at observed-at)))

  ;; --- awaiting latch の期限(issue #568 / ADR-DOE-AGENTS-010 R3 — #582 穴 c
  ;; の根治)。『agent への prompt が owed』は期限つきの命題: 正の作業証拠が
  ;; 期限内に一度も観測されなければ typed terminal — awaiting_response が
  ;; stall watchdog と turn-end 検出を無効化したまま永久 blocked に沈む形
  ;; (2026-08-06 実測: 7 席 × 14.7〜44.7h、後続 66 件が 7 日停止)を構造的に
  ;; 禁止する。基点 = max(awaiting_response_since | started_at,
  ;; observation_gap_at) — 観測断の窓は『観測し続けたのに証拠が無い』premise
  ;; を void にする(ADR-DOE-AGENTS-009 R2 と同型)。
  (when row.awaiting-response
    (setv awaiting-secs knobs.awaiting-response-timeout-seconds)
    (setv armed-age (seconds-since now (or row.awaiting-response-since
                                           row.started-at)))
    (setv gap-arm-age (seconds-since now row.observation-gap-at))
    (setv wait-age (if (and (is-not armed-age None) (is-not gap-arm-age None))
                       (min armed-age gap-arm-age)
                       armed-age))
    (when (and (is-not wait-age None) (> wait-age awaiting-secs))
      (setv reason (+ "awaiting-response timeout: prompt/solicitation was "
                      "delivered but no work evidence appeared within "
                      f"{awaiting-secs}s (turn never started)"))
      (setv row (replace row :last-validation-error reason))
      ;; 行動系終端は provider 観測を先に見る(S8c/S8e と同じ蒸留 —— 第 3
      ;; 改訂で単一の家 action-terminal-cause 経由)。timed_out は席帰属でない
      ;; transient なので provider 族に上書きされない。
      (setv row
            (cause-if-absent
              row (action-terminal-cause row "timed_out" reason observed-at)))
      (<- timed-row (finalize row entry-status "failed" obs output observed-at))
      (return timed-row)))

  ;; --- 凍結分類順: failure → api-limit → waiting → running。
  (setv raw-status (observed-status-from-markers obs))

  ;; --- turn-end 判定は snippet 書き戻しの前に(stable = 前回 500 字 tail 一致。
  ;; 後に書くと current == current に退化して毎観測 stable になる)。
  (setv stable (and (is-not row.output-snippet None)
                    (= row.output-snippet (tail-chars output 500))))
  (setv output-changed (not stable))
  ;; turn-end は表示層(idle ∧ ¬active ∧ stable)とデータ層の連言で確定する
  ;; (ADR-002 R-conversation-evidence / 宣言 sessionhost-liveness-conjunction
  ;; の同法理: 壊れやすい marker 物理 1 枚に自動終端の権限を預けない):
  ;; - 会話記録が鮮度窓内に更新されている間は turn 走行中 — 宣言しない。
  ;; - 自分が送った催促が composer に未消費で座っている(queued messages =
  ;;   markers.hy が「turn 走行中の明白な busy 証拠」と定義する状態)間は
  ;;   「催促に応えなかった」は成立しない — 宣言しない。
  (setv turn-ended (and (not row.awaiting-response)
                        obs.has-idle-prompt
                        (not obs.has-active-marker)
                        (not obs.has-queued-messages)
                        stable
                        (not conversation-fresh)))
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
                        ;; issue #573: 送出は安全壁越し。veto = 送出直前の
                        ;; fresh capture に busy 証拠 — turn-end 読み自体が
                        ;; stale だったので solicitation も走らせず、次 cycle
                        ;; の再観測に委ねる。
                        (<- sent (send-unblock-keys row verdict.keys))
                        (<- _ (session-store-upsert row))
                        (<- _ (session-store-record-event
                                row.session-id
                                (if sent
                                    "session_prompt_unblocked"
                                    "session_prompt_unblock_vetoed")
                                row))
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
                          ;; 再武装は期限の基点(ADR-DOE-AGENTS-010 R3)も
                          ;; 再打刻する — 促しごとに有界の待ちが始まる。
                          (setv row (replace row
                                             :awaiting-response True
                                             :awaiting-response-since observed-at))
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
                          ;; issue #557(S8c)+ ACP ADR 0049 R9 第 3 改訂:
                          ;; attempt 中に観測した provider 事実(上限 latch /
                          ;; 認証断・transport・文脈枯渇 latch)を先に見る。
                          ;; どちらも無い時だけ run_failed(S3)—— これが
                          ;; 「席が結果を報告しなかった」を主張してよい唯一の
                          ;; 断面である。ledger-integrity-steward の 13 時間
                          ;; 停止(2026-08-12)はこの arm が認証断を run_failed と
                          ;; 呼んだために起きた。
                          (setv row
                                (cause-if-absent
                                  row (action-terminal-cause
                                        row "run_failed" reason
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
                        ;; issue #573: 送出は安全壁越し。veto = stall 読みと
                        ;; 送出の間に pane が動き出した — typed failure にも
                        ;; 落とさず次 cycle の再観測に委ねる。
                        (<- sent (send-unblock-keys row verdict.keys))
                        (<- _ (session-store-upsert row))
                        (<- _ (session-store-record-event
                                row.session-id
                                (if sent
                                    "session_prompt_unblocked"
                                    "session_prompt_unblock_vetoed")
                                row))
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
      ;; ACP ADR 0049 R9(S8e): 行動系終端は provider-limit 観測を先に見る —
      ;; attempt 中に blocked_api を観測済み(durable latch、issue #557)なら
      ;; interactive_prompt_blocked でなく rate_limited/retryable=true
      ;; (上限下の凍結 pane は transient — ACP rotation/exhaustion の発火面)。
      ;; 生 marker の同時成立は分類順(api-limit → blocked_api ≠ running)に
      ;; より stall arm に到達しない — latch が唯一の到達形。第 3 改訂で
      ;; provider 失敗 latch も同じ家から見る(認証断で凍った pane を
      ;; interactive_prompt_blocked = 席帰属の確定的失敗と呼ばない)。
      (setv row
            (cause-if-absent
              row (action-terminal-cause
                    row "interactive_prompt_blocked" blocked-failure
                    observed-at)))))

  ;; --- 書き戻し + 終端 taxonomy + 遷移 event(cleanup は monitor-cycle の
  ;; 単一掃き取り所有 — ADR-DOE-AGENTS-010 R5)。
  (<- final-row (finalize row entry-status observed-status obs output observed-at))
  final-row)


(defk cleanup-terminal-session-once [row active-names]
  {:pre [(: row SessionRow) (: active-names set)]
   :post [(: % SessionRow)]}
  "終端 1 行の substrate cleanup(issue #568 / ADR-DOE-AGENTS-010 R5)。
   tmux session が生きていれば kill して session_cleaned event を記帳する。
   ただし active 行が同名を主張しているとき(session 名は呼び手採番で時間軸上
   再利用され得る)は kill しない — その tmux session はもうこの行のもの
   ではない(古い残骸行の名で生きている新席を殺さない)。いずれの経路でも
   cleaned_at を刻む(= 掃き取りが『残骸なし』を確認した witness — 対象集合が
   有界に収束し、台帳の古い終端行を毎 cycle 再走査しない)。"
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  (setv killed False)
  (when (not-in row.session-name active-names)
    (<- alive (tmux-has-session row.session-name))
    (when alive
      (<- _ (tmux-kill-session row.session-name))
      (setv killed True)))
  (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))
  (<- _ (session-store-upsert row))
  (when killed
    (<- _ (session-store-record-event row.session-id "session_cleaned" row)))
  row)


(defk monitor-cycle [knobs]
  {:pre [(: knobs MonitorKnobs)]
   :post [(: % dict)]}
  "monitor cycle = 非終端 session 行の一覧からの level-triggered 再導出(R1/R3)
   + 終端 session の単一掃き取り(issue #568 / ADR-DOE-AGENTS-010 R5)。
   per-session 隔離(S16 / DOE-004 R3): 1 session の例外は捕捉して次へ進む —
   oracle の tick 単位隔離(run_worker_tick)より細かい session 単位隔離。
   戻り値: {session-id: 処理後 status | \"error:<ExceptionType>\"}。

   掃き取りは終端 status という end-state を観測する宣言的 reconciler —
   終端経路ごとの片付け命令は存在しない(semgrep
   doeff-agents-terminal-cleanup-single-sweep が旧形を禁止する)。この cycle で
   終端した行も、過去のどの経路で終端した既存残骸も、同じ 1 本で回収される。
   active 名の集合は loop の後に再読する — この cycle で終端した行の名を
   active と誤認して kill を skip しない。"
  (<- rows (session-store-list-active))
  (setv outcomes {})
  (for [row (sorted rows :key (fn [r] r.session-id))]
    (try
      (<- updated (monitor-session-once row knobs))
      (setv (get outcomes row.session-id) updated.status)
      (except [e Exception]
        (setv (get outcomes row.session-id)
              f"error:{(. (type e) __name__)}"))))
  ;; --- 終端 session の単一掃き取り(R5)。
  (<- pending (session-store-list-cleanup-pending))
  (when pending
    (<- still-active (session-store-list-active))
    (setv active-names (sfor r still-active r.session-name))
    (for [row (sorted pending :key (fn [r] r.session-id))]
      ;; 防御の二重化(fail-closed): 対象集合の定義(store 側 filter)が
      ;; 刈り取り免除を落としても、program 側でも免除行には触れない。
      (when (not (reap-exempt row))
        (try
          (<- _ (cleanup-terminal-session-once row active-names))
          (except [e Exception]
            (setv (get outcomes f"cleanup:{row.session-id}")
                  f"error:{(. (type e) __name__)}"))))))
  outcomes)
