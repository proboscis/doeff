;;; claude-code per-kind defhandler(ADR-DOE-AGENTS-004 R2、C2)。
;;;
;;; protocol 物理の単一の家(protocol-physics-has-one-home): oracle =
;;; packages/doeff-agentd/src/main.rs build_claude_argv / trust_claude_workspace。
;;; conformance 凍結: S12(trust pre-seed: canonicalized work_dir key・
;;; temp+rename)/ S13(--settings disableAllHooks + --mcp-config stdio +
;;; --strict-mcp-config・prompt は argv に載らない)/ DOE-003 R3
;;; (CLAUDE_CONFIG_DIR 無しは warning のみの staged enforcement)。
;;;
;;; substrate-clean 領域: 生 IO 禁止(defsemgrep 執行)。FS / env は
;;; Fs* / EnvGet substrate effect の yield のみ。

(require doeff-hy.macros [defk deff <- defhandler])

(import json)

(import doeff_agents.sessionhost.effects [
  BuildLaunch
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  WireResultChannel
  fs-canonical-path
  fs-read-text
  fs-write-text-atomic
  fs-make-dirs
  env-get
  tmux-send-keys])
(import doeff_agents.sessionhost.impls.channel [
  REPORT-RESULT-MCP-SERVER
  result-channel-spec])
(import doeff_agents.sessionhost.impls.markers [classify-output])


;; ---------------------------------------------------------------------------
;; argv 物理(oracle build_claude_argv — S13 で oracle green 済みの凍結配線)
;; ---------------------------------------------------------------------------

(deff build-claude-argv [params]
  {:pre [(: params dict)]
   :post [(: % list)]}
  "claude の起動 argv。凍結物理:
   - `--dangerously-skip-permissions` + `--settings {\"disableAllHooks\":true}`
     (49b3549b 傷跡: 無人 session が config-dir 所有者の Stop-hook 連鎖で
     turn を切られた実障害 — hooks は human-workflow、agent の契約は
     result channel)
   - effort → model の順(oracle 順序、無指定はフラグ自体を出さない)
   - caller mcp_servers(sse)+ result channel(stdio)を単一 --mcp-config に
     まとめ、非空なら --strict-mcp-config を付ける
   - prompt は決して argv に載せない(live terminal transport のみ)・
     print mode(-p / --print)不使用"
  (setv args ["claude"
              "--dangerously-skip-permissions"
              "--settings" "{\"disableAllHooks\":true}"])
  (setv effort (.get params "effort"))
  (when effort
    (.extend args ["--effort" effort]))
  (setv model (.get params "model"))
  (when model
    (.extend args ["--model" model]))
  (setv servers {})
  (for [[name url] (.items (.get params "mcp_servers" {}))]
    (setv (get servers name) {"type" "sse" "url" url}))
  (setv channel (.get params "result_channel"))
  (when channel
    (setv (get servers REPORT-RESULT-MCP-SERVER)
          {"type" "stdio"
           "command" (get channel "command")
           "args" (get channel "args")}))
  (when servers
    (.extend args ["--mcp-config"
                   (json.dumps {"mcpServers" servers} :separators #("," ":"))
                   "--strict-mcp-config"]))
  args)


;; ---------------------------------------------------------------------------
;; trust 物理(oracle trust_claude_workspace — S12)
;; ---------------------------------------------------------------------------

(defk resolve-claude-config-dir [params]
  {:pre [(: params dict)]
   :post [(: % tuple)]}
  "実効 CLAUDE_CONFIG_DIR の解決(R7: typed binding → process env →
   HOME/.claude — session_env は非 auth overlay で運搬手段ではない)。
   戻り値 #(config-dir warnings) — 明示無しは DOE-003 R3 の
   staged enforcement で warning のみ(codex と違い reject しない)。"
  (setv binding (.get params "binding"))
  (setv warnings [])
  (setv config-dir (when (is-not binding None) (.get binding "config_dir")))
  (when (is None config-dir)
    (.append warnings
             (+ "claude session launched without an explicit CLAUDE_CONFIG_DIR "
                "auth profile (ADR-DOE-AGENTS-003 R3: enforcement follows once "
                "callers migrate)"))
    (<- from-env (env-get "CLAUDE_CONFIG_DIR"))
    (setv config-dir from-env))
  (when (is None config-dir)
    (<- home (env-get "HOME"))
    (when (is None home)
      (raise (RuntimeError
               "cannot resolve CLAUDE_CONFIG_DIR: no session_env entry, no process env, no HOME")))
    (setv config-dir f"{home}/.claude"))
  #(config-dir warnings))


(defk preseed-claude-trust [config-dir work-dir]
  {:pre [(: config-dir str) (: work-dir str)]
   :post [(: % "None — trust state の書き込みのみ")]}
  "`<CLAUDE_CONFIG_DIR>/.claude.json` へ per-workspace trust を pre-seed する
   (42fb28fa 傷跡: fresh workspace の trust ダイアログ永久ハング)。
   claude は projects を cwd の REALPATH でキーする(S12: /tmp →
   /private/tmp)ため canonicalize が先。temp+rename で torn read を防ぐ。"
  (<- _ (fs-make-dirs config-dir))
  (<- trusted-dir (fs-canonical-path work-dir))
  (setv state-path f"{config-dir}/.claude.json")
  (<- raw (fs-read-text state-path))
  (setv state (if (is None raw) {} (json.loads raw)))
  (when (not (isinstance state dict))
    (raise (RuntimeError f"claude state file is not a JSON object: {state-path}")))
  (setv projects (.setdefault state "projects" {}))
  (setv project (.setdefault projects trusted-dir {}))
  (setv (get project "hasTrustDialogAccepted") True)
  (setv (get project "hasCompletedProjectOnboarding") True)
  (<- _ (fs-write-text-atomic state-path (json.dumps state) ".agentd-tmp"))
  None)


(defk claude-pre-launch [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "PreLaunchSetup の claude 実体: 実効 identity の解決(S14 の Hy positive 化 —
   launch program が session 行へ永続化する)+ trust pre-seed
   (skip_trust_setup で trust だけを飛ばす。oracle: gate は launch 側で常時、
   trust は skip 可能 — claude に hard gate は無い)。"
  (<- resolved (resolve-claude-config-dir params))
  (setv [config-dir warnings] resolved)
  (when (not (.get params "skip_trust_setup" False))
    (<- _ (preseed-claude-trust config-dir (get params "work_dir"))))
  {"CLAUDE_CONFIG_DIR" config-dir "warnings" warnings})


;; ---------------------------------------------------------------------------
;; per-kind defhandler(R2: 直接束縛と host 束縛の両方で同一モジュール)
;; ---------------------------------------------------------------------------

(defhandler claude-code-impl [result-command]
  (BuildLaunch [agent-type params]
    :when (= agent-type "claude")
    (resume (build-claude-argv params)))

  (PreLaunchSetup [agent-type params]
    :when (= agent-type "claude")
    (<- identity (claude-pre-launch params))
    (resume identity))

  (ClassifyPane [agent-type output]
    :when (= agent-type "claude")
    (resume (classify-output output)))

  (WireResultChannel [agent-type session-id socket-path]
    :when (= agent-type "claude")
    (resume (result-channel-spec result-command session-id socket-path)))

  (DeliverMessage [pane-id text]
    ;; live REPL への paste + submit。盲窓(paste→confirm ループ)の物理は
    ;; substrate の TmuxSendKeys(literal+submit)所有 — impl は転送のみ。
    (<- _ (tmux-send-keys pane-id text True True))
    (resume None)))
