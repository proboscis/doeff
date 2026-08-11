;;; claude-code per-kind defhandler(ADR-DOE-AGENTS-004 R2、C2)。
;;;
;;; protocol 物理の単一の家(protocol-physics-has-one-home): oracle =
;;; agentd-rust-final:src/main.rs build_claude_argv / trust_claude_workspace。
;;; conformance 凍結: S12(trust pre-seed: canonicalized work_dir key・
;;; temp+rename)/ S13(--settings disableAllHooks + --mcp-config stdio +
;;; --strict-mcp-config・prompt は argv に載らない)/ DOE-003 R3
;;; (CLAUDE_CONFIG_DIR 無しは warning のみの staged enforcement)。
;;;
;;; substrate-clean 領域: 生 IO 禁止(defsemgrep 執行)。FS / env は
;;; Fs* / EnvGet substrate effect の yield のみ。

(require doeff-hy.macros [defk deff <- defhandler])

(import json)
(import re)
(import uuid)

(import doeff_agents.sessionhost.effects [
  BuildLaunch
  BuildResume
  DiscoverConversation
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
  TransplantConversation
  WireResultChannel
  fs-canonical-path
  fs-link-artifact
  fs-list-dir
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
  ;; ADR-006 R1: launch 時に鋳造した会話 identity を --session-id で注入する
  ;; (boot 前に identity が stored fact になる)。resume / fork の argv は
  ;; build-claude-resume-argv の所有 — ここは fresh launch のみ。
  (setv conversation (.get params "conversation"))
  (when (and (isinstance conversation dict)
             (is (.get params "resume_mode") None))
    (setv conv-id (.get conversation "session_id"))
    (when conv-id
      (.extend args ["--session-id" conv-id])))
  args)


(deff build-claude-resume-argv [params]
  {:pre [(: params dict)
         (in (.get params "resume_mode") #{"resume" "fork"})
         (: (.get params "conversation") dict)]
   :post [(: % list)]}
  "claude の resume / fork argv(ADR-DOE-AGENTS-006 R3)。凍結物理:
   - fresh launch と同じ基礎フラグ群(--dangerously-skip-permissions /
     --settings / effort / model / mcp-config)を共有し、並行実装を作らない
   - resume: `--resume <conversation session_id>`
   - fork: さらに `--fork-session`(claude が新 session ID を鋳造 — 新会話
     identity は事後発見: DiscoverConversation)
   - prompt は argv に載せない(BuildLaunch と同一の live-terminal 物理)"
  (setv base-params (dict params))
  (.pop base-params "conversation" None)
  (setv args (build-claude-argv base-params))
  (setv conv-id (get (get params "conversation") "session_id"))
  (.extend args ["--resume" conv-id])
  (when (= (get params "resume_mode") "fork")
    (.append args "--fork-session"))
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
   trust は skip 可能 — claude に hard gate は無い)+ ADR-006 R1 の会話
   identity 鋳造(fresh launch では launch program がこの UUID を
   --session-id 注入と row.conversation の両方に使う — boot 前に identity が
   stored fact になる。resume / fork では捨てられる)。"
  (<- resolved (resolve-claude-config-dir params))
  (setv [config-dir warnings] resolved)
  (when (not (.get params "skip_trust_setup" False))
    (<- _ (preseed-claude-trust config-dir (get params "work_dir"))))
  {"CLAUDE_CONFIG_DIR" config-dir
   "warnings" warnings
   "conversation" {"session_id" (str (uuid.uuid4))}})


;; ---------------------------------------------------------------------------
;; 会話 identity の事後発見(ADR-006 R1 — claude では fork の新 ID 用)
;; ---------------------------------------------------------------------------

(defk claude-discover-conversation [params]
  {:pre [(: params dict)]
   :post [(: % (| dict None))]}
  "claude の会話 identity 発見。物理: transcripts は
   `<CLAUDE_CONFIG_DIR>/projects/<mangled canonical work_dir>/<uuid>.jsonl`
   (mangle = 非英数字を '-' に置換。project key は S12 と同じく canonicalize
   済み cwd)。exclude_session_ids(既知の全会話 + fork 親)を除いた候補が
   ちょうど 1 つのときだけ捕獲する — 複数は曖昧(同 cwd の他 session の
   可能性)なので None を返し、次 cycle に委ねる(level-triggered)。"
  (setv identity (or (.get params "effective_identity") {}))
  (setv config-dir (.get identity "CLAUDE_CONFIG_DIR"))
  (setv excludes (set (or (.get params "exclude_session_ids") [])))
  (if (is config-dir None)
      None
      (do
        (<- canon (fs-canonical-path (get params "work_dir")))
        (setv mangled (re.sub "[^A-Za-z0-9]" "-" canon))
        (setv project-dir f"{config-dir}/projects/{mangled}")
        (<- entries (fs-list-dir project-dir))
        (setv candidates
              (sorted (lfor name entries
                            :if (and (.endswith name ".jsonl")
                                     (not-in (cut name 0 -6) excludes))
                            (cut name 0 -6))))
        (if (= (len candidates) 1)
            {"session_id" (get candidates 0)}
            None))))


;; ---------------------------------------------------------------------------
;; cross-binding transplant(ADR-006 改訂 R7 — 別 auth home への会話の持ち出し)
;; ---------------------------------------------------------------------------

(defk claude-transplant-conversation [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "cross-binding resume の transcript transplant(ADR-DOE-AGENTS-006 改訂
   R7)。物理 = dotfiles agentcli share.py の 4 対と同型: transcript(必須 —
   不在は typed 値で返し、resume-session が transcript_not_discoverable の
   reject にする)+ sessions-index.json / session-env/<sid> /
   file-history/<sid>(best-effort: あれば張る・無ければ skip)。所有
   profile は source 行の effective_identity で既知なので registry 走査は
   持ち込まない。同一 home(binding config_dir = source の
   CLAUDE_CONFIG_DIR)は no-op。transcript の家は projects/<mangled
   canonical work_dir>/(resume-physics.md 2026-08-11 プローブ (b):
   symlink 越しの --resume が文脈を保ち、追記は解決先の実体へ届く)。"
  (setv binding (get params "binding"))
  (setv target-dir (get binding "config_dir"))
  (setv identity (or (.get params "source_identity") {}))
  (setv source-dir (.get identity "CLAUDE_CONFIG_DIR"))
  (setv conv-id (get (get params "conversation") "session_id"))
  (when (is source-dir None)
    (return {"ok" False
             "code" RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
             "message" (+ "session.resume: the source incarnation of "
                          f"conversation '{conv-id}' has no recorded "
                          "CLAUDE_CONFIG_DIR — its transcript cannot be "
                          "located for a cross-binding transplant "
                          "(transcript-not-discoverable)")}))
  (when (= source-dir target-dir)
    (return {"ok" True "action" "same-home"}))
  (<- canon (fs-canonical-path (get params "work_dir")))
  (setv mangled (re.sub "[^A-Za-z0-9]" "-" canon))
  (setv source-project f"{source-dir}/projects/{mangled}")
  (setv target-project f"{target-dir}/projects/{mangled}")
  (<- outcome (fs-link-artifact f"{source-project}/{conv-id}.jsonl"
                                f"{target-project}/{conv-id}.jsonl"))
  (when (= outcome "source-missing")
    (return {"ok" False
             "code" RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
             "message" (+ f"session.resume: transcript "
                          f"'{source-project}/{conv-id}.jsonl' does not exist "
                          "(transcript-not-discoverable) — a cross-binding "
                          "transplant requires the source transcript "
                          "(resume-physics.md probe (a): the real CLI fails "
                          "loud without it)")}))
  ;; 周辺 artifact は best-effort(share.py の残り 3 対と同型)。
  (<- _ (fs-link-artifact f"{source-project}/sessions-index.json"
                          f"{target-project}/sessions-index.json"))
  (<- _ (fs-link-artifact f"{source-dir}/session-env/{conv-id}"
                          f"{target-dir}/session-env/{conv-id}"))
  (<- _ (fs-link-artifact f"{source-dir}/file-history/{conv-id}"
                          f"{target-dir}/file-history/{conv-id}"))
  {"ok" True "action" outcome})


;; ---------------------------------------------------------------------------
;; per-kind defhandler(R2: 直接束縛と host 束縛の両方で同一モジュール)
;; ---------------------------------------------------------------------------

(defhandler claude-code-impl [result-command]
  (BuildLaunch [agent-type params]
    :when (= agent-type "claude")
    (resume (build-claude-argv params)))

  (BuildResume [agent-type params]
    :when (= agent-type "claude")
    (resume (build-claude-resume-argv params)))

  (DiscoverConversation [agent-type params]
    :when (= agent-type "claude")
    (<- found (claude-discover-conversation params))
    (resume found))

  (TransplantConversation [agent-type params]
    :when (= agent-type "claude")
    (<- transplanted (claude-transplant-conversation params))
    (resume transplanted))

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
