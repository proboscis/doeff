;;; 直接束縛 deftest: session-host 共有 policy program の全分岐検証(DOE-004 C1)。
;;;
;;; daemon 不要 — fake substrate handler(dict-backed SessionStore・F-* フレーム
;;; 台本 Tmux・固定 Clock・台本 judge Proc)で policy program を直接束縛して回す。
;;; フレーム語彙・TerminalCause 表・knob 表・文言は conformance README
;;; (packages/doeff-agents/conformance/README.md、CONTRACT FIXED 2026-07-05)から
;;; verbatim 転記。oracle: agentd-rust-final:src/main.rs monitor_once。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import dataclasses [replace])
(import datetime [datetime timezone timedelta])
(import json)
(import pytest)

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  PaneObservation
  JudgeVerdict
  ProcResult
  MonitorKnobs
  BuildLaunch
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  WireResultChannel
  SessionStoreListActive
  SessionStoreListCleanupPending
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds
  DiscoverConversation
  ProbeConversationActivity
  TmuxHasSession
  TmuxPaneCurrentCommand
  TmuxSessionPaneIds
  TmuxCapture
  TmuxSendKeys
  TmuxKillSession
  ClockNow
  ProcRun
  build-launch
  pre-launch-setup
  classify-pane
  deliver-message
  wire-result-channel
  session-store-list-active
  session-store-get
  session-store-upsert
  session-store-result-payload
  session-store-record-event
  tmux-has-session
  tmux-pane-current-command
  tmux-capture
  tmux-send-keys
  tmux-kill-session
  clock-now
  proc-run])
(import doeff_agents.sessionhost.policy [
  ACTIVE-STATUSES
  TERMINAL-STATUSES
  RESULT-SOLICITATION-MESSAGE
  TERMINAL-CAUSE-RETRYABLE
  make-cause
  tail-chars
  tail-lower
  monitor-cycle])
(import doeff_agents.sessionhost.impls.markers [has-api-limit-marker
                                                provider-failure-class])


;; ---------------------------------------------------------------------------
;; 凍結フレーム語彙(F-*)— conformance README の表の verbatim 断片
;; ---------------------------------------------------------------------------

(setv F-IDLE-CODEX "› ")
(setv F-IDLE-CLAUDE "❯")
(setv F-ACTIVE-CODEX "working (12s • esc to interrupt)")
(setv F-TURN-ACTIVITY-CLAUDE "⏺ Read file\n⎿ 42 lines")
(setv F-FAILED "fatal error: kaboom")
(setv F-API-LIMIT "rate limit exceeded")
(setv F-WAITING "Type your message")
;; ACP issue 55b1bd: 働いている pane に waiting 語が同時に居る現物の型
;; (常設フッター + live spinner)。実物の逐語は impls 側 deftest
;; test-classify-claude-working-pane-carries-both-marker-facts が固定する —
;; ここは fake classify-frame の語彙(Type your message / … ()で同じ
;; 「waiting ∧ active」の同時成立を作る。
(setv F-WAITING-WHILE-WORKING
      "✶ Whatchamacalliting… (31m 5s · 91.4k tokens)\nType your message")
(setv F-MENU-CODEX "› 1. Switch to gpt-5.4-mini\n  2. Keep current model\n  Press enter to confirm")
(setv F-FROZEN "==> restricted login <==\n-- more --")
(setv F-DIALOG-MANAGED "Managed settings require approval\nSettings requiring approval:\n  - statusLine")


;; ---------------------------------------------------------------------------
;; fake substrate world(dict-backed store・台本 tmux・固定 clock)
;; ---------------------------------------------------------------------------

(defclass FakeWorld []
  (defn __init__ [self]
    (setv self.rows {})                ;; session-id -> SessionRow
    (setv self.result-payloads {})     ;; session-id -> report_result payload(json str)
    (setv self.events [])              ;; [(session-id, event-type)]
    (setv self.frames {})              ;; pane-id -> 現在のフレーム(F-*)
    (setv self.pane-commands {})       ;; pane-id -> foreground command
    (setv self.pane-sessions {})       ;; pane-id -> 帰属 session 名(ADR-010 R4)
    (setv self.tmux-sessions (set))    ;; 生きている tmux session 名
    (setv self.sent-keys [])           ;; [(pane-id, text, literal, submit)]
    (setv self.delivered [])           ;; [(pane-id, text)] — DeliverMessage 受領
    (setv self.killed [])              ;; kill された session 名
    (setv self.has-session-calls [])   ;; TmuxHasSession probe の記録
    (setv self.capture-count 0)
    (setv self.proc-calls [])          ;; [(command, stdin)]
    (setv self.judge-script [])        ;; 順に pop される ProcResult 台本
    (setv self.broken-panes (set))     ;; capture が例外を投げる pane(隔離検証用)
    (setv self.frame-script {})        ;; pane-id -> capture ごとに pop される
                                       ;; フレーム列(issue #573: tick 内で画面が
                                       ;; 変わる pane の台本。尽きたら frames)
    (setv self.discovered None)        ;; DiscoverConversation の台本(ADR-006)
    (setv self.conversation-mtimes {}) ;; conversation session_id -> epoch mtime
                                       ;; (ProbeConversationActivity の台本 —
                                       ;; ADR-002 R-conversation-evidence。
                                       ;; 無記帳は None = probe 不能 fallback)
    (setv self.now (datetime 2026 7 5 12 0 0 :tzinfo timezone.utc))))


(defn iso-at [world offset-secs]
  (.isoformat (+ world.now (timedelta :seconds offset-secs))))


(defn make-row [world #** overrides]
  (setv fields {"session_id" "s1"
                "session_name" "doeff-s1"
                "pane_id" "%1"
                "agent_type" "codex"
                "lifecycle" "run_to_completion"
                "status" "running"
                "started_at" (iso-at world -30)
                "last_observed_at" (iso-at world -1)
                "finished_at" None
                "cleaned_at" None
                "output_snippet" None
                "last_output_change_at" (iso-at world -1)
                "awaiting_response" False
                "observed_active_at" (iso-at world -20)
                "expected_result" {"type" "object"}
                "result_payload" None
                "last_validation_error" None
                "result_solicitations_used" 0
                "prompt_unblock_attempts" 0
                "terminal_cause" None})
  (.update fields overrides)
  (SessionRow #** fields))


(defn seed [world row #** kw]
  "row を store に置き、tmux session / pane / フレーム / pane 帰属を生かす。"
  (setv (get world.rows row.session-id) row)
  (.add world.tmux-sessions row.session-name)
  (setv (get world.pane-commands row.pane-id) (.get kw "pane_command" "codex"))
  (setv (get world.pane-sessions row.pane-id) row.session-name)
  (setv (get world.frames row.pane-id) (.get kw "frame" ""))
  row)


(defn classify-frame [output]
  "F-* 凍結フレーム語彙 → PaneObservation(fake の kind 別 ClassifyPane 実装。
   marker→分類は impl 所有 — ここでは oracle の実マーカー(main.rs:2969-3106)を
   台本用に再現する)。"
  (setv lower10 (tail-lower output 10))
  (setv lower30 (tail-lower output 30))
  (setv lowerall (.lower output))
  (setv failure (or (in "fatal error" lower10)
                    (in "unrecoverable error" lower10)
                    (in "agent crashed" lower10)
                    (in "session terminated" lower10)
                    (in "authentication failed" lower10)))
  ;; api-limit だけは oracle 再現でなく実物 markers.hy へ委譲する
  ;; (ACP ADR 0049 R9 改訂 2026-08-07): 語彙が逐語列挙から族照合へ
  ;; 進化したため、fake 側の語彙再現は実物との版ずれの温床 — 実物表に
  ;; 欠落があってもテストだけ green になる盲点(2026-08-06 の 22 件
  ;; run_failed 落ちと同型)を作る。marker→検出は impl 所有
  ;; (ADR-DOE-AGENTS-004)であり、fake であるべきは substrate であって
  ;; marker 物理ではない。
  (setv api-limit (has-api-limit-marker output))
  ;; ACP ADR 0049 R9 第 3 改訂: provider 失敗族も同じ理由で実物へ委譲する
  ;; (fake 側で族の逐語を再現すると、実物の族に穴が開いてもテストだけ
  ;;  green になる — 上の api-limit と同一の版ずれ盲点)。
  (setv provider-failure (provider-failure-class output))
  (setv waiting (or (in "Type your message" output)
                    (in "tell Claude what to do differently" output)))
  (setv idle (or (.startswith output "› ")
                 (in "\n› " output)
                 (any (gfor line (.splitlines output) (.startswith line "❯")))))
  (setv starting-mcp (in "starting mcp servers" lower30))
  (setv active (and (not starting-mcp)
                    (or (in "working (" lower30)
                        (in "esc to interrupt" lower30)
                        (in "… (" lower30))))
  (setv turn-activity (or (in "⏺" output) (in "⎿" output)))
  (setv managed (and (in "managed settings require approval" lowerall)
                     (in "settings requiring approval" lowerall)))
  (setv startup (and (not starting-mcp) (not managed)
                     (or active idle turn-activity)))
  ;; issue #573: queued-messages 事実(最終 prompt 行の queue 表示)。
  ;; True の時だけ kwarg を渡す — field が生えるまで既存テストを汚さない。
  (setv queued-kwargs {})
  (when (in "Press up to edit queued messages" output)
    (setv (get queued-kwargs "has_queued_messages") True))
  (PaneObservation
    :has-failure-marker failure
    :has-api-limit-marker api-limit
    :provider-failure-class provider-failure
    :has-waiting-marker waiting
    :has-idle-prompt idle
    :has-active-marker active
    :has-turn-activity turn-activity
    :startup-finished startup
    :has-unsubmitted-paste (in "<unsubmitted-paste>" output)
    :dialog (if managed "managed" None)
    :dialog-dismiss-keys (if managed #("Enter") #())
    #** queued-kwargs))


(defhandler fake-substrate [world]
  "直接束縛用 fake handler: substrate(SessionStore / Tmux / Clock / Proc)+
   monitor policy が yield する interface effect(ClassifyPane / DeliverMessage)。"

  (SessionStoreListActive []
    (resume (lfor r (list (.values world.rows)) :if (in r.status ACTIVE-STATUSES) r)))

  (SessionStoreListCleanupPending []
    ;; ADR-010 R5 の対象集合: 終端 ∧ cleaned_at 未刻印 ∧ RTC ∧ 非 adopted。
    (resume (lfor r (list (.values world.rows))
                  :if (and (in r.status TERMINAL-STATUSES)
                           (is r.cleaned-at None)
                           (= r.lifecycle "run_to_completion")
                           (not r.adopted))
                  r)))

  (SessionStoreGet [session-id]
    (resume (.get world.rows session-id)))

  (SessionStoreUpsert [row]
    ;; COALESCE 規律(main.rs:2339): upsert は永続化済み result-payload を消せない
    (setv existing (.get world.rows row.session-id))
    (when (and (is-not existing None)
               (is-not existing.result-payload None)
               (is None row.result-payload))
      (setv row (replace row :result-payload existing.result-payload)))
    (setv (get world.rows row.session-id) row)
    (resume None))

  (SessionStoreResultPayload [session-id]
    (resume (.get world.result-payloads session-id)))

  (SessionStoreKnownConversationIds []
    ;; ADR-006 発見 arm の除外集合。fake world は rows の conversation から導出。
    (resume (sorted (sfor r (list (.values world.rows))
                          :if (is-not r.conversation None)
                          (get r.conversation "session_id")))))

  (DiscoverConversation [agent-type params]
    ;; 発見物理は impls の所有(sessionhost_resume_deftests が実 impl を検査
    ;; する)— policy deftest では台本(world.discovered、既定 None=未発見)。
    (resume world.discovered))

  (ProbeConversationActivity [agent-type params]
    ;; 所在物理(transcript / rollout の path)は impls の所有 — policy
    ;; deftest では台本(conversation session_id → epoch mtime。無記帳は
    ;; None = probe 不能の fallback 面)。ADR-002 R-conversation-evidence。
    (setv conv (or (.get params "conversation") {}))
    (resume (.get world.conversation-mtimes (.get conv "session_id"))))

  (SessionStoreRecordEvent [session-id event-type row]
    (.append world.events #(session-id event-type))
    (resume None))

  (TmuxHasSession [session-name]
    (.append world.has-session-calls session-name)
    (resume (in session-name world.tmux-sessions)))

  (TmuxPaneCurrentCommand [pane-id]
    (resume (.get world.pane-commands pane-id)))

  (TmuxSessionPaneIds [session-name]
    (resume (lfor [pid owner] (.items world.pane-sessions)
                  :if (= owner session-name)
                  pid)))

  (TmuxCapture [pane-id lines]
    (when (in pane-id world.broken-panes)
      (raise (RuntimeError f"tmux capture failed for pane {pane-id}")))
    (setv world.capture-count (+ world.capture-count 1))
    (setv script (.get world.frame-script pane-id))
    (if script
        (resume (.pop script 0))
        (resume (.get world.frames pane-id ""))))

  (TmuxSendKeys [pane-id text literal submit]
    (.append world.sent-keys #(pane-id text literal submit))
    (resume None))

  (TmuxKillSession [session-name]
    (.discard world.tmux-sessions session-name)
    (.append world.killed session-name)
    (resume None))

  (ClockNow []
    (resume world.now))

  (ProcRun [command stdin]
    (.append world.proc-calls #(command stdin))
    (resume (.pop world.judge-script 0)))

  (ClassifyPane [agent-type output]
    (resume (classify-frame output)))

  (DeliverMessage [pane-id text]
    (.append world.delivered #(pane-id text))
    (resume None)))


(defk run-cycle [world knobs]
  {:pre [(: world FakeWorld) (: knobs MonitorKnobs)]
   :post [(: % dict)]}
  (<- outcomes ((fake-substrate world) (monitor-cycle knobs)))
  outcomes)


(defn judge-ok [#** kw]
  "blocked verdict を返す台本 judge 応答。"
  (ProcResult :exit-code 0
              :stdout (json.dumps {"blocked" (.get kw "blocked" True)
                                   "keys" (.get kw "keys" ["Down" "Enter"])
                                   "reason" (.get kw "reason" "menu")})))

(defn judge-inconclusive []
  (ProcResult :exit-code 0
              :stdout (json.dumps {"blocked" False "keys" [] "reason" "looks idle"})))

(defn judge-broken []
  "実在しないコマンド相当(sh -c exit 127)。"
  (ProcResult :exit-code 127 :stdout "" :stderr "command not found"))


;; ---------------------------------------------------------------------------
;; result-first 終端
;; ---------------------------------------------------------------------------

(deftest test-golden-result-first-done
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-ACTIVE-CODEX)
  (setv (get world.result-payloads "s1") "{\"ok\": true}")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  ;; result-first: turn-end を経ずとも報告済み payload が終端を勝ち取る
  (assert (= row.status "done"))
  (assert (= row.result-payload "{\"ok\": true}"))
  (assert (is None row.last-validation-error))
  (assert (is-not row.finished-at None))
  (assert (in "doeff-s1" world.killed))
  (assert (in #("s1" "session_done") world.events)))


(deftest test-result-first-wins-at-turn-end
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (setv (get world.result-payloads "s1") "{\"ok\": true}")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "done"))
  ;; solicitation は走らない(result-first)
  (assert (= world.delivered [])))


;; ---------------------------------------------------------------------------
;; 分類順(failure → api-limit → waiting → running)+ output 写像
;; ---------------------------------------------------------------------------

(deftest test-classification-failure-beats-api-limit
  ;; S8b: failure マーカー + api-limit 文言の複合フレーム → failed、
  ;; output 写像で cause rate_limited retryable=true
  (setv world (FakeWorld))
  (seed world (make-row world) :frame (+ F-FAILED "\n" F-API-LIMIT))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True))
  (assert (in #("s1" "session_failed") world.events)))


(deftest test-api-limit-blocked-api-non-terminal
  ;; S8a: F-api-limit 単独 → blocked_api は非終端が正(level-triggered 回復可能)
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-API-LIMIT)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked_api"))
  (assert (in row.status ACTIVE-STATUSES))
  (assert (is None row.finished-at))
  (assert (is None row.terminal-cause))
  (assert (in #("s1" "session_blocked") world.events)))


(deftest test-waiting-marker-blocked
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-WAITING)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked"))
  (assert (is None row.finished-at))
  (assert (in #("s1" "session_blocked") world.events)))


(deftest test-working-pane-with-waiting-footer-stays-running
  ;; ACP issue 55b1bd の根治の固定: waiting 腕は作業証拠との連言である。
  ;; 現行 claude TUI の waiting 語(accept edits / bypass permissions /
  ;; shift+tab to cycle)は permission-mode の常設フッターで、起動から終了まで
  ;; 消えない。旧分類はこれ 1 語で blocked を確定させたため、全席が起動直後から
  ;; 入力待ちと記録され、その状態を『指示未配達』と読む上流の有界 dwell
  ;; (ACP ADR 9211b3)が働いている席を終端した — 2026-08-06..12 の壁帯 218 席の
  ;; うち 142 席(65%)は死の 10 秒前まで画面が動いていた。live active marker が
  ;; 見えている観測は working が確定事実なので blocked にしない。
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-WAITING-WHILE-WORKING)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (is None row.finished-at))
  (assert (not-in #("s1" "session_blocked") world.events)))


(deftest test-turn-activity-residue-does-not-mask-blocked
  ;; 連言に has-turn-activity を入れない、の固定(依頼書の decisions 逐語からの
  ;; 意図的逸脱 — 実読で確定)。⏺ / ⎿ は idle 画面にも残留する痕跡であって live の
  ;; 作業証拠ではない(markers.hy の逐語: latch clear と startup watchdog 解除に
  ;; のみ使う)。連言に入れると一度でも作業した claude 席は二度と blocked に
  ;; ならず、本当に固まった席(2026-08-05..06 の 7 席 × 22h27m)が再び不可視に
  ;; なる — 上流の有界 dwell が働く前提そのものを壊す。
  (setv world (FakeWorld))
  (seed world (make-row world)
        :frame (+ F-TURN-ACTIVITY-CLAUDE "\n" F-WAITING))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked")))


;; ---------------------------------------------------------------------------
;; 監査 event は status 遷移でのみ記録(edge-triggered — 2026-07-27 sessionhost
;; wedge 根治)。blocked のまま静止する行が毎 tick full-snapshot event を注ぎ、
;; agentd.sqlite が 1.5GB(session_blocked 251k 行 / 1.16GB)へ肥大 → 単一
;; StoreActor の per-op コスト増で socket 応答不能に至った実 incident の再発
;; 防止。upsert(state 更新)は level-triggered のまま — 止めるのは journal
;; 追記のみ。
;; ---------------------------------------------------------------------------

(deftest test-blocked-quiescent-tick-records-no-event
  ;; 既に blocked の行が blocked のまま観測される tick は event を書かない。
  (setv world (FakeWorld))
  (seed world (make-row world :status "blocked") :frame F-WAITING)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked"))
  ;; state 更新(upsert)は生きている — 観測時刻は進む
  (assert (= row.last-observed-at (iso-at world 0)))
  (assert (not-in #("s1" "session_blocked") world.events)))


(deftest test-blocked-transition-records-single-event-across-cycles
  ;; running → blocked の遷移 tick だけが event を書く。以降の静止 tick は
  ;; 何 cycle 回しても増えない。
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-WAITING)
  (<- _ (run-cycle world (MonitorKnobs)))
  (<- _ (run-cycle world (MonitorKnobs)))
  (<- _ (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked"))
  (assert (= 1 (.count world.events #("s1" "session_blocked")))))


(deftest test-running-quiescent-tick-records-no-observed-event
  ;; running のまま働き続ける行も静止 tick では session_observed を書かない。
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-ACTIVE-CODEX)
  (<- _ (run-cycle world (MonitorKnobs)))
  (<- _ (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (not-in #("s1" "session_observed") world.events)))


;; ---------------------------------------------------------------------------
;; turn-end 判定(idle ∧ 非 active ∧ stable)と solicitation
;; ---------------------------------------------------------------------------

(deftest test-turn-end-requires-stable-tail
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX)
  (setv knobs (MonitorKnobs))
  (<- o1 (run-cycle world knobs))
  ;; 1 cycle 目: snapshot 無し → stable 不成立 → turn-end しない
  (assert (= (. (get world.rows "s1") status) "running"))
  (assert (= world.delivered []))
  (<- o2 (run-cycle world knobs))
  ;; 2 cycle 目: 同一フレーム(500 字 tail 一致)→ turn-end → solicitation
  (assert (= (len world.delivered) 1)))


(deftest test-solicitation-verbatim-latch-counter
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  ;; 文言 verbatim(S2: `AGENTD RESULT CONTRACT: ...`)
  (assert (= world.delivered [#("%1" RESULT-SOLICITATION-MESSAGE)]))
  ;; durable counter + awaiting_response latch 再武装 + 非終端維持(R4)
  (assert (= row.result-solicitations-used 1))
  (assert (= row.awaiting-response True))
  (assert (= row.status "running"))
  (assert (is None row.finished-at))
  (assert (in #("s1" "session_result_solicited") world.events)))


(deftest test-latch-blocks-turn-end-until-active
  ;; ハザード 4: awaiting_response latch は active marker の観測でのみ clear
  (setv world (FakeWorld))
  (seed world (make-row world
                        :awaiting-response True
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (setv knobs (MonitorKnobs))
  (<- o1 (run-cycle world knobs))
  ;; idle 安定でも latch が立っている間は turn-end に到達しない
  (assert (= world.delivered []))
  (assert (= (. (get world.rows "s1") awaiting-response) True))
  ;; active marker で latch clear
  (setv (get world.frames "%1") F-ACTIVE-CODEX)
  (<- o2 (run-cycle world knobs))
  (assert (= (. (get world.rows "s1") awaiting-response) False))
  ;; idle に戻す: 1 cycle 目は不安定、2 cycle 目で turn-end → solicitation
  (setv (get world.frames "%1") F-IDLE-CODEX)
  (<- o3 (run-cycle world knobs))
  (assert (= world.delivered []))
  (<- o4 (run-cycle world knobs))
  (assert (= (len world.delivered) 1)))


(deftest test-latch-clears-on-turn-activity
  ;; claude は active marker を出さない turn がある — ⏺ / ⎿(turn-activity)でも clear
  (setv world (FakeWorld))
  (seed world (make-row world :agent-type "claude" :awaiting-response True)
        :frame F-TURN-ACTIVITY-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.awaiting-response False))
  (assert (= row.status "running")))


(deftest test-solicitation-exhaustion-runfailed
  ;; S3: budget(2)超過 → failed・reason 文言 verbatim・cause run_failed false
  (setv world (FakeWorld))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.last-validation-error
             "session reached turn-end without reporting a result via report_result (after 2 solicitation(s))"))
  (assert (= row.terminal-cause.category "run_failed"))
  (assert (= row.terminal-cause.retryable False))
  (assert (in "doeff-s1" world.killed))
  (assert (in #("s1" "session_failed") world.events)))


;; ---------------------------------------------------------------------------
;; ADR-002 R-conversation-evidence: turn 生死のデータ層証拠
;; (2026-08-18 実弾: claude CLI 2.1.234 が走行中 turn を描かず、表示層のみの
;;  turn-end 判定が走行中の番人 22 席を約 20 秒で run_failed に焼いた)
;; ---------------------------------------------------------------------------

(deftest test-turn-end-not-declared-while-conversation-fresh
  ;; 反例(実弾の再現): pane は idle ∧ stable(走行中 turn が描かれない)
  ;; でも、会話記録が鮮度窓内に更新されている間は turn-end を宣言しない。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :conversation {"session_id" "conv-1"}
                        :output-snippet (tail-chars F-IDLE-CLAUDE 500))
        :frame F-IDLE-CLAUDE)
  (setv (get world.conversation-mtimes "conv-1")
        (- (.timestamp world.now) 5))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= world.delivered []))
  (assert (= row.result-solicitations-used 0))
  (assert (= row.status "running")))


(deftest test-turn-end-fires-when-conversation-quiescent
  ;; 保存対照: 会話記録が鮮度窓の外(静止)なら従来どおり turn-end →
  ;; solicitation — probe の導入は真の turn-end 検出を遅らせない。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :conversation {"session_id" "conv-1"}
                        :output-snippet (tail-chars F-IDLE-CLAUDE 500))
        :frame F-IDLE-CLAUDE)
  (setv (get world.conversation-mtimes "conv-1")
        (- (.timestamp world.now) 300))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (= (len world.delivered) 1))
  (assert (= (. (get world.rows "s1") result-solicitations-used) 1)))


(deftest test-turn-end-not-declared-while-queued-messages
  ;; 自分が送った催促が composer に未消費で座っている(queued messages =
  ;; markers.hy が「turn 走行中の明白な busy 証拠」と定義する状態)間は
  ;; 「催促に応えなかった」は成立しない — budget 超過の終端にも至らない。
  (setv world (FakeWorld))
  (setv queued-frame "❯ Press up to edit queued messages")
  (seed world (make-row world
                        :agent-type "claude"
                        :result-solicitations-used 2
                        :output-snippet (tail-chars queued-frame 500))
        :frame queued-frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (is None row.terminal-cause))
  (assert (= world.delivered [])))


(deftest test-awaiting-cleared-by-conversation-progress
  ;; 配送後に会話記録が進んだ(margin 超)= turn は始まっている — 描画され
  ;; なくても awaiting latch を clear する(「turn never started」誤判定の
  ;; データ層根治)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :conversation {"session_id" "conv-1"}
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -60))
        :frame F-IDLE-CLAUDE)
  (setv (get world.conversation-mtimes "conv-1")
        (- (.timestamp world.now) 20))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (= (. (get world.rows "s1") awaiting-response) False)))


(deftest test-awaiting-not-cleared-by-delivery-write
  ;; 反対照: 配送そのもの(prompt / solicitation の queue 書き込み)が動かす
  ;; mtime(margin 内)は進行ではない — latch は保持される。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :conversation {"session_id" "conv-1"}
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -5))
        :frame F-IDLE-CLAUDE)
  (setv (get world.conversation-mtimes "conv-1")
        (- (.timestamp world.now) 3))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (= (. (get world.rows "s1") awaiting-response) True)))


;; ---------------------------------------------------------------------------
;; issue #557: api-limit 観測の durable latch と終端分類での蒸留
;; ---------------------------------------------------------------------------

(deftest test-api-limit-observation-latches-durably
  ;; S8c 前段: blocked_api 観測は S8a の非終端 semantics を保ったまま、
  ;; 観測事実を行へ durable に latch する(first-write-wins)
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-API-LIMIT)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "blocked_api"))
  (assert (is None row.terminal-cause))
  (assert (is-not row.api-limit-observed-at None))
  ;; first-write-wins: 2 cycle 目の再観測で打刻は動かない
  (setv first-latch row.api-limit-observed-at)
  (setv world.now (+ world.now (timedelta :seconds 30)))
  (<- outcomes2 (run-cycle world (MonitorKnobs)))
  (assert (= (. (get world.rows "s1") api-limit-observed-at) first-latch)))


(deftest test-solicitation-exhaustion-with-latch-rate-limited
  ;; S8c: 上限文言が terminal 前に scroll out(終端フレームは素の idle)でも、
  ;; attempt 中の blocked_api 観測 latch が turn-end-without-result 終端を
  ;; rate_limited/retryable=true へ蒸留する(issue #557 の実障害形)
  (setv world (FakeWorld))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :api-limit-observed-at (iso-at world -60)
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  ;; 基底 reason は S3 文言 verbatim のまま(last_validation_error は不変)
  (assert (= row.last-validation-error
             "session reached turn-end without reporting a result via report_result (after 2 solicitation(s))"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True))
  ;; cause reason は latch の証跡を運ぶ
  (assert (in "api-limit" row.terminal-cause.reason))
  (assert (in #("s1" "session_failed") world.events)))


(deftest test-failed-output-with-latch-rate-limited
  ;; S8d: failure marker 単独の終端フレーム(上限文言は scroll out 済み)でも、
  ;; latch があれば reason 無し failed の output 写像は rate_limited へ
  (setv world (FakeWorld))
  (seed world (make-row world :api-limit-observed-at (iso-at world -60))
        :frame F-FAILED)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-solicitation-exhaustion-live-marker-rate-limited
  ;; ACP ADR 0049 R9(2026-07-26 実 incident の直接形): 終端 cycle の pane に
  ;; 上限文言が生きて見えている(事前 latch 無し)。同 cycle の観測が latch を
  ;; 立ててから budget 超過分類が走る順序(latch arm が turn-end arm より先)を
  ;; 契約として固定する — marker が立っていれば category は run_failed でなく
  ;; rate_limited/retryable=true。
  (setv world (FakeWorld))
  (setv frame (+ F-API-LIMIT "\n› "))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (is-not row.api-limit-observed-at None))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-solicitation-exhaustion-live-fable-wording-rate-limited
  ;; ACP ADR 0049 R9 改訂(2026-08-06 実 incident の直接形): 実物告知
  ;; 「You've reached your Fable 5 limit. /model to switch models.」は
  ;; 旧逐語 16 語のどれにも部分一致せず、22 件(7/13〜8/6、8/6 単日 16 件)が
  ;; latch を立てられないまま run_failed/retryable=false で捨てられた。
  ;; 族照合後: 実物 marker(classify-frame は api-limit を markers.hy へ
  ;; 委譲)が同 cycle で latch を立て、budget 超過終端が
  ;; rate_limited/retryable=true へ蒸留される — 表 1 か所の修理で下流の
  ;; 蒸留分岐が同時に正しくなることの統合検証。
  (setv world (FakeWorld))
  (setv frame "You've reached your Fable 5 limit. /model to switch models.\n› ")
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (is-not row.api-limit-observed-at None))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True)))


;; ---------------------------------------------------------------------------
;; ACP ADR 0049 R9 第 3 改訂: provider 失敗は上限族だけではない
;; (2026-08-12 ledger-integrity-steward 13 時間停止の根治)
;; ---------------------------------------------------------------------------
;;
;; 反例の実弾: sandbox:responsibility:ledger-integrity-steward の attend が
;; 2026-08-12T08:24:32Z に "session reached turn-end without reporting a result
;; via report_result (after 2 solicitation(s))" / run_failed / retryable=false で
;; 終端し、argus が attendFailureClass=deterministic を刻んで初回 gate に latch、
;; 13 時間停止した。実際の死因は席の判断ではなく provider の認証断で、
;; 席 transcript(ecba4783-15f5-4896-949b-c630768f9440)の 08:23:30.507Z に
;; isApiErrorMessage=true / "Not logged in · Please run /login" が在る。
;; 上限族しか見ない蒸留がこの族を run_failed へ落としていた。
;;
;; 母数(agentd.sqlite の terminal × 席 transcript の突合、2026-08-12 実測):
;; 所有格族が着地した 2026-08-08 以降の同型終端 19 件中 12 件(63%)が provider
;; 由来 —— 認証 11 / 文脈枯渇 1。同区間の上限族の取りこぼしは 0 件。

(setv F-REAUTH-REQUIRED "  ⎿ Not logged in · Please run /login")
(setv F-ACCESS-REVOKED
      (+ "Your organization has disabled Claude subscription access for "
         "Claude Code · Use an Anthropic API key instead, or ask your admin "
         "to enable access"))
(setv F-CONTEXT-EXHAUSTED "  ⎿ Context limit reached · /compact or /clear to continue")


(deftest test-provider-failure-observation-latches-durably
  ;; 上限 latch(S8c 前段)と同型: 観測した族名と初回時刻を first-write-wins で
  ;; 行へ固定する。終端 tail が racy なのは上限文言に限らない — 実測
  ;; 2026-08-10 wi_86957315d9157a2c は transcript に組織 access 剥奪が在るのに
  ;; 終端 snapshot は催促文だけだった。
  (setv world (FakeWorld))
  (setv frame (+ F-REAUTH-REQUIRED "\n› "))
  (seed world (make-row world) :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.provider-failure-class "reauth-required"))
  (assert (is-not row.provider-failure-observed-at None))
  ;; ★status は動かさない: provider 失敗族は blocked_api のような非終端
  ;; status を作らない(生きた pane を新しい status へ落とす経路を作らない)。
  (assert (= row.status "running"))
  (assert (is None row.terminal-cause))
  ;; first-write-wins: 2 cycle 目の再観測で打刻は動かない
  (setv first-latch row.provider-failure-observed-at)
  (setv world.now (+ world.now (timedelta :seconds 30)))
  (<- outcomes2 (run-cycle world (MonitorKnobs)))
  (assert (= (. (get world.rows "s1") provider-failure-observed-at) first-latch)))


(deftest test-solicitation-exhaustion-with-reauth-latch-is-auth-failed
  ;; ★本改訂の中心反例(ledger-integrity-steward 2026-08-12 の実弾形)。
  ;; 認証断は終端フレームでは既に押し流されている(催促文が composer を
  ;; 占めている)— latch 経由が唯一の到達形。旧実装ではここが run_failed /
  ;; retryable=false = deterministic 恒久 gate だった。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :provider-failure-class "reauth-required"
                        :provider-failure-observed-at (iso-at world -60)
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  ;; 基底 reason は S3 文言 verbatim のまま(last_validation_error は不変)
  (assert (= row.last-validation-error
             "session reached turn-end without reporting a result via report_result (after 2 solicitation(s))"))
  (assert (= row.terminal-cause.category "auth_failed"))
  (assert (= row.terminal-cause.retryable True))
  ;; cause reason は族名の証跡を運ぶ(診断が下流へ届く)
  (assert (in "reauth-required" row.terminal-cause.reason)))


(deftest test-solicitation-exhaustion-live-reauth-wording-is-auth-failed
  ;; 実物 frame が生きて見えている形(事前 latch 無し)。同 cycle の観測が
  ;; latch を立ててから budget 超過分類が走る順序を契約として固定する
  ;; (上限族の live-marker テストと同型)。
  (setv world (FakeWorld))
  (setv frame (+ F-REAUTH-REQUIRED "\n› "))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.provider-failure-class "reauth-required"))
  (assert (= row.terminal-cause.category "auth_failed"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-solicitation-exhaustion-with-access-revoked-is-auth-failed
  ;; 組織側の access 剥奪(2026-08-09〜10 cryptic-x が 24h で 7 席を恒久停止
  ;; させた形)。retryable=true —— admin 操作 / binding rotation で解ける事実で
  ;; あって席の判断ではない。有界 budget を焼き切った先は従来どおり人の gate
  ;; だが、そこに載る名前が実際の死因になる。
  (setv world (FakeWorld))
  (setv frame (+ F-ACCESS-REVOKED "\n› "))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.provider-failure-class "access-revoked"))
  (assert (= row.terminal-cause.category "auth_failed"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-solicitation-exhaustion-with-context-exhausted-stays-deterministic
  ;; ★負の対照 = 本改訂は「provider 由来ならすべて再試行可」ではない。
  ;; 文脈枯渇は同じ封筒での再試行が同じ地点で必ず死ぬ(hard rule 7)ので
  ;; retryable=false のまま。直るのは再試行可否ではなく **名前** で、下流は
  ;; 初めて文脈枯渇を run_failed から分離して読める
  ;; (ACP issue argus-attend-00738c 要件 3 の弁別軸)。
  (setv world (FakeWorld))
  (setv frame (+ F-CONTEXT-EXHAUSTED "\n› "))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.provider-failure-class "context-exhausted"))
  (assert (= row.terminal-cause.category "context_exhausted"))
  (assert (= row.terminal-cause.retryable False))
  ;; run_failed ではない = 「席が結果を報告しなかった」という誤った帰属が消える
  (assert (!= row.terminal-cause.category "run_failed")))


(deftest test-api-limit-latch-outranks-provider-failure-latch
  ;; 凍結順の pin: 両方立った断面では上限族が勝つ(既存 S8c/S8d の意味論と
  ;; ACP の provider-exhaustion 述語〔blocked_api / rate_limited〕を動かさない)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :result-solicitations-used 2
                        :api-limit-observed-at (iso-at world -90)
                        :provider-failure-class "reauth-required"
                        :provider-failure-observed-at (iso-at world -60)
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-provider-failure-does-not-override-transient-defaults
  ;; 法の射程の pin: 上書きするのは **席帰属の既定**(run_failed /
  ;; interactive_prompt_blocked)だけ。既定が既に transient で別の会計を
  ;; 担っている場合(prompt_undelivered = ADR-DOE-AGENTS-011 の未配達
  ;; 第一級化)は、その分類を尊重する — 「provider 由来なら全部 provider 名」
  ;; にすると未配達の集計が割れる。
  (setv world (FakeWorld))
  (setv frame (+ F-REAUTH-REQUIRED "\n<unsubmitted-paste>\n› "))
  (seed world (make-row world
                        :awaiting-response True
                        :paste-resubmit-attempts 5
                        :output-snippet (tail-chars frame 500))
        :frame frame)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "prompt_undelivered"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-failed-output-with-provider-failure-latch-is-not-run-failed
  ;; reason 無し failed の output 写像(S8d と同型)。既存の名前つき分類
  ;; (timed_out / runner_unavailable / protocol_error)の優先順は動かさず、
  ;; run_failed へ落ちるはずだった断面だけが族の名前を得る。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :provider-failure-class "transport-failure"
                        :provider-failure-observed-at (iso-at world -60))
        :frame F-FAILED)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "transport_failed"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-healthy-frames-never-latch-a-provider-failure
  ;; 陰性対照: 働いている pane / 素の idle / turn-activity 痕跡だけの画面で
  ;; 族が立たないこと(健全席を新しい分類へ落とさない)。
  (for [frame [F-IDLE-CODEX F-IDLE-CLAUDE F-ACTIVE-CODEX F-TURN-ACTIVITY-CLAUDE
               F-WAITING RESULT-SOLICITATION-MESSAGE]]
    (assert (is None (provider-failure-class frame))
            f"healthy frame must not latch a provider failure: {frame !r}")))


;; ---------------------------------------------------------------------------
;; judge-before-solicitation(R6)と judge 変種(R7)
;; ---------------------------------------------------------------------------

(deftest test-judge-before-solicitation
  ;; S5: F-menu-codex は idle glyph でメニュー描画 — solicitation より先に judge
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-MENU-CODEX 500))
        :frame F-MENU-CODEX)
  (.extend world.judge-script [(judge-ok :keys ["Down" "Enter"])])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  ;; judge が先(journal 順): ProcRun 受領済み・solicitation は未送出
  (assert (= (len world.proc-calls) 1))
  (assert (in F-MENU-CODEX (get (get world.proc-calls 0) 1)))
  (assert (= world.delivered []))
  ;; unblock keys 受領・budget 消費・非終端継続
  (assert (= world.sent-keys [#("%1" "Down" False False) #("%1" "Enter" False False)]))
  (assert (= row.prompt-unblock-attempts 1))
  (assert (= row.status "running"))
  (assert (in #("s1" "session_prompt_unblocked") world.events)))


(deftest test-judge-disabled-degrades-to-solicitation
  ;; R7: turn-end 点で judge 無効("")→ solicitation へ degrade(hang しない)
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-MENU-CODEX 500))
        :frame F-MENU-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd None)))
  (setv row (get world.rows "s1"))
  (assert (= world.proc-calls []))
  (assert (= (len world.delivered) 1))
  (assert (= row.prompt-unblock-attempts 0)))


(deftest test-judge-error-turn-end-degrades-to-solicitation
  ;; R7: turn-end 点の judge error(実在しないコマンド)→ 同 cycle で solicitation
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-MENU-CODEX 500))
        :frame F-MENU-CODEX)
  (.extend world.judge-script [(judge-broken)])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "no-such-judge")))
  (setv row (get world.rows "s1"))
  (assert (= (len world.proc-calls) 1))
  (assert (= row.prompt-unblock-attempts 1))
  (assert (= (len world.delivered) 1))
  (assert (= row.status "running")))


(deftest test-judge-inconclusive-turn-end-falls-to-solicitation
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-MENU-CODEX 500))
        :frame F-MENU-CODEX)
  (.extend world.judge-script [(judge-inconclusive)])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  (assert (= row.prompt-unblock-attempts 1))
  (assert (= world.sent-keys []))
  (assert (= (len world.delivered) 1)))


;; ---------------------------------------------------------------------------
;; issue #573: 中断キー安全壁(busy veto)— 誤検知しても不可逆な中断だけは
;; 飛ばない壁。judge verdict がどうであれ、送出直前の fresh capture が明白な
;; busy 証拠(live active marker / 未消費 queue)を示すなら中断キーを送らない。
;; ---------------------------------------------------------------------------

(setv F-BUSY-SPINNER-CLAUDE
      (+ "✦ Cerebrating… (1m 34s · ↓ 2.2k tokens · thought for 39s)\n"
         "\n"
         (* "─" 80) "\n"
         "❯ \n"
         (* "─" 80)))

(setv F-QUEUED-CLAUDE
      (+ (* "─" 80) "\n"
         "❯ Press up to edit queued messages\n"
         (* "─" 80)))


(deftest test-unblock-keys-vetoed-on-fresh-active-capture
  ;; issue #573(2026-07-29 実 incident): turn-end 誤検知 → judge が blocked と
  ;; 誤答 → Escape が走行中 turn を破壊(transcript に [Request interrupted by
  ;; user])。送出直前の再観測が live spinner を見たら、キーは送らず veto を
  ;; 記帳して次 cycle の再観測に委ねる。solicitation も走らない(busy 証拠は
  ;; turn-end 判定そのものの誤りを示す)。
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-IDLE-CLAUDE 500))
        :frame F-IDLE-CLAUDE)
  ;; capture 1(観測)= idle・stable → turn-end → judge。
  ;; capture 2(送出直前の再観測)= live spinner。
  (setv (get world.frame-script "%1") [F-IDLE-CLAUDE F-BUSY-SPINNER-CLAUDE])
  (.extend world.judge-script [(judge-ok :keys ["Escape"])])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys []))
  (assert (in #("s1" "session_prompt_unblock_vetoed") world.events))
  (assert (not-in #("s1" "session_prompt_unblocked") world.events))
  (assert (= world.delivered []))
  (assert (= row.status "running")))


(deftest test-unblock-keys-vetoed-on-queued-messages
  ;; issue #573 event 1997731 現物: `❯ Press up to edit queued messages` =
  ;; solicitation が composer に積まれ未消費 = agent は明白に busy。この表示を
  ;; 観測したら中断キーは送らない。
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-IDLE-CLAUDE 500))
        :frame F-IDLE-CLAUDE)
  (setv (get world.frame-script "%1") [F-IDLE-CLAUDE F-QUEUED-CLAUDE])
  (.extend world.judge-script [(judge-ok :keys ["Escape"])])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys []))
  (assert (in #("s1" "session_prompt_unblock_vetoed") world.events))
  (assert (not-in #("s1" "session_prompt_unblocked") world.events))
  (assert (= world.delivered []))
  (assert (= row.status "running")))


;; ---------------------------------------------------------------------------
;; interactive-prompt stall watchdog(R5/R7、S6/S6b)
;; ---------------------------------------------------------------------------

(defn seed-stalled [world #** overrides]
  "F-frozen で stall T(180s)を超えて凍結した running session。"
  (setv defaults {"output_snippet" (tail-chars F-FROZEN 500)
                  "last_output_change_at" (iso-at world -200)})
  (.update defaults overrides)
  (seed world (make-row world #** defaults) :frame F-FROZEN))


(deftest test-stall-judge-bounded-then-exhausted-failure
  ;; S6: bounded judge(3)— inconclusive も budget を消費し、超過で typed failure
  (setv world (FakeWorld))
  (seed-stalled world)
  (setv knobs (MonitorKnobs :judge-cmd "scripted-judge"))
  (.extend world.judge-script [(judge-inconclusive) (judge-inconclusive) (judge-inconclusive)])
  (<- o1 (run-cycle world knobs))
  (<- o2 (run-cycle world knobs))
  (<- o3 (run-cycle world knobs))
  (setv row (get world.rows "s1"))
  (assert (= row.prompt-unblock-attempts 3))
  (assert (= row.status "running"))
  (assert (= (.count world.events #("s1" "session_prompt_judge_inconclusive")) 3))
  ;; 4 cycle 目: budget 超過 → typed failure
  (<- o4 (run-cycle world knobs))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.last-validation-error
             "interactive-prompt-blocked: pane unchanged for over 180s and 3 unblock attempt(s) exhausted"))
  (assert (= row.terminal-cause.category "interactive_prompt_blocked"))
  (assert (= row.terminal-cause.retryable False)))


(deftest test-stall-no-judge-immediate-typed-failure
  ;; S6b: judge 無効("")= stall 点では attempt 0 のまま即 typed failure
  (setv world (FakeWorld))
  (seed-stalled world)
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd None)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.prompt-unblock-attempts 0))
  (assert (= row.last-validation-error
             "interactive-prompt-blocked: pane unchanged for over 180s and no prompt judge configured"))
  (assert (= row.terminal-cause.category "interactive_prompt_blocked"))
  (assert (= row.terminal-cause.retryable False)))


(deftest test-stall-judge-error-typed-failure
  ;; S6b: judge error(不在パス)= attempt 1 消費して typed failure
  (setv world (FakeWorld))
  (seed-stalled world)
  (.extend world.judge-script [(judge-broken)])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "no-such-judge")))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.prompt-unblock-attempts 1))
  (assert (.startswith row.last-validation-error
                       "interactive-prompt-blocked: pane unchanged for over 180s and prompt judge failed:"))
  (assert (= row.terminal-cause.category "interactive_prompt_blocked")))


(deftest test-stall-judge-blocked-sends-keys
  ;; R5: blocked verdict → whitelist keys を送出して監視継続(1 tick 1 unblock)
  (setv world (FakeWorld))
  (seed-stalled world)
  (.extend world.judge-script [(judge-ok :keys ["Escape"])])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys [#("%1" "Escape" False False)]))
  (assert (= row.status "running"))
  (assert (= row.prompt-unblock-attempts 1))
  (assert (in #("s1" "session_prompt_unblocked") world.events)))


(deftest test-stall-unblock-vetoed-on-fresh-busy-capture
  ;; issue #573: stall 判定と judge の間に pane が動き出した(送出直前の
  ;; fresh capture に live active marker)— 中断キーは送らず veto を記帳し、
  ;; typed failure にも落とさず次 cycle の再観測に委ねる。
  (setv world (FakeWorld))
  (seed-stalled world)
  (setv (get world.frame-script "%1") [F-FROZEN F-BUSY-SPINNER-CLAUDE])
  (.extend world.judge-script [(judge-ok :keys ["Escape"])])
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd "scripted-judge")))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys []))
  (assert (in #("s1" "session_prompt_unblock_vetoed") world.events))
  (assert (not-in #("s1" "session_prompt_unblocked") world.events))
  (assert (= row.status "running")))


(deftest test-stall-with-latch-rate-limited
  ;; ACP ADR 0049 R9(S8e): 行動系終端の stall でも provider-limit 観測を
  ;; 先に見る — 上限文言 scroll out 後に凍結した pane(attempt 中の
  ;; blocked_api 観測が latch 済み)は interactive_prompt_blocked でなく
  ;; rate_limited/retryable=true(上限下の凍結は transient — ACP
  ;; rotation/exhaustion の発火面)。生 marker の同時成立は分類順
  ;; (api-limit → blocked_api ≠ running)により stall arm に到達しない —
  ;; latch が唯一の到達形。last_validation_error は S6b 文言 verbatim のまま。
  (setv world (FakeWorld))
  (seed-stalled world :api_limit_observed_at (iso-at world -400))
  (<- outcomes (run-cycle world (MonitorKnobs :judge-cmd None)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.last-validation-error
             "interactive-prompt-blocked: pane unchanged for over 180s and no prompt judge configured"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True))
  ;; cause reason は latch の証跡を運ぶ(S8c と同じ形)
  (assert (in "api-limit" row.terminal-cause.reason)))


;; ---------------------------------------------------------------------------
;; watchdog 群(S19: launch-timeout / stale-observation / zombie)+ 帯域外 kill(S9)
;; ---------------------------------------------------------------------------

(deftest test-monitor-leaves-booting-row-to-launch-pipeline
  ;; booting 所有権 arm(issue agentd-session-registration-after-ready-gate):
  ;; BOOTING 行は in-flight の launch pipeline が所有する — 登録が ready gate
  ;; より前になったため、配送中の pane は launch transport の中間状態(素の
  ;; shell 等)であり、観測 arm(zombie reaper・turn-end・solicitation)が
  ;; 誤読する。monitor は tmux にも store にも一切触れずに素通りする。
  ;; pane を「command 送出前の zsh + shell の ❯ prompt」にして、旧挙動なら
  ;; zombie reaper / turn-end が誤発火する最悪フレームで検証する。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :status "booting"
                        :awaiting-response False
                        :observed-active-at None
                        :last-observed-at None
                        :started-at (iso-at world -5))
        :frame F-IDLE-CLAUDE :pane-command "zsh")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "booting"))
  (assert (= world.capture-count 0))
  (assert (= world.has-session-calls []))
  (assert (= world.sent-keys []))
  (assert (= world.delivered []))
  (assert (= world.events [])))


(deftest test-monitor-reaps-booting-row-after-boot-timeout
  ;; boot watchdog: launch pipeline が死んで BOOTING が残置されたら terminal へ
  ;; (daemon crash mid-launch の受け皿)。予算は launch timeout + repl-idle
  ;; 予算(ready gate は正規に repl-idle 予算まで待つため)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :status "booting"
                        :awaiting-response False
                        :observed-active-at None
                        :last-observed-at None
                        :started-at (iso-at world -181))   ;; 60 + 120 + 1
        :frame F-IDLE-CLAUDE :pane-command "zsh")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (is-not row.finished-at None))
  (assert (= row.terminal-cause.category "timed_out"))
  (assert (= row.terminal-cause.retryable True))
  (assert (in "launch pipeline did not complete within 180s"
              row.last-validation-error))
  (assert (in #("s1" "session_launch_timeout") world.events))
  ;; reap の裁定自体は SQL 直行(pane の観測はしない)
  (assert (= world.capture-count 0))
  ;; 片付けは同 cycle の単一掃き取り(ADR-010 R5)— boot 残置も残骸に
  ;; しない(実測 2026-08-06: この経路の残骸 2 台)。
  (assert (in "doeff-s1" world.killed))
  (assert (is-not (. (get world.rows "s1") cleaned-at) None)))


(deftest test-launch-timeout-watchdog
  ;; F-frozen のまま startup 完了マーカー無し → launch timeout で failed・timed_out true
  (setv world (FakeWorld))
  (seed world (make-row world
                        :observed-active-at None
                        :started-at (iso-at world -61))
        :frame F-FROZEN)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.last-validation-error
             "launch timeout: never reached active state within 60s (stuck in startup — likely a hung MCP server)"))
  (assert (= row.terminal-cause.category "timed_out"))
  (assert (= row.terminal-cause.retryable True))
  (assert (in #("s1" "session_launch_timeout") world.events))
  ;; reap の裁定自体は pane を観測しない
  (assert (= world.capture-count 0))
  ;; 片付けは同 cycle の単一掃き取り(ADR-010 R5)— launch timeout は
  ;; 実測 2026-08-06 で残骸 16 台を産んだ最大の漏れ経路だった。
  (assert (in "doeff-s1" world.killed))
  (assert (is-not (. (get world.rows "s1") cleaned-at) None)))


(deftest test-launch-timeout-disarmed-after-startup
  ;; observed_active_at が立っていれば起動遅延では reap しない
  (setv world (FakeWorld))
  (seed world (make-row world :started-at (iso-at world -3600))
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running")))


(deftest test-launch-timeout-window-restarts-after-gap
  ;; ADR-DOE-AGENTS-009 R2: 観測断は「観測し続けたのに active を見ていない」
  ;; premise を void にする — gap 検出 cycle では started_at 基点の launch
  ;; timeout を発火させない(wedge 直前に running へ手渡された生存 agent を
  ;; timed_out で誤終端し ACP transient 自動再試行 = 二重実装を解放する形)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :started-at (iso-at world -3600)
                        :observed-active-at None
                        :last-observed-at (iso-at world -301))
        :frame F-FROZEN)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (is row.terminal-cause None))
  (assert (= row.observation-gap-at (iso-at world 0)))
  (assert (not-in #("s1" "session_launch_timeout") world.events)))


(deftest test-launch-timeout-fires-from-gap-base
  ;; ADR-DOE-AGENTS-009 R2 の有界性: 供給回復後(last_observed_at 前進済み)、
  ;; gap 基点から改めて launch timeout 秒の連続観測で active 未観測なら従来
  ;; どおり reap する — genuinely stuck な session は回復後に必ず終端に至る。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :started-at (iso-at world -3600)
                        :observed-active-at None
                        :last-observed-at (iso-at world -1)
                        :observation-gap-at (iso-at world -61))
        :frame F-FROZEN)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "timed_out"))
  (assert (= row.terminal-cause.retryable True))
  (assert (in #("s1" "session_launch_timeout") world.events)))


(deftest test-stale-observation-holds-and-records-gap
  ;; ADR-DOE-AGENTS-009 R1(観測断 ≠ 死亡): last_observed_at 凍結は観測経路の
  ;; 命題であり死亡命題ではない — terminal 化せず observation_gap_at へ刻印し、
  ;; session_observation_gap event を記帳して通常 arm へ fall-through する。
  ;; 生存 agent(F-ACTIVE-CODEX)はそのまま観測が再開され running を保つ。
  ;; 旧挙動(exited・cause lost・tmux probe 前 reap)は 2026-07-27 wedge で
  ;; tmux 生存 agent を死亡刻印した実 incident の根因。
  (setv world (FakeWorld))
  (seed world (make-row world :last-observed-at (iso-at world -301))
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (is row.terminal-cause None))
  (assert (is row.finished-at None))
  (assert (= row.observation-gap-at (iso-at world 0)))
  (assert (in #("s1" "session_observation_gap") world.events))
  (assert (not-in #("s1" "session_stale_reaped") world.events))
  ;; 観測は再開される: tmux probe + capture が実際に走った
  (assert (= world.has-session-calls ["doeff-s1"]))
  (assert (= world.capture-count 1))
  ;; 観測成功で last_observed_at も前進する(gap の再検出は止まる)
  (assert (= row.last-observed-at (iso-at world 0))))


(deftest test-stale-observation-gap-event-is-rate-bounded
  ;; ADR-DOE-AGENTS-009 R1 の journal 有界性(PR #564 肥大の再発防止):
  ;; 観測が不能なまま(capture が毎 cycle 失敗)でも、gap 検出条件は
  ;; observation_gap_at 自身で再武装されるため event は 1/stale-secs に有界。
  (setv world (FakeWorld))
  (seed world (make-row world :last-observed-at (iso-at world -301))
        :frame F-ACTIVE-CODEX)
  (.add world.broken-panes "%1")
  (<- outcomes1 (run-cycle world (MonitorKnobs)))
  (<- outcomes2 (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  ;; 供給断のままでは裁定素材が無い — unknown 保持(有界性は ACP deadman)
  (assert (= row.status "running"))
  (assert (is row.terminal-cause None))
  (setv gap-events (lfor e world.events :if (= e #("s1" "session_observation_gap")) e))
  (assert (= (len gap-events) 1)))


(deftest test-stale-observation-with-dead-session-vanishes
  ;; ADR-DOE-AGENTS-009 R1+R3: 観測断のあとの死亡裁定は第 2 証拠 arm が下す —
  ;; tmux session が実際に消えていれば同 cycle 内で exited・vanished に到達する
  ;; (観測断は裁定を遅らせない — 証拠さえあれば即終端)。
  (setv world (FakeWorld))
  (seed world (make-row world :last-observed-at (iso-at world -301))
        :frame F-IDLE-CODEX)
  (.discard world.tmux-sessions "doeff-s1")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "vanished"))
  (assert (= row.terminal-cause.retryable True))
  (assert (= row.terminal-cause.reason "tmux session disappeared"))
  (assert (in #("s1" "session_observation_gap") world.events))
  (assert (in #("s1" "session_exited") world.events)))


(deftest test-zombie-reaper-idle-shell
  ;; zombie: pane の foreground が idle shell へ戻った → exited・Vanished true
  ;; (第 2 証拠つき死亡 — ADR-DOE-AGENTS-009 R3 で lost から分割。ACP は
  ;; category≠lost → StatusExited で即時終端し deadman gate を待たない)
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX :pane-command "zsh")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "vanished"))
  (assert (= row.terminal-cause.retryable True))
  (assert (= row.terminal-cause.reason "tmux pane returned to idle shell: zsh"))
  (assert (= world.capture-count 0))
  (assert (in #("s1" "session_exited") world.events)))


(deftest test-tmux-gone-result-first-done
  ;; S9: 帯域外 kill でも result 報告済なら done(result-first)
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX)
  (.discard world.tmux-sessions "doeff-s1")
  (setv (get world.result-payloads "s1") "{\"ok\": true}")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "done"))
  (assert (= row.result-payload "{\"ok\": true}"))
  (assert (is-not row.finished-at None))
  (assert (in #("s1" "session_done") world.events)))


(deftest test-tmux-gone-without-result-vanished
  ;; S9: 未報告の帯域外 kill → exited・cause Vanished retryable=true
  ;; (第 2 証拠つき死亡 — ADR-DOE-AGENTS-009 R3 で lost から分割)
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX)
  (.discard world.tmux-sessions "doeff-s1")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "vanished"))
  (assert (= row.terminal-cause.retryable True))
  (assert (= row.terminal-cause.reason "tmux session disappeared"))
  (assert (in #("s1" "session_exited") world.events)))


;; ---------------------------------------------------------------------------
;; fast-path 群(paste 再送・managed dialog)
;; ---------------------------------------------------------------------------

(deftest test-unsubmitted-paste-resubmit
  ;; ハザード 4 付随物理: paste 残留 + awaiting → Enter 再送、latch は保持
  (setv world (FakeWorld))
  (seed world (make-row world :awaiting-response True)
        :frame (+ "<unsubmitted-paste>\n" F-IDLE-CODEX))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys [#("%1" "Enter" False False)]))
  (assert (= row.awaiting-response True))
  (assert (= row.status "running"))
  (assert (in #("s1" "session_unsubmitted_paste_resubmitted") world.events)))


(deftest test-managed-dialog-fast-path
  ;; S18: managed のみ monitor loop で発火(main.rs:3618)— Enter 送出 +
  ;; observed_active_at set(managed 分岐でしか立たない主 assert)
  (setv world (FakeWorld))
  (seed world (make-row world :observed-active-at None)
        :frame F-DIALOG-MANAGED)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= world.sent-keys [#("%1" "Enter" False False)]))
  (assert (is-not row.observed-active-at None))
  (assert (= row.status "running"))
  (assert (in #("s1" "session_observed") world.events)))


;; ---------------------------------------------------------------------------
;; taxonomy(凍結表・first-write-wins)
;; ---------------------------------------------------------------------------

(deftest test-taxonomy-first-write-wins
  ;; set_terminal_cause_if_absent + DB COALESCE も契約
  (setv world (FakeWorld))
  (setv pre-cause (make-cause "rate_limited" "pre-existing" (iso-at world -5)))
  (seed world (make-row world :terminal-cause pre-cause) :frame F-FAILED)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.reason "pre-existing")))


(defk failure-cause-for [frame]
  {:pre [(: frame str)]
   :post [(: % TerminalCause)]}
  (setv world (FakeWorld))
  (seed world (make-row world) :frame frame)
  (<- _ (run-cycle world (MonitorKnobs)))
  (. (get world.rows "s1") terminal-cause))


(deftest test-failed-output-cause-frozen-table
  ;; S7 + TerminalCause 凍結表(reason 無し failed のみ output 写像 — ハザード 2)
  (<- c-timeout (failure-cause-for "fatal error: request timeout"))
  (assert (= #(c-timeout.category c-timeout.retryable) #("timed_out" True)))
  (<- c-auth (failure-cause-for "fatal error: authentication failed"))
  (assert (= #(c-auth.category c-auth.retryable) #("runner_unavailable" False)))
  (<- c-proto (failure-cause-for "fatal error: invalid json body"))
  (assert (= #(c-proto.category c-proto.retryable) #("protocol_error" False)))
  (<- c-run (failure-cause-for F-FAILED))
  (assert (= #(c-run.category c-run.retryable) #("run_failed" False)))
  (<- c-rate (failure-cause-for (+ F-FAILED "\n" F-API-LIMIT)))
  (assert (= #(c-rate.category c-rate.retryable) #("rate_limited" True))))


(deftest test-terminal-cause-retryable-frozen-table
  ;; conformance README「TerminalCause 凍結表」の wire 値(serde snake_case)
  ;; での転記 — S3 が cause["category"] == "run_failed" を wire で assert する
  ;; とおり、category は snake_case が真(README の CamelCase はラベル)。
  ;; cancelled は host RPC(session.cancel / cleanup)所有で oracle が
  ;; retryable=false を明示(:1985-1991)。
  ;; ADR-DOE-AGENTS-009 R3(2026-07-28): lost は表から除去(観測断は terminal
  ;; cause として構築されない)。証拠つき死亡は vanished(retryable=true)。
  ;; ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8(2026-08-12):
  ;; prompt_undelivered を追加(retryable=true)— 起動段で prompt が一度も
  ;; 届かなかった attempt は timed_out の一形ではなく独立の分類。
  ;; ACP ADR 0049 R9 第 3 改訂(2026-08-12): provider 由来の非上限終端を 3 つ
  ;; 追加。auth_failed / transport_failed は retryable=true(identity・転送の
  ;; 一時状態 = 席の判断ではない)、context_exhausted は **retryable=false の
  ;; まま**(同じ封筒での再試行は同じ地点で死ぬ — hard rule 7)。追加は
  ;; additive で後方安全: 未知 category に対する下流の既定は
  ;; 「CommandNonZeroExit + causeRetryable 由来の retry 意味論」
  ;; (ACP Observed.hs failureKindForCause の `_` 分岐)。
  (assert (= TERMINAL-CAUSE-RETRYABLE
             {"rate_limited" True
              "timed_out" True
              "vanished" True
              "prompt_undelivered" True
              "auth_failed" True
              "transport_failed" True
              "context_exhausted" False
              "runner_unavailable" False
              "protocol_error" False
              "run_failed" False
              "interactive_prompt_blocked" False
              "cancelled" False})))


;; ---------------------------------------------------------------------------
;; lifecycle 分岐・per-session 隔離・knob 表
;; ---------------------------------------------------------------------------

(deftest test-interactive-turn-end-noop
  ;; Kind 1(interactive): idle 安定は「次の入力待ち」— 終端でも solicitation でもない
  (setv world (FakeWorld))
  (seed world (make-row world
                        :lifecycle "interactive"
                        :expected-result None
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (= world.delivered []))
  (assert (is None row.finished-at)))


(deftest test-rtc-without-contract-turn-end-done
  ;; RunToCompletion で contract 無し: turn-end 信号を work-end として信頼
  (setv world (FakeWorld))
  (seed world (make-row world
                        :expected-result None
                        :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "done"))
  (assert (in "doeff-s1" world.killed))
  (assert (in #("s1" "session_done") world.events)))


(deftest test-per-session-isolation
  ;; S16 / DOE-004 R3: 1 session の障害(capture 例外)は他 session を止めない。
  ;; 壊れた session がソート順で先に処理される配置にして隔離を証明する。
  (setv world (FakeWorld))
  (seed world (make-row world :session-id "bad" :session-name "doeff-bad" :pane-id "%9")
        :frame F-ACTIVE-CODEX)
  (seed world (make-row world :session-id "good" :session-name "doeff-good" :pane-id "%2")
        :frame F-ACTIVE-CODEX)
  (.add world.broken-panes "%9")
  (setv (get world.result-payloads "good") "{\"ok\": true}")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (= (get outcomes "bad") "error:RuntimeError"))
  (assert (= (get outcomes "good") "done"))
  (assert (= (. (get world.rows "good") status) "done")))


(deftest test-monitor-knobs-frozen-defaults
  ;; conformance README「testability knobs」表の既定値を凍結
  (setv knobs (MonitorKnobs))
  (assert (= knobs.prompt-stall-seconds 180))
  (assert (= knobs.result-solicitation-limit 2))
  (assert (= knobs.prompt-unblock-limit 3))
  (assert (= knobs.launch-timeout-seconds 60))
  (assert (= knobs.stale-observation-seconds 300))
  ;; ハザード 1: 既定 judge は無効(実モデル judge を起動しない)
  (assert (is None knobs.judge-cmd)))


;; ---------------------------------------------------------------------------
;; effect 語彙(deff 署名)の契約面
;; ---------------------------------------------------------------------------

(deftest test-effect-vocabulary-contracts
  ;; deff 署名 = 契約: 構築子は effect instance を返し、:pre が引数型を検査する
  (setv eff (tmux-capture "%1" 100))
  (assert (isinstance eff TmuxCapture))
  (assert (= #(eff.pane-id eff.lines) #("%1" 100)))
  (with [(pytest.raises AssertionError)]
    (tmux-capture 123 100))
  (assert (isinstance (build-launch "codex" {}) BuildLaunch))
  (assert (isinstance (pre-launch-setup "claude" {}) PreLaunchSetup))
  (assert (isinstance (classify-pane "codex" "› ") ClassifyPane))
  (assert (isinstance (deliver-message "%1" "hello") DeliverMessage))
  (assert (isinstance (wire-result-channel "codex" "s1" "/tmp/agentd.sock")
                      WireResultChannel))
  (assert (isinstance (session-store-list-active) SessionStoreListActive))
  (assert (isinstance (session-store-get "s1") SessionStoreGet))
  (assert (isinstance (session-store-result-payload "s1") SessionStoreResultPayload))
  (assert (isinstance (tmux-has-session "doeff-s1") TmuxHasSession))
  (assert (isinstance (tmux-pane-current-command "%1") TmuxPaneCurrentCommand))
  (assert (isinstance (tmux-send-keys "%1" "Enter" False False) TmuxSendKeys))
  (assert (isinstance (tmux-kill-session "doeff-s1") TmuxKillSession))
  (assert (isinstance (clock-now) ClockNow))
  (assert (isinstance (proc-run "scripted-judge" "pane text") ProcRun))
  ;; 署名と docstring が契約 — 全構築子に docstring があること
  (for [ctor [build-launch pre-launch-setup classify-pane deliver-message
              wire-result-channel session-store-list-active session-store-get
              session-store-upsert session-store-result-payload
              session-store-record-event tmux-has-session tmux-pane-current-command
              tmux-capture tmux-send-keys tmux-kill-session clock-now proc-run]]
    (assert ctor.__doc__ (str ctor))))


;; ---------------------------------------------------------------------------
;; ADR-DOE-AGENTS-006: 会話 identity の事後発見 arm(level-triggered)
;; ---------------------------------------------------------------------------

(deftest test-monitor-discovers-conversation
  ;; conversation 未確定の非終端行は毎 cycle 発見を試み、発見したら行へ書いて
  ;; session_conversation_discovered を積む。この arm は status を変えない。
  (setv world (FakeWorld))
  (setv world.discovered {"session_id" "conv-found" "rollout_path" "/r.jsonl"})
  (seed world (make-row world) :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv stored (get world.rows "s1"))
  (assert (= (get stored.conversation "session_id") "conv-found"))
  (assert (in #("s1" "session_conversation_discovered") world.events))
  (assert (= stored.status "running")))


(deftest test-monitor-discovery-absent-defers
  ;; 未発見(None)は行を変えず event も積まない — 次 cycle 再試行。
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv stored (get world.rows "s1"))
  (assert (is stored.conversation None))
  (assert (not-in #("s1" "session_conversation_discovered") world.events)))


(deftest test-monitor-discovery-skips-known-conversation
  ;; conversation 確定済みの行では発見 arm は走らない(上書きしない)。
  (setv world (FakeWorld))
  (setv world.discovered {"session_id" "conv-other"})
  (seed world (make-row world :conversation {"session_id" "conv-known"})
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv stored (get world.rows "s1"))
  (assert (= (get stored.conversation "session_id") "conv-known"))
  (assert (not-in #("s1" "session_conversation_discovered") world.events)))
