;;; 実 substrate handler(ADR-DOE-AGENTS-004 C2)— 生 IO の唯一の家。
;;;
;;; impls/(per-kind)と policy / launch(共有 program)は substrate effect を
;;; yield するのみで、実世界(tmux / FS / clock / 子 process)に触るのはこの
;;; モジュールだけ。oracle: agentd-rust-final:src/main.rs の
;;; tmux_* / run_judge_command / fs 物理を verbatim 移植。
;;;
;;; SessionStore の実体(SQLite 単一 writer actor)は host の外部性で C3 所有 —
;;; ここには直接束縛用の in-memory store のみ置く(呼び手 process 内で
;;; policy / launch を回すための最小の真実置き場)。

(require doeff-hy.macros [deff defk defhandler])

(import dataclasses [replace])
(import doeff [run])
(import datetime [datetime timezone])
(import errno)
(import hashlib)
(import json)
(import os)
(import stat)
(import subprocess)
(import sys)
(import tempfile)
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
  TmuxSessionPaneIds
  TmuxCapture
  TmuxSendKeys
  TmuxKillSession
  ClockNow
  ClockSleep
  ProcRun
  FsCanonicalPath
  FsComposeHomeView
  FsReadText
  FsEnsureSymlink
  FsWriteTextAtomic
  FsMakeDirs
  FsLinkArtifact
  FsSymlinkOutcome
  FS-SYMLINK-LINKED
  FS-SYMLINK-OCCUPIED
  FS-SYMLINK-REFUSED
  FS-SYMLINK-SAME-ENTITY
  FS-SYMLINK-SOURCE-MISSING
  FS-SYMLINK-TARGET-CONFLICT
  FS-SYMLINK-UNCHANGED
  FsListDir
  FsRemoveFile
  FsDirExists
  FsFileExists
  FsFileMtime
  GitRun
  EnvGet
  LogLine])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES
                                         PROVIDER-AUTH-ENV-KEYS
                                         env-offenders-against
                                         policy-normalized-env-key])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs)
;; ---------------------------------------------------------------------------

;; agent process へ決して渡さない env(oracle FORBIDDEN_AGENT_ENV_KEYS —
;; API-key 呼び出しは memoized LLM handler 経由のみ、agent session env は禁止)。
;; 綴りは持たず policy の語彙を名指す(card acp:kanban-issue:ki-2a061da56ca9:
;; 3 層が別々の literal を持っていたので 3 つとも中身が違った)。ここは最後の砦
;; なので **PROVIDER-AUTH ちょうど** — 手番の札(TURN-AUTH)は受理と同じく
;; わざと通す(ADR 012 R5・R30)。
(setv FORBIDDEN-AGENT-ENV-KEYS PROVIDER-AUTH-ENV-KEYS)

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
  "env key の正規化(oracle normalized_env_key: `-`→`_`・大文字化)。
   規約の定義点は policy 側の 1 つ — ここは oracle の名を保つ薄い呼び出し。"
  (policy-normalized-env-key key))

(deff ensure-no-forbidden-agent-env [env]
  {:pre [(: env dict)]
   :post [(: % "None — 違反は raise")]}
  "禁止 env の hard reject(oracle ensure_no_forbidden_agent_env)。"
  (setv forbidden (env-offenders-against env FORBIDDEN-AGENT-ENV-KEYS))
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
  "未 submit paste / 添付の検出(oracle output_has_unsubmitted_paste_input +
   issue #568 / ADR-DOE-AGENTS-010 R1 の composer 領域拡張):
   末尾 20 行の composer 領域(最終 prompt 行とそれ以降)に collapsed paste
   marker・添付チップ([Image #N])・queued ヒント、または送出断片が
   prompt 領域に可視のまま残っている。添付チップは prompt 行の外(直下の行)
   に描かれる — prompt 行 1 行だけの走査は実 wedge 形に盲目だった。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-index None)
  (for [[index line] (enumerate recent)]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-index index)))
  (when (is-not last-prompt-index None)
    (setv composer (.join "\n" (cut recent last-prompt-index None)))
    (when (or (in "[Pasted text" composer)
              (in "[Pasted Content" composer)
              (in "[Image #" composer)
              (in "Press up to edit queued messages" composer))
      (return True)))
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
  ;; -p = bracketed paste — 生の改行は Enter として届き、TUI のバースト
  ;; 検出頼みの行分割 submit になる(agentd-codex-coldstart-paste-race)。
  (setv paste (run-tmux tmux-bin ["paste-buffer" "-p" "-b" buffer-name "-t" pane-id]))
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

(defk compose-home-view-name [auth-resolved profile-resolved]
  {:pre [(: auth-resolved str) (: profile-resolved str)]
   :post [(: % str)]}
  "決定的な view 名: 人間可読 prefix(profile basename)+ resolved realpath
   ペアの sha256 先頭 8 桁。wire には path しか載らないため名前は path から
   導出するしかなく、basename 単独は別 registry の同名 bundle で衝突する。"
  (setv digest (.hexdigest (hashlib.sha256 (.encode f"{auth-resolved}\x00{profile-resolved}" "utf-8"))))
  f"{(os.path.basename profile-resolved)}--{(cut digest 0 8)}")

(defk refusal-of [operation code]
  {:pre [(: operation str) (: code (| int None))]
   :post [(: % str)]}
  "据え付けを断られた syscall(operation)と errno(code)から結末の状態を解く
   **純関数の 1 点**(設計 docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/design.md
   の `refusal-of(operation, errno)`。第 2 引数を `errno` と綴ると module 名を
   隠して `errno.EISDIR` が引けなくなるので `code`)。
   `occupied-by-real-entity` になる組はただ 1 つ = (rename, EISDIR)。仮は必ず
   symlink なので、rename(2) が『宛先は dir だ』と言う拍だけが実体の居座りで、
   残りはすべて据え付けの断り(権限 / 容量 / 読み取り専用)。
   ⚠ **errno の表を syscall 間で共有しない**: makedirs は『親の位置に実体 file』で
   EEXIST を、symlink も同じ形で EEXIST を出すので、1 つの表を両方に当てると
   在りもしない居座りを名乗り、運用者が居ない実体を手で片付けに行く(扉 2 の誤診断の
   再生産)。ENOTEMPTY も rename からは出ない(仮が symlink なので dir → dir に
   ならない)— 表に足さない。"
  (if (and (= operation "rename") (= code errno.EISDIR))
      FS-SYMLINK-OCCUPIED
      FS-SYMLINK-REFUSED))

(defk _container-verdict [syscall error]
  {:pre [(: syscall str) (: error OSError)]
   :post [(: % FsSymlinkOutcome)]}
  "器(file system)が断った OSError を結末へ写す唯一の口。状態の割りは
   refusal-of の 1 点で、ここは理由(errno と detail)を積むだけ — ENOSPC /
   EACCES / EROFS は運用者の取る手がまったく違うので、理由の無い『断られた』は
   無益な文言にしかならない。"
  (setv code (. error errno))
  (setv name (.get errno.errorcode code "ERRNO?"))
  (FsSymlinkOutcome
    :state (! (refusal-of syscall code))
    :errno code
    :detail f"{syscall}: {name} {(. error strerror)}"))

(defk _link-artifact-seated [source-path target-path]
  {:pre [(: source-path str) (: target-path str)]
   :post [(: % FsSymlinkOutcome)]}
  "敷設先に何かが据わっている拍の名乗り(FsLinkArtifact 専用)。同一実体なら
   same-entity、別実体なら target-conflict。
   ⚠ samefile の OSError(broken link 等)は『同一実体と確認できない』の意味なので
   conflict 側へ倒す(share.py の except OSError: pass と同型 — 据わっている物を
   触らない約束は、観測できない時こそ守る側に倒すのが安全)。ただし理由は detail に
   載せる: **語彙は安全側・報告は正直**。"
  (try
    (if (os.path.samefile target-path source-path)
        (FsSymlinkOutcome :state FS-SYMLINK-SAME-ENTITY)
        (FsSymlinkOutcome :state FS-SYMLINK-TARGET-CONFLICT))
    (except [error OSError]
      (setv code (. error errno))
      (setv name (.get errno.errorcode code "ERRNO?"))
      (FsSymlinkOutcome
        :state FS-SYMLINK-TARGET-CONFLICT
        :errno code
        :detail f"samefile: {name} {(. error strerror)}"))))

(deff ensure-symlink-outcome [link target]
  {:pre [(: link str) (: target str)]
   :post [(: % FsSymlinkOutcome)]}
  "symlink を正しい先へ据える **1 つの動詞**(card acp:kanban-issue:ki-62aa1f4e9c9c D8・盲検 A)。
   4 値を返す(raise しない — 方針判断は呼び手所有):
     同じ先を指す symlink   \"unchanged\"(触らない — 本体は skills の dir を見張っているので、
                            用の無い張り替えは走っている席にまで効く)
     別の先を指す symlink   **張り替えて** \"linked\"
     symlink でない実体     触らず \"occupied-by-real-entity\"(erosion guard)
     何も居ない             親 dir を作って張り \"linked\"
     器が据え付けを断った   \"refused-by-container\"(errno / detail つき)
   ⚠ FsLinkArtifact(据わっている物を絶対に置き換えない)との違いは**張り替えるか**の 1 点で、
   それが無いと正本の path が動いた日に家の symlink が古い先を指したまま残る。
   ⚠ **張りも張り替えも rename 1 手**(D9 の書きと同じ物理・計画段の実測
   evidence/symlink_install_race.log と tests の A / B)。家は資格ごとに鋳られ、同じ資格の
   複数の席が 1 つの家へ同時に降りる(pool の入れ替えの直後は排水中に溜まった郵便が一斉に
   手番になる)。素の symlink / unlink→symlink の 2 手には、そこで 2 つの穴が開く:
     * 空の家へ 2 席が同拍で張ると片方が FileExistsError で落ちる(直す前の実測: 会社 Mac
       200/200・pod 198/200)。この動詞は raise しない約束なのに、その約束ごと破れて席が起きない。
     * 張り替えの 2 手の間に読んだ席が **根の無い瞬間**を見る(同 52 % / 53 %)。そこへ本体の
       skills の discovery が当たると、その席の user 層の skills は 0 件。
   ⇒ 書き手ごとに一意な名の仮の symlink を張り、rename(2) で被せる。rename は宛先が symlink なら
   symlink そのものを原子的に置き換えるので、読み手は常に古い先か新しい先のどちらかを見る。
   lock は足さない(家は process をまたいで共有されるので、process の中の lock は届かない)。
   ⚠ **器の断りは語彙の中に在る**(2026-09-22 — 設計 symlink-verbs-fail-vocabulary-ZCN5BD):
   makedirs / symlink / rename は権限・容量・読み取り専用・親の位置の実体で必ず断る。
   語彙に無かった頃はその断りが素の OSError として effect の外まで抜け、raise しない約束が
   破れて席が起きなかった(実測 = 設計 2.2 の 3 形すべて)。
   ⚠ 残る窓は 1 つ: 下の見分けと rename の間に **実体の file** が現れると rename はそれを黙って
   置き換える(実体の dir なら rename が EISDIR になるので occupied に落ちる)。erosion guard が
   守る形は長く据わっている実体なので見分けが先に当たるが、「symlink か不在の時だけ被せる」を
   原子的に言える syscall は移植できる形では無い。"
  ;; ⚠ 在否と種別は **1 回の lstat** で見る。`islink` と `lexists` の 2 回に割ると、その隙に
  ;; 相手の席の rename が着地した拍で「symlink では無いのに在る」= 実体が居るに見え、空の家に
  ;; 居もしない実体を名乗って席が skills を失う(この形は tests の A が 200 回中 4 回で掴んだ)。
  (setv seated
    (try
      (os.lstat link)
      (except [OSError]
        ;; 何も居ない(親 dir がまだ無い拍を含む)
        None)))
  (when (is-not seated None)
    (when (not (stat.S-ISLNK seated.st-mode))
      (return (FsSymlinkOutcome :state FS-SYMLINK-OCCUPIED)))
    (try
      (when (= (os.readlink link) target)
        (return (FsSymlinkOutcome :state FS-SYMLINK-UNCHANGED)))
      (except [OSError]
        ;; 読む間に別の席が張り替えた(または消した)— 下の据え付けで決める(raise しない)
        None)))
  (setv parent (os.path.dirname link))
  (when parent
    (try
      (os.makedirs parent :exist-ok True)
      (except [error OSError]
        ;; 親の位置に実体 file が居る / 家が書けない / 読み取り専用 — 据え付けの断り
        (return (run (_container-verdict "makedirs" error))))))
  ;; 仮の名は**書き手ごとに一意**(D9 と同じ反例 — 固定名だと 2 席が互いの仮を踏む)。
  ;; suffix は残す(残骸の見分けの綴り)。
  (setv staged (os.path.join (or parent ".")
                             f".{(os.path.basename link)}.{(os.getpid)}.{(.hex (os.urandom 4))}.agentd-tmp"))
  (try
    (os.symlink target staged)
    (except [error OSError]
      ;; 仮すら張れない(権限 / 容量 / 読み取り専用)— 仮は生まれていないので掃除も要らない
      (return (run (_container-verdict "symlink" error)))))
  (try
    (os.replace staged link)
    (except [error OSError]
      ;; 自分の仮だけ掃除してから名乗る(他の書き手の仮には触らない)。
      (try
        (os.unlink staged)
        (except [OSError] None))
      ;; 実体の居座り(EISDIR)と据え付けの断りの割りは refusal-of の 1 点 —
      ;; ここで 2 つ目の表を作らない。
      (return (run (_container-verdict "rename" error)))))
  (FsSymlinkOutcome :state FS-SYMLINK-LINKED))

(deff _ensure-view-symlink [link target]
  {:pre [(: link str) (: target str)]
   :post [(: % "None — 実ファイル/実 dir は raise(erosion guard)")]}
  "apps _ensure-symlink の意味移植: symlink は張り替え、実ファイル/実 dir が
   居たら typed fail(erosion guard — 黙って置換しない。silent 置換は registry
   と token の fork を隠す)。
   ⚠ 張り替えの物理は ensure-symlink-outcome の 1 点へ畳んである(D8)— ここはその 4 値のうち
   `occupied-by-real-entity` と `refused-by-container` を home view の契約(typed fail)へ
   戻す薄い層で、FsComposeHomeView の振る舞いは 1 byte も変わらない。
   ⚠ **2 つの断りに同じ文言を当てない**(2026-09-22): 実体が居座っているなら「手で片付けろ」が
   正しい案内だが、器が権限 / 容量 / 読み取り専用で断ったのなら片付ける実体は存在しない —
   同じ文言にすると運用者が居もしない実ファイルを探しに行く。理由は outcome の detail が運ぶ。"
  (setv outcome (ensure-symlink-outcome link target))
  (when (= (. outcome state) FS-SYMLINK-OCCUPIED)
    (raise (RuntimeError
             (+ link " is a real file where a symlink into the profile "
                "bundle is required (erosion guard) — reconcile it manually; "
                "refusing to overwrite"))))
  (when (= (. outcome state) FS-SYMLINK-REFUSED)
    (raise (RuntimeError
             (+ link " could not be installed as a symlink into the profile "
                f"bundle — the container refused the install ({(. outcome detail)}); "
                "nothing is seated at that path to reconcile"))))
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
  (setv view (os.path.join view-root (run (compose-home-view-name auth-resolved profile-resolved))))
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

  (TmuxSessionPaneIds [session-name]
    ;; 宛先 pane の帰属観測(ADR-DOE-AGENTS-010 R4): session が現に所有する
    ;; 全 pane(-s = 全 window)。非 0 = session 不在 → 空 list(直前の
    ;; has-session probe が応答済みの文脈で呼ばれる — 応答した tmux の否定は
    ;; 積極証拠)。
    (setv res (run-tmux tmux-bin
                        ["list-panes" "-s" "-t" session-name
                         "-F" "#{pane_id}"]))
    (resume (if (= res.returncode 0)
                (lfor line (.splitlines res.stdout) :if (.strip line) (.strip line))
                [])))

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

  (LogLine [text]
    ;; 名乗りの 1 行(R13): agentd の log(stderr)へ。program は substrate-clean なので
    ;; 書くのはここ 1 点 — agentd の同名の effect(acp/handlers.py)と同じ行き先。
    (.write sys.stderr (+ text "\n"))
    (.flush sys.stderr)
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
    ;; ⚠ tmp の名は**書き手ごとに一意**(card acp:kanban-issue:ki-62aa1f4e9c9c D9・盲検 A の反例):
    ;; 固定名 <path><suffix> は、同じ家へ 2 席が同拍で書くと互いの書きかけを上書きし、
    ;; 先に rename した側の tmp を後の側が消して **FileNotFoundError で片方の書きが落ちる**。
    ;; 家は資格ごとに共有され(1 つの config-dir に複数の席)、この便でその家へ書く物が増えるので、
    ;; 既存の preseed-claude-trust の競りもここで同時に閉じる。suffix は残す(残骸の見分けの綴り)。
    (setv directory (or (os.path.dirname path) "."))
    (setv [fd tmp-path] (tempfile.mkstemp :dir directory
                                          :prefix (+ (os.path.basename path) ".")
                                          :suffix tmp-suffix))
    (try
      (with [f (os.fdopen fd "w" :encoding "utf-8")]
        (.write f text))
      (os.replace tmp-path path)
      (except [error Exception]
        ;; 自分の tmp だけを掃除して送出する(他の書き手の tmp には触らない)。
        (try
          (os.unlink tmp-path)
          (except [OSError] None))
        (raise)))
    (resume None))

  (FsEnsureSymlink [link target]
    ;; D8: 張り替える symlink の据え付け(3 値)。物理は ensure-symlink-outcome の 1 点。
    (resume (ensure-symlink-outcome link target)))

  (FsMakeDirs [path]
    (os.makedirs path :exist-ok True)
    (resume None))

  (FsLinkArtifact [source-path target-path]
    ;; agentcli share.py link_session_artifact の意味移植(transplant の
    ;; symlink 敷設)。source 不在は触らず値で返す(方針判断は呼び手所有)。
    ;; target の別実体は share.py 同型の no-op — silent 置換はしない。
    ;;
    ;; ⚠ **見てから張る**を判断の座にしない(2026-09-22・設計 3.4)。同じ敷設先へ
    ;; 2 process が同拍で降りると、見た後・張る前に相手が張って FileExistsError が
    ;; effect の外まで抜ける(直す前の実測: 200 ラウンド中 184〜196 = 92〜98 %)。
    ;; 家は資格ごとに鋳られ、同じ資格の複数の会話が 1 つの家へ同じ拍で降りるので、
    ;; これは理論上の窓ではない。lock は足さない(家は process をまたぐので process の
    ;; 中の lock は届かない)— 閉じ方は物理そのもの: os.symlink を撃ち、
    ;; FileExistsError を「見た後に何かが現れた」の合図として samefile を読み直す。
    ;; 語彙は 1 つも増えない(same-entity / target-conflict のどちらかに落ちる)。
    ;;
    ;; ⚠ 器の断り(権限 / 容量 / 読み取り専用 / 親の位置の実体)は
    ;; refused-by-container。理由は errno / detail が運ぶ。
    (resume
      (cond
        (not (or (os.path.exists source-path) (os.path.islink source-path)))
          (FsSymlinkOutcome :state FS-SYMLINK-SOURCE-MISSING)
        (or (os.path.exists target-path) (os.path.islink target-path))
          (! (_link-artifact-seated source-path target-path))
        True
          (do
            (setv parent (os.path.dirname target-path))
            (setv refused None)
            ;; 親を作れない(親の位置に実体 file / 家が r-x / 読み取り専用)も据え付けの断り。
            ;; parent の空文字は makedirs("") = FileNotFoundError になるので先に外す
            ;; (ensure-symlink-outcome 側には在ったガード — 非対称を畳む)。
            (when parent
              (try
                (os.makedirs parent :exist-ok True)
                (except [error OSError]
                  (setv refused (! (_container-verdict "makedirs" error))))))
            (if (is-not refused None)
                refused
                (try
                  (do
                    (os.symlink source-path target-path)
                    (FsSymlinkOutcome :state FS-SYMLINK-LINKED))
                  (except [FileExistsError]
                    ;; 見た後に何かが現れた(相手の席が同拍で張った / 実体が置かれた)。
                    ;; 据わっている物を読み直して名乗る — 置き換えは絶対にしない。
                    (! (_link-artifact-seated source-path target-path)))
                  (except [error OSError]
                    (! (_container-verdict "symlink" error)))))))))

  (FsRemoveFile [path]
    ;; card acp:kanban-issue:ki-6b5c4b270ca0: 名指した 1 file を落とす。**dir は触らない**
    ;; (再帰も glob も無い)。不在は成功 = 端の状態を観測する形で、既に無いのは望みの状態。
    ;; ⚠ raise しない: 置き場の掃除は席を起こすための前置きなので、ここで送出すると記憶を使う
    ;; 会話だけが起動できなくなる。dir を渡された拍(IsADirectoryError)・権限も同じ扱いで、
    ;; 消せなかったことは戻りの False として呼び手の名乗りに出る。
    (setv removed False)
    (try
      (os.remove path)
      (setv removed True)
      (except [OSError]))
    (resume removed))

  (FsListDir [path]
    ;; 発見用の非破壊読み(ADR-006): 不在・非 dir・権限は空 list —
    ;; discovery は level-triggered に再試行されるので観測不能 = 未発見。
    (setv entries [])
    (try
      (setv entries (sorted (os.listdir path)))
      (except [OSError]))
    (resume entries))

  (FsDirExists [path]
    ;; ADR-DOE-AGENTS-006 R10(発注の物理前提検査)。isdir は symlink を
    ;; 解決する — 解決先が dir なら実在、壊れた symlink は不在。
    (resume (os.path.isdir path)))

  (GitRun [repo args]
    ;; ACP W2(workspace seed の実体化)— subprocess 物理のみ。失敗判断は
    ;; 呼び手(launch program)所有なので code をそのまま返す(raise しない)。
    (setv proc (subprocess.run ["git" "-C" repo #* (list args)]
                               :capture-output True :text True))
    (resume {"code" proc.returncode
             "stdout" (or proc.stdout "")
             "stderr" (or proc.stderr "")}))

  (FsFileExists [path]
    ;; ADR-DOE-AGENTS-006 R10(transcript 実在検査)。isfile は symlink を
    ;; 解決する — 壊れた symlink は不在(R7 の意味論と同義)。
    (resume (os.path.isfile path)))

  (FsFileMtime [path]
    ;; ADR-002 R-conversation-evidence(会話記録の鮮度読み)。getmtime は
    ;; symlink を解決する。不在・観測不能(OSError)は None — raise しない
    ;; (probe は反証面であって門ではない)。
    (resume (try (os.path.getmtime path)
                 (except [OSError] None))))

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
