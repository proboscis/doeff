;;; 直接束縛 deftest: issue #568 — 未送信 prompt の検知と有界補償 + 終端 session の
;;; 単一掃き取り(ADR-DOE-AGENTS-010)。
;;;
;;; TDD red first(2026-08-06): このファイルは新契約を assert する — 実装前は
;;; module import(新 effect 語彙 TmuxPaneSessionName / SessionStoreListCleanupPending)
;;; の時点で失敗する。fake substrate は sessionhost_policy_deftests.hy と同型
;;; (dict-backed store・台本 tmux・固定 clock)+ pane 帰属台帳(R4)と
;;; cleanup-pending 一覧(R5)を加えたもの。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import dataclasses [replace])
(import datetime [datetime timezone timedelta])
(import json)
(import os)
(import shutil)
(import tempfile)

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  PaneObservation
  ProcResult
  MonitorKnobs
  ClassifyPane
  DeliverMessage
  DiscoverConversation
  SessionStoreListActive
  SessionStoreListCleanupPending
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds
  TmuxHasSession
  TmuxPaneCurrentCommand
  TmuxSessionPaneIds
  TmuxCapture
  TmuxSendKeys
  TmuxKillSession
  ClockNow
  ProcRun])
(import doeff_agents.sessionhost.policy [
  ACTIVE-STATUSES
  TERMINAL-STATUSES
  RESULT-SOLICITATION-MESSAGE
  tail-chars
  tail-lower
  monitor-cycle])


;; ---------------------------------------------------------------------------
;; 凍結フレーム語彙(sessionhost_policy_deftests.hy と同じ断片)
;; ---------------------------------------------------------------------------

(setv F-IDLE-CODEX "› ")
(setv F-IDLE-CLAUDE "❯")
(setv F-ACTIVE-CODEX "working (12s • esc to interrupt)")
(setv F-FROZEN "==> restricted login <==\n-- more --")


;; ---------------------------------------------------------------------------
;; fake substrate world(policy deftests と同型 + pane 帰属 + cleanup-pending)
;; ---------------------------------------------------------------------------

(defclass FakeWorld []
  (defn __init__ [self]
    (setv self.rows {})
    (setv self.result-payloads {})
    (setv self.events [])
    (setv self.frames {})
    (setv self.pane-commands {})
    (setv self.pane-sessions {})       ;; pane-id -> 帰属 tmux session 名(R4)
    (setv self.tmux-sessions (set))
    (setv self.sent-keys [])
    (setv self.delivered [])
    (setv self.killed [])
    (setv self.has-session-calls [])
    (setv self.capture-count 0)
    (setv self.proc-calls [])
    (setv self.judge-script [])
    (setv self.discovered None)
    (setv self.now (datetime 2026 8 6 12 0 0 :tzinfo timezone.utc))))


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
  "policy deftests と同じ台本分類器(<unsubmitted-paste> sentinel)。"
  (setv lower10 (tail-lower output 10))
  (setv lower30 (tail-lower output 30))
  (setv failure (in "fatal error" lower10))
  (setv api-limit (in "rate limit exceeded" lower30))
  (setv waiting (in "Type your message" output))
  (setv idle (or (.startswith output "› ")
                 (in "\n› " output)
                 (any (gfor line (.splitlines output) (.startswith line "❯")))))
  (setv active (or (in "working (" lower30) (in "esc to interrupt" lower30)))
  (setv turn-activity (or (in "⏺" output) (in "⎿" output)))
  (PaneObservation
    :has-failure-marker failure
    :has-api-limit-marker api-limit
    :has-waiting-marker waiting
    :has-idle-prompt idle
    :has-active-marker active
    :has-turn-activity turn-activity
    :startup-finished (or active idle turn-activity)
    :has-unsubmitted-paste (in "<unsubmitted-paste>" output)
    :dialog None
    :dialog-dismiss-keys #()))


(defhandler fake-substrate [world]
  (SessionStoreListActive []
    (resume (lfor r (list (.values world.rows)) :if (in r.status ACTIVE-STATUSES) r)))

  (SessionStoreListCleanupPending []
    ;; R5 の対象集合: 終端 ∧ cleaned_at 未刻印 ∧ RTC ∧ 非 adopted。
    (resume (lfor r (list (.values world.rows))
                  :if (and (in r.status TERMINAL-STATUSES)
                           (is r.cleaned-at None)
                           (= r.lifecycle "run_to_completion")
                           (not r.adopted))
                  r)))

  (SessionStoreGet [session-id]
    (resume (.get world.rows session-id)))

  (SessionStoreUpsert [row]
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
    (resume (sorted (sfor r (list (.values world.rows))
                          :if (is-not r.conversation None)
                          (get r.conversation "session_id")))))

  (DiscoverConversation [agent-type params]
    (resume world.discovered))

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


;; ---------------------------------------------------------------------------
;; R2: paste 再送補償器の有界化
;; ---------------------------------------------------------------------------

(deftest test-paste-resubmit-increments-durable-counter
  ;; budget 内の再送は従来物理(Enter + event + latch 保持)のまま、durable
  ;; counter が 1 進む。
  (setv world (FakeWorld))
  (seed world (make-row world :awaiting-response True)
        :frame (+ "<unsubmitted-paste>\n" F-IDLE-CODEX))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.paste-resubmit-attempts 1))
  (assert (= row.status "running"))
  (assert (= row.awaiting-response True))
  (assert (in #("%1" "Enter" False False) world.sent-keys))
  (assert (in #("s1" "session_unsubmitted_paste_resubmitted") world.events)))


(deftest test-paste-resubmit-budget-exhaustion-terminalizes
  ;; budget(5)超過 → loud typed terminal: failed・reason 接頭
  ;; `unsubmitted-prompt:`・cause prompt_undelivered retryable=true。同 cycle の
  ;; 掃き取りが tmux を kill し cleaned_at を刻む(無限 blocked の構造的禁止)。
  ;; ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8(010 R2 改訂 2026-08-12):
  ;; category は timed_out から prompt_undelivered へ。この arm が終端させる行は
  ;; turn が一度も始まっていない(実測 25 件全数で turn-activity marker 不在)—
  ;; 起動段 gate の失敗と同じ命題であり、2 category に割れていると未配達の
  ;; 集計が割れる。retryable=true は不変。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :awaiting-response True
                        :paste-resubmit-attempts 5)
        :frame (+ "<unsubmitted-paste>\n" F-IDLE-CODEX))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (.startswith row.last-validation-error "unsubmitted-prompt:"))
  (assert (= row.terminal-cause.category "prompt_undelivered"))
  (assert (= row.terminal-cause.retryable True))
  (assert (is-not row.finished-at None))
  (assert (in #("s1" "session_failed") world.events))
  ;; Enter は撃たない(budget 超過)
  (assert (not-in #("%1" "Enter" False False) world.sent-keys))
  ;; 同 cycle の掃き取りで残骸ゼロ
  (assert (in "doeff-s1" world.killed))
  (assert (is-not (. (get world.rows "s1") cleaned-at) None)))


;; ---------------------------------------------------------------------------
;; R3: awaiting_response latch の期限(#582 穴 c の根治)
;; ---------------------------------------------------------------------------

(deftest test-awaiting-response-deadline-terminalizes
  ;; 促し/prompt 配送から期限(600s)を超えて正の作業証拠が無い →
  ;; failed・reason 接頭 `awaiting-response timeout:`・timed_out retryable=true。
  ;; 2026-08-06 実 wedge の pane 形(挨拶画面 + 空の入力欄)そのもの。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -601)
                        :observed-active-at None)
        :frame F-IDLE-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (.startswith row.last-validation-error "awaiting-response timeout:"))
  (assert (= row.terminal-cause.category "timed_out"))
  (assert (= row.terminal-cause.retryable True))
  (assert (is-not row.finished-at None))
  (assert (in #("s1" "session_failed") world.events))
  ;; 同 cycle の掃き取りで残骸ゼロ
  (assert (in "doeff-s1" world.killed))
  (assert (is-not (. (get world.rows "s1") cleaned-at) None)))


(deftest test-awaiting-response-deadline-not-expired-holds
  ;; 期限内は従来どおり非終端(latch は turn-end を塞いだまま)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -30))
        :frame F-IDLE-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (in row.status ACTIVE-STATUSES))
  (assert (is row.terminal-cause None)))


(deftest test-awaiting-response-deadline-window-restarts-after-gap
  ;; ADR-DOE-AGENTS-009 と同型: 観測断の窓は「観測し続けたのに証拠が無い」
  ;; premise を void にする — 期限の基点は max(since, observation_gap_at)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -3600)
                        :observation-gap-at (iso-at world -60))
        :frame F-IDLE-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (in row.status ACTIVE-STATUSES))
  (assert (is row.terminal-cause None)))


(deftest test-awaiting-response-since-cleared-by-work-evidence
  ;; 正の作業証拠は latch と since を同時に clear する(期限の再武装は
  ;; 次の solicitation まで起きない)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -3600))
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "running"))
  (assert (= row.awaiting-response False))
  (assert (is row.awaiting-response-since None))
  (assert (is row.terminal-cause None)))


(deftest test-awaiting-response-legacy-row-falls-back-to-started-at
  ;; since 未打刻の legacy 行(migration 前の残存)は started_at を基点に
  ;; 期限が効く — どの awaiting 行も無限 blocked に戻れない。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :awaiting-response True
                        :started-at (iso-at world -3600))
        :frame F-IDLE-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (.startswith row.last-validation-error "awaiting-response timeout:")))


(deftest test-awaiting-response-deadline-distills-api-limit-latch
  ;; 行動系終端は provider-limit 観測を先に見る(ACP ADR 0049 R9 と同じ蒸留):
  ;; attempt 中に blocked_api を latch 済みなら rate_limited/retryable=true。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :agent-type "claude"
                        :awaiting-response True
                        :awaiting-response-since (iso-at world -601)
                        :api-limit-observed-at (iso-at world -300))
        :frame F-IDLE-CLAUDE)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "rate_limited"))
  (assert (= row.terminal-cause.retryable True)))


(deftest test-solicitation-rearm-stamps-awaiting-since
  ;; solicitation の latch 再武装は期限の基点も再打刻する。
  (setv world (FakeWorld))
  (seed world (make-row world :output-snippet (tail-chars F-IDLE-CODEX 500))
        :frame F-IDLE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.awaiting-response True))
  (assert (= row.awaiting-response-since (iso-at world 0))))


;; ---------------------------------------------------------------------------
;; R4: 宛先 pane の帰属検証(#582 穴 a/b の根治)
;; ---------------------------------------------------------------------------

(deftest test-pane-ownership-mismatch-vanishes
  ;; row.pane_id が別 session に帰属(pane 番号の再利用)→ 観測・送出せず
  ;; exited + vanished で即時終端。以降どの送出 arm にも到達しない。
  (setv world (FakeWorld))
  (seed world (make-row world :awaiting-response True)
        :frame (+ "<unsubmitted-paste>\n" F-IDLE-CODEX))
  (setv (get world.pane-sessions "%1") "someone-elses-session")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "vanished"))
  (assert (= row.terminal-cause.retryable True))
  (assert (in "%1" row.terminal-cause.reason))
  (assert (in "no longer belongs to session doeff-s1" row.terminal-cause.reason))
  ;; 帰属不一致の pane へは何も送らない・観測もしない
  (assert (= world.sent-keys []))
  (assert (= world.delivered []))
  (assert (= world.capture-count 0))
  (assert (in #("s1" "session_exited") world.events)))


(deftest test-pane-gone-while-session-alive-vanishes
  ;; pane 自体の消滅(帰属 None)も同じ積極証拠 — 盲目送出はしない。
  (setv world (FakeWorld))
  (seed world (make-row world))
  (.pop world.pane-sessions "%1")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "exited"))
  (assert (= row.terminal-cause.category "vanished"))
  (assert (= world.sent-keys []))
  (assert (= world.delivered [])))


;; ---------------------------------------------------------------------------
;; R5: 終端 session の単一掃き取り
;; ---------------------------------------------------------------------------

(deftest test-terminal-sweep-collects-every-terminal-path
  ;; どの終わり方(failed / exited / stopped / cancelled)で終端した行も、
  ;; 単一の掃き取りが tmux を kill し cleaned_at を刻む — 経路別の片付け
  ;; 命令は存在しない(existing 残骸 26 台の回収と同じ機構)。
  (setv world (FakeWorld))
  (for [[sid status] [#("f1" "failed") #("e1" "exited")
                      #("st1" "stopped") #("c1" "cancelled")]]
    (seed world (make-row world
                          :session-id sid
                          :session-name f"doeff-{sid}"
                          :pane-id f"%{sid}"
                          :status status
                          :finished-at (iso-at world -3600))))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (for [sid ["f1" "e1" "st1" "c1"]]
    (setv row (get world.rows sid))
    (assert (is-not row.cleaned-at None) f"cleaned_at not stamped for {sid}")
    (assert (in f"doeff-{sid}" world.killed) f"tmux not killed for {sid}")
    (assert (in #(sid "session_cleaned") world.events))))


(deftest test-terminal-sweep-stamps-rows-whose-tmux-is-already-gone
  ;; tmux session が既に無い終端行にも cleaned_at を刻む(掃き取りの対象
  ;; 集合が有界に収束する — 台帳の古い残骸行を毎 cycle 再走査しない)。
  (setv world (FakeWorld))
  (seed world (make-row world :status "exited" :finished-at (iso-at world -3600)))
  (.discard world.tmux-sessions "doeff-s1")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (is-not row.cleaned-at None))
  (assert (not-in "doeff-s1" world.killed)))


(deftest test-terminal-sweep-respects-reap-exemption
  ;; 刈り取り免除(ADR-DOE-AGENTS-007 安全条項 1)の継承: adopted 行と
  ;; 非 RTC(interactive)行は終端でも掃き取らない。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :session-id "ad1" :session-name "doeff-ad1" :pane-id "%a"
                        :status "stopped" :adopted True))
  (seed world (make-row world
                        :session-id "in1" :session-name "doeff-in1" :pane-id "%i"
                        :status "stopped" :lifecycle "interactive"))
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (is (. (get world.rows "ad1") cleaned-at) None))
  (assert (is (. (get world.rows "in1") cleaned-at) None))
  (assert (= world.killed []))
  (assert (in "doeff-ad1" world.tmux-sessions))
  (assert (in "doeff-in1" world.tmux-sessions)))


(deftest test-terminal-sweep-does-not-kill-name-reused-by-active-row
  ;; session 名は呼び手採番で時間軸上再利用され得る — active 行が同名を
  ;; 主張しているときは kill せず cleaned_at のみ刻む(古い残骸行の名で
  ;; 生きている新席を殺さない)。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :session-id "old" :session-name "doeff-shared" :pane-id "%old"
                        :status "failed" :finished-at (iso-at world -86400)))
  (seed world (make-row world
                        :session-id "new" :session-name "doeff-shared" :pane-id "%new")
        :frame F-ACTIVE-CODEX)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (assert (is-not (. (get world.rows "old") cleaned-at) None))
  (assert (not-in "doeff-shared" world.killed))
  (assert (in "doeff-shared" world.tmux-sessions))
  (assert (= (. (get world.rows "new") status) "running")))


(deftest test-launch-timeout-residue-swept-same-cycle
  ;; 起動時間切れ(実測で残骸 16 台を産んだ経路)も同 cycle の掃き取りが
  ;; 拾う — 終端 arm は片付けを知らないまま、残骸ゼロが成立する。
  (setv world (FakeWorld))
  (seed world (make-row world
                        :observed-active-at None
                        :started-at (iso-at world -61))
        :frame F-FROZEN)
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "failed"))
  (assert (= row.terminal-cause.category "timed_out"))
  (assert (in "doeff-s1" world.killed))
  (assert (is-not (. (get world.rows "s1") cleaned-at) None)))


(deftest test-done-cleanup-preserved-with-cleaned-at
  ;; 受入条件 (iii): 既存の正常片付け経路(done)は壊れない — result-first の
  ;; done は同 cycle に kill + cleaned_at 刻印のまま。
  (setv world (FakeWorld))
  (seed world (make-row world) :frame F-ACTIVE-CODEX)
  (setv (get world.result-payloads "s1") "{\"ok\": true}")
  (<- outcomes (run-cycle world (MonitorKnobs)))
  (setv row (get world.rows "s1"))
  (assert (= row.status "done"))
  (assert (in "doeff-s1" world.killed))
  (assert (is-not row.cleaned-at None))
  (assert (in #("s1" "session_done") world.events)))


;; ---------------------------------------------------------------------------
;; store 層: additive 列 2 本 + cleanup-pending 一覧 + latch clear の since 同時 clear
;; ---------------------------------------------------------------------------

(defn with-tmp-conn [thunk]
  (import doeff_agents.sessionhost.store [open-conn db-migrate])
  (setv d (tempfile.mkdtemp))
  (try
    (setv conn (open-conn (os.path.join d "agentd.sqlite")))
    (try
      (db-migrate conn)
      (thunk conn)
      (finally (.close conn)))
    (finally (shutil.rmtree d :ignore-errors True))))


(defn make-snap [session-id #** overrides]
  (setv base {"session_id" session-id
              "session_name" f"name-{session-id}"
              "pane_id" "%1"
              "agent_type" "claude"
              "work_dir" "/tmp/w"
              "lifecycle" "run_to_completion"
              "status" "running"
              "backend_kind" "tmux"
              "backend_ref" {"session_name" f"name-{session-id}"
                             "pane_id" "%1"
                             "command" "claude"}
              "started_at" "2026-08-06T00:00:00+00:00"
              "last_observed_at" None
              "finished_at" None
              "cleaned_at" None
              "pr_url" None
              "output_snippet" None
              "terminal_cause" None
              "expected_result" None
              "retries_used" 0
              "last_validation_error" None
              "awaiting_response" False
              "observed_active_at" None
              "result_payload" None
              "result_solicitations_used" 0
              "prompt_unblock_attempts" 0
              "last_output_change_at" None
              "effective_identity" None})
  (.update base overrides)
  base)


(deftest test-store-roundtrip-new-columns
  (import doeff_agents.sessionhost.store [db-upsert-snapshot db-session-get
                                          snapshot-to-policy-row])
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "b1"
                                        :paste_resubmit_attempts 3
                                        :awaiting_response_since "2026-08-06T01:00:00+00:00"))
    (setv snap (db-session-get conn "b1"))
    (assert (= (get snap "paste_resubmit_attempts") 3))
    (assert (= (get snap "awaiting_response_since") "2026-08-06T01:00:00+00:00"))
    (setv row (snapshot-to-policy-row snap))
    (assert (= row.paste-resubmit-attempts 3))
    (assert (= row.awaiting-response-since "2026-08-06T01:00:00+00:00")))
  (with-tmp-conn check))


(deftest test-store-list-cleanup-pending-filter
  ;; 対象集合 = 終端 ∧ cleaned_at IS NULL ∧ RTC ∧ 非 adopted のみ。
  (import doeff_agents.sessionhost.store [db-upsert-snapshot
                                          db-session-list-cleanup-pending])
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "in-scope" :status "failed"))
    (db-upsert-snapshot conn (make-snap "already-cleaned" :status "done"
                                        :cleaned_at "2026-08-06T00:00:00+00:00"))
    (db-upsert-snapshot conn (make-snap "interactive" :status "stopped"
                                        :lifecycle "interactive"))
    (db-upsert-snapshot conn (make-snap "adopted" :status "stopped"
                                        :adopted True))
    (db-upsert-snapshot conn (make-snap "still-active" :status "running"))
    (setv pending (db-session-list-cleanup-pending conn))
    (assert (= (lfor s pending (get s "session_id")) ["in-scope"])))
  (with-tmp-conn check))


(deftest test-store-clear-awaiting-latch-clears-since
  ;; 起動時 latch clear は awaiting_response_since も同時に消す(期限の基点が
  ;; 死んだ process の配送に束縛されているのは latch と同じ)。
  (import doeff_agents.sessionhost.store [db-upsert-snapshot db-session-get
                                          db-clear-awaiting-latches])
  (defn check [conn]
    (db-upsert-snapshot conn (make-snap "a1" :awaiting_response True
                                        :awaiting_response_since "2026-08-06T01:00:00+00:00"
                                        :status "running"))
    (db-clear-awaiting-latches conn)
    (setv snap (db-session-get conn "a1"))
    (assert (= (get snap "awaiting_response") False))
    (assert (is (get snap "awaiting_response_since") None)))
  (with-tmp-conn check))
