;;; session-host effect 語彙(ADR-DOE-AGENTS-004 R1、C1)。
;;;
;;; 署名と docstring が契約。凍結物理の出典は
;;; packages/doeff-agents/conformance/README.md(CONTRACT FIXED 2026-07-05)と
;;; oracle = packages/doeff-agentd/src/main.rs。
;;;
;;; 二層構造(R1/R2):
;;;   - interface effects(BuildLaunch / PreLaunchSetup / ClassifyPane /
;;;     DeliverMessage / WireResultChannel)— kind 別の protocol 物理。
;;;     per-kind defhandler モジュール(C2、packages/doeff-agents/impls/)が解釈する。
;;;   - substrate effects(SessionStore* / Tmux* / Clock* / Proc*)— 寿命の外部性を
;;;     持つ側(agentd host / 直接束縛の fake)が解釈する生 IO の境界。
;;;     impls/ は substrate effect を yield するのみ(substrate-clean defsemgrep が守る)。
;;;
;;; 各 effect の deff 構築子が契約面: :pre が引数型を fail-fast 検査し、
;;; docstring が凍結物理(F-* マーカー・TerminalCause 表・knob 表)への束縛を書く。

(require doeff-hy.macros [deff])

(import dataclasses [dataclass])
(import datetime [datetime])
(import typing [Any])
(import doeff [EffectBase])


;; ===========================================================================
;; データ型(契約の語彙)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] TerminalCause []
  "終端理由。category / retryable は conformance README『TerminalCause 凍結表』の
   契約所有 — 変更は ADR 改訂が先(黙った変更は conformance red)。
   first-write-wins(set_terminal_cause_if_absent + DB COALESCE)も契約。"
  #^ str category
  #^ (| str None) reason
  #^ bool retryable
  #^ str observed-at)


(defclass [(dataclass :frozen True :kw-only True)] SessionRow []
  "agent_sessions 行の policy 可視部分。真実は行のみ(DOE-004 R3:
   truth-is-rows-not-continuations)— monitor は毎 cycle この行から再導出する。"
  #^ str session-id
  #^ str session-name
  #^ str pane-id
  #^ str agent-type
  #^ str lifecycle
  #^ str status
  #^ str started-at
  #^ (| str None) last-observed-at
  (setv last-observed-at None)
  #^ (| str None) finished-at
  (setv finished-at None)
  #^ (| str None) cleaned-at
  (setv cleaned-at None)
  #^ (| str None) output-snippet
  (setv output-snippet None)
  #^ (| str None) last-output-change-at
  (setv last-output-change-at None)
  #^ bool awaiting-response
  (setv awaiting-response False)
  #^ (| str None) observed-active-at
  (setv observed-active-at None)
  #^ Any expected-result
  (setv expected-result None)
  #^ (| str None) result-payload
  (setv result-payload None)
  #^ (| str None) last-validation-error
  (setv last-validation-error None)
  #^ int result-solicitations-used
  (setv result-solicitations-used 0)
  #^ int prompt-unblock-attempts
  (setv prompt-unblock-attempts 0)
  #^ (| TerminalCause None) terminal-cause
  (setv terminal-cause None))


(defclass [(dataclass :frozen True :kw-only True)] PaneObservation []
  "ClassifyPane の結果 = pane capture の marker 事実。marker→検出は impl 所有
   (F-* 表: F-idle-codex `› ` / F-idle-claude `❯` / F-active-codex `working (`
   or `esc to interrupt` / F-active-claude `… (` / F-turn-activity `⏺` `⎿` /
   F-failed tail10 / F-api-limit tail30 / F-waiting raw 一致)。
   分類順(failure → api-limit → waiting → running)は policy 所有。"
  #^ bool has-failure-marker
  (setv has-failure-marker False)
  #^ bool has-api-limit-marker
  (setv has-api-limit-marker False)
  #^ bool has-waiting-marker
  (setv has-waiting-marker False)
  #^ bool has-idle-prompt
  (setv has-idle-prompt False)
  #^ bool has-active-marker
  (setv has-active-marker False)
  #^ bool has-turn-activity
  (setv has-turn-activity False)
  #^ bool startup-finished
  (setv startup-finished False)
  #^ bool has-unsubmitted-paste
  (setv has-unsubmitted-paste False)
  #^ (| str None) dialog
  (setv dialog None)
  ;; dialog の決定的 dismissal キー列(R9 fast-path、S18 で Rust detector と
  ;; verbatim 一致まで確定した物理)。dialog 検出と同じく per-kind impl 所有 —
  ;; policy はこの keys を送るだけで、per-kind のキー物理を知らない
  ;; (protocol-physics-has-one-home)。C2 で追加(oracle:
  ;; dismiss_codex_update_dialog の selected-option 依存 Down 数 /
  ;; bypass・fullscreen = Down,Enter / managed = Enter)。
  #^ Any dialog-dismiss-keys
  (setv dialog-dismiss-keys #()))


(defclass [(dataclass :frozen True :kw-only True)] JudgeVerdict []
  "prompt judge の strict JSON verdict {blocked, keys, reason}
   (main.rs PromptJudgeVerdict)。keys は whitelist 内のみ・最大 8。"
  #^ bool blocked
  #^ Any keys
  (setv keys #())
  #^ str reason
  (setv reason ""))


(defclass [(dataclass :frozen True :kw-only True)] ProcResult []
  "Proc substrate の完了結果(sh -c 同等)。exit-code != 0 は raise ではなく
   値 — 解釈(judge error 等)は policy 所有。"
  #^ int exit-code
  #^ str stdout
  (setv stdout "")
  #^ str stderr
  (setv stderr ""))


(defclass [(dataclass :frozen True :kw-only True)] MonitorKnobs []
  "testability knob 表(conformance README、契約凍結値):
   stall T 180s / solicitation budget 2 / unblock budget 3 /
   launch timeout 60s / stale-observation 300s。
   judge-cmd None = judge 無効 — 既定で無効(ハザード 1: 既定 judge が実 claude を
   起動する事故の防止。conformance 実行時も同じ既定を維持する)。"
  #^ int prompt-stall-seconds
  (setv prompt-stall-seconds 180)
  #^ int result-solicitation-limit
  (setv result-solicitation-limit 2)
  #^ int prompt-unblock-limit
  (setv prompt-unblock-limit 3)
  #^ int launch-timeout-seconds
  (setv launch-timeout-seconds 60)
  #^ int stale-observation-seconds
  (setv stale-observation-seconds 300)
  #^ (| str None) judge-cmd
  (setv judge-cmd None))


;; ===========================================================================
;; interface effects(kind 別 protocol 物理 — per-kind defhandler が解釈)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] BuildLaunch [EffectBase]
  "kind の起動 argv を組み立てる。戻り値: list[str]。
   凍結配線(S13 / 49b3549b 傷跡): claude は
   `--settings {\"disableAllHooks\":true}` + `--mcp-config`(doeff_result stdio)+
   `--strict-mcp-config`、codex は `-c mcp_servers.\"doeff_result\".command=` /
   `.args=[...]`。prompt は argv に載せない(live terminal transport のみ)。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] PreLaunchSetup [EffectBase]
  "tmux 起動前の kind 別 home / trust 準備。凍結物理(S11/S12 / DOE-003 R1/R3 /
   42fb28fa 傷跡): codex は CODEX_HOME 必須 — 無ければ tmux 呼び出し前に fail
   (tmux 痕跡ゼロ・session 行無し)。claude は CLAUDE_CONFIG_DIR 無しは warning のみ、
   `<CLAUDE_CONFIG_DIR>/.claude.json` に canonicalized work_dir の
   `hasTrustDialogAccepted=true` を temp+rename で pre-seed する。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] ClassifyPane [EffectBase]
  "pane capture(tail 100 行)を kind 別 marker で観測する。戻り値: PaneObservation。
   marker は lowercase tail の部分文字列一致(oracle main.rs:2775-3229、F-* 表)。
   分類の順序・意味づけは policy 所有 — この effect は事実だけを返す。"
  #^ str agent-type
  #^ str output)

(defclass [(dataclass :frozen True :kw-only True)] DeliverMessage [EffectBase]
  "live REPL へメッセージを paste + submit する(prompt / solicitation の配送路)。
   凍結物理(ハザード 4): 配送は同期 confirm ループを含み、その間 monitor は
   session を観測できない(盲窓)。tty echo の paste 残留で confirm が Enter を
   再送し得る — 受信側はこれに耐えること。"
  #^ str pane-id
  #^ str text)

(defclass [(dataclass :frozen True :kw-only True)] WireResultChannel [EffectBase]
  "kind の result 報告チャネル(report-result-mcp)を session に配線する。
   凍結物理(S1/S13 / main.rs:1306 mcp_command_args):
   `$DOEFF_AGENTD_BIN report-result-mcp --session $DOEFF_RESULT_SESSION_ID
   --socket $DOEFF_AGENTD_SOCKET` と同物理。結果は常にこのデータチャネル経由 —
   pane を result として parse することは禁止(result-first、ADR 0035)。"
  #^ str agent-type
  #^ str session-id
  #^ str socket-path)


;; ===========================================================================
;; substrate effects(SessionStore / Tmux / Clock / Proc — DOE-004 R1)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreListActive [EffectBase]
  "非終端 session 行の一覧(active_statuses = pending / booting / running /
   blocked / blocked_api、main.rs:1931)。戻り値: list[SessionRow]。
   monitor cycle はこの level-triggered 再読からのみ駆動される(R3)。"
  )

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreGet [EffectBase]
  "session 行の単読。戻り値: SessionRow | None。"
  #^ str session-id)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreUpsert [EffectBase]
  "session 行の書き戻し(単一 writer、R3)。COALESCE 規律(main.rs:2339):
   永続化済み result_payload_json を upsert が消すことは禁止。戻り値: None。"
  #^ SessionRow row)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreResultPayload [EffectBase]
  "result_payload_json の fresh read。report_result は別 connection で書くため、
   monitor は手元の行を信じず毎回この読みで result-first を判定する
   (main.rs current_result_payload)。戻り値: str | None(byte-faithful payload)。"
  #^ str session-id)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreRecordEvent [EffectBase]
  "監査 event の追記(session_done / session_failed / session_blocked /
   session_observed / session_result_solicited / session_prompt_unblocked /
   session_prompt_judge_inconclusive / session_stale_reaped /
   session_launch_timeout / session_exited / session_unsubmitted_paste_resubmitted)。
   戻り値: None。"
  #^ str session-id
  #^ str event-type
  #^ SessionRow row)

(defclass [(dataclass :frozen True :kw-only True)] TmuxHasSession [EffectBase]
  "tmux session の生存確認。戻り値: bool。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] TmuxPaneCurrentCommand [EffectBase]
  "pane の foreground command 名。戻り値: str | None。zombie 判定
   (idle shell 列挙: zsh/bash/sh/dash/fish/ksh)は policy 所有。"
  #^ str pane-id)

(defclass [(dataclass :frozen True :kw-only True)] TmuxCapture [EffectBase]
  "pane の末尾 LINES 行を capture する(monitor は 100 行窓)。戻り値: str。
   turn-end の stable 判定は 500 字 tail の前回一致(main.rs:2832/2932)。"
  #^ str pane-id
  #^ int lines
  (setv lines 100))

(defclass [(dataclass :frozen True :kw-only True)] TmuxSendKeys [EffectBase]
  "pane へのキー送出。literal=True はテキスト paste、submit=True は Enter 付き。
   literal=False はキー名(Up/Down/Enter/Escape 等)。戻り値: None。"
  #^ str pane-id
  #^ str text
  #^ bool literal
  #^ bool submit)

(defclass [(dataclass :frozen True :kw-only True)] TmuxKillSession [EffectBase]
  "tmux session の破棄(RunToCompletion の done/failed cleanup)。戻り値: None。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] ClockNow [EffectBase]
  "現在時刻(timezone-aware UTC datetime)。watchdog(stall 180s / launch 60s /
   stale 300s)の時間算術はすべてこの effect 経由 — policy は wall clock を
   直接読まない。戻り値: datetime。"
  )

(defclass [(dataclass :frozen True :kw-only True)] ProcRun [EffectBase]
  "子 process の実行(sh -c 同等、stdin 供給、wall-clock cap は substrate 所有)。
   戻り値: ProcResult — exit-code != 0 も値として返す(raise しない)。
   monitor policy では prompt judge の実行にのみ使う。"
  #^ str command
  #^ (| str None) stdin
  (setv stdin None))


;; --- C2 拡張(DOE-004 R1 改訂と同一チェンジセット): launch 経路と per-kind
;; trust 物理(S11/S12)が要求する substrate。impls/ は生 IO を持てない
;; (substrate-clean defsemgrep)ため、FS / env / session 作成もすべて effect 境界。

(defclass [(dataclass :frozen True :kw-only True)] TmuxNewSession [EffectBase]
  "detached tmux session の作成(oracle tmux_new_session: new-session -d -P -F #D、
   -c work-dir、session_env を -e で注入)。戻り値: pane-id str。
   禁止 env(ANTHROPIC_API_KEY*)の hard reject は substrate 所有(oracle
   ensure_no_forbidden_agent_env — C0 README 実装メモの凍結物理)。"
  #^ str session-name
  #^ str work-dir
  #^ dict env)

(defclass [(dataclass :frozen True :kw-only True)] FsCanonicalPath [EffectBase]
  "path の canonicalize(realpath。S12: claude trust は canonicalized work_dir を
   project key にする — /tmp は macOS で /private/tmp)。解決不能なら入力を
   そのまま返す(oracle: canonicalize(...).unwrap_or(work_dir))。戻り値: str。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsReadText [EffectBase]
  "テキストファイルの読み。不存在は None(oracle: exists 分岐)。戻り値: str | None。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsWriteTextAtomic [EffectBase]
  "temp+rename の原子的書き込み(S12: 並走 claude が torn state を読まない・
   temp 残骸を残さない)。substrate は path + tmp-suffix の一時ファイルに書いて
   rename する。戻り値: None。"
  #^ str path
  #^ str text
  #^ str tmp-suffix
  (setv tmp-suffix ".agentd-tmp"))

(defclass [(dataclass :frozen True :kw-only True)] FsMakeDirs [EffectBase]
  "ディレクトリの再帰作成(exist-ok。oracle: fs::create_dir_all)。戻り値: None。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] EnvGet [EffectBase]
  "呼び手 process env の単読(S11 caveat: trust writer は session_env に無い
   home を process env から fallback 参照する — daemon 束縛では daemon env、
   直接束縛では呼び手 env)。戻り値: str | None。"
  #^ str name)

(defclass [(dataclass :frozen True :kw-only True)] ClockSleep [EffectBase]
  "実時間待ち(wait-for-repl-idle の poll 間隔・dialog 再描画待ち)。
   時間算術が ClockNow 経由であるのと同じく、待ちも effect 経由 — program は
   wall clock に直接触れない。戻り値: None。"
  #^ float seconds)


;; ===========================================================================
;; deff 構築子(署名 = 契約面)
;; ===========================================================================

(deff build-launch [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % BuildLaunch)]}
  "BuildLaunch を構築する。kind 追加 = per-kind defhandler モジュール 1 個 +
   kind スキーマ + conformance green(R2)— Rust agentd への物理追加は禁止。"
  (BuildLaunch :agent-type agent-type :params params))

(deff pre-launch-setup [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % PreLaunchSetup)]}
  "PreLaunchSetup を構築する(S11/S12 の trust / home 物理)。"
  (PreLaunchSetup :agent-type agent-type :params params))

(deff classify-pane [agent-type output]
  {:pre [(: agent-type str) (: output str)]
   :post [(: % ClassifyPane)]}
  "ClassifyPane を構築する(F-* marker 事実の観測)。"
  (ClassifyPane :agent-type agent-type :output output))

(deff deliver-message [pane-id text]
  {:pre [(: pane-id str) (: text str) (> (len text) 0)]
   :post [(: % DeliverMessage)]}
  "DeliverMessage を構築する(live REPL への paste + submit)。"
  (DeliverMessage :pane-id pane-id :text text))

(deff wire-result-channel [agent-type session-id socket-path]
  {:pre [(: agent-type str) (: session-id str) (: socket-path str)]
   :post [(: % WireResultChannel)]}
  "WireResultChannel を構築する(report-result-mcp 配線)。"
  (WireResultChannel :agent-type agent-type :session-id session-id
                     :socket-path socket-path))

(deff session-store-list-active []
  {:pre [True]
   :post [(: % SessionStoreListActive)]}
  "SessionStoreListActive を構築する(active_statuses の level-triggered 再読)。"
  (SessionStoreListActive))

(deff session-store-get [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionStoreGet)]}
  "SessionStoreGet を構築する。"
  (SessionStoreGet :session-id session-id))

(deff session-store-upsert [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionStoreUpsert)]}
  "SessionStoreUpsert を構築する(単一 writer・COALESCE 規律)。"
  (SessionStoreUpsert :row row))

(deff session-store-result-payload [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionStoreResultPayload)]}
  "SessionStoreResultPayload を構築する(result-first の fresh read)。"
  (SessionStoreResultPayload :session-id session-id))

(deff session-store-record-event [session-id event-type row]
  {:pre [(: session-id str) (: event-type str) (: row SessionRow)]
   :post [(: % SessionStoreRecordEvent)]}
  "SessionStoreRecordEvent を構築する(監査 event 追記)。"
  (SessionStoreRecordEvent :session-id session-id :event-type event-type :row row))

(deff tmux-has-session [session-name]
  {:pre [(: session-name str)]
   :post [(: % TmuxHasSession)]}
  "TmuxHasSession を構築する。"
  (TmuxHasSession :session-name session-name))

(deff tmux-pane-current-command [pane-id]
  {:pre [(: pane-id str)]
   :post [(: % TmuxPaneCurrentCommand)]}
  "TmuxPaneCurrentCommand を構築する(zombie 判定の観測面)。"
  (TmuxPaneCurrentCommand :pane-id pane-id))

(deff tmux-capture [pane-id lines]
  {:pre [(: pane-id str) (: lines int) (> lines 0)]
   :post [(: % TmuxCapture)]}
  "TmuxCapture を構築する(monitor は 100 行窓)。"
  (TmuxCapture :pane-id pane-id :lines lines))

(deff tmux-send-keys [pane-id text literal submit]
  {:pre [(: pane-id str) (: text str) (: literal bool) (: submit bool)]
   :post [(: % TmuxSendKeys)]}
  "TmuxSendKeys を構築する(unblock keys / dialog dismiss / Enter 再送)。"
  (TmuxSendKeys :pane-id pane-id :text text :literal literal :submit submit))

(deff tmux-kill-session [session-name]
  {:pre [(: session-name str)]
   :post [(: % TmuxKillSession)]}
  "TmuxKillSession を構築する(RTC 終端 cleanup)。"
  (TmuxKillSession :session-name session-name))

(deff clock-now []
  {:pre [True]
   :post [(: % ClockNow)]}
  "ClockNow を構築する(watchdog 時間算術の唯一の時刻源)。"
  (ClockNow))

(deff proc-run [command stdin]
  {:pre [(: command str) (> (len command) 0) (: stdin (| str None))]
   :post [(: % ProcRun)]}
  "ProcRun を構築する(prompt judge 実行)。"
  (ProcRun :command command :stdin stdin))

(deff tmux-new-session [session-name work-dir env]
  {:pre [(: session-name str) (: work-dir str) (: env dict)]
   :post [(: % TmuxNewSession)]}
  "TmuxNewSession を構築する(launch 経路。禁止 env reject は substrate 所有)。"
  (TmuxNewSession :session-name session-name :work-dir work-dir :env env))

(deff fs-canonical-path [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsCanonicalPath)]}
  "FsCanonicalPath を構築する(S12 の canonicalized project key)。"
  (FsCanonicalPath :path path))

(deff fs-read-text [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsReadText)]}
  "FsReadText を構築する(不存在は None)。"
  (FsReadText :path path))

(deff fs-write-text-atomic [path text tmp-suffix]
  {:pre [(: path str) (> (len path) 0) (: text str)
         (: tmp-suffix str) (> (len tmp-suffix) 0)]
   :post [(: % FsWriteTextAtomic)]}
  "FsWriteTextAtomic を構築する(temp+rename、S12)。"
  (FsWriteTextAtomic :path path :text text :tmp-suffix tmp-suffix))

(deff fs-make-dirs [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsMakeDirs)]}
  "FsMakeDirs を構築する(exist-ok 再帰作成)。"
  (FsMakeDirs :path path))

(deff env-get [name]
  {:pre [(: name str) (> (len name) 0)]
   :post [(: % EnvGet)]}
  "EnvGet を構築する(process env fallback、S11 caveat)。"
  (EnvGet :name name))

(deff clock-sleep [seconds]
  {:pre [(: seconds (| int float)) (>= seconds 0)]
   :post [(: % ClockSleep)]}
  "ClockSleep を構築する(poll 間隔・再描画待ち)。"
  (ClockSleep :seconds (float seconds)))
