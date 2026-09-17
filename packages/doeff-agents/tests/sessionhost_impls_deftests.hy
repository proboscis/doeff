;;; 直接束縛 deftest: per-kind defhandler(claude-code / codex)の protocol 物理検証
;;; (DOE-004 C2)。
;;;
;;; oracle parity の対象(conformance README の凍結物理):
;;;   - S13: argv 配線(claude --settings disableAllHooks は既定のみ —
;;;     session_hooks=inherit で外れる。+ --mcp-config stdio +
;;;     --strict-mcp-config / codex -c mcp_servers."doeff_result".command/.args。
;;;     prompt は argv に載らない・print mode 不使用)
;;;   - S12: claude trust pre-seed(canonicalized work_dir key・temp+rename)
;;;   - S11: codex CODEX_HOME ゲート(tmux 効果ゼロで typed fail)
;;;   - F-* marker 分類 + R9 dialog 検出(dismiss keys は impl 所有)
;;;
;;; fake substrate(dict-backed fs / 台本 env / 記録 tmux)で impl handler を
;;; 直接束縛する。生 IO ゼロ。oracle: agentd-rust-final:src/main.rs
;;; build_claude_argv / build_codex_argv / trust_*_workspace /
;;; output_has_* / dismiss_*。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import json)
(import pytest)
(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  PaneObservation
  BuildLaunch
  FsComposeHomeView
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
(import doeff_agents.sessionhost.impls.markers [is-api-limit-refusal])


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
    (setv self.composed [])        ;; FsComposeHomeView の記録(二軸形の家)
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
  (FsComposeHomeView [auth-file profile-dir view-root]
    ;; 二軸形の家の合成(#15)。実体は substrate の compose-home-view —
    ;; ここは決定的な view の path を返すだけ(従量課金の便 lane A の metered codex が
    ;; 通る経路)。
    (.append world.composed #(auth-file profile-dir view-root))
    (resume f"{view-root}/composed-view"))
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


(deftest test-claude-argv-session-hooks-inherit
  ;; session_hooks=inherit(daemon env knob DOEFF_AGENTD_SESSION_HOOKS —
  ;; launch.hy が params へ写す)では --settings {"disableAllHooks":true} を
  ;; 出さない: 全 hook 無効化は安全側 hook まで切る(2026-08-18 ACP 起動会話で
  ;; 発火 0 の実測 — route-c03fe34745)。他の凍結物理は既定と同一。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :session_hooks "inherit"
                            :effort "high"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (= (cut argv 0 2) ["claude" "--dangerously-skip-permissions"]))
  (assert (not-in "--settings" argv))
  (assert (in "--strict-mcp-config" argv))
  (assert (not-in "-p" argv)))


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
  ;; process env に CODEX_HOME が居ても、binding / command の明示が無ければ
  ;; 拒否(R7: ゲートは typed binding と command 埋め込みのみを見る — 暗黙の
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
  (setv params (base-params :binding {"kind" "codex" "codex_home" "/x/codex"}))
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
  (setv params (base-params :binding {"kind" "codex" "codex_home" "/x/codex"}))
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
                            :binding {"kind" "claude-code"
                                      "config_dir" "/x/claude"}))
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
                            :binding {"kind" "claude-code"
                                      "config_dir" "/x/claude"}))
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
;; PreLaunchSetup の課金の階級(従量課金の便 lane A・ADR-DOE-AGENTS-004 R9 改訂):
;; 家の中の従量課金の宣言と binding の kind の一致を起動前に検める
;; ---------------------------------------------------------------------------

(deftest test-claude-metered-pre-launch-requires-declared-credential-in-settings
  ;; kind `claude-code-metered` は「この家は従量課金」という宣言を型で運ぶので、
  ;; 家の中身が本当にそう宣言しているかを起動前に検める。受けるのは公式文書の
  ;; 2 形だけ: settings.json の apiKeyHelper か、Vertex の env の対
  ;; (CLAUDE_CODE_USE_VERTEX=1 + ANTHROPIC_VERTEX_PROJECT_ID)。
  ;; 断りは trust の書きより前(fs への書き込みゼロ・tmux 効果ゼロ)。
  ;; **鍵の値は読まない**: 判定は policy の純関数が宣言の名だけを返す。
  (setv metered-binding {"kind" "claude-code-metered" "config_dir" "/x/claude-metered"})

  ;; (1) apiKeyHelper で通る + 課金の階級の印が identity に載る
  (setv world (ImplWorld))
  (setv (get world.fs "/x/claude-metered/settings.json")
        (json.dumps {"apiKeyHelper" "cat /secrets/anthropic-key"}))
  (setv params (base-params :agent_type "claude" :binding metered-binding))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude-metered"))
  (assert (= (get identity "billing") "metered"))
  ;; 鍵を読む道具の綴り(apiKeyHelper の値)は identity に載らない
  (assert (not-in "secrets/anthropic-key" (json.dumps identity)))
  ;; trust の pre-seed は従来どおり同じ本体を通る(並行実装を作っていない pin)
  (assert (in "/x/claude-metered/.claude.json" world.fs))

  ;; (2) Vertex の env の対でも通る(鍵の無い従量課金 — 課金は GCP)
  (setv world2 (ImplWorld))
  (setv (get world2.fs "/x/claude-metered/settings.json")
        (json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"
                            "ANTHROPIC_VERTEX_PROJECT_ID" "proj-x"}}))
  (<- identity2 (run-claude world2 (pre-launch-setup "claude" params)))
  (assert (= (get identity2 "billing") "metered"))

  ;; (3) 宣言が無い / 家が無い / 壊れている / 受けない形 → typed reject・副作用ゼロ
  (for [settings [None
                  "{not json"
                  (json.dumps {})
                  (json.dumps {"env" {"ANTHROPIC_API_KEY" "sk-x"}})
                  (json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"}})
                  (json.dumps {"apiKeyHelper" "   "})]]
    (setv w (ImplWorld))
    (when (is-not settings None)
      (setv (get w.fs "/x/claude-metered/settings.json") settings))
    (setv raised None)
    (try
      (<- _ (run-claude w (pre-launch-setup "claude" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {settings !r}")
    (assert (in "claude-code-metered" (str raised)))
    (assert (in "apiKeyHelper" (str raised)))
    ;; trust の書きより前に断っている
    (assert (= w.atomic-writes []))
    (assert (not-in "/x/claude-metered/.claude.json" w.fs))
    (assert (= w.tmux-calls [])))

  ;; (4) 定額の kind に metered の枝が漏れない: 宣言の無い定額の家は今日どおり通り、
  ;; 課金の階級の印も付かない(宣言が **在る** 定額の家の締め直しは lane B の
  ;; test-claude-pre-launch-rejects-metered-declaration-in-subscription-home が持つ)。
  (setv plain (ImplWorld))
  (setv (get plain.fs "/x/claude/settings.json") (json.dumps {"model" "opus"}))
  (<- plain-identity
      (run-claude plain (pre-launch-setup
                          "claude"
                          (base-params :agent_type "claude"
                                       :binding {"kind" "claude-code"
                                                 "config_dir" "/x/claude"}))))
  (assert (= (get plain-identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (not-in "billing" plain-identity)))


(deftest test-codex-metered-pre-launch-requires-api-key-field-in-auth-file
  ;; kind `codex-metered` の受理形は制御面の二軸宣言 {auth_file, profile_dir}。
  ;; host は宣言から家の view を合成し、その auth.json が **非空の**
  ;; OPENAI_API_KEY を持つことだけを検める(値は読まない)。
  ;; ⚠ 欄の有無では判じない: codex の CLI は定額(ChatGPT)の login でも
  ;; auth.json にこの欄を null で書き出す(実測 2026-09-15 — 運用主の家 3 つとも
  ;; 欄は在る・中身は空)。欄の有無で判じると定額の家を全部「従量課金」と誤る。
  (setv metered-binding {"kind" "codex-metered"
                         "auth_file" "/auths/metered.json"
                         "profile_dir" "/profiles/metered"})
  (setv params (base-params :binding metered-binding))
  (setv view "/state/doeff/agent-homes/composed-view")

  ;; (1) 非空の鍵の欄で通る + 印と二軸の宣言が identity に載る(resume 用)
  (setv world (ImplWorld))
  (setv (get world.env "XDG_STATE_HOME") "/state")
  (setv (get world.fs f"{view}/auth.json")
        (json.dumps {"OPENAI_API_KEY" "sk-test-not-a-real-key" "auth_mode" "apikey"}))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  (assert (= (get identity "CODEX_HOME") view))
  (assert (= (get identity "billing") "metered"))
  (assert (= (get identity "codex_auth_file") "/auths/metered.json"))
  (assert (= (get identity "codex_profile_dir") "/profiles/metered"))
  ;; 鍵の値は identity に載らない
  (assert (not-in "sk-test-not-a-real-key" (json.dumps identity)))
  ;; 合成は宣言二軸 + 解決した view root で 1 回
  (assert (= world.composed [#("/auths/metered.json" "/profiles/metered"
                               "/state/doeff/agent-homes")]))
  ;; trust は従来どおり同じ本体が view の config.toml へ書く
  (assert (in "trust_level = \"trusted\"" (get world.fs f"{view}/config.toml")))

  ;; (2) 欄が空 / null / 家が無い / 壊れている → typed reject・trust の書きゼロ
  (for [auth [None
              "{not json"
              (json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"
                           "tokens" {"access_token" "t"}})
              (json.dumps {"OPENAI_API_KEY" ""})
              (json.dumps {"auth_mode" "chatgpt"})]]
    (setv w (ImplWorld))
    (setv (get w.env "XDG_STATE_HOME") "/state")
    (when (is-not auth None)
      (setv (get w.fs f"{view}/auth.json") auth))
    (setv raised None)
    (try
      (<- _ (run-codex w (pre-launch-setup "codex" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {auth !r}")
    (assert (in "codex-metered" (str raised)))
    (assert (in "codex login --with-api-key" (str raised)))
    (assert (= w.atomic-writes []))
    (assert (= w.tmux-calls [])))

  ;; (3) 定額の kind に metered の枝が漏れない: 定額の login の家(欄は在るが空)は
  ;; 今日どおり通り、課金の階級の印も付かない(非空の欄を持つ定額の家の締め直しは
  ;; lane B の test-codex-pre-launch-rejects-api-key-in-subscription-auth-file が持つ)。
  (setv plain (ImplWorld))
  (setv (get plain.fs "/x/codex/auth.json")
        (json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"}))
  (<- plain-identity
      (run-codex plain (pre-launch-setup
                         "codex"
                         (base-params :binding {"kind" "codex"
                                                "codex_home" "/x/codex"}))))
  (assert (= (get plain-identity "CODEX_HOME") "/x/codex"))
  (assert (not-in "billing" plain-identity)))


(deftest test-claude-pre-launch-rejects-metered-declaration-in-subscription-home
  ;; lane B の締め直し(従量課金の便・ADR-DOE-AGENTS-003 R4 改訂): 定額の kind の家に従量課金の
  ;; 宣言があれば拒否する。旧形は env の名しか見ないので、家の中に鍵を入れれば黙って
  ;; 通る道が開いていた — 課金の階級が型に現れず、監査点も消える形。
  ;; 直し方 2 択(kind を *-metered にする / 家から宣言を外す)を文言が名指す。
  ;; account(どの人の login か)は今も検めない(R4 の不変部分)。
  (setv subscription {"kind" "claude-code" "config_dir" "/x/claude"})
  (setv params (base-params :agent_type "claude" :binding subscription))
  (for [[settings declared]
        [#((json.dumps {"apiKeyHelper" "cat /secrets/key"}) "apiKeyHelper")
         #((json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"}})
           "env.CLAUDE_CODE_USE_VERTEX")
         #((json.dumps {"env" {"ANTHROPIC_API_KEY" "sk-x"}}) "env.ANTHROPIC_API_KEY")
         #((json.dumps {"env" {"ANTHROPIC_AUTH_TOKEN" "t"}}) "env.ANTHROPIC_AUTH_TOKEN")]]
    (setv world (ImplWorld))
    (setv (get world.fs "/x/claude/settings.json") settings)
    (setv raised None)
    (try
      (<- _ (run-claude world (pre-launch-setup "claude" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {settings}")
    (setv message (str raised))
    (assert (in "is a subscription kind" message) message)
    (assert (in declared message) message)
    (assert (in "claude-code-metered" message) message)
    (assert (in "remove the metered declaration" message) message)
    ;; 断りは trust の書きより前(副作用ゼロ)
    (assert (= world.atomic-writes []))
    (assert (not-in "/x/claude/.claude.json" world.fs))
    (assert (= world.tmux-calls []))
    ;; 鍵の値そのものは文言に出ない(名だけ)
    (assert (not-in "secrets/key" message))
    (assert (not-in "sk-x" message)))

  ;; 宣言の無い家・不在の家は今日どおり通る(段階強制を巻き戻さない)
  (for [settings [None (json.dumps {}) (json.dumps {"model" "opus"})]]
    (setv ok-world (ImplWorld))
    (when (is-not settings None)
      (setv (get ok-world.fs "/x/claude/settings.json") settings))
    (<- identity (run-claude ok-world (pre-launch-setup "claude" params)))
    (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude"))
    (assert (not-in "billing" identity))
    (assert (in "/x/claude/.claude.json" ok-world.fs)))

  ;; 破損した settings.json は **通す**(今日と同じ — 破損は claude 自身が loud に
  ;; 落ちる)。ただし検められなかったことを warning に 1 行残す。
  (setv broken (ImplWorld))
  (setv (get broken.fs "/x/claude/settings.json") "{not json")
  (<- broken-identity (run-claude broken (pre-launch-setup "claude" params)))
  (assert (= (get broken-identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (in "/x/claude/.claude.json" broken.fs))
  (assert (any (gfor w (get broken-identity "warnings") (in "not a JSON object" w)))
          (str (get broken-identity "warnings"))))


(deftest test-codex-pre-launch-rejects-api-key-in-subscription-auth-file
  ;; lane B の codex 面: 定額の kind の家(native 形 {codex_home} も二軸形も同じ
  ;; 1 点の読み)に **非空の** OPENAI_API_KEY が在れば拒否する。
  ;; ⚠ 欄が null / 空の家は通る — codex の CLI は定額(ChatGPT)の login でも欄を
  ;; null で書き出す(実測 2026-09-15: 運用主の家 3 つとも欄は在る・中身は空)。
  ;; 欄の有無で判じると運用主の codex の起動を 100% 断る。
  (setv native {"kind" "codex" "codex_home" "/x/codex"})
  (setv two-axis {"kind" "codex" "auth_file" "/auths/sub.json"
                  "profile_dir" "/profiles/sub"})
  (setv view "/state/doeff/agent-homes/composed-view")

  ;; (1) native 形: 非空の鍵の欄 → 拒否(trust の書きより前)
  (setv world (ImplWorld))
  (setv (get world.fs "/x/codex/auth.json")
        (json.dumps {"OPENAI_API_KEY" "sk-x" "auth_mode" "apikey"}))
  (setv raised None)
  (try
    (<- _ (run-codex world (pre-launch-setup "codex" (base-params :binding native))))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (setv message (str raised))
  (assert (in "is a subscription kind" message) message)
  (assert (in "codex-metered" message) message)
  (assert (in "OPENAI_API_KEY" message) message)
  (assert (not-in "sk-x" message) message)
  (assert (= world.atomic-writes []))
  (assert (= world.tmux-calls []))

  ;; (2) 二軸形も同じ 1 点で検まる(合成 view の auth.json を読む)
  (setv world2 (ImplWorld))
  (setv (get world2.env "XDG_STATE_HOME") "/state")
  (setv (get world2.fs f"{view}/auth.json") (json.dumps {"OPENAI_API_KEY" "sk-y"}))
  (setv raised2 None)
  (try
    (<- _ (run-codex world2 (pre-launch-setup "codex" (base-params :binding two-axis))))
    (except [e RuntimeError] (setv raised2 e)))
  (assert (is-not raised2 None))
  (assert (in "is a subscription kind" (str raised2)))
  (assert (= world2.atomic-writes []))

  ;; (3) 定額の login の家(欄は在るが null / 空 / 欄なし)は今日どおり通る —
  ;; 運用主の配備が止まらないことの pin
  (for [auth [(json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"
                           "last_refresh" "2026-09-15T00:00:00Z"
                           "tokens" {"access_token" "t"}})
              (json.dumps {"OPENAI_API_KEY" ""})
              (json.dumps {"OPENAI_API_KEY" "   "})
              (json.dumps {"auth_mode" "chatgpt"})
              None]]
    (setv ok-world (ImplWorld))
    (when (is-not auth None)
      (setv (get ok-world.fs "/x/codex/auth.json") auth))
    (<- identity (run-codex ok-world (pre-launch-setup "codex" (base-params :binding native))))
    (assert (= (get identity "CODEX_HOME") "/x/codex"))
    (assert (not-in "billing" identity))
    ;; trust は従来どおり書かれる
    (assert (in "trust_level = \"trusted\"" (get ok-world.fs "/x/codex/config.toml"))))

  ;; (4) 破損した auth.json は通す(今日と同じ — 破損は codex 自身が loud に落ちる)
  (setv broken (ImplWorld))
  (setv (get broken.fs "/x/codex/auth.json") "{not json")
  (<- broken-identity (run-codex broken (pre-launch-setup "codex" (base-params :binding native))))
  (assert (= (get broken-identity "CODEX_HOME") "/x/codex")))


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


(deftest test-classify-claude-spinner-crosses-composer-border
  ;; issue #573(2026-07-29 実 incident の pane snapshot 現物): 現行 claude TUI
  ;; は入力欄を全幅罫線で囲む — `❯` 直上の非空行は罫線になり、live spinner 行は
  ;; その 1 つ上に居る。罫線で探索が止まると active-marker が常に false になり、
  ;; turn-end 判定が stability guard 単独に退化 → spinner の秒刻みと monitor
  ;; 間隔の aliasing で走行中 turn へ solicitation・中断キーが飛ぶ。
  (setv bordered-working
        (+ "s/adr/defadr_0066_merge_lane_land_queue.hy\n"
           "\n"
           "✦ Cerebrating… (1m 34s · ↓ 2.2k tokens · thought for 39s)\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle) · gh auth login for PR status ·\n"))
  (<- active (classify-claude bordered-working))
  (assert active.has-active-marker)
  ;; 枠線付きの idle pane — 罫線 skip が本文から spinner を捏造しないこと
  (setv bordered-idle
        (+ "⏺ done. result written.\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"))
  (<- idle (classify-claude bordered-idle))
  (assert (not idle.has-active-marker))
  (assert idle.has-idle-prompt)
  ;; 歴史 spinner(罫線の上の直近非空行が本文)は live ではない
  (setv bordered-history
        (+ "✢ Swooping… (1s · thinking)\n"
           "wrote invalid result\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"))
  (<- hist (classify-claude bordered-history))
  (assert (not hist.has-active-marker)))


(deftest test-classify-claude-working-pane-carries-both-marker-facts
  ;; ACP issue 55b1bd の前提の逐語固定(agentd.sqlite 直読 2026-08-12・
  ;; session agent_inv_wi_5f3fa0e22242b74d_a1 の死亡時 output_snippet 現物)。
  ;; この席は 31 分 5 秒・91.4k トークンを走らせている最中に「入力待ちが
  ;; 1800 秒続いた」として終端された。現物が示す事実は 2 つ同時である:
  ;;   - waiting marker は True(permission-mode フッターの逐語 3 語が常設)
  ;;   - active marker も True(罫線を跨いだ live spinner + esc to interrupt)
  ;; marker 検出はどちらも正しい。誤りは『waiting だけを見て状態を決める』
  ;; 分類の側にあった(policy 側 deftest
  ;; test-working-pane-with-waiting-footer-stays-running が連言を固定する)。
  (setv working-31min
        (+ "✶ Whatchamacalliting… (31m 5s · ↓ 91.4k tokens)\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for age…\n"))
  (<- obs (classify-claude working-31min))
  (assert obs.has-waiting-marker)
  (assert obs.has-active-marker))


(deftest test-classify-claude-queued-messages-fact
  ;; issue #573(incident event 1997731 現物): mid-turn に配送された message は
  ;; composer に積まれ、入力行が `❯ Press up to edit queued messages` になる。
  ;; 未消費 queue = turn 走行中の明白な busy 証拠 — PaneObservation が事実として
  ;; 運び、中断キー送出の安全壁が参照する。
  (setv queued
        (+ (* "─" 80) "\n"
           "❯ Press up to edit queued messages\n"
           (* "─" 80) "\n"))
  (<- obs (classify-claude queued))
  (assert obs.has-queued-messages)
  ;; 素の idle pane に queue 事実は立たない
  (<- idle (classify-claude "some scroll\n❯ \n"))
  (assert (not idle.has-queued-messages))
  ;; 履歴に同文言が写っても最終 prompt 行に居なければ事実にしない
  (<- hist (classify-claude
             "⏺ echoed: Press up to edit queued messages\n\n❯ \n"))
  (assert (not hist.has-queued-messages)))


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


(deftest test-api-limit-marker-current-claude-tui-exhausted-wordings
  ;; issue #557 二次補強: 現行 claude TUI の exhausted 側文言は marker 表に
  ;; 無かった(2026-07-20 実測)。exhausted 側だけを追補する。
  (for [frame ["Claude usage limit reached. Your limit will reset at 6pm (Asia/Tokyo)."
               "You've reached your usage limit · resets Jul 26 at 6am"
               "You've hit your usage limit. Upgrade to increase your limits."
               "5-hour limit reached ∙ resets 3am"
               "Weekly limit reached · resets Jul 29 at 9am"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}"))
  ;; approaching 側(まだ動ける)は blocked_api を立ててはならない —
  ;; issue #557 実測 footer 文言(88% 到達)は marker 非対象
  (<- approaching (classify-claude
                    "You've used 88% of your Fable 5 limit · resets Jul 26 at 6am"))
  (assert (not approaching.has-api-limit-marker)))


(deftest test-api-limit-marker-adr0049-r9-spend-limit-wordings
  ;; ACP ADR 0049 R9(2026-07-26 実 incident): Fable 月次枠 0% の実物文言
  ;; 「You've hit your monthly spend limit. /model to switch models.」は
  ;; 既存の "you've hit your limit" に部分一致しない("monthly spend" が
  ;; 挟まる)。credits 枯渇(out of usage credits)も同系の実物文言で
  ;; 語彙に無かった。両方とも exhausted 側 = blocked_api が正。
  (for [frame ["You've hit your monthly spend limit. /model to switch models."
               "You're out of usage credits · buy more credits or upgrade your plan"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}")))


(deftest test-api-limit-marker-possessive-family-bounded-match
  ;; ACP ADR 0049 R9 改訂(2026-08-07): 所有格〜limit 間へ provider 可変語
  ;; (AI 名・プラン名・期間名)が挟まる exhausted 告知の族。逐語列挙は
  ;; 2026-07-20 / 07-26 / 08-06 と 3 度同型で破れた(8/6 実 incident:
  ;; 「You've reached your Fable 5 limit. /model to switch models.」が
  ;; 16 逐語のどれにも部分一致せず、22 件が run_failed/retryable=false で
  ;; 捨てられた)— 語を足すのではなく、有界可変挿入(空白区切り 0〜4 語・
  ;; 各語は英数 + 内部ピリオドのみ = 文境界を越えない)を許す族照合で
  ;; 根治する。
  (for [frame [;; 2026-08-06 実 incident verbatim(22 件を落とした形)
               "You've reached your Fable 5 limit. /model to switch models."
               ;; 2026-07-26 実 incident verbatim(monthly spend 形)も同じ族
               "You've hit your monthly spend limit. /model to switch models."
               ;; 2026-07-20 実測(usage limit 形)も同じ族
               "You've reached your usage limit · resets Jul 26 at 6am"
               "You've hit your usage limit. Upgrade to increase your limits."
               ;; 挿入 0 語(旧 "you've hit your limit" 逐語の被覆)
               "You've hit your limit. Upgrade to continue."
               ;; 族の一般形: 未知の provider 語でも有界内なら陽性
               ;;(バージョン番号の内部ピリオドも語として許す)
               "You've reached your Opus 4.5 weekly limit. /model to switch models."]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}")))


(deftest test-api-limit-marker-family-negative-controls
  ;; 陰性対照: 族照合は無限定の緩い照合(誤検知で健全席を上限扱い =
  ;; 走行中の席を blocked_api に落とす)であってはならない側の壁。
  (for [frame [;; approaching 側(まだ動ける)— 動詞 used は族外のまま
               ;;(issue #557 の対象外規律を族照合後も維持)
               "You've used 88% of your Fable 5 limit · resets Jul 26 at 6am"
               ;; 所有格と limit が文境界を越えて共起しても族外
               ;;(挿入語の内部ピリオド許容は文末ピリオドを含まない)
               "You've reached your destination. Set a new rate limit in settings."
               ;; 上限と無関係の通常出力
               "⏺ done reading the file\n\n❯"
               "All tests passed. 26 passed in 27.94s"]]
    (<- obs (classify-claude frame))
    (assert (not obs.has-api-limit-marker)
            f"unexpected api-limit marker: {frame !r}")))


(deftest test-api-limit-marker-org-cap-family
  ;; agora-redesign #513(2026-09-17 実 incident): 組織の側の上限「Your group's usage limit is
  ;; set to $0 · ask your admin for a higher limit」は所有格族の外の述部で、会社の口座 p10174 の
  ;; 18 手番が普通の終わりとして流れた(族の表が破れた 4 度目)。固定するのは述部と金額の先頭だけ。
  (for [frame ["Your group's usage limit is set to $0 · ask your admin for a higher limit"
               "Your organization’s usage limit is set to $25 · ask your admin for a higher limit"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}"))
  ;; 陰性対照: 設定の説明(金額なし)は上限の断りではない
  (<- prose (classify-claude "Your usage limit is set to the plan default. See /usage."))
  (assert (not prose.has-api-limit-marker)))


(deftest test-is-api-limit-refusal-structure-first-then-wording
  ;; agora-redesign #513: CLI が構造で名乗る status が在れば 429 ちょうどが限度(文は読まない)。
  ;; 実測 2026-09-17 の 4 種の文は全部 429 —— 未知の言い回しでも 429 なら限度。
  (setv unknown-wording (is-api-limit-refusal "Usage is paused for this workspace · ask your admin" 429))
  (assert unknown-wording)
  (setv group-cap (is-api-limit-refusal "Your group's usage limit is set to $0 · ask your admin for a higher limit" 429))
  (assert group-cap)
  ;; 429 でない status を名乗る断りは、文に limit の語が在っても限度ではない
  ;; (403 組織の剥奪・401 失効 —— 別の族・別の手当て)
  (setv revoked (is-api-limit-refusal "Your organization has disabled Claude subscription access · usage limit reached" 403))
  (assert (not revoked))
  (setv expired (is-api-limit-refusal "Failed to authenticate. API Error: 401 OAuth access token has been revoked." 401))
  (assert (not expired))
  ;; status を名乗らない面(pane の画面・旧い CLI・codex)は文の族の表に落ちる
  (setv worded (is-api-limit-refusal "You've reached your Fable limit. /model to switch models." None))
  (assert worded)
  (setv plain (is-api-limit-refusal "error_max_turns" None))
  (assert (not plain)))


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


(deftest test-classify-unsubmitted-attachment-chip
  ;; issue #568(ADR-DOE-AGENTS-010 R1): [Image #N] 添付チップは prompt 行の
  ;; 外(直下の行・行頭空白)に描かれる — 空の ❯ prompt + チップ行
  ;; (2026-07-28 実 wedge の pane 形)を unsubmitted と判定する。検知は
  ;; composer 領域(最終 prompt 行とそれ以降)を見る。
  (<- wedged (classify-claude (+ "❯\n"
                                 "  [Image #150]\n"
                                 "\n"
                                 "  ⏵⏵ bypass permissions on (shift+tab to cycle)")))
  (assert wedged.has-unsubmitted-paste)
  ;; paste チップ・queued ヒントも prompt 行の直下に落ちる形がある
  (<- pasted (classify-claude "❯\n  [Pasted text #1 +12 lines]"))
  (assert pasted.has-unsubmitted-paste)
  (<- queued (classify-claude "❯\n  Press up to edit queued messages"))
  (assert queued.has-unsubmitted-paste)
  ;; 送信済み履歴のチップ(最終 prompt 行より上)は unsubmitted ではない
  (<- historical (classify-claude (+ "❯ [Image #3]\n"
                                     "⏺ Read file\n"
                                     "❯")))
  (assert (not historical.has-unsubmitted-paste)))


;; ---------------------------------------------------------------------------
;; DeliverMessage — live REPL paste + submit(盲窓物理は substrate 所有)
;; ---------------------------------------------------------------------------

(deftest test-deliver-message-yields-literal-submit
  (setv world (ImplWorld))
  (<- _ (run-codex world (deliver-message "%1" "hello agent")))
  (assert (= world.sent-keys [#("%1" "hello agent" True True)])))
