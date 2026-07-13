;;; 直接束縛 deftest: resume / fork program と会話 identity 発見物理
;;; (ADR-DOE-AGENTS-006)。
;;;
;;; 検証する凍結物理:
;;;   - resume = 同一会話 ref の新 incarnation 行(generation + 1、系譜記録)。
;;;     terminal の蘇生元行は不変(conversation-outlives-incarnation)
;;;   - fork = 新会話の新行(generation 1、conversation は事後発見まで None)
;;;   - identity-unknown 行への resume / fork は typed 失敗(R1)
;;;   - one-live-incarnation: 同一会話の非終端 incarnation が居る resume は reject
;;;   - incarnation 命名(~g<N> / ~fork<N>)の衝突は前進で回避
;;;   - 未達成 result contract は resume が引き継ぎ、達成済みなら引き継がない
;;;   - auth binding は蘇生元行の effective_identity から再構成(行が auth の家)
;;;   - 発見物理(impls 所有): claude = projects dir の一意候補 /
;;;     codex = rollout 先頭行の cwd-match(新しい順)
;;;
;;; fake substrate は sessionhost_launch_deftests の LaunchWorld を再利用する。
;;; 生 IO ゼロ。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import json)

(import doeff_agents.sessionhost.effects [SessionRow])
(import doeff_agents.sessionhost.impls.claude_code [
  claude-code-impl
  claude-discover-conversation])
(import doeff_agents.sessionhost.impls.codex [
  codex-impl
  codex-discover-conversation])
(import doeff_agents.sessionhost.launch [
  RESULT-PROTOCOL-INSTRUCTION
  resume-session])
(import sessionhost_launch_deftests [LaunchWorld fake-launch-substrate])


;; ---------------------------------------------------------------------------
;; ヘルパ
;; ---------------------------------------------------------------------------

(defn seed-source [world #** overrides]
  "蘇生元の session 行を store に置く(既定: terminal な codex 行 +
   conversation 確定済み + 実効 identity 永続済み)。"
  (setv fields {"session_id" "s1"
                "session_name" "doeff-s1"
                "pane_id" "%1"
                "agent_type" "codex"
                "lifecycle" "run_to_completion"
                "status" "done"
                "started_at" "2026-07-05T00:00:00+00:00"
                "work_dir" "/work/dir"
                "effective_identity" {"CODEX_HOME" "/x/codex"}
                "conversation" {"session_id" "conv-1"}
                "generation" 1})
  (.update fields overrides)
  (setv row (SessionRow #** fields))
  (setv (get world.rows row.session-id) row)
  row)


(defn resume-params [#** overrides]
  (setv params {"session_id" "s1"
                "mode" "resume"
                "prompt" "continue please"
                "model" None
                "effort" None
                "mcp_servers" {}
                "socket_path" "/tmp/agentd.sock"
                "max_running" None
                "repl_idle_max_wait_seconds" None
                "backend_kind" "tmux"})
  (.update params overrides)
  params)


(defk run-resume [world params]
  {:pre [(: world LaunchWorld) (: params dict)]
   :post [(: % "SessionRow(成功時)")]}
  (<- row ((fake-launch-substrate world)
           ((codex-impl "/opt/doeff-sessionhost")
            ((claude-code-impl "/opt/doeff-sessionhost")
             (resume-session params)))))
  row)


;; ---------------------------------------------------------------------------
;; golden path
;; ---------------------------------------------------------------------------

(deftest test-resume-codex-golden-path
  (setv world (LaunchWorld))
  (seed-source world)
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params)))
  ;; 新 incarnation 行: 同一会話・generation+1・系譜記録
  (assert (= row.session-id "s1~g2"))
  (assert (= row.generation 2))
  (assert (= (get row.conversation "session_id") "conv-1"))
  (assert (= row.resumed-from-session-id "s1"))
  (assert (is row.forked-from-session-id None))
  ;; argv: fresh launch と同じ基礎配線 + `resume <conv-id>`
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (.startswith cmd "codex --yolo"))
  (assert (.endswith cmd "resume conv-1"))
  ;; auth は行の effective_identity から再構成され tmux env に届く
  (assert (= (get (get world.tmux-envs "doeff-s1~g2") "CODEX_HOME") "/x/codex"))
  ;; prompt は live REPL 配送(impl の DeliverMessage → TmuxSendKeys)
  (setv [ppane ptext pliteral psubmit] (get world.sent-keys 1))
  (assert (= ptext "continue please"))
  (assert pliteral)
  (assert psubmit)
  ;; events: 新行に session_started + session_resumed
  (assert (in #("s1~g2" "session_started") world.events))
  (assert (in #("s1~g2" "session_resumed") world.events))
  ;; 蘇生元行は不変(terminal のまま — 行は蘇らず会話が乗り換える)
  (assert (= (. (get world.rows "s1") status) "done"))
  (assert (= (. (get world.rows "s1") generation) 1)))


(deftest test-fork-claude-parent-alive
  ;; fork は親の生死に依存しない(R4)— 親 running のまま新会話の行を作る。
  (setv world (LaunchWorld))
  (seed-source world :agent_type "claude" :status "running"
               :effective_identity {"CLAUDE_CONFIG_DIR" "/x/claude"}
               :conversation {"session_id" "conv-A"})
  (.add world.tmux-sessions "doeff-s1")
  (setv world.capture-script ["❯"])
  (<- row (run-resume world (resume-params :mode "fork"
                                           :prompt "explore alternative")))
  (assert (= row.session-id "s1~fork1"))
  (assert (= row.generation 1))
  ;; 新会話 identity は claude 側が鋳造 → 事後発見まで identity-unknown
  (assert (is row.conversation None))
  (assert (= row.forked-from-session-id "s1"))
  (assert (is row.resumed-from-session-id None))
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (in "--resume conv-A" cmd))
  (assert (in "--fork-session" cmd))
  ;; fork argv に --session-id は載らない(鋳造は CLI 側)
  (assert (not-in "--session-id" cmd))
  (assert (in #("s1~fork1" "session_forked") world.events))
  ;; 親行は不変
  (assert (= (. (get world.rows "s1") status) "running")))


;; ---------------------------------------------------------------------------
;; admission(typed reject — tmux 効果ゼロ)
;; ---------------------------------------------------------------------------

(deftest test-resume-rejects-identity-unknown
  (setv world (LaunchWorld))
  (seed-source world :conversation None)
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "identity-unknown" raised))
  (assert (not world.tmux-sessions)))


(deftest test-fork-rejects-identity-unknown
  (setv world (LaunchWorld))
  (seed-source world :conversation None)
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params :mode "fork")))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "identity-unknown" raised)))


(deftest test-resume-rejects-live-incarnation
  ;; 同一会話の非終端 incarnation(ここでは蘇生元自身が running)は reject。
  (setv world (LaunchWorld))
  (seed-source world :status "running")
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "one-live-incarnation" raised))
  (assert (not world.tmux-sessions)))


(deftest test-resume-rejects-unsupported-kind
  (setv world (LaunchWorld))
  (seed-source world :agent_type "gemini")
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "does not support" raised)))


(deftest test-resume-rejects-unknown-session
  (setv world (LaunchWorld))
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params :session_id "nope")))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "not registered" raised)))


;; ---------------------------------------------------------------------------
;; 命名と世代(衝突は前進で回避)
;; ---------------------------------------------------------------------------

(deftest test-resume-collision-advances-generation
  ;; 過去の incarnation 行(~g2)が既在 → 新行は ~g3 / generation 3。
  (setv world (LaunchWorld))
  (seed-source world)
  (seed-source world :session_id "s1~g2" :session_name "doeff-s1~g2"
               :generation 2)
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params)))
  (assert (= row.session-id "s1~g3"))
  (assert (= row.generation 3)))


(deftest test-resume-from-old-incarnation-strips-suffix
  ;; ~g2 の行から resume しても基底名は s1(~g2~g3 にならない)。
  (setv world (LaunchWorld))
  (seed-source world :session_id "s1~g2" :session_name "doeff-s1~g2"
               :generation 2)
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params :session_id "s1~g2")))
  (assert (= row.session-id "s1~g3"))
  (assert (= row.generation 3)))


(deftest test-fork-collision-advances-counter
  (setv world (LaunchWorld))
  (seed-source world)
  (seed-source world :session_id "s1~fork1" :session_name "doeff-s1~fork1")
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params :mode "fork")))
  (assert (= row.session-id "s1~fork2"))
  (assert (= row.generation 1)))


;; ---------------------------------------------------------------------------
;; result contract の引き継ぎ
;; ---------------------------------------------------------------------------

(deftest test-resume-carries-unfulfilled-contract
  (setv world (LaunchWorld))
  (seed-source world :expected_result {"type" "object"})
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params)))
  (assert (= row.expected-result {"type" "object"}))
  ;; contract 持ちの prompt には result-protocol instruction が追記される
  (setv [ppane ptext pliteral psubmit] (get world.sent-keys 1))
  (assert (.endswith ptext RESULT-PROTOCOL-INSTRUCTION))
  ;; result channel が argv に配線される
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (in "doeff_result" cmd)))


(deftest test-resume-drops-fulfilled-contract
  (setv world (LaunchWorld))
  (seed-source world :expected_result {"type" "object"}
               :result_payload "{\"ok\":true}")
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params)))
  (assert (is row.expected-result None)))


(deftest test-fork-does-not-carry-contract
  (setv world (LaunchWorld))
  (seed-source world :expected_result {"type" "object"})
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params :mode "fork")))
  (assert (is row.expected-result None)))


;; ---------------------------------------------------------------------------
;; 発見物理(impls 所有 — 実 impl を fake Fs 台本で検査)
;; ---------------------------------------------------------------------------

(deftest test-claude-discovery-exactly-one-candidate
  ;; projects dir(canonicalize 済み cwd の mangle)の除外後候補が
  ;; ちょうど 1 つ → 捕獲。
  (setv world (LaunchWorld))
  (setv (get world.listings "/x/claude/projects/-work-dir")
        ["conv-new.jsonl" "conv-old.jsonl" "notes.txt"])
  (<- found ((fake-launch-substrate world)
             (claude-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" {"CLAUDE_CONFIG_DIR" "/x/claude"}
                "exclude_session_ids" ["conv-old"]})))
  (assert (= found {"session_id" "conv-new"})))


(deftest test-claude-discovery-ambiguous-defers
  ;; 除外後候補が複数(同 cwd の他 session の可能性)→ None で次 cycle へ。
  (setv world (LaunchWorld))
  (setv (get world.listings "/x/claude/projects/-work-dir")
        ["conv-a.jsonl" "conv-b.jsonl"])
  (<- found ((fake-launch-substrate world)
             (claude-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" {"CLAUDE_CONFIG_DIR" "/x/claude"}
                "exclude_session_ids" []})))
  (assert (is found None)))


(deftest test-claude-discovery-no-identity-defers
  (setv world (LaunchWorld))
  (<- found ((fake-launch-substrate world)
             (claude-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" None
                "exclude_session_ids" []})))
  (assert (is found None)))


(deftest test-codex-discovery-newest-cwd-match
  ;; rollout の新しい順に先頭行を読み、cwd 一致 + 未知会話の最初の候補を返す。
  (setv world (LaunchWorld))
  (setv root "/x/codex/sessions")
  (setv (get world.listings root) ["2026"])
  (setv (get world.listings f"{root}/2026") ["07"])
  (setv (get world.listings f"{root}/2026/07") ["04" "05"])
  (setv (get world.listings f"{root}/2026/07/05")
        ["rollout-b-new.jsonl" "rollout-a-old.jsonl"])
  (setv (get world.listings f"{root}/2026/07/04")
        ["rollout-z-ancient.jsonl"])
  (setv (get world.fs f"{root}/2026/07/05/rollout-b-new.jsonl")
        (+ (json.dumps {"type" "session_meta"
                        "payload" {"id" "conv-new" "cwd" "/work/dir"}})
           "\n{\"type\":\"turn\"}\n"))
  (setv (get world.fs f"{root}/2026/07/05/rollout-a-old.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-old" "cwd" "/work/dir"}}) "\n"))
  (setv (get world.fs f"{root}/2026/07/04/rollout-z-ancient.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-z" "cwd" "/work/dir"}}) "\n"))
  (<- found ((fake-launch-substrate world)
             (codex-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" {"CODEX_HOME" "/x/codex"}
                "exclude_session_ids" ["conv-old"]})))
  (assert (= found {"session_id" "conv-new"
                    "rollout_path" f"{root}/2026/07/05/rollout-b-new.jsonl"})))


(deftest test-codex-discovery-skips-excluded-and-wrong-cwd
  ;; 最新は cwd 不一致、その次は既知会話 → さらに古い一致候補へ落ちる。
  (setv world (LaunchWorld))
  (setv root "/x/codex/sessions")
  (setv (get world.listings root) ["2026"])
  (setv (get world.listings f"{root}/2026") ["07"])
  (setv (get world.listings f"{root}/2026/07") ["05"])
  (setv (get world.listings f"{root}/2026/07/05")
        ["rollout-c-latest.jsonl" "rollout-b-known.jsonl" "rollout-a-match.jsonl"])
  (setv (get world.fs f"{root}/2026/07/05/rollout-c-latest.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-c" "cwd" "/other"}}) "\n"))
  (setv (get world.fs f"{root}/2026/07/05/rollout-b-known.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-known" "cwd" "/work/dir"}}) "\n"))
  (setv (get world.fs f"{root}/2026/07/05/rollout-a-match.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-a" "cwd" "/work/dir"}}) "\n"))
  (<- found ((fake-launch-substrate world)
             (codex-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" {"CODEX_HOME" "/x/codex"}
                "exclude_session_ids" ["conv-known"]})))
  (assert (= found {"session_id" "conv-a"
                    "rollout_path" f"{root}/2026/07/05/rollout-a-match.jsonl"})))


(deftest test-codex-discovery-canonical-cwd-match
  ;; rollout の cwd が canonicalize 済み(/tmp → /private/tmp 型)でも一致する。
  (setv world (LaunchWorld))
  (setv (get world.canonical "/work/dir") "/private/work/dir")
  (setv root "/x/codex/sessions")
  (setv (get world.listings root) ["2026"])
  (setv (get world.listings f"{root}/2026") ["07"])
  (setv (get world.listings f"{root}/2026/07") ["05"])
  (setv (get world.listings f"{root}/2026/07/05") ["rollout-a-x.jsonl"])
  (setv (get world.fs f"{root}/2026/07/05/rollout-a-x.jsonl")
        (+ (json.dumps {"payload" {"id" "conv-x" "cwd" "/private/work/dir"}}) "\n"))
  (<- found ((fake-launch-substrate world)
             (codex-discover-conversation
               {"work_dir" "/work/dir"
                "effective_identity" {"CODEX_HOME" "/x/codex"}
                "exclude_session_ids" []})))
  (assert (= found {"session_id" "conv-x"
                    "rollout_path" f"{root}/2026/07/05/rollout-a-x.jsonl"})))


;; ---------------------------------------------------------------------------
;; launch 意図の復元(行が意図の家)
;; ---------------------------------------------------------------------------

(deftest test-resume-restores-launch-overlay
  ;; model / effort / session_env は蘇生元行の launch_overlay から復元され、
  ;; 呼び手 params が per-key で上書きする。
  (setv world (LaunchWorld))
  (seed-source world :launch_overlay {"session_env" {"FOO" "bar"}
                                      "model" "gpt-5.4"
                                      "effort" "high"
                                      "mcp_servers" {}})
  (setv world.capture-script ["› "])
  (<- row (run-resume world (resume-params :effort "low")))
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  ;; model は overlay から、effort は呼び手上書き
  (assert (in "--model gpt-5.4" cmd))
  (assert (in "model_reasoning_effort=\"low\"" cmd))
  ;; overlay の session_env が tmux env に届く
  (assert (= (get (get world.tmux-envs "doeff-s1~g2") "FOO") "bar"))
  ;; 新行の launch_overlay も実効値で永続(resume-of-resume の復元源)
  (assert (= (get row.launch-overlay "model") "gpt-5.4"))
  (assert (= (get row.launch-overlay "effort") "low"))
  (assert (= (get (get row.launch-overlay "session_env") "FOO") "bar")))
