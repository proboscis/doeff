;;; 直接束縛 deftest: 共有 launch program(DOE-004 C2)の凍結物理検証。
;;;
;;; oracle: agentd-rust-final:src/main.rs session_launch + wait_for_repl_idle。
;;; 凍結物理:
;;;   - 重複 session / 既存 tmux session の reject
;;;   - S11 ゲートが tmux より前(per-kind PreLaunchSetup 経由)
;;;   - ADR 0035 reject-at-launch(result contract は codex/claude か明示 command)
;;;   - prompt は argv でなく live REPL へ(wait-for-repl-idle 後に paste)
;;;   - expected_result 付き prompt へ result-protocol instruction を追記
;;;   - R9 launch dialog の決定的 dismissal(wait-for-repl-idle 内)
;;;   - booting 行の登録は tmux-new-session 直後・ready 待ちの前(登録は TUI
;;;     readiness に依存しない簿記 — issue
;;;     agentd-session-registration-after-ready-gate、2026-07-17)
;;;   - repl-idle 予算切れは fail-closed(prompt 未配送・session kill・
;;;     証拠 frame 込み typed error — 2026-07-07 契約修正)。登録済みの行は
;;;     terminal(failed / prompt_undelivered)へ遷移し booting 残置ゼロ
;;;     (2026-07-17 改訂 — oracle main.rs と同一契約 / 2026-08-12
;;;     ADR-DOE-AGENTS-011 で category を prompt_undelivered へ分離)。
;;;     予算は host 注入 knob で圧縮可
;;;   - ready の必要条件は 3 つ(ADR-DOE-AGENTS-011): 入力 loop 配線
;;;     (idle prompt ∧ ¬MCP boot)・composer に未送信内容なし・貼り付け
;;;     可能性 probe の消費と消去。不成立は閉語彙 class つきで終端
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
  SessionStoreListActive
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
  FsLinkArtifact
  FsListDir
  FsDirExists
  FsFileExists
  EnvGet])
(import doeff_agents.sessionhost.effects [READY-PROBE-TEXT])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES])
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
    ;; composer simulator(ADR-DOE-AGENTS-011): 台本の frame に `{composer}` が
    ;; 在れば、そこへ「今 composer に座っている文字列」を差し込んで描画する。
    ;; literal paste は composer へ積まれ、BSpace は 1 文字消し、submit は
    ;; 空にする — ready gate の貼り付け可能性 probe が観測するのはこの状態。
    ;; composer-starved=True は「reader が我々の byte を消費しない」pane
    ;; (2026-08-11 実測の起動段 wedge)の模型: paste が積まれない。
    (setv self.composer "")
    (setv self.composer-starved False)
    (setv self.captures 0)
    (setv self.trace [])            ;; 効果の時系列(順序 assert 用)
    (setv self.sent-keys [])
    (setv self.delivered [])
    (setv self.fs {})
    (setv self.env {})
    (setv self.canonical {})        ;; path → realpath(FsCanonicalPath の台本)
    (setv self.tmux-envs {})        ;; session-name → new-session に渡った env
    (setv self.listings {})         ;; path → エントリ名 list(FsListDir の台本)
    (setv self.links {})            ;; target → source(FsLinkArtifact の記録)
    (setv self.kill-broken False)   ;; True: TmuxKillSession が raise(cleanup 失敗)
    ;; ADR-DOE-AGENTS-006 R10: FsDirExists の台本。既定 = 実在(全 dir が在る
    ;; 世界)。work_dir 消失(2026-08-17 実測の ACP workspace 削除)を模す
    ;; テストだけがここへ path を積む。
    (setv self.missing-dirs (set))
    (setv self.now (datetime 2026 7 5 12 0 0 :tzinfo timezone.utc))))


(defhandler fake-launch-substrate [world]
  (SessionStoreGet [session-id]
    (resume (.get world.rows session-id)))
  (SessionStoreListActive []
    (resume (lfor r (list (.values world.rows))
                  :if (in r.status ACTIVE-STATUSES) r)))
  (FsListDir [path]
    (resume (sorted (.get world.listings path []))))
  (FsDirExists [path]
    ;; 既定 = 実在。missing-dirs に積まれた path だけ不在(work_dir 消失の模型)。
    (.append world.trace #("dir-exists" path))
    (resume (not-in path world.missing-dirs)))
  (FsFileExists [path]
    ;; fs(実体台本)か links(敷設済み symlink)に居れば実在 — FsLinkArtifact の
    ;; source-known と同じ知識面(listings は dir なので見ない)。
    (.append world.trace #("file-exists" path))
    (resume (or (in path world.fs) (in path world.links))))
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
    ;; capture は launch の gate loop(wait-for-repl-idle / fail 証拠収集)から
    ;; しか呼ばれない — capture 時点の store 可視状態({sid: status})を trace に
    ;; 積むと「ready 待ち中に外部から何が見えたか」がそのまま assert できる
    ;; (issue agentd-session-registration-after-ready-gate の観測点)。
    (.append world.trace
             #("capture" (dfor [k v] (.items world.rows) k v.status)))
    (setv world.captures (+ world.captures 1))
    (setv frame (if world.capture-script
                    (if (> (len world.capture-script) 1)
                        (.pop world.capture-script 0)
                        (get world.capture-script 0))
                    ""))
    (resume (.replace frame "{composer}" world.composer)))
  (TmuxSendKeys [pane-id text literal submit]
    (.append world.trace #("send-keys" text))
    (.append world.sent-keys #(pane-id text literal submit))
    ;; composer simulator(上記): literal paste は積む(starved なら消費されない)、
    ;; BSpace は 1 文字消す、submit は composer を空にする。
    (when (and literal text (not world.composer-starved))
      (setv world.composer (+ world.composer text)))
    (when (and (not literal) (= text "BSpace"))
      (setv world.composer (cut world.composer 0 -1)))
    (when submit
      (setv world.composer ""))
    (resume None))
  (TmuxKillSession [session-name]
    (.append world.trace #("kill-session" session-name))
    (when world.kill-broken
      (raise (RuntimeError f"tmux kill-session failed for {session-name}")))
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
  (FsLinkArtifact [source-path target-path]
    ;; 実 substrate の share.py 同型意味論の台本版: source は fs / links /
    ;; listings(dir 台本)のいずれかに実在するときのみ敷設できる。
    (.append world.trace #("link-artifact" source-path target-path))
    (setv source-known (or (in source-path world.fs)
                           (in source-path world.links)
                           (in source-path world.listings)))
    (setv outcome
          (cond
            (not source-known) "source-missing"
            (in target-path world.fs) "target-conflict"
            (in target-path world.links)
              (if (= (get world.links target-path) source-path)
                  "same-entity"
                  "target-conflict")
            True
              (do (setv (get world.links target-path) source-path)
                  "linked")))
    (resume outcome))
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


(defn prompt-send [world]
  "配送された prompt の send 記録(= 最後の literal 送出)。
   ready gate の貼り付け可能性 probe(ADR-DOE-AGENTS-011)が起動 command と
   prompt の間に literal 送出を 1 本挟むため、固定 index では取れない。"
  (get (lfor [p t l s] world.sent-keys :if l #(p t l s)) -1))


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
  (setv world.capture-script ["codex booting banner" "› {composer}"])
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
  ;; prompt は ready gate(idle 観測 + 貼り付け可能性 probe)の後に
  ;; DeliverMessage → impl → TmuxSendKeys(literal+submit)で配送され、
  ;; result-protocol instruction が追記されている。
  ;; capture 4 回 = banner / idle(→ probe 貼り)/ probe 可視(→ 消去)/
  ;; 消去済み(→ ready)。ADR-DOE-AGENTS-011: 旧 gate は idle の 1 枚で
  ;; 即 paste しており(capture 2 回)、reader が我々の byte を消費している
  ;; という証拠を 1 つも持っていなかった。
  (assert (= world.captures 4))
  (setv [ppane ptext pliteral psubmit] (prompt-send world))
  (assert pliteral)
  (assert psubmit)
  (assert (.startswith ptext "do the task"))
  (assert (.endswith ptext RESULT-PROTOCOL-INSTRUCTION))
  ;; session 行: launch 完了の手渡しで running・awaiting latch 武装・実効
  ;; identity 永続化(S14)。booting は launch pipeline 所有の in-flight 状態
  ;; (issue agentd-session-registration-after-ready-gate)— 登録〜配送完了の
  ;; 間だけ外部に見え、完了 upsert が monitor へ観測を引き渡す。
  (setv stored (get world.rows "s1"))
  (assert (= stored.status "running"))
  (assert stored.awaiting-response)
  (assert (= (get stored.effective-identity "CODEX_HOME") "/x/codex"))
  ;; R7: binding 由来の auth env は host が合成して tmux env に載せる
  ;; (session_env 経由ではない)
  (assert (= (get (get world.tmux-envs "doeff-s1") "CODEX_HOME") "/x/codex"))
  (assert (in #("s1" "session_started") world.events)))


(deftest test-launch-claude-uses-claude-impl
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
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


(deftest test-launch-materializes-context-file-before-spawn
  ;; law context-file-rides-the-wire(ACP steward W1b 2026-08-20): launcher が
  ;; workdir へ直接書く旧法は launcher と session host が別機械に分かれた瞬間に
  ;; 壊れた(ACP pod の write が Mac にしか無い workdir を指す — 実測 368 launch
  ;; 失敗/2h)。file は wire(context_file)で運ばれ、session の走る機械 =
  ;; この host が tmux spawn より前に atomic write で実体化する。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- row (run-launch world (launch-params
                              :context_file {"path" ".acp-context.json"
                                             "content" {"work_item_id" "wi-1"
                                                        "attempt" 2}})))
  ;; workdir 直下に JSON として実体化されている
  (setv ctx-path "/work/dir/.acp-context.json")
  (assert (in ctx-path world.fs))
  (assert (= (json.loads (get world.fs ctx-path))
             {"work_item_id" "wi-1" "attempt" 2}))
  ;; 書きは tmux new-session より前(spawn した session が必ず読める)
  (setv ctx-write-idx
        (.index world.trace #("fs-write" ctx-path)))
  (setv spawn-idx
        (.index (lfor t world.trace (get t 0)) "new-session"))
  (assert (< ctx-write-idx spawn-idx)))


(deftest test-launch-without-context-file-writes-nothing
  ;; context_file 省略(payload workdir lane — 実 checkout へ簿記 file を
  ;; 落とさない法の相方)では workdir へ何も書かれない。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- row (run-launch world (launch-params)))
  (assert (not-in "/work/dir/.acp-context.json" world.fs)))


(deftest test-launch-env-declares-unattended-session-class
  ;; agentd 起動の会話は無人 — spawn env が AGENT_SESSION_CLASS=unattended を
  ;; 宣言する(hook 層の会話種別 self-gate 契約の相方 — 2026-08-18
  ;; route-c03fe34745)。caller overlay の明示は尊重する。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- _ (run-launch world (launch-params)))
  (assert (= (get (get world.tmux-envs "doeff-s1") "AGENT_SESSION_CLASS")
             "unattended"))
  (setv world2 (LaunchWorld))
  (setv world2.capture-script ["codex booting banner" "› {composer}"])
  (<- _ (run-launch world2 (launch-params
                             :session_env {"AGENT_SESSION_CLASS" "attended"})))
  (assert (= (get (get world2.tmux-envs "doeff-s1") "AGENT_SESSION_CLASS")
             "attended")))


(deftest test-launch-claude-session-hooks-inherit-knob
  ;; DOEFF_AGENTD_SESSION_HOOKS=inherit: claude argv から
  ;; --settings {"disableAllHooks":true} が外れ、config-dir 所有者の hook 層へ
  ;; 委ねる(既定の凍結物理は test-launch-claude-uses-claude-impl がピン)。
  (setv world (LaunchWorld))
  (setv (get world.env "DOEFF_AGENTD_SESSION_HOOKS") "inherit")
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude"})))
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (.startswith cmd "claude --dangerously-skip-permissions"))
  (assert (not-in "disableAllHooks" cmd))
  (assert (not-in "--settings" cmd)))


(deftest test-launch-rejects-unknown-session-hooks-vocab
  ;; 語彙外の DOEFF_AGENTD_SESSION_HOOKS は fail-loud(黙った綴り違いが
  ;; 「安全 hook 全滅」を無言で復活させるため)。tmux 効果ゼロ。
  (setv world (LaunchWorld))
  (setv (get world.env "DOEFF_AGENTD_SESSION_HOOKS") "on")
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params)))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "DOEFF_AGENTD_SESSION_HOOKS" (str raised)))
  (assert (not-in "new-session" (lfor t world.trace (get t 0)))))


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
;; max_running 容量ガード — 母数 = launch 所有行のみ(ADR-DOE-AGENTS-004
;; capacity-counts-only-launch-owned-rows)。adopt(session.adopt)の行は
;; 観測の登記であって容量の消費ではない(ADR-007 R2 — SessionHost はその
;; substrate を作っていない)。
;; ---------------------------------------------------------------------------

(defn seed-active-row [world sid adopted]
  "容量ガード試験の下地 active 行。adopted=True = session.adopt の観測行
   (実測 2026-08-17 の 32 行はすべてこの形: running・interactive・adopted=1)、
   False = launch 所有行。"
  (setv (get world.rows sid)
        (SessionRow :session-id sid :session-name f"doeff-{sid}"
                    :pane-id f"%{sid}" :agent-type "claude"
                    :lifecycle "interactive" :status "running"
                    :started-at "2026-08-17T00:00:00+00:00"
                    :adopted adopted)))


(deftest test-launch-capacity-ignores-adopted-rows
  ;; 2026-08-17 実測(agentd-herdr.sqlite: adopted 行 32 ≥ 上限 10 で
  ;; session.launch 恒久 100% 拒否)の再現形: adopted 行が上限を超えて
  ;; 何行あっても、launch 所有の空きがあれば launch は通る。
  (setv world (LaunchWorld))
  (for [i (range 12)]
    (seed-active-row world f"adopted-{i}" True))
  (seed-active-row world "owned-0" False)
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- row (run-launch world (launch-params :max_running 10)))
  (assert (= row.status "running"))
  ;; 容量判定は観測行に触れない: adopted 行は本便の後も刈られない
  ;; (刈り取り免除そのものの pin は ADR-007 enforcement / S26)。
  (for [i (range 12)]
    (assert (= (. (get world.rows f"adopted-{i}") status) "running"))))


(deftest test-launch-capacity-counts-launch-owned-rows
  ;; launch 所有行が上限に達した時は従来どおり拒否する。文言の先頭逐語
  ;; "max running agent sessions reached" は ACP の throttle 分類
  ;; (Scheduler.hs の infix 照合 — 席を解放して後で再試行)が消費する凍結面。
  ;; 分子も launch 所有行の数(2/2)であって active 全行の数(5/2)ではない。
  (setv world (LaunchWorld))
  (seed-active-row world "owned-0" False)
  (seed-active-row world "owned-1" False)
  (for [i (range 3)]
    (seed-active-row world f"adopted-{i}" True))
  (setv raised None)
  (try
    (<- _ (run-launch world (launch-params :max_running 2)))
    (except [e RuntimeError] (setv raised e)))
  (assert (in "max running agent sessions reached" (str raised)))
  (assert (in "2/2" (str raised)))
  (assert (not-in "new-session" (lfor t world.trace (get t 0)))))


(deftest test-launch-capacity-unlimited-when-max-running-absent
  ;; 上限なし(max_running None)= admission の check ごと飛ぶ。2026-08-19 の
  ;; operator 裁定「同時走行の上限は ACP の都合であって sessionhost の性質
  ;; ではない」の受け口で、直接束縛(config を持たない使い方 = params に key が
  ;; 載らない)と同じ形。key 自体が無い場合と、key が None で載る場合(host が
  ;; 上限なしで起動した時の注入形)の両方で通ること。
  (for [params [(launch-params) (launch-params :max_running None)]]
    (setv world (LaunchWorld))
    (for [i (range 50)]
      (seed-active-row world f"owned-{i}" False))
    (setv world.capture-script ["codex booting banner" "› {composer}"])
    (<- row (run-launch world params))
    (assert (= row.status "running"))))


;; ---------------------------------------------------------------------------
;; command override(escape hatch)
;; ---------------------------------------------------------------------------

(deftest test-launch-command-override-verbatim
  (setv world (LaunchWorld))
  (setv world.capture-script ["› {composer}"])
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
  (setv world.capture-script ["› {composer}"])
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
  (setv world.capture-script ["codex booting banner" "› {composer}"])
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
  (setv world.capture-script ["codex booting banner" "› {composer}"])
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
  (setv world.capture-script [update-frame "› {composer}"])
  (<- row (run-launch world (launch-params)))
  ;; Down Down Enter が(起動 command の後・prompt 配送の前に)送られている。
  ;; その後ろは ready gate probe の消去打鍵(BSpace × probe 文字数)。
  (setv keys (lfor [p t l s] world.sent-keys :if (not l) t))
  (assert (= (cut keys 0 3) ["Down" "Down" "Enter"]))
  (assert (= (set (cut keys 3 None)) #{"BSpace"}))
  ;; literal 送出は起動 command / probe / prompt の 3 本で、prompt が最後
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 3))
  (assert (= (get literal-texts 1) READY-PROBE-TEXT))
  (assert (.endswith (get literal-texts -1) RESULT-PROTOCOL-INSTRUCTION)))


(deftest test-launch-registers-booting-row-before-repl-idle-wait
  ;; issue agentd-session-registration-after-ready-gate: 登録は TUI readiness に
  ;; 依存しない簿記 — tmux-new-session 直後・wait-for-repl-idle の最初の capture
  ;; より前に booting 行が store で観測可能でなければならない。旧配置(ready 待ち
  ;; の後ろ)では最大 120s の「session は物理的に在るのに記録が無い」窓が正規に
  ;; 開き、60s handshake を仮定する外部監視(mediagen engine)から orphan に
  ;; 見えた(2026-07-14 実測)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- row (run-launch world (launch-params)))
  ;; wait-for-repl-idle の最初の capture 時点で s1 が booting として可視
  (setv capture-views (lfor t world.trace :if (= (get t 0) "capture") (get t 1)))
  (assert capture-views)
  (assert (= (.get (get capture-views 0) "s1") "booting")
          "booting 行が ready 待ちの開始前に永続化されていない")
  ;; trace 順序でも: 最初の upsert が最初の capture より前
  (setv trace-kinds (lfor t world.trace (get t 0)))
  (assert (< (.index trace-kinds "upsert") (.index trace-kinds "capture")))
  ;; session_started event も登録時点で記録済み
  (assert (in #("s1" "session_started") world.events))
  ;; 配送完了の手渡し: running + awaiting latch 武装(booting は launch
  ;; pipeline 所有の in-flight 状態としてだけ外部に見える)
  (assert (= row.status "running"))
  (assert row.awaiting-response)
  (assert (= (get world.rows "s1") row)))


(deftest test-launch-fails-closed-when-repl-never-idle
  ;; 2026-07-07 契約修正: R9 に無い未知 dialog が startup を塞いだら launch は
  ;; typed error で fail する。旧 oracle は repl-idle 予算切れ後に構わず
  ;; paste していた — trust dialog のカバレッジ欠落がそれで silent hang に
  ;; 化けた実障害(prompt が dialog に送出され session は永遠に待つ)。
  ;;
  ;; 2026-07-17 契約改訂(issue agentd-session-registration-after-ready-gate):
  ;; 「リークさせない」の実現手段が変わった — 行を作らないのではなく、登録済みの
  ;; booting 行を terminal(failed / timed_out)へ遷移させる。行はリークではなく
  ;; ライフサイクルとして残り、booting のままの残置はゼロ。
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
  ;; エラーは明確に語る: REPL 未 ready・分類・prompt 未配送・画面 tail(証拠)。
  ;; ADR-DOE-AGENTS-011: 分類は閉語彙で、この形は「R9 fast-path の外の
  ;; dialog」= unknown-dialog(旧実装は同じ文言でどの画面でも
  ;; 「unrecognized screen」と断定していた)。
  (assert (in "did not become ready" (str raised)))
  (assert (in "[unknown-dialog]" (str raised)) (str raised))
  (assert (in "Share anonymous usage data" (str raised)))
  ;; prompt は一切配送されていない(literal 送出は起動 command の 1 本のみ)
  (assert (= (len world.delivered) 0))
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 1))
  ;; 作った tmux session は片付けられる(既存保証)
  (assert (not-in "doeff-s1" world.tmux-sessions))
  (assert (in #("kill-session" "doeff-s1") world.trace))
  ;; 行はリークではなくライフサイクル: terminal failed(timed_out)へ遷移し、
  ;; booting 残置ゼロ + session_failed event 記録
  (setv stored (get world.rows "s1"))
  (assert (= stored.status "failed"))
  (assert (is-not stored.finished-at None))
  (assert (is-not stored.terminal-cause None))
  ;; ADR-DOE-AGENTS-011 R-undelivered-first-class-b5e8: 起動段で prompt が
  ;; 一度も届かなかった attempt の category は timed_out ではなく
  ;; prompt_undelivered(retryable は true のまま)。
  (assert (= stored.terminal-cause.category "prompt_undelivered"))
  (assert (= stored.terminal-cause.retryable True))
  (assert (in #("s1" "session_failed") world.events))
  ;; cleanup 成功は cleaned-at で表現(terminal-first 契約)
  (assert (is-not stored.cleaned-at None))
  (assert (not (lfor [k v] (.items world.rows) :if (= v.status "booting") k))))


(deftest test-launch-ready-timeout-terminalizes-before-failed-cleanup
  ;; terminal-first(#542 レビュー由来): FAILED 終端化の永続化は tmux cleanup
  ;; より先。kill が upsert より先だと、cleanup 失敗(tmux server 死亡・帯域外
  ;; teardown)で終端化がスキップされ booting 残置 + 元エラーのマスクが起きる。
  ;; cleanup 失敗は cleaned-at 無し(行は既に terminal)で表現し、raise は
  ;; ready-timeout の typed error のまま。
  (setv world (LaunchWorld))
  (setv world.kill-broken True)
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
                                        "config_dir" "/x/claude"}
                              :repl_idle_max_wait_seconds 5)))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  ;; cleanup 失敗が ready-timeout の typed error をマスクしない
  (assert (in "did not become ready" (str raised)) (str raised))
  ;; FAILED 終端化は cleanup 試行より先に永続化済み
  (setv stored (get world.rows "s1"))
  (assert (= stored.status "failed"))
  (assert (is-not stored.finished-at None))
  (assert (= stored.terminal-cause.category "prompt_undelivered"))
  (assert (in #("s1" "session_failed") world.events))
  ;; cleanup は試行されたが失敗 → cleaned-at は NULL のまま
  (assert (in #("kill-session" "doeff-s1") world.trace))
  (assert (is stored.cleaned-at None))
  (assert (not (lfor [k v] (.items world.rows) :if (= v.status "booting") k))))


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
;; ADR-DOE-AGENTS-011: 起動段の分類化 + 貼り付け可能性(反例スイート)
;;
;; 出典は 2026-08-11 断面の実測(agentd.sqlite 直読):
;;   - launch ready gate 終端 341 件の実物 frame 分類 = 描画ゼロ 24 /
;;     shell echo だけ 314 / 最終 frame が ready 3。旧文言が毎回断定していた
;;     「unrecognized screen (a dialog outside the R9 fast-path set?)」は 0 件。
;;   - unsubmitted-prompt 終端 25 件 = idle prompt を見て即 paste した prompt が
;;     composer に collapsed chip として座り turn は一度も始まらなかった
;;     (chip 3〜10 個 = 1 回の bracketed paste の断片化着弾が 12 席)。
;; ---------------------------------------------------------------------------

(defk gate-failure [world params]
  {:pre [(: world LaunchWorld) (: params dict)]
   :post [(: % RuntimeError)]}
  "launch を走らせて typed error を捕える(起動段の不成立ケース共通)。
   defk なのは run-launch が defk だから — 素の fn から呼ぶと program object が
   黙って流れて『raise されなかった』になる(doeff-hy の await 規律)。"
  (setv raised None)
  (try
    (<- _ (run-launch world params))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None) "launch が fail-closed しなかった")
  raised)


(deftest test-launch-ready-gate-requires-paste-consumption
  ;; 根治の中核: 『入力欄が描かれた』は reader が我々の byte を消費している
  ;; 証拠ではない。composer-starved(paste が積まれない pane)は idle prompt を
  ;; 描き続けるが probe を消費しない — 旧 gate はこの pane に 1238 行の prompt を
  ;; 貼って running を書き、5 回の Enter 再送のあと unsubmitted-prompt で死んだ
  ;; (実測 25 件)。新 gate は prompt を配送せず、probe 局面の名前で終端する。
  (setv world (LaunchWorld))
  (setv world.composer-starved True)
  (setv world.capture-script ["› {composer}"])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[paste-not-consumed]" (str raised)) (str raised))
  ;; literal 送出は起動 command と probe の 2 本だけ — prompt は貼られていない
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 2) literal-texts)
  (assert (= (get literal-texts 1) READY-PROBE-TEXT))
  (assert (= world.delivered []))
  ;; 行は terminal(prompt_undelivered)で、reason は自己記述する
  (setv stored (get world.rows "s1"))
  (assert (= stored.status "failed"))
  (assert (= stored.terminal-cause.category "prompt_undelivered"))
  (assert (in "[paste-not-consumed]" stored.terminal-cause.reason))
  (assert (in "the prompt was never delivered" stored.terminal-cause.reason))
  ;; DB 面の分類(受入条件 (e))は last_validation_error からも同じ token で引ける
  (assert (in "[paste-not-consumed]" stored.last-validation-error)))


(deftest test-launch-ready-gate-rejects-mcp-boot-window
  ;; gate 形式(ready_physics CODEX-READY-PATTERN)は MCP boot 窓を最初から
  ;; 除いていたのに、observation 形式(この gate)は idle prompt 単独で ready を
  ;; 主張していた。verbatim capture(tests/data/ready_screens/codex_mcp_boot.txt)
  ;; と同じ形 = composer は描かれているが input loop 未配線。
  (setv world (LaunchWorld))
  (setv world.capture-script ["Starting MCP servers (1/2)\n› {composer}"])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[mcp-boot-window]" (str raised)) (str raised))
  ;; probe すら貼らない(この窓に keys を送ると Enter がロード画面に食われる)
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 1) literal-texts))


(deftest test-launch-ready-gate-refuses-occupied-composer
  ;; 未送信の内容が座ったままの composer へ重ね貼りしない(前 incarnation の
  ;; 残留・添付チップ)。待っても誰も掃除しないので即 loud。
  (setv world (LaunchWorld))
  (setv world.capture-script ["› [Pasted text #1 +12 lines]"])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[composer-occupied]" (str raised)) (str raised))
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 1) literal-texts)
  ;; 予算を使い切らずに落ちる(待っても解けない命題)
  (assert (< world.captures 3) world.captures))


(deftest test-launch-ready-gate-separates-blank-screen-from-shell-echo
  ;; 裁定の主題: (A) 認識できない画面が在る / (B) 何も描画されていない の弁別。
  ;; 実測ではさらに (B') 「shell echo だけ在る」が 314/341 を占め、旧実装は
  ;; (B) と (B') を同じ 14 改行の tail で残していた(証拠が消えていた)。
  (setv blank-world (LaunchWorld))
  (setv blank-world.capture-script [""])
  (<- blank-raised (gate-failure blank-world
                                   (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[no-output]" (str blank-raised)) (str blank-raised))
  ;; shell echo だけ(実物 frame の写し — 起動 command の echo + zsh の告知)
  (setv echo-world (LaunchWorld))
  (setv echo-world.capture-script
        [(+ "The default interactive shell is now zsh.\n"
            "CA-20038667:inv_wi_x s22625$ claude --dangerously-skip-permissions "
            "--settings '{\"disableAllHooks\":true}' --model claude-opus-5")])
  (<- echo-raised (gate-failure echo-world
                                  (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[no-agent-frame]" (str echo-raised)) (str echo-raised))
  ;; 証拠は逐語で残る(旧実装は末尾 15 行 = 空行だけを残していた)
  (setv stored (get echo-world.rows "s1"))
  (assert (in "$ claude --dangerously-skip-permissions" stored.output-snippet)))


(deftest test-launch-ready-gate-loud-on-dialog-outside-fast-path
  ;; 受入条件 (b): R9 fast-path 集合の外の dialog を 1 件でも観測したら loud。
  ;; 合成標本 — 実測 367 件の失敗 frame には 1 件も無い(= 今日の live には
  ;; 標本が無い形。0 標本の区分を「実装済み」と呼ばないため合成で撃つ)。
  ;; codex の trust dialog は選択 marker が `›` なので idle prompt 判定を
  ;; 通過する: 判定を idle の有無で緩めてはならない(verbatim capture
  ;; tests/data/ready_screens/codex_trust_dialog.txt と同じ幾何)。
  (setv world (LaunchWorld))
  (setv world.capture-script
        [(+ "Do you trust the files in this folder?\n"
            "› 1. Yes, proceed\n"
            "  2. No, exit\n"
            "Enter to confirm · Esc to cancel")])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[unknown-dialog]" (str raised)) (str raised))
  ;; dismissal キーを推測して送らない(誤った Enter は選択肢を確定させる)
  (setv keys (lfor [p t l s] world.sent-keys :if (not l) t))
  (assert (= keys []) keys)
  (setv literal-texts (lfor [p t l s] world.sent-keys :if l t))
  (assert (= (len literal-texts) 1) literal-texts))


(deftest test-launch-ready-gate-provider-limit-screen-is-rate-limited
  ;; 起動直後の provider 上限告知は「起動段の失敗」ではなく枠の話 —
  ;; category は rate_limited(ACP ADR 0049 の failover が引き取る)。
  ;; 実物形: 稼働席の画面 283 件中 16 件に実在する告知 dialog。
  (setv world (LaunchWorld))
  (setv world.capture-script
        [(+ "What do you want to do?\n"
            "› 1. Stop and wait for limit to reset\n"
            "  2. Ask your admin for more usage\n"
            "Enter to confirm · Esc to cancel")])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (assert (in "[provider-limit-screen]" (str raised)) (str raised))
  (setv stored (get world.rows "s1"))
  (assert (= stored.terminal-cause.category "rate_limited"))
  (assert (= stored.terminal-cause.retryable True)))


(deftest test-launch-rejects-missing-work-dir
  ;; ADR-DOE-AGENTS-006 R10: work_dir の物理実在は launch の admission 事実。
  ;; tmux は不在 start-directory を黙って $HOME に差し替える(2026-08-17 実測
  ;; 91 件: pane の shell prompt が `CA-…:~` で立ち、claude の cwd 鍵の会話
  ;; 解決が全滅)。全副作用(trust 書き・tmux・行登録)より前に loud に落とす。
  (setv world (LaunchWorld))
  (.add world.missing-dirs "/work/dir")
  (<- raised (gate-failure world (launch-params)))
  (assert (in "work_dir does not exist" (str raised)) (str raised))
  (assert (in "/work/dir" (str raised)))
  ;; 副作用ゼロ: 行不生成・tmux 不接触・trust 書きなし
  (assert (= (len world.rows) 0))
  (assert (not world.tmux-sessions))
  (setv trace-kinds (lfor t world.trace (get t 0)))
  (assert (not-in "fs-write" trace-kinds))
  (assert (not-in "new-session" trace-kinds)))


(deftest test-launch-ready-gate-conversation-not-found-fails-fast
  ;; ADR-DOE-AGENTS-011 改訂(2026-08-18): CLI が会話を解決できないと rc=1 で
  ;; 即 loud 終了する(resume-physics.md プローブ (a))。この画面は 120s の
  ;; 予算を待つ命題ではない — 実測 91 件が全件予算切れまで待って
  ;; no-agent-frame に誤分類されていた。実物 frame の写しで即時の分類を検証。
  (setv world (LaunchWorld))
  (setv world.capture-script
        [(+ "The default interactive shell is now zsh.\n"
            "CA-20038667:~ s22625$ claude --dangerously-skip-permissions "
            "--resume 6129f510-70f3-4cac-a29c-3956d5c925a0\n"
            "No conversation found with session ID: "
            "6129f510-70f3-4cac-a29c-3956d5c925a0\n"
            "CA-20038667:~ s22625$")])
  (<- raised (gate-failure world (launch-params
                                   :agent_type "claude"
                                   :binding {"kind" "claude-code"
                                             "config_dir" "/x/claude"})))
  (assert (in "[conversation-not-found]" (str raised)) (str raised))
  ;; 予算を使い切らずに落ちる(最初の観測で確定する命題)
  (assert (< world.captures 3) world.captures)
  ;; 未配達の分類は保つ(category は prompt_undelivered)
  (setv stored (get world.rows "s1"))
  (assert (= stored.terminal-cause.category "prompt_undelivered"))
  (assert (in "[conversation-not-found]" stored.last-validation-error)))


(deftest test-launch-ready-gate-retains-bounded-evidence-frames
  ;; 受入条件 (g): 失敗画面の逐語の保持数を 1 件から増やす — ただし有界。
  ;; 保持は「最初の画面」を固定し最新側を入れ替える(局面の経過秒つき)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["frame one alpha" "frame two beta"
                              "frame three gamma" "frame four delta"])
  (<- raised (gate-failure world (launch-params :repl_idle_max_wait_seconds 5)))
  (setv stored (get world.rows "s1"))
  (setv snippet stored.output-snippet)
  ;; 3 枚(READY-GATE-FRAME-RETENTION)— 1 枚でも無制限でもない
  (assert (= (.count snippet "=== frame @") 3) snippet)
  ;; 最初の画面は必ず残る(起動直後に何が見えたか)
  (assert (in "frame one alpha" snippet) snippet)
  ;; 最後の画面も残る(尽きた台本は最後の frame を保持し続ける)
  (assert (in "frame four delta" snippet) snippet)
  ;; 経過秒が付く(どの局面の画面かが事後に置ける)
  (assert (in "=== frame @0.0s ===" snippet) snippet))


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


;; ---------------------------------------------------------------------------
;; ADR-DOE-AGENTS-006 R1: claude の会話 identity 鋳造(launch 時)
;; ---------------------------------------------------------------------------

(deftest test-launch-claude-mints-conversation
  ;; 鋳造した UUID が --session-id 注入と row.conversation の両方に同値で
  ;; 現れる(boot 前に identity が stored fact)。identity 列には混ざらない。
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude"})))
  (assert (isinstance row.conversation dict))
  (setv conv-id (get row.conversation "session_id"))
  (assert conv-id)
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (in f"--session-id {conv-id}" cmd))
  (assert (= row.generation 1))
  (assert (is row.resumed-from-session-id None))
  (assert (is row.forked-from-session-id None))
  (assert (not-in "conversation" row.effective-identity)))


(deftest test-launch-codex-conversation-unknown-until-discovery
  ;; codex の会話 identity は CLI 側が鋳造する — launch 直後の行は
  ;; identity-unknown(None)で、捕獲は monitor の発見 arm の仕事。
  (setv world (LaunchWorld))
  (setv world.capture-script ["codex booting banner" "› {composer}"])
  (<- row (run-launch world (launch-params)))
  (assert (is row.conversation None))
  (assert (= row.generation 1)))
