;;; 直接束縛 deftest: 共有 launch program(DOE-004 C2)の凍結物理検証。
;;;
;;; oracle: packages/doeff-agentd/src/main.rs session_launch + wait_for_repl_idle。
;;; 凍結物理:
;;;   - 重複 session / 既存 tmux session の reject
;;;   - S11 ゲートが tmux より前(per-kind PreLaunchSetup 経由)
;;;   - ADR 0035 reject-at-launch(result contract は codex/claude か明示 command)
;;;   - prompt は argv でなく live REPL へ(wait-for-repl-idle 後に paste)
;;;   - expected_result 付き prompt へ result-protocol instruction を追記
;;;   - R9 launch dialog の決定的 dismissal(wait-for-repl-idle 内)
;;;   - 実効 identity の session 行永続化(S14 の Hy positive 化)
;;;
;;; fake substrate(台本 tmux capture・進む clock・dict store)+ 両 impl を
;;; 直接束縛して launch-session を回す。生 IO ゼロ。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import datetime [datetime timezone timedelta])
(import json)
(import pytest)

(import doeff_agents.sessionhost.effects [
  SessionRow
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreRecordEvent
  TmuxHasSession
  TmuxNewSession
  TmuxCapture
  TmuxSendKeys
  ClockNow
  ClockSleep
  ClassifyPane
  DeliverMessage
  FsCanonicalPath
  FsReadText
  FsWriteTextAtomic
  FsMakeDirs
  EnvGet])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.impls.codex [codex-impl])
(import doeff_agents.sessionhost.launch [
  RESULT-PROTOCOL-INSTRUCTION
  launch-session
  shell-join])


;; ---------------------------------------------------------------------------
;; fake world(台本 capture・進む clock・記録一式)
;; ---------------------------------------------------------------------------

(defclass LaunchWorld []
  (defn __init__ [self]
    (setv self.rows {})
    (setv self.events [])
    (setv self.tmux-sessions (set))
    (setv self.capture-script [])   ;; 順に消費、尽きたら最後を保持
    (setv self.captures 0)
    (setv self.trace [])            ;; 効果の時系列(順序 assert 用)
    (setv self.sent-keys [])
    (setv self.delivered [])
    (setv self.fs {})
    (setv self.env {})
    (setv self.now (datetime 2026 7 5 12 0 0 :tzinfo timezone.utc))))


(defhandler fake-launch-substrate [world]
  (SessionStoreGet [session-id]
    (resume (.get world.rows session-id)))
  (SessionStoreUpsert [row]
    (.append world.trace #("upsert" row.session-id))
    (setv (get world.rows row.session-id) row)
    (resume None))
  (SessionStoreRecordEvent [session-id event-type row]
    (.append world.events #(session-id event-type))
    (resume None))
  (TmuxHasSession [session-name]
    (resume (in session-name world.tmux-sessions)))
  (TmuxNewSession [session-name work-dir env]
    (.append world.trace #("new-session" session-name))
    (.add world.tmux-sessions session-name)
    (resume "%7"))
  (TmuxCapture [pane-id lines]
    (setv world.captures (+ world.captures 1))
    (if world.capture-script
        (resume (if (> (len world.capture-script) 1)
                    (.pop world.capture-script 0)
                    (get world.capture-script 0)))
        (resume "")))
  (TmuxSendKeys [pane-id text literal submit]
    (.append world.trace #("send-keys" text))
    (.append world.sent-keys #(pane-id text literal submit))
    (resume None))
  (DeliverMessage [pane-id text]
    ;; impl の DeliverMessage は substrate TmuxSendKeys へ転送するが、
    ;; ここでは配送記録を直接取る(launch の配送内容 assert 用)
    (.append world.trace #("deliver" pane-id))
    (.append world.delivered #(pane-id text))
    (resume None))
  (ClockNow []
    (resume world.now))
  (ClockSleep [seconds]
    (setv world.now (+ world.now (timedelta :seconds seconds)))
    (resume None))
  (FsCanonicalPath [path]
    (resume path))
  (FsReadText [path]
    (resume (.get world.fs path)))
  (FsWriteTextAtomic [path text tmp-suffix]
    (.append world.trace #("fs-write" path))
    (setv (get world.fs path) text)
    (resume None))
  (FsMakeDirs [path]
    (resume None))
  (EnvGet [name]
    (resume (.get world.env name))))


(defn launch-params [#** overrides]
  (setv params {"session_id" "s1"
                "session_name" "doeff-s1"
                "agent_type" "codex"
                "work_dir" "/work/dir"
                "lifecycle" "run_to_completion"
                "session_env" {"CODEX_HOME" "/x/codex"}
                "prompt" "do the task"
                "command" None
                "expected_result" {"type" "object"}
                "model" None
                "effort" None
                "mcp_servers" {}
                "socket_path" "/tmp/agentd.sock"
                "skip_trust_setup" False})
  (.update params overrides)
  params)


(defk run-launch [world params]
  {:pre [(: world LaunchWorld) (: params dict)]
   :post [(: % "SessionRow(成功時)")]}
  (<- row ((fake-launch-substrate world)
           ((codex-impl "/opt/doeff-sessionhost")
            ((claude-code-impl "/opt/doeff-sessionhost")
             (launch-session params)))))
  row)


;; ---------------------------------------------------------------------------
;; golden path
;; ---------------------------------------------------------------------------

(deftest test-launch-codex-golden-path
  (setv world (LaunchWorld))
  ;; wait-for-repl-idle: 1 回目は banner(idle 無し)、以後 idle prompt
  (setv world.capture-script ["codex booting banner" "› "])
  (<- row (run-launch world (launch-params)))
  ;; trust 書き込み(PreLaunchSetup)が tmux new-session より前
  (setv trace-kinds (lfor t world.trace (get t 0)))
  (assert (< (.index trace-kinds "fs-write") (.index trace-kinds "new-session")))
  ;; 起動 command は shell-join 済み argv(--yolo + channel 配線)で literal+submit
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (= pane "%7"))
  (assert literal)
  (assert submit)
  (assert (.startswith cmd "codex --yolo"))
  (assert (in "doeff_result" cmd))
  (assert (in "report-result-mcp" cmd))
  ;; prompt は wait-for-repl-idle(idle 観測 — capture が実際に走った)後に
  ;; DeliverMessage → impl → TmuxSendKeys(literal+submit)で配送され、
  ;; result-protocol instruction が追記されている
  (assert (= world.captures 2))
  (setv [ppane ptext pliteral psubmit] (get world.sent-keys 1))
  (assert pliteral)
  (assert psubmit)
  (assert (.startswith ptext "do the task"))
  (assert (.endswith ptext RESULT-PROTOCOL-INSTRUCTION))
  ;; session 行: booting・awaiting latch 武装・実効 identity 永続化(S14)
  (setv stored (get world.rows "s1"))
  (assert (= stored.status "booting"))
  (assert stored.awaiting-response)
  (assert (= (get stored.effective-identity "CODEX_HOME") "/x/codex"))
  (assert (in #("s1" "session_started") world.events)))


(deftest test-launch-claude-uses-claude-impl
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :session_env {"CLAUDE_CONFIG_DIR" "/x/claude"})))
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (.startswith cmd "claude --dangerously-skip-permissions"))
  (assert (in "disableAllHooks" cmd))
  ;; trust pre-seed が .claude.json に書かれている
  (assert (in "/x/claude/.claude.json" world.fs))
  (assert (= (get row.effective-identity "CLAUDE_CONFIG_DIR") "/x/claude")))


;; ---------------------------------------------------------------------------
;; reject 経路(すべて tmux 効果ゼロ)
;; ---------------------------------------------------------------------------

(deftest test-launch-rejects-duplicate-session
  (setv world (LaunchWorld))
  (setv (get world.rows "s1")
        (SessionRow :session-id "s1" :session-name "doeff-s1" :pane-id "%1"
                    :agent-type "codex" :lifecycle "run_to_completion"
                    :status "running" :started-at "2026-07-05T00:00:00+00:00"))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params)))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "already registered" (str raised)))
  (assert (not-in "new-session" (lfor t world.trace (get t 0)))))


(deftest test-launch-rejects-existing-tmux-session
  (setv world (LaunchWorld))
  (.add world.tmux-sessions "doeff-s1")
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params)))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "tmux session already exists" (str raised))))


(deftest test-launch-codex-gate-fires-before-tmux
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params :session_env {})))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "no agent auth profile" (str raised)))
  ;; tmux 痕跡ゼロ・session 行無し(S11 の直接束縛版)
  (assert (not-in "new-session" (lfor t world.trace (get t 0))))
  (assert (= world.rows {})))


(deftest test-launch-rejects-unsupported-lifecycle
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params :lifecycle "oneshot")))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "unsupported session lifecycle" (str raised))))


(deftest test-launch-reject-at-launch-gate
  ;; ADR 0035: result contract を配線できない agent は受けない
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params :agent_type "generic")))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "cannot deliver a result" (str raised))))


;; ---------------------------------------------------------------------------
;; command override(escape hatch)
;; ---------------------------------------------------------------------------

(deftest test-launch-command-override-verbatim
  (setv world (LaunchWorld))
  (setv world.capture-script ["› "])
  (<- row (run-launch world (launch-params
                              :agent_type "generic"
                              :command "/usr/bin/fake-agent --serve"
                              :session_env {})))
  ;; command は verbatim・wait-for-repl-idle は走らない(capture 0 回)
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (= cmd "/usr/bin/fake-agent --serve"))
  (assert (= world.captures 0))
  ;; expected_result があるので instruction は付く(oracle: override でも付く)
  (setv [ppane ptext pliteral psubmit] (get world.sent-keys 1))
  (assert (.endswith ptext RESULT-PROTOCOL-INSTRUCTION)))


(deftest test-launch-command-override-mentioning-codex-gated
  ;; oracle command_mentions_codex: 明示 command が codex を起動するなら
  ;; CODEX_HOME ゲートは同じく効く
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params
                              :agent_type "generic"
                              :command "codex --yolo"
                              :session_env {})))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "no agent auth profile" (str raised))))


;; ---------------------------------------------------------------------------
;; R9 launch dialog fast-path(wait-for-repl-idle 内)
;; ---------------------------------------------------------------------------

(deftest test-launch-dismisses-update-dialog-then-delivers
  (setv world (LaunchWorld))
  (setv update-frame (+ "✨ Update available!\n"
                        "› 1. Update now (runs npm install)\n"
                        "  2. Skip\n"
                        "  3. Skip until next version\n"
                        "Press enter to continue"))
  (setv world.capture-script [update-frame "› "])
  (<- row (run-launch world (launch-params)))
  ;; Down Down Enter が(起動 command の後・prompt 配送の前に)送られている
  (setv keys (lfor [p t l s] world.sent-keys :if (not l) t))
  (assert (= keys ["Down" "Down" "Enter"]))
  ;; literal 送出は起動 command と prompt の 2 本で、prompt が最後
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 2))
  (assert (.endswith (get literal-texts 1) RESULT-PROTOCOL-INSTRUCTION)))


;; ---------------------------------------------------------------------------
;; shell-join(oracle shell_quote 物理)
;; ---------------------------------------------------------------------------

(deftest test-shell-join-quote-physics
  ;; 安全文字はそのまま・危険文字は single-quote・埋め込み quote はエスケープ
  (assert (= (shell-join ["codex" "--yolo"]) "codex --yolo"))
  (assert (= (shell-join ["a b"]) "'a b'"))
  (assert (= (shell-join ["it's"]) "'it'\\''s'"))
  (assert (= (shell-join [""]) "''"))
  (assert (= (shell-join ["-_./:=@,%+"]) "-_./:=@,%+")))
