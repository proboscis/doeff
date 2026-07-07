;;; 直接束縛 deftest: per-kind defhandler(claude-code / codex)の protocol 物理検証
;;; (DOE-004 C2)。
;;;
;;; oracle parity の対象(conformance README の凍結物理):
;;;   - S13: argv 配線(claude --settings disableAllHooks + --mcp-config stdio +
;;;     --strict-mcp-config / codex -c mcp_servers."doeff_result".command/.args。
;;;     prompt は argv に載らない・print mode 不使用)
;;;   - S12: claude trust pre-seed(canonicalized work_dir key・temp+rename)
;;;   - S11: codex CODEX_HOME ゲート(tmux 効果ゼロで typed fail)
;;;   - F-* marker 分類 + R9 dialog 検出(dismiss keys は impl 所有)
;;;
;;; fake substrate(dict-backed fs / 台本 env / 記録 tmux)で impl handler を
;;; 直接束縛する。生 IO ゼロ。oracle: packages/doeff-agentd/src/main.rs
;;; build_claude_argv / build_codex_argv / trust_*_workspace /
;;; output_has_* / dismiss_*。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import json)
(import pytest)
(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  PaneObservation
  BuildLaunch
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  WireResultChannel
  TmuxNewSession
  TmuxSendKeys
  FsCanonicalPath
  FsReadText
  FsWriteTextAtomic
  FsMakeDirs
  EnvGet
  build-launch
  pre-launch-setup
  classify-pane
  deliver-message
  wire-result-channel])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.impls.codex [codex-impl])


;; ---------------------------------------------------------------------------
;; fake substrate world(impls は substrate effect しか yield できない —
;; ここで受けて記録する。tmux-calls の空を assert することが S11 の
;; 「tmux 呼び出し前に fail」の直接束縛版)
;; ---------------------------------------------------------------------------

(defclass ImplWorld []
  (defn __init__ [self]
    (setv self.fs {})              ;; path -> text
    (setv self.dirs [])            ;; FsMakeDirs の記録
    (setv self.atomic-writes [])   ;; [(path, tmp-suffix)]
    (setv self.env {})             ;; EnvGet 台本(process env fallback)
    (setv self.canonical {})       ;; path -> canonical path 台本
    (setv self.tmux-calls [])      ;; あらゆる tmux effect の記録
    (setv self.sent-keys [])))     ;; TmuxSendKeys の記録


(defhandler fake-impl-substrate [world]
  (FsCanonicalPath [path]
    (resume (.get world.canonical path path)))
  (FsReadText [path]
    (resume (.get world.fs path)))
  (FsWriteTextAtomic [path text tmp-suffix]
    (.append world.atomic-writes #(path tmp-suffix))
    (setv (get world.fs path) text)
    (resume None))
  (FsMakeDirs [path]
    (.append world.dirs path)
    (resume None))
  (EnvGet [name]
    (resume (.get world.env name)))
  (TmuxNewSession [session-name work-dir env]
    (.append world.tmux-calls #("new-session" session-name))
    (resume "%99"))
  (TmuxSendKeys [pane-id text literal submit]
    (.append world.tmux-calls #("send-keys" pane-id))
    (.append world.sent-keys #(pane-id text literal submit))
    (resume None)))


(defn base-params [#** overrides]
  (setv params {"session_id" "s1"
                "session_name" "doeff-s1"
                "agent_type" "codex"
                "work_dir" "/work/dir"
                "lifecycle" "run_to_completion"
                "session_env" {}
                "prompt" "do the task"
                "command" None
                "expected_result" {"type" "object"}
                "model" None
                "effort" None
                "mcp_servers" {}
                "result_channel" None
                "socket_path" "/tmp/agentd.sock"
                "skip_trust_setup" False})
  (.update params overrides)
  params)


(defn channel-spec []
  {"command" "/opt/doeff-sessionhost"
   "args" ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]})


(defk perform [op]
  {:pre [(: op EffectBase)] :post [(: % "effect の handler 解釈結果")]}
  "bare effect を 1 回 yield する最小 program(handler は program を包むため)。"
  (<- result op)
  result)

(defk run-claude [world op]
  {:pre [(: world ImplWorld) (: op EffectBase)]
   :post [(: % "impl handler 実行結果")]}
  (<- result ((fake-impl-substrate world)
              ((claude-code-impl "/opt/doeff-sessionhost") (perform op))))
  result)

(defk run-codex [world op]
  {:pre [(: world ImplWorld) (: op EffectBase)]
   :post [(: % "impl handler 実行結果")]}
  (<- result ((fake-impl-substrate world)
              ((codex-impl "/opt/doeff-sessionhost") (perform op))))
  result)


;; ---------------------------------------------------------------------------
;; BuildLaunch — S13 argv 配線 parity(oracle build_claude_argv / build_codex_argv)
;; ---------------------------------------------------------------------------

(deftest test-claude-argv-golden-wiring
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :model "claude-fable-5"
                            :effort "high"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  ;; 凍結接頭(49b3549b 傷跡: hooks 無効化は agent contract の一部)
  (assert (= (cut argv 0 4)
             ["claude" "--dangerously-skip-permissions"
              "--settings" "{\"disableAllHooks\":true}"]))
  ;; effort → model の順(oracle 順序)
  (assert (= (.index argv "--effort") 4))
  (assert (= (get argv 5) "high"))
  (assert (= (get argv (+ (.index argv "--model") 1)) "claude-fable-5"))
  ;; doeff_result stdio server が --mcp-config JSON に載る + strict
  (setv mcp-json (get argv (+ (.index argv "--mcp-config") 1)))
  (setv mcp (json.loads mcp-json))
  (setv server (get mcp "mcpServers" "doeff_result"))
  (assert (= (get server "type") "stdio"))
  (assert (= (get server "command") "/opt/doeff-sessionhost"))
  (assert (= (get server "args")
             ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]))
  (assert (in "--strict-mcp-config" argv))
  ;; prompt は argv に載らない・print mode 不使用(launch invariant)
  (assert (not-in "do the task" argv))
  (assert (not-in "-p" argv))
  (assert (not-in "--print" argv)))


(deftest test-claude-argv-caller-sse-servers
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :mcp_servers {"tools" "http://127.0.0.1:9/sse"}
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (setv mcp (json.loads (get argv (+ (.index argv "--mcp-config") 1))))
  (assert (= (get mcp "mcpServers" "tools")
             {"type" "sse" "url" "http://127.0.0.1:9/sse"}))
  (assert (in "doeff_result" (get mcp "mcpServers"))))


(deftest test-claude-argv-no-mcp-config-without-channel
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude" :result_channel None
                            :expected_result None))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (not-in "--mcp-config" argv))
  (assert (not-in "--strict-mcp-config" argv))
  ;; effort / model 無指定はフラグ自体を出さない
  (assert (not-in "--effort" argv))
  (assert (not-in "--model" argv)))


(deftest test-codex-argv-golden-wiring
  (setv world (ImplWorld))
  (setv params (base-params :model "gpt-5"
                            :effort "high"
                            :mcp_servers {"tools" "http://127.0.0.1:9/sse"}
                            :result_channel (channel-spec)))
  (<- argv (run-codex world (build-launch "codex" params)))
  (assert (= (cut argv 0 2) ["codex" "--yolo"]))
  ;; effort は TOML 文字列(oracle toml_quoted_string)
  (assert (in "model_reasoning_effort=\"high\"" argv))
  ;; caller server は url、channel は command + args(TOML 配列、key は常に quote)
  (assert (in "mcp_servers.\"tools\".url=\"http://127.0.0.1:9/sse\"" argv))
  (assert (in "mcp_servers.\"doeff_result\".command=\"/opt/doeff-sessionhost\"" argv))
  (assert (in (+ "mcp_servers.\"doeff_result\".args="
                 "[\"report-result-mcp\",\"--session\",\"s1\",\"--socket\",\"/tmp/agentd.sock\"]")
              argv))
  ;; -c が各値の直前に居る
  (setv effort-idx (.index argv "model_reasoning_effort=\"high\""))
  (assert (= (get argv (- effort-idx 1)) "-c"))
  ;; model は channel 配線の後(oracle 順序)
  (assert (= (get argv (+ (.index argv "--model") 1)) "gpt-5"))
  (assert (> (.index argv "--model")
             (.index argv "mcp_servers.\"doeff_result\".command=\"/opt/doeff-sessionhost\"")))
  ;; prompt は argv に載らない
  (assert (not-in "do the task" argv)))


(deftest test-codex-argv-minimal
  (setv world (ImplWorld))
  (setv params (base-params :result_channel None :expected_result None))
  (<- argv (run-codex world (build-launch "codex" params)))
  (assert (= argv ["codex" "--yolo"])))


;; ---------------------------------------------------------------------------
;; WireResultChannel — mcp_command_args 同物理(main.rs:1319)
;; ---------------------------------------------------------------------------

(deftest test-wire-result-channel-spec
  (setv world (ImplWorld))
  (<- spec (run-codex world (wire-result-channel "codex" "s1" "/tmp/agentd.sock")))
  (assert (= (get spec "command") "/opt/doeff-sessionhost"))
  (assert (= (get spec "args")
             ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]))
  (setv world2 (ImplWorld))
  (<- spec2 (run-claude world2 (wire-result-channel "claude" "s1" "/tmp/agentd.sock")))
  (assert (= spec spec2)))


;; ---------------------------------------------------------------------------
;; PreLaunchSetup codex — S11 ゲート + config.toml trust 物理
;; ---------------------------------------------------------------------------

(deftest test-codex-prelaunch-rejects-missing-codex-home
  (setv world (ImplWorld))
  ;; process env に CODEX_HOME が居ても、session_env に明示が無ければ拒否
  ;; (oracle: ゲートは session_env / command 明示のみを見る — 暗黙の
  ;; ~/.codex fallback が個人アカウントを焼いた実障害)
  (setv (get world.env "CODEX_HOME") "/env/home")
  (setv params (base-params))
  (setv raised None)
  (try
    (<- _ (run-codex world (pre-launch-setup "codex" params)))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (in "no agent auth profile" (str raised)))
  (assert (in "CODEX_HOME" (str raised)))
  ;; tmux 効果ゼロ = 「tmux 呼び出し前に fail」の直接束縛版
  (assert (= world.tmux-calls []))
  ;; fs にも触っていない
  (assert (= world.atomic-writes []))
  (assert (= world.fs {})))


(deftest test-codex-prelaunch-accepts-command-embedded-home
  (setv world (ImplWorld))
  (setv params (base-params
                 :command "CODEX_HOME=/x/codex codex --yolo"))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  ;; command 埋め込みはゲート通過(trust 書き先は解決不能なので env fallback、
  ;; ここでは env 台本も無し → trust 書き込みはスキップされない:
  ;; oracle は daemon env fallback で ~/.codex に書くが、直接束縛では
  ;; EnvGet(None) → 書き先無しの typed skip とし、identity は None を返す)
  (assert (= world.tmux-calls [])))


(deftest test-codex-prelaunch-trust-creates-config
  (setv world (ImplWorld))
  (setv params (base-params :session_env {"CODEX_HOME" "/x/codex"}))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  ;; 実効 identity が返る(S14 の Hy positive 化の布石)
  (assert (= (get identity "CODEX_HOME") "/x/codex"))
  (assert (in "/x/codex" world.dirs))
  (setv written (get world.fs "/x/codex/config.toml"))
  (assert (in "[projects.\"/work/dir\"]" written))
  (assert (in "trust_level = \"trusted\"" written)))


(deftest test-codex-prelaunch-trust-idempotent-replace
  (setv world (ImplWorld))
  (setv (get world.fs "/x/codex/config.toml")
        "[projects.\"/work/dir\"]\ntrust_level = \"untrusted\"\n[other]\nkey = 1\n")
  (setv params (base-params :session_env {"CODEX_HOME" "/x/codex"}))
  (<- _ (run-codex world (pre-launch-setup "codex" params)))
  (setv written (get world.fs "/x/codex/config.toml"))
  ;; 既存 header 内の trust_level を差し替え(重複 append しない)
  (assert (= (.count written "trust_level") 1))
  (assert (in "trust_level = \"trusted\"" written))
  (assert (in "[other]" written)))


;; ---------------------------------------------------------------------------
;; PreLaunchSetup claude — S12 trust pre-seed(canonical key・temp+rename)
;; ---------------------------------------------------------------------------

(deftest test-claude-prelaunch-preseeds-trust
  (setv world (ImplWorld))
  (setv (get world.canonical "/work/dir") "/private/work/dir")
  (setv params (base-params :agent_type "claude"
                            :session_env {"CLAUDE_CONFIG_DIR" "/x/claude"}))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (in "/x/claude" world.dirs))
  ;; canonicalized work_dir が project key(S12: /tmp → /private/tmp)
  (setv state (json.loads (get world.fs "/x/claude/.claude.json")))
  (setv project (get state "projects" "/private/work/dir"))
  (assert (= (get project "hasTrustDialogAccepted") True))
  (assert (= (get project "hasCompletedProjectOnboarding") True))
  ;; temp+rename(oracle: .agentd-tmp suffix — 残骸不在は substrate 契約)
  (assert (= world.atomic-writes [#("/x/claude/.claude.json" ".agentd-tmp")])))


(deftest test-claude-prelaunch-merges-existing-state
  (setv world (ImplWorld))
  (setv (get world.fs "/x/claude/.claude.json")
        (json.dumps {"projects" {"/old" {"hasTrustDialogAccepted" True}}
                     "userID" "u1"}))
  (setv params (base-params :agent_type "claude"
                            :session_env {"CLAUDE_CONFIG_DIR" "/x/claude"}))
  (<- _ (run-claude world (pre-launch-setup "claude" params)))
  (setv state (json.loads (get world.fs "/x/claude/.claude.json")))
  ;; 既存 state は保持されつつ新 project が追記される
  (assert (= (get state "userID") "u1"))
  (assert (in "/old" (get state "projects")))
  (assert (in "/work/dir" (get state "projects"))))


(deftest test-claude-prelaunch-env-fallback-no-raise
  (setv world (ImplWorld))
  ;; CLAUDE_CONFIG_DIR 無し = warning のみ(DOE-003 R3 staged)。
  ;; process env fallback → HOME/.claude 既定(oracle home_dir().join(.claude))
  (setv (get world.env "HOME") "/home/u")
  (setv params (base-params :agent_type "claude"))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/home/u/.claude"))
  (assert (in "explicit" (get identity "warnings" 0))))


;; ---------------------------------------------------------------------------
;; ClassifyPane — F-* marker + R9 dialog(dismiss keys は impl 所有)
;; ---------------------------------------------------------------------------

(defk classify-codex [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  (setv world (ImplWorld))
  (<- obs (run-codex world (classify-pane "codex" output)))
  obs)

(defk classify-claude [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  (setv world (ImplWorld))
  (<- obs (run-claude world (classify-pane "claude" output)))
  obs)


(deftest test-classify-codex-idle-and-active
  (<- idle (classify-codex "some output\n› "))
  (assert idle.has-idle-prompt)
  (assert (not idle.has-active-marker))
  (assert idle.startup-finished)
  (<- active (classify-codex "working (12s • esc to interrupt)"))
  (assert active.has-active-marker)
  ;; MCP boot 中の spinner は active ではない(16h-stuck 実障害)
  (<- booting (classify-codex "Starting MCP servers (1/5) (esc to interrupt)"))
  (assert (not booting.has-active-marker))
  (assert (not booting.startup-finished)))


(deftest test-classify-claude-spinner-physics
  ;; 最終 ❯ の上の非空行に `… (` = live spinner(oracle
  ;; output_has_live_claude_spinner_marker)
  (<- active (classify-claude "✢ Swooping… (37s · thinking…)\n\n❯"))
  (assert active.has-active-marker)
  ;; ❯ の上が普通の出力なら active ではない
  (<- idle (classify-claude "⏺ done reading\n\n❯"))
  (assert (not idle.has-active-marker))
  (assert idle.has-idle-prompt)
  (assert idle.has-turn-activity))


(deftest test-classify-failure-api-waiting-windows
  ;; failure は tail 10 行窓
  (<- f (classify-codex "fatal error: kaboom"))
  (assert f.has-failure-marker)
  ;; api-limit は tail 30 行窓
  (<- a (classify-codex "rate limit exceeded"))
  (assert a.has-api-limit-marker)
  ;; waiting は raw 一致
  (<- w (classify-claude "Type your message"))
  (assert w.has-waiting-marker))


(deftest test-classify-codex-update-dialog-down-steps
  (setv frame-sel1 (+ "✨ Update available!\n"
                      "› 1. Update now (runs npm install)\n"
                      "  2. Skip\n"
                      "  3. Skip until next version\n"
                      "Press enter to continue"))
  (<- obs1 (classify-codex frame-sel1))
  (assert (= obs1.dialog "codex-update"))
  (assert (= (list obs1.dialog-dismiss-keys) ["Down" "Down" "Enter"]))
  (assert (not obs1.startup-finished))
  ;; 0.142.x は "2. Skip" が初期選択(oracle: selected-option から Down 数を導出)
  (setv frame-sel2 (.replace frame-sel1 "› 1. Update now" "  1. Update now"))
  (setv frame-sel2 (.replace frame-sel2 "  2. Skip\n" "› 2. Skip\n"))
  (<- obs2 (classify-codex frame-sel2))
  (assert (= (list obs2.dialog-dismiss-keys) ["Down" "Enter"])))


(deftest test-classify-claude-dialogs
  (setv bypass (+ "Bypass Permissions mode\n"
                  "❯ 1. No, exit\n  2. Yes, I accept\n"
                  "Enter to confirm"))
  (<- b (classify-claude bypass))
  (assert (= b.dialog "bypass"))
  (assert (= (list b.dialog-dismiss-keys) ["Down" "Enter"]))
  (assert (not b.startup-finished))
  (setv fullscreen (+ "Try the new fullscreen renderer?\n"
                      "❯ 1. Yes, try it\n  2. Not now\n"
                      "Enter to confirm"))
  (<- fs (classify-claude fullscreen))
  (assert (= fs.dialog "fullscreen"))
  (assert (= (list fs.dialog-dismiss-keys) ["Down" "Enter"]))
  (setv managed "Managed settings require approval\nSettings requiring approval:\n  - statusLine")
  (<- m (classify-claude managed))
  (assert (= m.dialog "managed"))
  (assert (= (list m.dialog-dismiss-keys) ["Enter"])))


(deftest test-classify-claude-trust-dialog
  ;; 実物 frame(herdr demo-claude-2 で 2026-07-07 逐語採取。workspace path
  ;; 行のみ可変なので一般化)。claude CLI が未 trust の cwd で起動すると出す
  ;; startup gate — R9 で dismiss しないと wait-for-repl-idle が永久に idle を
  ;; 見ず、120s 上限縮退 → launch が prompt を dialog に送出して死ぬ実障害。
  ;; 長文の質問文は pane 幅次第で reflow されるため marker には使わない
  ;; (幾何学物理: 折返しは部分文字列一致を殺す)。
  (setv trust (+ "\n"
                 " Accessing workspace:\n"
                 "\n"
                 " /home/user\n"
                 "\n"
                 " Quick safety check: Is this a project you created or one you trust? (Like your own code,\n"
                 " a well-known open source project, or work from your team). If not, take a moment to\n"
                 " review what's in this folder first.\n"
                 "\n"
                 " Claude Code'll be able to read, edit, and execute files here.\n"
                 "\n"
                 " Security guide\n"
                 "\n"
                 " ❯ 1. Yes, I trust this folder\n"
                 "   2. No, exit\n"
                 "\n"
                 " Enter to confirm · Esc to cancel\n"))
  (<- t (classify-claude trust))
  ;; 既定選択が option 1(trust 側)なので dismiss は Enter 単発。doeff が
  ;; 制御する work_dir を信頼する = pre-seed(hasTrustDialogAccepted=True)と
  ;; 同じポリシー(bypass は既定 No,exit だから Down,Enter — trust は違う)
  (assert (= t.dialog "trust"))
  (assert (= (list t.dialog-dismiss-keys) ["Enter"]))
  ;; 選択行は行頭スペース付き ` ❯` — idle prompt と誤認しないこと
  (assert (not t.has-idle-prompt))
  ;; trust dialog は stuck-in-startup — launch watchdog の解除信号ではない
  (assert (not t.startup-finished)))


(deftest test-classify-unsubmitted-paste
  (setv frame "❯ [Pasted text +40 lines]")
  (<- obs (classify-claude frame))
  (assert obs.has-unsubmitted-paste)
  (<- clean (classify-claude "❯"))
  (assert (not clean.has-unsubmitted-paste)))


;; ---------------------------------------------------------------------------
;; DeliverMessage — live REPL paste + submit(盲窓物理は substrate 所有)
;; ---------------------------------------------------------------------------

(deftest test-deliver-message-yields-literal-submit
  (setv world (ImplWorld))
  (<- _ (run-codex world (deliver-message "%1" "hello agent")))
  (assert (= world.sent-keys [#("%1" "hello agent" True True)])))
