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
;;;   - repl-idle 予算切れは fail-closed(prompt 未配送・session 掃除・
;;;     画面 tail 込み typed error — 2026-07-07 契約修正、oracle からの
;;;     意図的乖離。予算は host 注入 knob で圧縮可)
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
  TmuxKillSession
  ClockNow
  ClockSleep
  ClassifyPane
  DeliverMessage
  FsCanonicalPath
  FsComposeHomeView
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
    (setv self.canonical {})        ;; path → realpath(FsCanonicalPath の台本)
    (setv self.tmux-envs {})        ;; session-name → new-session に渡った env
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
    (setv (get world.tmux-envs session-name) (dict env))
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
  (TmuxKillSession [session-name]
    (.append world.trace #("kill-session" session-name))
    (.discard world.tmux-sessions session-name)
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
    (resume (.get world.canonical path path)))
  (FsComposeHomeView [auth-file profile-dir view-root]
    (.append world.trace #("compose-view" auth-file profile-dir view-root))
    (resume f"{view-root}/composed-view"))
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
                "binding" {"kind" "codex" "codex_home" "/x/codex"}
                "session_env" {}
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
  ;; R7: binding 由来の auth env は host が合成して tmux env に載せる
  ;; (session_env 経由ではない)
  (assert (= (get (get world.tmux-envs "doeff-s1") "CODEX_HOME") "/x/codex"))
  (assert (in #("s1" "session_started") world.events)))


(deftest test-launch-claude-uses-claude-impl
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude"})))
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
    (<- _ (run-launch world (launch-params :binding None)))
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
    (<- _ (run-launch world (launch-params :agent_type "generic" :binding None)))
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
                              :binding None
                              :command "/usr/bin/fake-agent --serve")))
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
                              :binding None
                              :command "codex --yolo")))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "no agent auth profile" (str raised))))


;; ---------------------------------------------------------------------------
;; R7: launch effect は auth-blind — binding が auth を運び、
;; session_env は非 auth overlay(ADR-DOE-AGENTS-004 R7)
;; ---------------------------------------------------------------------------

(deftest test-launch-allows-non-auth-overlay-env
  ;; 非 auth の per-launch env(観測フラグ・result channel の配線値など)は
  ;; overlay として通り、binding 由来 auth env と並んで tmux env に載る。
  (setv world (LaunchWorld))
  (setv world.capture-script ["› "])
  (<- row (run-launch world (launch-params
                              :session_env {"PYTHONUNBUFFERED" "1"
                                            "DOEFF_RESULT_SESSION_ID" "s1"})))
  (setv tmux-env (get world.tmux-envs "doeff-s1"))
  (assert (= (get tmux-env "PYTHONUNBUFFERED") "1"))
  (assert (= (get tmux-env "DOEFF_RESULT_SESSION_ID") "s1"))
  (assert (= (get tmux-env "CODEX_HOME") "/x/codex")))


(deftest test-launch-rejects-auth-in-session-env
  ;; binding 所有キーの overlay 混入 = 裏口 — 全副作用(trust 書き込み・tmux)
  ;; より前に typed reject。
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params
                              :session_env {"CODEX_HOME" "/sneaky/home"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "non-auth overlay" (str raised)))
  (assert (in "CODEX_HOME" (str raised)))
  (assert (= world.trace []))
  (assert (= world.rows {})))


(deftest test-launch-rejects-foreign-owned-key-in-overlay
  ;; 所有権は kind を跨いで効く: codex launch でも CLAUDE_CONFIG_DIR は
  ;; overlay に住めない(所有権ベース — キー列挙の腐敗を許さない)。
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params
                              :session_env {"CLAUDE_CONFIG_DIR" "/x/claude"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "CLAUDE_CONFIG_DIR" (str raised)))
  (assert (= world.rows {})))


(deftest test-launch-rejects-malformed-binding
  ;; binding admission(ADR 0044 R3 と同思想): parse できた binding だけが
  ;; launch に到達する。全 reject は副作用ゼロ。codex v2(#15)は受理形の
  ;; XOR({codex_home} か {auth_file, profile_dir})— 混在・部分・未知 field
  ;; はどの shape にも一致せず reject。
  (setv cases
        [#({"kind" "gemini" "config_dir" "/x"} "unknown binding kind")
         #({"kind" "claude-code" "config_dir" "/x"} "drives agent_type")   ;; agent_type は codex
         #({"kind" "codex"} "exactly one field set")
         #({"kind" "codex" "codex_home" ""} "non-empty string field")
         #({"kind" "codex" "codex_home" "/x" "auth_file" "/y"} "exactly one field set")   ;; 混在
         #({"kind" "codex" "auth_file" "/y"} "exactly one field set")                     ;; 部分二軸
         #({"kind" "codex" "codex_home" "/x" "unknown_extra" "1"} "exactly one field set");; 未知 field
         #({"kind" "codex" "auth_file" "/y" "profile_dir" ""} "non-empty string field")])
  (for [[bad-binding expected] cases]
    (setv world (LaunchWorld))
    (setv raised None)
    (try
      (<- _ (run-launch world (launch-params :binding bad-binding)))
      (except [e RuntimeError] (setv raised e)))
    (assert (in "invalid binding" (str raised)) (str raised))
    (assert (in expected (str raised)) (str raised))
    (assert (= world.trace []))))


(deftest test-launch-composes-view-for-two-axis-codex-binding
  ;; #15(DOE-004 R5 v2): 二軸宣言 {auth_file, profile_dir} は host が
  ;; FsComposeHomeView で view を合成し、native 形(codex_home)へ合流する。
  ;; view root は $XDG_STATE_HOME/doeff/agent-homes(store DB と同じ解決系)。
  ;; binding が在る限り process env の CODEX_HOME fallback には決して到達
  ;; しない(decoy で pin)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› "])
  (setv (get world.env "XDG_STATE_HOME") "/state")
  (setv (get world.env "CODEX_HOME") "/decoy/never-used")
  (<- row (run-launch world (launch-params
                              :binding {"kind" "codex"
                                        "auth_file" "/auths/company.json"
                                        "profile_dir" "/profiles/agent"})))
  ;; 合成は宣言二軸 + 解決済み view root で呼ばれる
  (assert (in #("compose-view" "/auths/company.json" "/profiles/agent"
                "/state/doeff/agent-homes")
              world.trace))
  ;; 実効 identity と tmux env は合成 view(decoy ではない)
  (setv stored (get world.rows "s1"))
  (assert (= (get stored.effective-identity "CODEX_HOME")
             "/state/doeff/agent-homes/composed-view"))
  (assert (= (get (get world.tmux-envs "doeff-s1") "CODEX_HOME")
             "/state/doeff/agent-homes/composed-view"))
  ;; trust 書きは合成 view の config.toml へ(canonicalize は fake では恒等)
  (assert (in #("fs-write" "/state/doeff/agent-homes/composed-view/config.toml")
              world.trace)))


(deftest test-launch-trust-write-lands-on-canonical-path
  ;; #15 同梱修理: trust 書きは realpath へ。temp+rename は symlink を辿らず
  ;; 置換するため、config.toml が profile bundle への symlink のとき旧形は
  ;; view の link を実ファイル化して registry と fork させていた(cutover 起源
  ;; の地雷 — 旧 Rust の plain write は貫通していた)。canonicalize を殺すと
  ;; ここが red(mutation ピン)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› "])
  (setv (get world.canonical "/x/codex/config.toml") "/bundle/config.toml")
  (<- row (run-launch world (launch-params)))
  (assert (in #("fs-write" "/bundle/config.toml") world.trace))
  (assert (not-in #("fs-write" "/x/codex/config.toml") world.trace))
  ;; trust の中身は bundle 側の path に居る
  (assert (in "trust_level" (get world.fs "/bundle/config.toml"))))


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


(deftest test-launch-fails-closed-when-repl-never-idle
  ;; 2026-07-07 契約修正: R9 に無い未知 dialog が startup を塞いだら launch は
  ;; typed error で fail する。旧 oracle は repl-idle 予算切れ後に構わず
  ;; paste していた — trust dialog のカバレッジ欠落がそれで silent hang に
  ;; 化けた実障害(prompt が dialog に送出され session は永遠に待つ)。
  (setv world (LaunchWorld))
  ;; 未知 dialog: R9 detector のどれにも合致せず、選択行は行頭スペース付き
  ;; ` ❯` なので idle でもない — wait-for-repl-idle は予算切れまで poll する
  (setv unknown-frame (+ " Share anonymous usage data with Anthropic?\n"
                         " ❯ 1. Yes, share usage data\n"
                         "   2. Maybe later\n"
                         " Enter to confirm · Esc to cancel"))
  (setv world.capture-script [unknown-frame])
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  ;; エラーは明確に語る: REPL 未 ready・prompt 未配送・画面 tail(証拠)
  (assert (in "did not become ready" (str raised)))
  (assert (in "Share anonymous usage data" (str raised)))
  ;; prompt は一切配送されていない(literal 送出は起動 command の 1 本のみ)
  (assert (= (len world.delivered) 0))
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 1))
  ;; 作った tmux session は片付けられ、session 行も永続化されていない
  (assert (not-in "doeff-s1" world.tmux-sessions))
  (assert (in #("kill-session" "doeff-s1") world.trace))
  (assert (not-in "s1" world.rows)))


(deftest test-launch-repl-idle-budget-knob-injected
  ;; host が params["repl_idle_max_wait_seconds"] を注入したらそれが予算になる
  ;; (max_running と同じ host 所有 knob の注入パターン。conformance は
  ;; DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS 経由で同じ口を使う)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["nothing ready here"])
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude"}
                              :repl_idle_max_wait_seconds 5)))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in "within 5s" (str raised)))
  ;; 予算 5s / poll 0.3s → capture は高々 ~20 回(120s 既定なら ~400 回)
  (assert (< world.captures 25)))


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
