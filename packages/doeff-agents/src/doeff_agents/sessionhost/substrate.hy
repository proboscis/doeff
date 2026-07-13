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
(import hashlib)
(import json)
(import os)
(import subprocess)
(import threading)
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
  FsComposeHomeView
  FsReadText
  FsWriteTextAtomic
  FsMakeDirs
  FsListDir
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
   実 Claude Code が -l の長文を落とした実測)。buffer 内容は load-buffer の
   STDIN で流し込む — tmux の client-server protocol は 1 コマンド ~16KB
   (imsg framing)で、set-buffer の argv 渡しは message がそれを超えると
   \"command too long\" で必ず落ちる(oracle 33ab4bae と同修正。argus attend
   prompt の成長で live 実測)。"
  (setv buffer-name
        (+ "doeff-sessionhost-" (str (os.getpid)) "-"
           (.join "" (gfor c pane-id (if (.isalnum c) c "_")))))
  (setv res (subprocess.run [tmux-bin "load-buffer" "-b" buffer-name "-"]
                            :input message :capture-output True :text True))
  (when (!= res.returncode 0)
    (raise (RuntimeError "tmux load-buffer failed")))
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
;; home view の合成(#15 FsComposeHomeView の実 IO — apps ensure-agent-home の
;; 意味移植。合成 CODEX_HOME は adapter 物理で、その家は host に一本化)
;; ---------------------------------------------------------------------------

;; view 単位の合成 lock: host は connection 毎 thread で launch を回すため、
;; 同一 binding の並行 launch が symlink の unlink/relink で race しないよう
;; view path で直列化する(trust upsert の read-modify-write は incumbent の
;; last-writer-wins のまま — 対象外、DOE-004 R5 v2 の記録参照)。
(setv _COMPOSE-VIEW-LOCKS {})
(setv _COMPOSE-VIEW-LOCKS-GUARD (threading.Lock))

(deff _compose-view-lock [view-path]
  {:pre [(: view-path str)]
   :post [(: % "value")]}
  (with [_ _COMPOSE-VIEW-LOCKS-GUARD]
    (when (not-in view-path _COMPOSE-VIEW-LOCKS)
      (setv (get _COMPOSE-VIEW-LOCKS view-path) (threading.Lock)))
    (get _COMPOSE-VIEW-LOCKS view-path)))

(deff compose-home-view-name [auth-resolved profile-resolved]
  {:pre [(: auth-resolved str) (: profile-resolved str)]
   :post [(: % str)]}
  "決定的な view 名: 人間可読 prefix(profile basename)+ resolved realpath
   ペアの sha256 先頭 8 桁。wire には path しか載らないため名前は path から
   導出するしかなく、basename 単独は別 registry の同名 bundle で衝突する。"
  (setv digest (.hexdigest (hashlib.sha256 (.encode f"{auth-resolved}\x00{profile-resolved}" "utf-8"))))
  f"{(os.path.basename profile-resolved)}--{(cut digest 0 8)}")

(deff _ensure-view-symlink [link target]
  {:pre [(: link str) (: target str)]
   :post [(: % "None — 実ファイル/実 dir は raise(erosion guard)")]}
  "apps _ensure-symlink の意味移植: symlink は張り替え、実ファイル/実 dir が
   居たら typed fail(erosion guard — 黙って置換しない。silent 置換は registry
   と token の fork を隠す)。"
  (when (os.path.islink link)
    (os.unlink link)
    (os.symlink target link)
    (return None))
  (when (os.path.exists link)
    (raise (RuntimeError
             (+ link " is a real file where a symlink into the profile "
                "bundle is required (erosion guard) — reconcile it manually; "
                "refusing to overwrite"))))
  (os.symlink target link)
  None)

(deff compose-home-view [auth-file profile-dir view-root]
  {:pre [(: auth-file str) (: profile-dir str) (: view-root str)]
   :post [(: % str)]}
  "二軸宣言から home view を実体化して絶対パスを返す(冪等・view 単位 lock)。
   実在検証はここが単一の家(登録時検証の launch-time 移設、ACP 0040 R2 改訂):
   auth-file は実ファイル・profile-dir は実 dir でなければ typed fail。"
  (setv auth-resolved (os.path.realpath auth-file))
  (when (not (os.path.isfile auth-resolved))
    (raise (RuntimeError
             (+ "binding auth_file does not resolve to a file: " auth-file))))
  (setv profile-resolved (os.path.realpath profile-dir))
  (when (not (os.path.isdir profile-resolved))
    (raise (RuntimeError
             (+ "binding profile_dir does not resolve to a directory: " profile-dir))))
  (setv view (os.path.join view-root (compose-home-view-name auth-resolved profile-resolved)))
  (with [_ (_compose-view-lock view)]
    (os.makedirs view :exist-ok True)
    ;; sessions は bundle 側に掘る(incumbent 意味論: session 履歴は profile
    ;; 単位で共有 — host が bundle へ書ける同一ユーザー前提は #15 で明示)。
    (setv sessions (os.path.join profile-resolved "sessions"))
    (when (not (os.path.exists sessions))
      (os.makedirs sessions :exist-ok True))
    (_ensure-view-symlink (os.path.join view "auth.json") auth-resolved)
    (for [entry (sorted (os.listdir profile-resolved))]
      (when (!= entry "auth.json")
        (_ensure-view-symlink (os.path.join view entry)
                              (os.path.join profile-resolved entry)))))
  view)


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

  (FsListDir [path]
    ;; 発見用の非破壊読み(ADR-006): 不在・非 dir・権限は空 list —
    ;; discovery は level-triggered に再試行されるので観測不能 = 未発見。
    (setv entries [])
    (try
      (setv entries (sorted (os.listdir path)))
      (except [OSError]))
    (resume entries))

  (FsComposeHomeView [auth-file profile-dir view-root]
    (resume (compose-home-view auth-file profile-dir view-root)))

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
