;;; 直接束縛 deftest: session.resume の cross-binding 拡張
;;; (ADR-DOE-AGENTS-006 改訂 — ACP 枠切れ failover の受け口)。
;;;
;;; 検証する凍結物理:
;;;   - binding param は蘇生元行の effective_identity 再構成を上書きし、
;;;     home が異なるときだけ transcript transplant(symlink 敷設)が発火する
;;;   - claude transplant = agentcli share.py の 4 対と同型(transcript 必須・
;;;     周辺 artifact は best-effort)/ codex transplant = rollout の
;;;     sessions 相対 path 保存 link(resume-physics.md 2026-08-11 プローブ)
;;;   - source transcript 不在は typed reject(error_code
;;;     transcript_not_discoverable・row 不生成・tmux 不接触)
;;;   - new_session_id は新 incarnation の id/name を呼び手が指定する
;;;     (未指定は従来の ~g<N> 鋳造。重複は launch と同語彙で reject)
;;;   - expected_result は明示指定(null 含む)が unfulfilled carry より優先
;;;   - resume 経路でも session_env の binding 所有キーは reject
;;;     (overlay-env-offenders の回帰)
;;;   - admission reject 群は typed error_code を例外に載せる(host が wire の
;;;     error_code へ写す): one_live_incarnation / identity_unknown /
;;;     kind_not_supported / transcript_not_discoverable
;;;
;;; fake substrate は sessionhost_launch_deftests の LaunchWorld を再利用。
;;; 生 IO ゼロ。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import sessionhost_launch_deftests [LaunchWorld])
(import sessionhost_resume_deftests [seed-source resume-params run-resume])


;; ---------------------------------------------------------------------------
;; ヘルパ
;; ---------------------------------------------------------------------------

(deff claude-seed-kwargs []
  {:pre [True]
   :post [(: % dict)]}
  {"agent_type" "claude"
   "effective_identity" {"CLAUDE_CONFIG_DIR" "/x/claude-A"}
   "conversation" {"session_id" "conv-1"}})

(setv CLAUDE-SOURCE-TRANSCRIPT "/x/claude-A/projects/-work-dir/conv-1.jsonl")
(setv CLAUDE-TARGET-TRANSCRIPT "/x/claude-B/projects/-work-dir/conv-1.jsonl")

(setv CODEX-ROLLOUT "/x/codex/sessions/2026/07/05/rollout-t1-conv-1.jsonl")
(setv CODEX-TARGET-ROLLOUT "/y/codex/sessions/2026/07/05/rollout-t1-conv-1.jsonl")


;; ---------------------------------------------------------------------------
;; binding 上書き + transplant(claude)
;; ---------------------------------------------------------------------------

(deftest test-resume-binding-override-claude-transplants
  ;; golden path: 別 config_dir への resume は transcript を symlink transplant
  ;; し、新 binding が identity 再構成を上書きして env / 記帳へ届く。
  (setv world (LaunchWorld))
  (seed-source world #** (claude-seed-kwargs))
  (setv (get world.fs CLAUDE-SOURCE-TRANSCRIPT) "{\"type\":\"meta\"}\n")
  ;; 周辺 artifact: sessions-index.json は実在(→ link される)、
  ;; session-env / file-history は不在(→ best-effort skip)
  (setv (get world.fs "/x/claude-A/projects/-work-dir/sessions-index.json") "{}")
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-resume world (resume-params
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude-B"})))
  ;; 新 incarnation: 会話同一・generation+1・系譜
  (assert (= row.session-id "s1~g2"))
  (assert (= row.generation 2))
  (assert (= (get row.conversation "session_id") "conv-1"))
  (assert (= row.resumed-from-session-id "s1"))
  ;; transplant: transcript + sessions-index が敷設され、不在 artifact は skip
  (assert (= (get world.links CLAUDE-TARGET-TRANSCRIPT)
             CLAUDE-SOURCE-TRANSCRIPT))
  (assert (= (get world.links "/x/claude-B/projects/-work-dir/sessions-index.json")
             "/x/claude-A/projects/-work-dir/sessions-index.json"))
  (assert (not-in "/x/claude-B/session-env/conv-1" world.links))
  (assert (not-in "/x/claude-B/file-history/conv-1" world.links))
  ;; binding が identity を上書き: tmux env と新行の記帳が新 home
  (assert (= (get (get world.tmux-envs "doeff-s1~g2") "CLAUDE_CONFIG_DIR")
             "/x/claude-B"))
  (assert (= (get row.effective-identity "CLAUDE_CONFIG_DIR") "/x/claude-B"))
  ;; argv は同一会話の resume
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (in "--resume conv-1" cmd)))


(deftest test-resume-binding-same-home-verifies-without-transplant
  ;; 同一 home への resume は symlink 敷設(transplant)はしないが、transcript の
  ;; 実在検査は cross-home と同じく発注時に走る(ADR-DOE-AGENTS-006 R10 —
  ;; 2026-08-18 契約改訂。旧 pin『FS 検査自体が走らない』は、起動段で死んだ行
  ;; 〔transcript 未実体化〕の resume を素通しして実 CLI の
  ;; 『No conversation found』120s 死に落としていた)。
  (setv world (LaunchWorld))
  (seed-source world #** (claude-seed-kwargs))
  (setv (get world.fs CLAUDE-SOURCE-TRANSCRIPT) "{\"type\":\"meta\"}\n")
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-resume world (resume-params
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude-A"})))
  (assert (= row.session-id "s1~g2"))
  ;; 敷設はゼロ(同一 home に link は要らない)
  (assert (= (len world.links) 0))
  (assert (= (get (get world.tmux-envs "doeff-s1~g2") "CLAUDE_CONFIG_DIR")
             "/x/claude-A")))


(deftest test-resume-binding-same-home-missing-transcript-rejects
  ;; 同一 home でも transcript 不在は typed reject(R10)— cross-home の
  ;; transcript_not_discoverable と同じ語彙・同じ前倒し。
  (setv world (LaunchWorld))
  (seed-source world #** (claude-seed-kwargs))
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude-A"})))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "transcript_not_discoverable"))
  (assert (= (sorted (.keys world.rows)) ["s1"]))
  (assert (not world.tmux-sessions)))


(deftest test-resume-cross-binding-missing-transcript-rejects
  ;; source transcript 不在の cross-binding resume は typed reject:
  ;; error_code transcript_not_discoverable・row 不生成・tmux 不接触。
  (setv world (LaunchWorld))
  (seed-source world #** (claude-seed-kwargs))
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params
                              :binding {"kind" "claude-code"
                                        "config_dir" "/x/claude-B"})))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "transcript_not_discoverable"))
  (assert (in "transcript" (str raised)))
  ;; row 不生成(source 行のみ)・tmux 不接触
  (assert (= (sorted (.keys world.rows)) ["s1"]))
  (assert (not world.tmux-sessions)))


(deftest test-resume-binding-admission-shared-with-launch
  ;; binding の admission は launch と同一実装(policy binding-admission-error)
  ;; を共有する — kind↔agent_type 不整合は launch と同語彙で reject し、
  ;; transplant にも tmux にも到達しない。
  (setv world (LaunchWorld))
  (seed-source world #** (claude-seed-kwargs))
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params
                              :binding {"kind" "codex" "codex_home" "/y"})))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "drives agent_type" raised))
  (assert (= (len world.links) 0))
  (assert (not world.tmux-sessions)))


;; ---------------------------------------------------------------------------
;; binding 上書き + transplant(codex — rollout の sessions 相対 path 保存)
;; ---------------------------------------------------------------------------

(deftest test-resume-binding-override-codex-transplants
  (setv world (LaunchWorld))
  (seed-source world :conversation {"session_id" "conv-1"
                                    "rollout_path" CODEX-ROLLOUT})
  (setv (get world.fs CODEX-ROLLOUT)
        "{\"type\":\"session_meta\",\"payload\":{\"id\":\"conv-1\"}}\n")
  (setv world.capture-script ["› {composer}"])
  (<- row (run-resume world (resume-params
                              :binding {"kind" "codex"
                                        "codex_home" "/y/codex"})))
  (assert (= row.session-id "s1~g2"))
  ;; rollout が target home の sessions 配下へ同相対 path で敷設される
  (assert (= (get world.links CODEX-TARGET-ROLLOUT) CODEX-ROLLOUT))
  ;; binding が identity を上書き
  (assert (= (get (get world.tmux-envs "doeff-s1~g2") "CODEX_HOME") "/y/codex"))
  (assert (= (get row.effective-identity "CODEX_HOME") "/y/codex"))
  (setv [pane cmd literal submit] (get world.sent-keys 0))
  (assert (.endswith cmd "resume conv-1")))


(deftest test-resume-codex-cross-binding-missing-rollout-rejects
  (setv world (LaunchWorld))
  (seed-source world :conversation {"session_id" "conv-1"
                                    "rollout_path" CODEX-ROLLOUT})
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params
                              :binding {"kind" "codex"
                                        "codex_home" "/y/codex"})))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "transcript_not_discoverable"))
  (assert (= (sorted (.keys world.rows)) ["s1"]))
  (assert (not world.tmux-sessions)))


;; ---------------------------------------------------------------------------
;; new_session_id(呼び手鋳造 — ACP は agent_<invocationId> 規約で指定する)
;; ---------------------------------------------------------------------------

(deftest test-resume-new-session-id-golden
  (setv world (LaunchWorld))
  (seed-source world)
  (setv world.capture-script ["› {composer}"])
  (<- row (run-resume world (resume-params :new_session_id "agent-inv42")))
  ;; 指定 id で row が立ち、session_name も同値。lineage は
  ;; resumed_from_session_id が真実(命名からの導出はしない)。
  (assert (= row.session-id "agent-inv42"))
  (assert (= row.session-name "agent-inv42"))
  (assert (= row.generation 2))
  (assert (= row.resumed-from-session-id "s1"))
  (assert (= (get row.conversation "session_id") "conv-1"))
  (assert (in #("agent-inv42" "session_started") world.events))
  (assert (in #("agent-inv42" "session_resumed") world.events)))


(deftest test-resume-new-session-id-duplicate-rejects
  ;; 既存 id との重複は launch と同語彙("already registered")で reject。
  (setv world (LaunchWorld))
  (seed-source world)
  (seed-source world :session_id "agent-inv42" :session_name "agent-inv42"
               :conversation {"session_id" "conv-other"})
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params :new_session_id "agent-inv42")))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "already registered" raised))
  (assert (not world.tmux-sessions)))


;; ---------------------------------------------------------------------------
;; expected_result: 明示指定 > unfulfilled carry
;; ---------------------------------------------------------------------------

(deftest test-resume-expected-result-explicit-overrides-carry
  (setv world (LaunchWorld))
  (seed-source world :expected_result {"payload_schema" {"type" "object"}})
  (setv world.capture-script ["› {composer}"])
  (<- row (run-resume world (resume-params
                              :expected_result {"payload_schema" {"type" "string"}}
                              :expected_result_specified True)))
  (assert (= row.expected-result {"payload_schema" {"type" "string"}})))


(deftest test-resume-expected-result-explicit-null-drops-carry
  ;; 明示 null(= key 実在で値 None)は「契約なし」の指定 — carry を落とす。
  (setv world (LaunchWorld))
  (seed-source world :expected_result {"payload_schema" {"type" "object"}})
  (setv world.capture-script ["› {composer}"])
  (<- row (run-resume world (resume-params
                              :expected_result None
                              :expected_result_specified True)))
  (assert (is row.expected-result None)))


;; ---------------------------------------------------------------------------
;; overlay-env-offenders の回帰(resume 経路)
;; ---------------------------------------------------------------------------

(deftest test-resume-rejects-binding-owned-env-overlay
  ;; session_env に binding 所有キーを載せる攻撃は resume 経路でも reject
  ;; される(launch-session の R7 admission を共有 — 既存挙動の回帰 pin)。
  (setv world (LaunchWorld))
  (seed-source world)
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params
                              :session_env {"CLAUDE_CONFIG_DIR" "/evil"})))
    (except [e RuntimeError]
      (setv raised (str e))))
  (assert (is-not raised None))
  (assert (in "non-auth overlay" raised))
  (assert (not world.tmux-sessions)))


;; ---------------------------------------------------------------------------
;; typed error_code(既存 reject 群の機械可読化)
;; ---------------------------------------------------------------------------

(deftest test-resume-identity-unknown-carries-code
  (setv world (LaunchWorld))
  (seed-source world :conversation None)
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "identity_unknown"))
  ;; message は後方互換で不変
  (assert (in "identity-unknown" (str raised))))


(deftest test-resume-one-live-incarnation-carries-code
  (setv world (LaunchWorld))
  (seed-source world :status "running")
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "one_live_incarnation"))
  (assert (in "one-live-incarnation" (str raised))))


(deftest test-resume-kind-not-supported-carries-code
  (setv world (LaunchWorld))
  (seed-source world :agent_type "gemini")
  (setv raised None)
  (try
    (<- _ (run-resume world (resume-params)))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (hasattr raised "code"))
  (assert (= raised.code "kind_not_supported"))
  (assert (in "does not support" (str raised))))
