;;; 実 substrate handler(ADR-DOE-AGENTS-004 C2)— 生 IO の唯一の家。
;;;
;;; impls/(per-kind)と policy / launch(共有 program)は substrate effect を
;;; yield するのみで、実世界(tmux / FS / clock / 子 process)に触るのはこの
;;; モジュールだけ。oracle: packages/doeff-agentd/src/main.rs の
;;; tmux_* / run_judge_command / fs 物理を verbatim 移植。
;;;
;;; SessionStore の実体(SQLite 単一 writer actor)は host の外部性で C3 所有 —
;;; ここには直接束縛用の in-memory store のみ置く(呼び手 process 内で
;;; policy / launch を回すための最小の真実置き場)。

(require doeff-hy.macros [deff defhandler])

(import dataclasses [replace])
(import datetime [datetime timezone])
(import json)
(import os)
(import subprocess)
(import time)

(import doeff_agents.sessionhost.effects [
  ProcResult
  SessionRow
  SessionStoreListActive
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  TmuxNewSession
  TmuxHasSession
  TmuxPaneCurrentCommand
  TmuxCapture
  TmuxSendKeys
  TmuxKillSession
  ClockNow
  ClockSleep
  ProcRun
  FsCanonicalPath
  FsReadText
  FsWriteTextAtomic
  FsMakeDirs
  EnvGet])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs)
;; ---------------------------------------------------------------------------

;; agent process へ決して渡さない env(oracle FORBIDDEN_AGENT_ENV_KEYS —
;; API-key 呼び出しは memoized LLM handler 経由のみ、agent session env は禁止)。
(setv FORBIDDEN-AGENT-ENV-KEYS
      #{"ANTHROPIC_API_KEY" "ANTHROPIC_API_KEY_PERSONAL" "ANTHROPIC_API_KEY__PERSONAL"})

;; 新 pane の shell に足す prompt 抑制 env(呼び手が明示していない時のみ)。
(setv SHELL-PROMPT-SUPPRESSING-ENV
      [#("DISABLE_AUTO_UPDATE" "true") #("DISABLE_UPDATE_PROMPT" "true")])

;; paste → Enter の settle(oracle tmux_send_keys: codex が入力箱を文字単位で
;; 描画するため、直後の Enter は transient 状態に食われ得る)。
(setv PASTE-SETTLE-SECONDS 1.0)
;; confirm ループの初期待ち + 再送間隔(oracle confirm_literal_prompt_submitted)。
(setv CONFIRM-INITIAL-SECONDS 1.2)
(setv CONFIRM-RETRY-SECONDS 1.0)
(setv CONFIRM-MAX-RETRIES 3)
;; prompt judge の wall-clock cap(hang した judge が monitor tick を止めない)。
(setv PROC-RUN-TIMEOUT-SECONDS 60)


(deff normalized-env-key [key]
  {:pre [(: key str)]
   :post [(: % str)]}
  "env key の正規化(oracle normalized_env_key: `-`→`_`・大文字化)。"
  (.upper (.replace key "-" "_")))

(deff ensure-no-forbidden-agent-env [env]
  {:pre [(: env dict)]
   :post [(: % "None — 違反は raise")]}
  "禁止 env の hard reject(oracle ensure_no_forbidden_agent_env)。"
  (setv forbidden
        (lfor key (.keys env)
              :if (in (normalized-env-key key) FORBIDDEN-AGENT-ENV-KEYS)
              key))
  (when forbidden
    (setv joined (.join ", " forbidden))
    (raise (RuntimeError
             (+ "doeff session host must never pass Anthropic API keys to agent "
                "processes. API-key-backed calls are allowed only through memoized "
                "LLM query handlers, never agent session environments. "
                f"Forbidden key(s): {joined}"))))
  None)


;; ---------------------------------------------------------------------------
;; paste 残留検出(oracle output_has_unsubmitted_paste_input の sent-text 面 —
;; confirm ループ専用。monitor 面(sent-text 無し)は impls/markers.hy 所有)
;; ---------------------------------------------------------------------------

(deff normalize-prompt-text [text]
  {:pre [(: text str)]
   :post [(: % str)]}
  "NBSP → space + 空白正規化(oracle normalize_prompt_text)。"
  (.join " " (.split (.replace text " " " "))))

(deff compact-prompt-text [text]
  {:pre [(: text str)]
   :post [(: % str)]}
  "空白を全て除去(oracle compact_prompt_text — TUI の折返し空白差を吸収)。"
  (.join "" (.split (normalize-prompt-text text))))

(deff literal-prompt-fragments [text]
  {:pre [(: text str)]
   :post [(: % list)]}
  "送出テキストの識別可能断片(oracle literal_prompt_fragments: 4-word 窓の
   24 文字以上 + 先頭/末尾 80 文字)。"
  (setv normalized (normalize-prompt-text text))
  (setv words (.split normalized))
  (setv fragments [])
  (for [start (range (max 0 (- (len words) 3)))]
    (setv fragment (.join " " (cut words start (+ start 4))))
    (when (>= (len fragment) 24)
      (.append fragments fragment)))
  (when (>= (len normalized) 24)
    (.append fragments (cut normalized 0 80))
    (.append fragments (cut normalized (max 0 (- (len normalized) 80)) None)))
  fragments)

(deff unsubmitted-paste-input? [output sent-text]
  {:pre [(: output str) (: sent-text (| str None))]
   :post [(: % bool)]}
  "未 submit paste の検出(oracle output_has_unsubmitted_paste_input):
   末尾 20 行の最終 prompt 行に collapsed paste marker、または送出断片が
   prompt 領域に可視のまま残っている。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-line None)
  (setv last-prompt-index None)
  (for [[index line] (enumerate recent)]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-line trimmed)
      (setv last-prompt-index index)))
  (when (and (is-not last-prompt-line None)
             (or (in "[Pasted text" last-prompt-line)
                 (in "[Pasted Content" last-prompt-line)
                 (in "Press up to edit queued messages" last-prompt-line)))
    (return True))
  (when (or (is None sent-text) (is None last-prompt-index))
    (return False))
  (setv prompt-region
        (normalize-prompt-text (.join "\n" (cut recent last-prompt-index None))))
  (setv prompt-region-compact (compact-prompt-text prompt-region))
  (for [fragment (literal-prompt-fragments sent-text)]
    (when (in fragment prompt-region)
      (return True))
    (setv compact-fragment (compact-prompt-text fragment))
    (when (and (>= (len compact-fragment) 24)
               (in compact-fragment prompt-region-compact))
      (return True)))
  False)


;; ---------------------------------------------------------------------------
;; tmux 生 IO(oracle tmux_*)
;; ---------------------------------------------------------------------------

(deff run-tmux [tmux-bin args]
  {:pre [(: tmux-bin str) (: args list)]
   :post [(: % subprocess.CompletedProcess)]}
  "tmux subprocess の実行(check はしない — 呼び手が status を解釈)。"
  (subprocess.run [tmux-bin #* args] :capture-output True :text True))

(deff tmux-capture-io [tmux-bin pane-id lines]
  {:pre [(: tmux-bin str) (: pane-id str) (: lines int)]
   :post [(: % str)]}
  "capture-pane -p -J -S -N(oracle tmux_capture)。"
  (setv res (run-tmux tmux-bin
                      ["capture-pane" "-t" pane-id "-p" "-J"
                       "-S" (str (- (max 1 lines)))]))
  (when (!= res.returncode 0)
    (raise (RuntimeError f"tmux capture-pane failed: {(.strip res.stderr)}")))
  res.stdout)

(deff tmux-send-enter-io [tmux-bin pane-id]
  {:pre [(: tmux-bin str) (: pane-id str)]
   :post [(: % "None")]}
  (setv res (run-tmux tmux-bin ["send-keys" "-t" pane-id "Enter"]))
  (when (!= res.returncode 0)
    (raise (RuntimeError "tmux send Enter failed")))
  None)

(deff tmux-paste-literal-io [tmux-bin pane-id message]
  {:pre [(: tmux-bin str) (: pane-id str) (: message str)]
   :post [(: % "None")]}
  "長文 prompt は send-keys -l でなく buffer paste(oracle tmux_paste_literal:
   実 Claude Code が -l の長文を落とした実測)。"
  (setv buffer-name
        (+ "doeff-sessionhost-" (str (os.getpid)) "-"
           (.join "" (gfor c pane-id (if (.isalnum c) c "_")))))
  (setv res (run-tmux tmux-bin ["set-buffer" "-b" buffer-name message]))
  (when (!= res.returncode 0)
    (raise (RuntimeError "tmux set-buffer failed")))
  (setv paste (run-tmux tmux-bin ["paste-buffer" "-b" buffer-name "-t" pane-id]))
  (run-tmux tmux-bin ["delete-buffer" "-b" buffer-name])
  (when (!= paste.returncode 0)
    (raise (RuntimeError "tmux paste-buffer failed")))
  None)

(deff tmux-send-keys-io [tmux-bin pane-id text literal submit]
  {:pre [(: tmux-bin str) (: pane-id str) (: text str)
         (: literal bool) (: submit bool)]
   :post [(: % "None")]}
  "TmuxSendKeys の実体(oracle tmux_send_keys): literal は buffer paste、
   submit は settle 後の Enter + confirm ループ(paste 残留の Enter 再送 —
   ハザード 4 の盲窓物理はここが所有する)。キー名送出(literal=False)は
   素の send-keys。"
  (if (and literal text)
      (tmux-paste-literal-io tmux-bin pane-id text)
      (do
        (setv args ["send-keys" "-t" pane-id])
        (when literal (.append args "-l"))
        (.append args text)
        (setv res (run-tmux tmux-bin args))
        (when (!= res.returncode 0)
          (raise (RuntimeError "tmux send-keys failed")))))
  (when submit
    (time.sleep PASTE-SETTLE-SECONDS)
    (tmux-send-enter-io tmux-bin pane-id)
    (when (and literal text)
      (time.sleep CONFIRM-INITIAL-SECONDS)
      (for [_ (range CONFIRM-MAX-RETRIES)]
        (setv output (tmux-capture-io tmux-bin pane-id 40))
        (when (not (unsubmitted-paste-input? output text))
          (break))
        (tmux-send-enter-io tmux-bin pane-id)
        (time.sleep CONFIRM-RETRY-SECONDS))))
  None)


;; ---------------------------------------------------------------------------
;; 実 substrate handler(tmux / clock / proc / fs / env)
;; ---------------------------------------------------------------------------

(defhandler real-substrate [tmux-bin]
  (TmuxNewSession [session-name work-dir env]
    (ensure-no-forbidden-agent-env env)
    (setv args ["new-session" "-d" "-s" session-name "-P" "-F" "#D"
                "-c" work-dir])
    (for [[key value] SHELL-PROMPT-SUPPRESSING-ENV]
      (when (not-in key env)
        (.extend args ["-e" f"{key}={value}"])))
    (for [[key value] (.items env)]
      (.extend args ["-e" f"{key}={value}"]))
    (setv res (run-tmux tmux-bin args))
    (when (!= res.returncode 0)
      (raise (RuntimeError f"tmux new-session failed: {(.strip res.stderr)}")))
    (resume (.strip res.stdout)))

  (TmuxHasSession [session-name]
    (setv res (run-tmux tmux-bin ["has-session" "-t" session-name]))
    (resume (= res.returncode 0)))

  (TmuxPaneCurrentCommand [pane-id]
    (setv res (run-tmux tmux-bin
                        ["display-message" "-p" "-t" pane-id
                         "#{pane_current_command}"]))
    (resume (if (= res.returncode 0) (.strip res.stdout) None)))

  (TmuxCapture [pane-id lines]
    (resume (tmux-capture-io tmux-bin pane-id lines)))

  (TmuxSendKeys [pane-id text literal submit]
    (tmux-send-keys-io tmux-bin pane-id text literal submit)
    (resume None))

  (TmuxKillSession [session-name]
    (setv res (run-tmux tmux-bin ["kill-session" "-t" session-name]))
    (when (!= res.returncode 0)
      (raise (RuntimeError f"tmux kill-session failed: {session-name}")))
    (resume None))

  (ClockNow []
    (resume (datetime.now timezone.utc)))

  (ClockSleep [seconds]
    (time.sleep seconds)
    (resume None))

  (ProcRun [command stdin]
    ;; sh -c + stdin + wall-clock cap(oracle run_judge_command — hang した
    ;; judge が他 session の観測を止めない)。timeout は値として返す。
    (try
      (setv res (subprocess.run ["sh" "-c" command]
                                :input (or stdin "")
                                :capture-output True
                                :text True
                                :timeout PROC-RUN-TIMEOUT-SECONDS))
      (resume (ProcResult :exit-code res.returncode
                          :stdout res.stdout
                          :stderr res.stderr))
      (except [subprocess.TimeoutExpired]
        (resume (ProcResult :exit-code 124
                            :stdout ""
                            :stderr f"process timed out after {PROC-RUN-TIMEOUT-SECONDS}s")))))

  (FsCanonicalPath [path]
    (resume (os.path.realpath path)))

  (FsReadText [path]
    (if (os.path.exists path)
        (do
          (with [f (open path :encoding "utf-8")]
            (setv content (.read f)))
          (resume content))
        (resume None)))

  (FsWriteTextAtomic [path text tmp-suffix]
    ;; write-new + rename(oracle: 並走 reader が torn state を読まない)
    (setv tmp-path (+ path tmp-suffix))
    (with [f (open tmp-path "w" :encoding "utf-8")]
      (.write f text))
    (os.replace tmp-path path)
    (resume None))

  (FsMakeDirs [path]
    (os.makedirs path :exist-ok True)
    (resume None))

  (EnvGet [name]
    (resume (.get os.environ name))))


;; ---------------------------------------------------------------------------
;; in-memory SessionStore(直接束縛用 — host の SQLite writer actor は C3)
;; ---------------------------------------------------------------------------

(defclass MemorySessionStore []
  "直接束縛の真実置き場: 呼び手 process 内で policy / launch を回すための
   最小 store。寿命の外部性(reap 生存・呼び手死後の継続)は提供しない —
   それは C3 host の存在理由(daemon-owns-only-exteriority)。"
  (defn __init__ [self]
    (setv self.rows {})
    (setv self.result-payloads {})
    (setv self.events [])))


(defhandler memory-session-store [store]
  (SessionStoreListActive []
    (resume (lfor r (list (.values store.rows))
                  :if (in r.status ACTIVE-STATUSES)
                  r)))

  (SessionStoreGet [session-id]
    (resume (.get store.rows session-id)))

  (SessionStoreUpsert [row]
    ;; COALESCE 規律(main.rs:2339): 永続化済み result-payload を upsert が
    ;; 消すことは禁止
    (setv existing (.get store.rows row.session-id))
    (when (and (is-not existing None)
               (is-not existing.result-payload None)
               (is None row.result-payload))
      (setv row (replace row :result-payload existing.result-payload)))
    (setv (get store.rows row.session-id) row)
    (resume None))

  (SessionStoreResultPayload [session-id]
    (resume (.get store.result-payloads session-id)))

  (SessionStoreRecordEvent [session-id event-type row]
    (.append store.events #(session-id event-type))
    (resume None)))
