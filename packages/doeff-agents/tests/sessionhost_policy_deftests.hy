;;; 直接束縛 deftest: session-host 共有 policy program の全分岐検証(DOE-004 C1)。
;;;
;;; daemon 不要 — fake substrate handler(dict-backed SessionStore・F-* フレーム
;;; 台本 Tmux・固定 Clock・台本 judge Proc)で policy program を直接束縛して回す。
;;; フレーム語彙・TerminalCause 表・knob 表・文言は conformance README
;;; (packages/doeff-agents/conformance/README.md、CONTRACT FIXED 2026-07-05)から
;;; verbatim 転記。oracle: packages/doeff-agentd/src/main.rs monitor_once。

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
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds
  DiscoverConversation
  TmuxHasSession
  TmuxPaneCurrentCommand
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
  RESULT-SOLICITATION-MESSAGE
  TERMINAL-CAUSE-RETRYABLE
  make-cause
  tail-chars
  tail-lower
  monitor-cycle])


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
    (setv self.tmux-sessions (set))    ;; 生きている tmux session 名
    (setv self.sent-keys [])           ;; [(pane-id, text, literal, submit)]
    (setv self.delivered [])           ;; [(pane-id, text)] — DeliverMessage 受領
    (setv self.killed [])              ;; kill された session 名
    (setv self.has-session-calls [])   ;; TmuxHasSession probe の記録
    (setv self.capture-count 0)
    (setv self.proc-calls [])          ;; [(command, stdin)]
    (setv self.judge-script [])        ;; 順に pop される ProcResult 台本
    (setv self.broken-panes (set))     ;; capture が例外を投げる pane(隔離検証用)
    (setv self.discovered None)        ;; DiscoverConversation の台本(ADR-006)
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
  "row を store に置き、tmux session / pane / フレームを生かす。"
  (setv (get world.rows row.session-id) row)
  (.add world.tmux-sessions row.session-name)
  (setv (get world.pane-commands row.pane-id) (.get kw "pane_command" "codex"))
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
  (setv api-limit (or (in "rate limit exceeded" lower30)
                      (in "rate limit reached" lower30)
                      (in "quota exceeded" lower30)
                      (in "insufficient quota" lower30)))
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
  (PaneObservation
    :has-failure-marker failure
    :has-api-limit-marker api-limit
    :has-waiting-marker waiting
    :has-idle-prompt idle
    :has-active-marker active
    :has-turn-activity turn-activity
    :startup-finished startup
    :has-unsubmitted-paste (in "<unsubmitted-paste>" output)
    :dialog (if managed "managed" None)
    :dialog-dismiss-keys (if managed #("Enter") #())))


(defhandler fake-substrate [world]
  "直接束縛用 fake handler: substrate(SessionStore / Tmux / Clock / Proc)+
   monitor policy が yield する interface effect(ClassifyPane / DeliverMessage)。"

  (SessionStoreListActive []
    (resume (lfor r (list (.values world.rows)) :if (in r.status ACTIVE-STATUSES) r)))

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

  (SessionStoreRecordEvent [session-id event-type row]
    (.append world.events #(session-id event-type))
    (resume None))

  (TmuxHasSession [session-name]
    (.append world.has-session-calls session-name)
    (resume (in session-name world.tmux-sessions)))

  (TmuxPaneCurrentCommand [pane-id]
    (resume (.get world.pane-commands pane-id)))

  (TmuxCapture [pane-id lines]
    (when (in pane-id world.broken-panes)
      (raise (RuntimeError f"tmux capture failed for pane {pane-id}")))
    (setv world.capture-count (+ world.capture-count 1))
    (resume (.get world.frames pane-id "")))

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


;; ---------------------------------------------------------------------------
;; watchdog 群(S19: launch-timeout / stale-observation / zombie)+ 帯域外 kill(S9)
;; ---------------------------------------------------------------------------

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
  ;; SQL 直行 reap: tmux には触れない
  (assert (= world.capture-count 0))
  (assert (= world.has-session-calls [])))


(deftest test-launch-timeout-disarmed-after-startup
  ;; observed_active_at が立っていれば起動遅延では reap しない
  (setv world (FakeWorld))
  (seed world (make-row world :started-at (iso-at world -3600))
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running")))


(deftest test-stale-observation-reaper-before-tmux
  ;; last_observed_at 凍結 → tmux probe より前に exited・Lost true で reap
  (setv world (FakeWorld))
  (seed world (make-row world :last-observed-at (iso-at world -301))
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "lost"))
  (assert (= row.terminal-cause.retryable True))
  (assert (= row.terminal-cause.reason "no monitor observation for more than 300s"))
  (assert (in #("s1" "session_stale_reaped") world.events))
  (assert (= world.capture-count 0))
  (assert (= world.has-session-calls [])))


(deftest test-zombie-reaper-idle-shell
  ;; zombie: pane の foreground が idle shell へ戻った → exited・Lost true
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX :pane-command "zsh")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "lost"))
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


(deftest test-tmux-gone-without-result-lost
  ;; S9: 未報告の帯域外 kill → exited・cause Lost retryable=true
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-IDLE-CODEX)
  (.discard world.tmux-sessions "doeff-s1")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "lost"))
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
  (assert (= TERMINAL-CAUSE-RETRYABLE
             {"rate_limited" True
              "timed_out" True
              "lost" True
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
