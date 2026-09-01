;;; 共有 session-launch program(ADR-DOE-AGENTS-004 C2)。
;;;
;;; oracle: agentd-rust-final:src/main.rs session_launch + wait_for_repl_idle。
;;; kind 別の protocol 物理(argv・trust・gate・marker・dialog キー)は
;;; interface effect 越しに per-kind defhandler(impls/)が所有し、この program は
;;; 凍結された起動の「順序と方針」だけを持つ:
;;;   admission(重複 / 既存 tmux)→ per-kind PreLaunchSetup(S11 gate + trust)
;;;   → result channel 配線(ADR 0035 reject-at-launch)→ argv 構築 →
;;;   TmuxNewSession → booting 行 upsert + session_started event(登録は TUI
;;;   readiness に依存しない簿記 — ready 待ちの前。issue
;;;   agentd-session-registration-after-ready-gate)→ 起動 command 送出 →
;;;   wait-for-launch-ready(R9 launch dialog の決定的 dismissal + 空 composer +
;;;   貼り付け可能性 probe の 3 条件。予算切れ / probe 不成立は fail-closed:
;;;   閉語彙の分類と証拠 frame つきで行を terminal failed(prompt_undelivered)
;;;   へ遷移 — ADR-DOE-AGENTS-011)→ prompt の live REPL 配送
;;;   (result-protocol instruction 追記)→ monitor への手渡し upsert
;;;   (running + awaiting latch — BOOTING は launch pipeline 所有)。
;;;
;;; program は effect を yield するのみで IO を直接呼ばない(substrate-clean)。
;;; 呼び手より長生きする部分(socket・writer actor・lease・cycle 起動)は
;;; C3 の host 所有 — ここには置かない(daemon-owns-only-exteriority)。

(require doeff-hy.macros [defk deff <-])

(import json)
(import os)
(import re)

(import doeff_agents.sessionhost.effects [
  REPL-IDLE-MAX-WAIT-SECONDS
  RESUME-ERR-IDENTITY-UNKNOWN
  RESUME-ERR-KIND-NOT-SUPPORTED
  RESUME-ERR-ONE-LIVE-INCARNATION
  RESUME-ERR-WORKDIR-NOT-FOUND
  READY-PROBE-TEXT
  READY-PROBE-CLEAR-KEY
  READY-GATE-FRAME-RETENTION
  READY-GATE-FRAME-LINES
  SessionRow
  PaneFrame
  PaneObservation
  ReadyGateVerdict
  classify-pane
  deliver-message
  build-launch
  build-resume
  pre-launch-setup
  transplant-conversation
  wire-result-channel
  session-store-get
  session-store-list-active
  session-store-upsert
  session-store-record-event
  env-get
  fs-dir-exists
  fs-link-artifact
  fs-make-dirs
  fs-read-text
  fs-write-text-atomic
  git-run
  tmux-has-session
  tmux-new-session
  tmux-send-keys
  tmux-capture
  tmux-kill-session
  clock-now
  clock-sleep])
(import dataclasses [replace])

(import doeff_agents.sessionhost.policy [
  BINDING-OWNED-ENV-KEYS
  binding-admission-error
  counts-toward-launch-capacity
  format-evidence-frames
  iso-format
  launch-not-ready-class
  launch-not-ready-category
  launch-not-ready-reason
  make-cause
  metered-credential-env-offenders
  overlay-env-offenders
  seconds-since])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle 定数と文言)
;; ---------------------------------------------------------------------------

(setv LIFECYCLE-RUN-TO-COMPLETION "run_to_completion")
(setv LIFECYCLE-INTERACTIVE "interactive")

;; agentd が argv を組める(= interface effect の per-kind impl が存在すると
;; 契約上約束されている)kind(oracle is_interactive_agent_type)。
(setv INTERACTIVE-AGENT-TYPES #{"codex" "claude"})

;; expected_result 付き launch の prompt へ追記する結果搬送契約
;; (oracle result_protocol_instruction — 文言 verbatim。ADR 0035:
;; 結果は byte-faithful データチャネルで回収し、決して画面から scrape しない)。
(setv RESULT-PROTOCOL-INSTRUCTION
      (+ " Result channel: when you have finished the task, call the "
         "`report_result` MCP tool exactly once, passing your result as the "
         "`payload` argument — a JSON object that satisfies the result schema. "
         "Do not print the result to the terminal and do not create JSON "
         "result files; agentd only accepts the result through the "
         "`report_result` tool. If the tool responds with a validation error, "
         "fix the payload and call `report_result` again in the same session."))

;; wait-for-repl-idle の上限は knob 語彙の家(effects.hy
;; REPL-IDLE-MAX-WAIT-SECONDS)から import — literal の再定義は禁止
;; (ADR-DOE-AGENTS-008 R2)。poll / 再描画待ちは退役 Rust 移植値
;; 300ms / 800ms(rollback 専用出自 — ADR-DOE-AGENTS-004 R7/U1)。
(setv REPL-IDLE-POLL-SECONDS 0.3)
(setv DIALOG-REDRAW-SECONDS 0.8)


;; ---------------------------------------------------------------------------
;; 純粋 helper(oracle shell_quote / shell_join / command_mentions_codex)
;; ---------------------------------------------------------------------------

(deff shell-quote [value]
  {:pre [(: value str)]
   :post [(: % str)]}
  "oracle shell_quote: 空は ''、安全文字([a-zA-Z0-9] と -_./:=@,%+)のみは
   素通し、それ以外は single-quote(埋め込み ' は '\\'' でエスケープ)。"
  (if (= value "")
      "''"
      (do
        (setv safe (all (gfor c value
                              (or (.isalnum c) (in c "-_./:=@,%+")))))
        (if safe
            value
            (+ "'" (.replace value "'" "'\\''") "'")))))

(deff shell-join [args]
  {:pre [(: args list)]
   :post [(: % str)]}
  "argv → tmux pane に流す 1 行 shell command(oracle shell_join)。"
  (.join " " (lfor a args (shell-quote a))))

(deff command-mentions-codex [command]
  {:pre [(: command str)]
   :post [(: % bool)]}
  "明示 command が codex を起動するか(oracle command_mentions_codex:
   whitespace token が `codex` そのもの、または `/codex` で終わる。
   `codexify` のような部分文字列は数えない)。"
  (bool (any (gfor token (.split command)
                   (or (= token "codex") (.endswith token "/codex"))))))


;; ---------------------------------------------------------------------------
;; wait-for-launch-ready(oracle wait_for_repl_idle の後継 — R9 launch dialog +
;; 貼り付け可能性 probe + 不成立の閉語彙分類。ADR-DOE-AGENTS-011)
;; ---------------------------------------------------------------------------

(deff retain-frame [frames at-seconds output]
  {:pre [(: frames list) (: at-seconds (| int float)) (: output str)]
   :post [(: % list)]}
  "証拠 frame の有界保持(ADR-DOE-AGENTS-011 R-evidence-frames-9c17)。
   保持は先頭(起動直後の画面)を固定し、以降は最新側を入れ替える —
   『最初にどう見えたか』と『最後にどう見えたか』の両方が残る形。
   保持数 READY-GATE-FRAME-RETENTION で有界(旧実装は最終 1 枚のみ)。
   同一 tail の連続は積まない(同じ画面で枠を埋めない)。
   末尾の空行は tail を取る前に落とす: 旧実装は capture の素の末尾 15 行を
   残していたため、shell echo だけの画面(実測 294 件)の証拠が『改行 14 個』
   になり、描画ゼロと区別できなかった(実測 23 件が同じ 14 改行の形)。"
  (setv lines (.splitlines output))
  (while (and lines (= (.strip (get lines -1)) ""))
    (.pop lines))
  (setv tail (.join "\n" (cut lines (- READY-GATE-FRAME-LINES) None)))
  (setv frame (PaneFrame :at-seconds (float at-seconds) :text tail))
  (cond
    (and frames (= (. (get frames -1) text) tail))
      (+ (cut frames 0 -1) [frame])
    (< (len frames) READY-GATE-FRAME-RETENTION)
      (+ frames [frame])
    True
      (+ (cut frames 0 1) (cut frames 2 None) [frame])))


(defk wait-for-launch-ready [agent-type pane-id max-wait-seconds]
  {:pre [(: agent-type str) (: pane-id str)
         (: max-wait-seconds (| int float)) (> max-wait-seconds 0)]
   :post [(: % ReadyGateVerdict)]}
  "prompt を配送してよい状態まで poll する(ADR-DOE-AGENTS-011 の 3 条件):
     (1) idle prompt が可視 — codex は banner + MCP ロードの後にしか input
         loop が配線されない(oracle 実障害)。R9 launch dialog(codex-update /
         bypass / fullscreen / trust / managed)は idle 判定より先に検出して
         決定的 keys で dismiss する(update dialog は `›` 選択 marker を
         描くため idle と誤認される)。
     (2) composer が空 — 内容が座ったままの入力欄へ重ね貼りしない。
     (3) 貼り付け可能性 — 短い probe を bracketed paste し、composer が
         それを消費した(領域が変化した)ことと、消去キーで空へ戻ることを
         観測する。『入力欄が描かれた』は reader が我々の byte を消費して
         いる証拠ではない: 実測 2026-08-11 の 25 席は idle prompt を見て
         即 paste した prompt が collapsed chip として座り(12 席は chip
         3〜10 個 = 1 回の bracketed paste の断片化着弾)、Enter は断片の
         隙間に食われて turn が一度も始まらなかった。
   予算切れ・probe 不成立は ready=False + 閉語彙 failure-class + 証拠 frame
   を返すだけ — 呼び手(launch-session)がこれを fail-closed の typed error に
   する(2026-07-07 契約修正。旧 oracle は構わず送出していたが、R9 外の未知
   dialog に prompt が送出されて silent hang になる実障害 — trust dialog —
   がそれで隠れた)。probe の送出も含めて全局面が同じ max-wait-seconds 予算の
   内側 — 起動段の壁時計は伸びない(ACP ADR 0042 R8 の順序不変量
   ready gate 120s < orphan grace 150s < launch deadline 180s を保つ)。"
  (<- start (clock-now))
  (setv frames [])
  (setv polls 0)
  (setv elapsed 0.0)
  ;; 局面: "idle"(idle + 空 composer 待ち)→ "consume"(probe の消費待ち)
  ;; → "clear"(probe の消去待ち)→ ready。
  (setv phase "idle")
  (setv verdict None)
  (setv last-output "")
  (setv last-obs (PaneObservation))
  (setv looping True)
  (while looping
    (<- now (clock-now))
    (setv elapsed (.total-seconds (- now start)))
    (if (>= elapsed max-wait-seconds)
        (setv looping False)
        (do
          (<- output (tmux-capture pane-id 60))
          (<- obs (classify-pane agent-type output))
          (setv polls (+ polls 1))
          (setv last-output output)
          (setv last-obs obs)
          (setv frames (retain-frame frames elapsed output))
          (setv probe-visible
                (and (is-not obs.composer-text None)
                     (or (in READY-PROBE-TEXT obs.composer-text)
                         obs.has-unsubmitted-paste)))
          (cond
            ;; R9 既知 dialog は決定的キーで dismiss(局面を問わず先に処理)。
            (is-not obs.dialog None)
              (do
                (for [key obs.dialog-dismiss-keys]
                  (<- _ (tmux-send-keys pane-id key False False)))
                (<- _ (clock-sleep DIALOG-REDRAW-SECONDS)))
            ;; provider 上限告知は起動段の失敗ではなく枠の話 — 即 loud で
            ;; failover(ACP ADR 0049)へ渡す。
            obs.has-api-limit-marker
              (do
                (setv verdict "provider-limit-screen")
                (setv looping False))
            ;; CLI が resume の会話を解決できず loud 終了した画面(ADR-006 R10
            ;; 第 2 層)— 待って解ける命題ではないので予算を待たず即終端する。
            ;; 実測 2026-08-16〜17: この形 91 件が全件 120s 待って
            ;; no-agent-frame に誤分類されていた。
            obs.has-conversation-not-found-marker
              (do
                (setv verdict "conversation-not-found")
                (setv looping False))
            ;; R9 の外の dialog 形は dismiss を推測しない — 即 loud。
            ;; idle prompt の有無で条件を緩めない: codex の trust dialog は
            ;; 選択 marker が `›` なので idle prompt 判定を通過してしまう
            ;; (verbatim capture codex_trust_dialog.txt)。
            obs.dialog-shaped
              (do
                (setv verdict "unknown-dialog")
                (setv looping False))
            (= phase "idle")
              (cond
                (and obs.input-loop-wired (not obs.composer-clear))
                  (do
                    ;; 誰も掃除しない未送信内容 — 待っても解けないので即 loud。
                    (setv verdict "composer-occupied")
                    (setv looping False))
                obs.input-loop-wired
                  (do
                    (<- _ (tmux-send-keys pane-id READY-PROBE-TEXT True False))
                    (setv phase "consume")
                    (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS)))
                True
                  (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS)))
            (= phase "consume")
              (if probe-visible
                  (do
                    ;; probe が composer に見えた = reader が我々の byte を
                    ;; 消費している(literal 形 / collapsed chip 形の両方を
                    ;; 消費の証拠として受ける)。消去キーは probe の文字数ぶん
                    ;; (chip 形なら 1 打で消え、残りは空 composer への no-op)。
                    (for [_ (range (len READY-PROBE-TEXT))]
                      (<- _ (tmux-send-keys pane-id READY-PROBE-CLEAR-KEY False False)))
                    (setv phase "clear")
                    (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS)))
                  (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS)))
            (= phase "clear")
              (if (and obs.composer-clear (not probe-visible))
                  (do
                    (setv verdict None)
                    (setv phase "ready")
                    (setv looping False))
                  (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS)))
            True
              (<- _ (clock-sleep REPL-IDLE-POLL-SECONDS))))))
  ;; 予算切れの分類: probe 局面で切れたならその局面が答え、idle 局面で
  ;; 切れたなら最後の全 capture の事実束から分類器(policy 所有)が答える。
  ;; 分類の入力は保持 frame(tail)ではなく capture 全体 — tail だけを見ると
  ;; shell echo が窓の外へ出て『描画ゼロ』と誤分類する。
  (setv failure-class
        (cond
          (= phase "ready") None
          (is-not verdict None) verdict
          (= phase "consume") "paste-not-consumed"
          (= phase "clear") "composer-not-clearable"
          True (launch-not-ready-class last-obs last-output)))
  (ReadyGateVerdict :ready (is failure-class None)
                    :failure-class failure-class
                    :frames (tuple frames)
                    :polls polls
                    :elapsed-seconds elapsed))


;; ---------------------------------------------------------------------------
;; launch program 本体(oracle session_launch の凍結順序)
;; ---------------------------------------------------------------------------

(defk link-workspace-sibling-deps [repo workspaces-root]
  {:pre [(: repo str) (: workspaces-root str)]
   :post [(: % "None — 不適合は raise")]}
  "workspace root へ cabal.project の sibling 依存(../<name> 形)を symlink で
   再現する(ACP engine linkProjectPathDependencies の host 側移植 — worktree の
   相対 path 依存が root/<name> を指すため、鏡が無いと impl/repair session は
   build/test 不能で盲走する)。manifest 不在は no-op。宣言された依存の実体
   不在は loud。既設 link は same-entity なら no-op、別実体への衝突は loud
   (第 1 波は非破壊 — engine 版の『古い symlink を張り替える』自己修復は
   持たない。衝突が実測されたら第 2 波で判断)。"
  (<- manifest (fs-read-text (os.path.join repo "cabal.project")))
  (when (is manifest None)
    (return None))
  (for [token (.split manifest)]
    (when (and (.startswith token "../")
               (> (len token) 3)
               (not-in "/" (cut token 3 None)))
      (setv sibling-name (cut token 3 None))
      (setv target (os.path.normpath (os.path.join repo token)))
      (<- target-is-dir (fs-dir-exists target))
      (when (not target-is-dir)
        (raise (RuntimeError
                 (+ f"workspace seed: cabal.project path dependency {token} "
                    f"expects directory {target} on this machine — absent"))))
      (<- outcome (fs-link-artifact target
                                    (os.path.join workspaces-root sibling-name)))
      (when (not-in outcome #{"linked" "same-entity"})
        (raise (RuntimeError
                 (+ f"workspace seed: sibling link {sibling-name} -> {target} "
                    f"failed ({outcome}) — 非破壊方針につき既存物は触らない"))))))
  None)


;; ---------------------------------------------------------------------------
;; ACP ADR 3d81bd — branch 形の実体化の前段: 同名 branch を既に持つ古い
;; worktree の解除(engine ローカル腕 Acp.App.Workspace.releaseStaleBranchHolder
;; の host 側の双子)。
;;
;; branch 名は作業ごとに固定(feat/<workflow>-<issue> — argus は責務ごと 1 本)
;; なので、解除なしでは同じ作業の 2 回目以降の実体化が必ず前回の worktree と
;; 衝突する。第 1 波(fc66c2da)はこれを持たずに出荷し、engine の回収係は pod
;; に居て host の disk に届かないため、stale holder は自然には消えなかった —
;; 反例 = 2026-08-23 番人 31 席中 25 席が丸 1 日 "branch … is already used by
;; worktree at <前回の inv dir>" で起動段に latch(acp-sandbox の worktree 19 本)。
;;
;; 判断と実行の分担(law resolved-materialization を解除にも適用): 解除して
;; よいか(release_stale_holder)と engine が生きていると見なす作業場の集合
;; (protected_dirs = Running invocation の workdir)は engine が data で送り、
;; 実行はこの host が行う。host は engine の名指しに自機の実在知識を union する
;; (ADR-DOE-AGENTS-006 R10 と同じ「名指しは呼び手・検査は受け手」の分担):
;;   ① engine の protected_dirs に在る holder は畳まない(live agent の checkout
;;      か、失効復帰 resume〔ACP ADR a64d9d〕が起こされる予定の作業場)
;;   ② この host の active session(pending/booting/running/blocked/blocked_api)
;;      の work_dir は畳まない(物理に使用中)
;;   ③ seed の置き場(dir の親 = workspaces root)の配下に無い holder は畳まない
;;      (人の worktree・他の置き場 — この host が実体化した覚えの無い物)
;;   ④ 所有 marker が在って別の workspacesRoot を名乗る holder は畳まない
;;      (他 control plane の所有 — ACP ADR 0058 R2 の「reap の権威は marker」)。
;;      marker 無しで置き場配下なら自前の実体化途中の残骸として畳む。
;; どれか 1 つでも欠ければ畳まず、続く worktree add の実失敗を理由つきで loud
;; に返す(engine 腕の「protected holder は no-op で caller が loud」と同じ)。
;; 鍵(release_stale_holder)の無い seed = 本 ADR より前の engine からの seed は
;; 第 1 波どおり解除しない(配備順序に依存して挙動が変わらない)。
;; ---------------------------------------------------------------------------

(defk parse-worktree-list-porcelain [text]
  {:pre [(: text str)]
   :post [(: % list)]}
  "`git worktree list --porcelain` の出力 → [{path, branch|None, detached,
   prunable}] の list。stanza は空行区切り・先頭行 `worktree <path>`・
   `branch refs/heads/<name>` か `detached`・`prunable …`(dir 消失など)。
   未知の行は無視(git の版差に寛容)。"
  (setv entries [])
  (setv current None)
  (for [raw (.splitlines text)]
    (setv line (.rstrip raw))
    (cond
      (.startswith line "worktree ")
        (do
          (when (is-not current None)
            (.append entries current))
          (setv current {"path" (cut line (len "worktree ") None)
                         "branch" None
                         "detached" False
                         "prunable" False}))
      (is current None) None
      (.startswith line "branch refs/heads/")
        (setv (get current "branch") (cut line (len "branch refs/heads/") None))
      (= line "detached")
        (setv (get current "detached") True)
      (.startswith line "prunable")
        (setv (get current "prunable") True)
      True None))
  (when (is-not current None)
    (.append entries current))
  entries)


(defk find-branch-holder [entries branch]
  {:pre [(: entries list) (: branch str)]
   :post [(: % "dict | None")]}
  "entries のうち branch を checkout している worktree の entry(無ければ None)。
   git は 1 branch を同時に 1 worktree にしか許さないので高々 1 件。"
  (setv found None)
  (for [entry entries]
    (when (and (is found None) (= (.get entry "branch") branch))
      (setv found entry)))
  found)


(defk path-inside-root? [path root]
  {:pre [(: path str) (: root str)]
   :post [(: % bool)]}
  "path が root の配下(root 自身は含まない)か — 文字列正規化のみ(IO なし)。"
  (setv p (os.path.normpath path))
  (setv r (os.path.normpath root))
  (and (!= p r) (.startswith p (+ r os.sep))))


(defk workspace-seed-owner-marker-root [holder marker-name]
  {:pre [(: holder str) (: marker-name "str | None")]
   :post [(: % "str | None")]}
  "holder の所有 marker(ACP 0058 R2 の .acp-owner.json 形)が名乗る
   workspacesRoot。marker 名が無い / file 不在 / JSON でない / 欄が無い は None
   (= 所有の証明なし)。"
  (when (is marker-name None)
    (return None))
  (<- text (fs-read-text (os.path.join holder marker-name)))
  (when (is text None)
    (return None))
  (setv parsed None)
  (try
    (setv parsed (json.loads text))
    (except [Exception]
      (setv parsed None)))
  (when (not (isinstance parsed dict))
    (return None))
  (setv root (.get parsed "workspacesRoot"))
  (if (isinstance root str) root None))


(defk host-active-workdirs []
  {:pre []
   :post [(: % set)]}
  "この host で active な session(launch / adopt を問わず)の work_dir の集合 —
   物理に使用中の作業場。空 work_dir(adopt 行の既定)は数えない。"
  (<- rows (session-store-list-active))
  (set (lfor r rows
             :if (and (isinstance r.work-dir str) (> (len r.work-dir) 0))
             (os.path.normpath r.work-dir))))


(defk release-stale-branch-holder [seed]
  {:pre [(: seed dict)]
   :post [(: % dict)]}
  "branch 形 seed の前段: seed.branch を既に持つ古い worktree を、安全側の
   4 条件(上の ①〜④)を全て満たす時だけ `git worktree remove --force` で
   畳む。戻り値 {\"released\" path|None, \"refused\" {path, reason}|None}。
   remove 自体の失敗は loud(raise)。holder の dir が既に消えて登録だけが
   残る形(locked 印などで prune が拾えない)は unlock → 再 prune で畳む
   (engine 腕の structural backstop の git 語彙版 — .git の外科手術はしない)。"
  (setv repo (get seed "repo"))
  (setv dir (os.path.normpath (get seed "dir")))
  (setv branch (get seed "branch"))
  (setv workspaces-root (os.path.dirname dir))
  (setv protected (set (lfor p (or (.get seed "protected_dirs") [])
                             (os.path.normpath p))))
  (setv marker (.get seed "owner_marker"))
  (setv marker-name (if (is-not marker None) (.get marker "name") None))
  ;; 1) 登録だけ残った worktree(dir が手で消された等)を先に畳む。
  (<- _ (git-run repo ["worktree" "prune" "--expire" "now"]))
  ;; 2) branch の holder を探す。
  (<- listed (git-run repo ["worktree" "list" "--porcelain"]))
  (when (!= (get listed "code") 0)
    (return {"released" None
             "refused" {"path" None
                        "reason" (+ "git worktree list failed: "
                                    (get listed "stderr"))}}))
  (<- entries (parse-worktree-list-porcelain (get listed "stdout")))
  (<- holder (find-branch-holder entries branch))
  (when (is holder None)
    (return {"released" None "refused" None}))
  (setv holder-path (os.path.normpath (get holder "path")))
  (when (= holder-path dir)
    ;; 自分の dir の登録が残っていて branch も持っている = 再 launch の形。
    ;; 呼び手(materialize)は dir 実在なら skip、不在ならここを通る — prune
    ;; 済みなので dir 不在ならもう一覧に居ない。居るなら add が扱う。
    (return {"released" None "refused" None}))
  ;; 3) 安全側の 4 条件。
  (when (in holder-path protected)
    (return {"released" None
             "refused" {"path" holder-path
                        "reason" "held by a live invocation (engine protected set)"}}))
  (<- active (host-active-workdirs))
  (when (in holder-path active)
    (return {"released" None
             "refused" {"path" holder-path
                        "reason" "held by an active session on this host"}}))
  (<- inside (path-inside-root? holder-path workspaces-root))
  (when (not inside)
    (return {"released" None
             "refused" {"path" holder-path
                        "reason" (+ "outside the seed's workspaces root "
                                    f"{workspaces-root}")}}))
  (<- owner-root (workspace-seed-owner-marker-root holder-path marker-name))
  (when (and (is-not owner-root None)
             (!= (os.path.normpath owner-root) (os.path.normpath workspaces-root)))
    (return {"released" None
             "refused" {"path" holder-path
                        "reason" (+ "owned by another control plane root "
                                    f"{owner-root}")}}))
  ;; 4) 畳む。dir が実在すれば remove --force、登録だけなら unlock → 再 prune。
  (<- holder-exists (fs-dir-exists holder-path))
  (if holder-exists
      (<- res (git-run repo ["worktree" "remove" "--force" holder-path]))
      (do
        (<- _ (git-run repo ["worktree" "unlock" holder-path]))
        (<- res (git-run repo ["worktree" "prune" "--expire" "now"]))))
  (when (!= (get res "code") 0)
    (setv release-err (get res "stderr"))
    (raise (RuntimeError
             (+ f"workspace seed: releasing stale holder {holder-path} of branch "
                f"{branch} failed: {release-err}"))))
  {"released" holder-path "refused" None})


(defk materialize-workspace-seed [seed]
  {:pre [(: seed dict)]
   :post [(: % "None — 失敗は raise")]}
  "workspace seed(ACP W2 — law resolved-materialization)の実体化。launcher
   (ACP scheduler)は判断(dir / repo / branch|detach / pin sha / 所有 marker /
   解除の可否と保護集合)を data で送り、worktree はセッションの走る機械 =
   この host が作る。launcher 側 twin は存在しない(pod に namespace repo が
   無い実測 2026-08-21)。冪等: dir 実在は skip(再 launch 互換)。branch 形は
   seed.release_stale_holder が真なら同名 branch の古い worktree を安全側の
   条件つきで先に畳む(ACP ADR 3d81bd — release-stale-branch-holder)。鍵の
   無い seed は非破壊(衝突は loud)のまま。"
  (setv dir (get seed "dir"))
  (<- dir-exists (fs-dir-exists dir))
  (when dir-exists
    (return None))
  (setv repo (get seed "repo"))
  (<- repo-exists (fs-dir-exists repo))
  (when (not repo-exists)
    (raise (RuntimeError
             (+ f"workspace seed: source repo does not exist on this machine: "
                f"{repo}"))))
  (setv workspaces-root (os.path.dirname dir))
  (<- _ (fs-make-dirs workspaces-root))
  (when (bool (.get seed "link_siblings"))
    (<- _ (link-workspace-sibling-deps repo workspaces-root)))
  ;; pin sha の実在 — 不在なら fetch を 1 回だけ試す(それでも無ければ loud)。
  (setv sha (.get seed "sha"))
  (when (is-not sha None)
    (<- probe (git-run repo ["rev-parse" "--verify" "--quiet"
                             (+ sha "^{commit}")]))
    (when (!= (get probe "code") 0)
      (<- _ (git-run repo ["fetch" "--all" "--quiet"]))
      (<- probe2 (git-run repo ["rev-parse" "--verify" "--quiet"
                                (+ sha "^{commit}")]))
      (when (!= (get probe2 "code") 0)
        (raise (RuntimeError
                 (+ f"workspace seed: pinned sha {sha} is not known to {repo} "
                    "even after one fetch — refusing to guess a head"))))))
  (setv mode (get seed "mode"))
  ;; branch 形の前段: 同名 branch の stale holder の解除(ACP ADR 3d81bd)。
  ;; detached 形は branch を持たないので解除の対象が無い。
  (setv refused None)
  (when (and (= mode "branch") (bool (.get seed "release_stale_holder")))
    (<- outcome (release-stale-branch-holder seed))
    (setv refused (get outcome "refused")))
  ;; worktree add(mode は host admission 済みの 2 語彙)。
  (setv argv
        (if (= mode "detached")
            ["worktree" "add" "--detach" dir sha]
            (+ ["worktree" "add" "-B" (get seed "branch") dir]
               (if (is-not sha None) [sha] []))))
  (<- res (git-run repo argv))
  (when (!= (get res "code") 0)
    (setv err (get res "stderr"))
    (setv why "")
    (when (is-not refused None)
      (setv refused-path (get refused "path"))
      (setv refused-reason (get refused "reason"))
      (setv why (+ f" — stale holder {refused-path} was not released: "
                   f"{refused-reason}")))
    (raise (RuntimeError
             (+ f"workspace seed: git worktree add failed for {dir} "
                f"(mode={mode}): {err}{why}"))))
  ;; 所有 marker(内容は launcher が data で送る — 形式の所有は engine 側)。
  (setv marker (.get seed "owner_marker"))
  (when (is-not marker None)
    (<- _ (fs-write-text-atomic (os.path.join dir (get marker "name"))
                                (get marker "content_text")
                                ".marker-tmp")))
  None)


(defk launch-session [params]
  {:pre [(: params dict)]
   :post [(: % SessionRow)]}
  "1 session の launch。params(oracle LaunchParams + R7 binding):
   session_id / session_name / agent_type / work_dir / lifecycle /
   binding(typed auth/profile 構成 — ADR-DOE-AGENTS-004 R7)/
   session_env(非 auth overlay)/ prompt / command(明示 override、
   escape hatch)/ expected_result / model / effort / mcp_servers /
   socket_path / skip_trust_setup。戻り値: 永続化済みの booting SessionRow。"
  (setv session-id (get params "session_id"))
  (setv session-name (get params "session_name"))
  (setv agent-type (get params "agent_type"))
  (setv lifecycle (get params "lifecycle"))
  (setv binding (.get params "binding"))
  (setv session-env (.get params "session_env" {}))
  (setv command-override (or (.get params "command") ""))
  (setv has-override (bool (.strip command-override)))
  (setv expected-result (.get params "expected_result"))

  ;; --- R7 admission(純粋検査 — 全副作用より前): auth は typed binding で
  ;; 運び、session_env は非 auth overlay。binding 所有キーの overlay 混入は
  ;; 裏口(2026-07 まで合成 CODEX_HOME がここを通っていた)なので typed reject。
  (setv binding-error (binding-admission-error binding agent-type))
  (when (is-not binding-error None)
    (raise (RuntimeError f"session.launch: invalid binding — {binding-error}")))
  (setv offenders (overlay-env-offenders session-env))
  (when offenders
    (raise (RuntimeError
             (+ "session.launch: session_env is a non-auth overlay and may not "
                f"carry binding-owned auth env (offending: {(.join ", " offenders) }). "
                "Declare the auth profile through the typed `binding` field "
                "(ADR-DOE-AGENTS-004 R7)."))))
  ;; 従量課金 credential は binding 所有キーと違い「正しい家」が無い — どの
  ;; 経路でも受けない(operator 裁定 2026-08-26。resume も本関所を通る)。
  (<- metered-offenders (metered-credential-env-offenders session-env))
  (when metered-offenders
    (raise (RuntimeError
             (+ "session.launch: metered-billing credentials are forbidden in "
                f"agent sessions (offending: {(.join ", " metered-offenders) }). "
                "Agent seats authenticate with subscription profiles via the "
                "typed `binding` field only (operator ruling 2026-08-26)."))))

  ;; --- admission(oracle 順序: lifecycle → 重複 → 既存 tmux)。
  (when (not-in lifecycle #{LIFECYCLE-RUN-TO-COMPLETION LIFECYCLE-INTERACTIVE})
    (raise (RuntimeError
             (+ f"unsupported session lifecycle: {lifecycle} "
                f"(expected {LIFECYCLE-RUN-TO-COMPLETION} or {LIFECYCLE-INTERACTIVE})"))))
  (<- existing (session-store-get session-id))
  (when (is-not existing None)
    (raise (RuntimeError f"session is already registered: {session-id}")))
  ;; max_running admission(oracle :1679-1685 — 重複 check の後・tmux check の
  ;; 前)。host が params["max_running"] に運用上限を注入する。直接束縛では
  ;; 省略 = 無制限(config を持たない)。母数 = launch 所有の行のみ
  ;; (ADR-DOE-AGENTS-004 capacity-counts-only-launch-owned-rows): adopt の
  ;; 行は観測の登記であって容量消費ではない — 終端遷移の書き手を持たず単調
  ;; 増加するため、全行母数は launch の恒久 100% 拒否になる(2026-08-17
  ;; 実測 32 ≥ 10)。拒否文言の先頭逐語は ACP Scheduler の throttle 分類
  ;; (infix 照合)が消費する凍結面 — 変更禁止。
  (setv max-running (.get params "max_running"))
  (when (is-not max-running None)
    (<- active-rows (session-store-list-active))
    (setv owned-count
          (len (lfor r active-rows :if (counts-toward-launch-capacity r) r)))
    (when (>= owned-count max-running)
      (raise (RuntimeError
               (+ f"max running agent sessions reached: {owned-count}/{max-running} "
                  "(launch-owned rows only; adopted rows are observations, "
                  "not capacity — ADR-DOE-AGENTS-004)")))))
  (<- tmux-exists (tmux-has-session session-name))
  (when tmux-exists
    (raise (RuntimeError f"tmux session already exists: {session-name}")))

  ;; --- workspace seed の実体化(ACP W2 — law resolved-materialization)。
  ;; work_dir 検証より前でなければならない(worktree はこれから生える)。
  ;; 純粋 admission(lifecycle / 重複 / capacity / tmux)より後 = 拒否される
  ;; launch のために worktree を作らない。
  (setv workspace-seed (.get params "workspace_seed"))
  (when (is-not workspace-seed None)
    (<- _ (materialize-workspace-seed workspace-seed)))

  ;; --- work_dir の物理実在(ADR-DOE-AGENTS-006 R10 — 全副作用より前)。
  ;; tmux は不在 start-directory を黙って $HOME に差し替える: pane は
  ;; `CA-…:~` の shell で立ち、claude の cwd 鍵会話解決・trust pre-seed 鍵・
  ;; transplant 敷設鍵のすべてが実際の cwd と食い違う(2026-08-16〜17 実測
  ;; 91 件 — 全件 120s 予算切れ no-agent-frame の無名の死に化けた)。
  (setv work-dir (get params "work_dir"))
  (<- work-dir-present (fs-dir-exists work-dir))
  (when (not work-dir-present)
    (raise (RuntimeError
             (+ f"session.launch: work_dir does not exist: {work-dir} — "
                "tmux would silently fall back to $HOME and every cwd-keyed "
                "physical (claude conversation lookup, trust pre-seed, "
                "transcript transplant) would target the wrong project "
                "(ADR-DOE-AGENTS-006 R10)"))))

  ;; --- context file の実体化(law context-file-rides-the-wire — ACP steward
  ;; W1b 2026-08-20)。launcher(ACP scheduler)が workdir へ直接書く旧法は、
  ;; launcher と session host が別機械に分かれた瞬間に壊れた(pod 側の write が
  ;; Mac にしか無い workdir を指す — 実測 368 launch 失敗/2h)。以後 file は
  ;; wire(context_file: {path, content})で運び、session の走る機械 = この
  ;; host が spawn 前に書く。path の裸ファイル名制約は host admission 所有
  ;; (build-launch-program-params)。work_dir 実在検査の直後 = 旧 launcher の
  ;; 書き順(残りの admission reject より前に file が実在する)と同位置。
  (setv context-file (.get params "context_file"))
  (when (is-not context-file None)
    (<- _ (fs-write-text-atomic
            (os.path.join work-dir (get context-file "path"))
            (json.dumps (get context-file "content") :ensure-ascii False)
            ".ctx-tmp")))

  ;; --- ADR 0035 reject-at-launch: result channel を配線できない agent が
  ;; result contract を持つことは受けない(silent timeout の予約になる)。
  (when (and (is-not expected-result None)
             (not has-override)
             (not-in agent-type INTERACTIVE-AGENT-TYPES))
    (raise (RuntimeError
             (+ f"session.launch: agent_type '{agent-type}' cannot deliver a "
                "result over the report_result channel; a result contract "
                "requires agent_type 'codex' or 'claude' (or an explicit "
                "`command` that reports results itself)"))))

  ;; --- per-kind PreLaunchSetup(S11 auth gate + trust、必ず tmux より前)。
  ;; 明示 command が codex を起動する場合も codex の gate/trust が効く
  ;; (oracle command_mentions_codex — 暗黙 ~/.codex が個人クォータを焼いた
  ;; 実障害)。
  (setv prelaunch-kind
        (cond
          (in agent-type INTERACTIVE-AGENT-TYPES) agent-type
          (command-mentions-codex command-override) "codex"
          True None))
  (setv identity None)
  (setv minted-conversation None)
  (when (is-not prelaunch-kind None)
    (<- resolved (pre-launch-setup prelaunch-kind params))
    ;; warnings は運用ログ向けの副産物(host が stderr へ出す)— 永続する
    ;; identity(S14 の effective_identity 列)は auth home の解決結果のみ。
    ;; conversation(ADR-006: claude が launch 時に鋳造する会話 identity)は
    ;; identity 列ではなく conversation 列の住人なのでここで分離する。
    (setv identity (dict resolved))
    (.pop identity "warnings" None)
    (setv minted-conversation (.pop identity "conversation" None)))
  (setv resume-context (.get params "resume_context"))

  ;; --- session hook 配布の宣言(2026-08-18 ACP 起動会話の安全 hook 全滅の
  ;; 根治 — route-c03fe34745)。daemon env knob DOEFF_AGENTD_SESSION_HOOKS を
  ;; use-site で読む(monitor knob と同じ流儀):
  ;;   未設定 / "disabled" = 従来物理(claude argv に
  ;;     --settings {"disableAllHooks":true} — 49b3549b 傷跡の既定を変えない)
  ;;   "inherit" = その pair を argv から外し、config-dir 所有者の hook 層へ
  ;;     委ねる(hook 層は下の AGENT_SESSION_CLASS で会話種別 self-gate する契約)
  ;; 語彙外は fail-loud: 黙った綴り違いは「安全 hook 全滅」を無言で復活させる。
  (<- session-hooks-raw (env-get "DOEFF_AGENTD_SESSION_HOOKS"))
  (setv session-hooks (or session-hooks-raw "disabled"))
  (when (not-in session-hooks #{"disabled" "inherit"})
    (raise (RuntimeError
             (+ f"session.launch: DOEFF_AGENTD_SESSION_HOOKS='{session-hooks-raw}' "
                "is not in the vocabulary {disabled, inherit} — refusing to "
                "guess whether agent sessions receive the config-dir owner's "
                "hooks (a silent typo here would silently re-disable the "
                "safety hooks)"))))

  ;; --- result channel 配線 + 起動 command(oracle resolve_launch_command:
  ;; override は verbatim、それ以外は per-kind argv builder)。
  (setv command-line command-override)
  (when (not has-override)
    (setv effective-params (dict params))
    (setv (get effective-params "session_hooks") session-hooks)
    (when (and (is-not expected-result None)
               (in agent-type INTERACTIVE-AGENT-TYPES))
      (<- channel (wire-result-channel agent-type session-id
                                       (.get params "socket_path" "")))
      (setv (get effective-params "result_channel") channel))
    ;; ADR-006 R3: incarnation の宿し(この program)は 1 本のまま、argv 構築
    ;; だけを fresh launch / resume / fork で分岐する。
    (if (is resume-context None)
        (do
          (when (and (= agent-type "claude") (is-not minted-conversation None))
            (setv (get effective-params "conversation") minted-conversation))
          (<- argv (build-launch agent-type effective-params)))
        (do
          (setv (get effective-params "resume_mode") (get resume-context "mode"))
          (setv (get effective-params "conversation")
                (get resume-context "conversation"))
          (<- argv (build-resume agent-type effective-params))))
    (setv command-line (shell-join argv)))

  ;; --- tmux session 作成(禁止 env reject は substrate 所有)+ 起動。
  ;; 実効 env = 非 auth overlay ∪ binding 由来 auth env(R7: auth の合成は
  ;; per-kind impl の解決した identity が唯一の源 — overlay は admission で
  ;; 所有キーを締め出し済みなので衝突は構造的に無い)。
  (setv binding-env
        (if (is identity None)
            {}
            (dfor [k v] (.items identity)
                  :if (and (in k BINDING-OWNED-ENV-KEYS) (isinstance v str))
                  k v)))
  ;; AGENT_SESSION_CLASS: agentd 起動の会話は無人(unattended)であることを
  ;; env で宣言する(hook 層の会話種別 self-gate 契約の相方 — 上の session
  ;; hook 註)。caller overlay の明示があればそちらを尊重する。
  (setv effective-env {"AGENT_SESSION_CLASS" "unattended"
                       #** session-env #** binding-env})
  (<- pane-id (tmux-new-session session-name (get params "work_dir") effective-env))

  ;; --- booting 行の登録(tmux-new-session 直後・ready 待ちの前 — issue
  ;; agentd-session-registration-after-ready-gate)。登録は TUI readiness に
  ;; 依存しない簿記: 物理 session が生まれた瞬間に外部から観測可能でなければ、
  ;; cold start の ready 待ち(最大 120s)の間 session は誰にも見えず、
  ;; 60s handshake を仮定する外部監視から orphan に見える(mediagen engine、
  ;; 2026-07-14 実測)。以降の失敗は行を terminal へ遷移させる(リークでは
  ;; なくライフサイクル)。実効 identity 込み — S14 の Hy positive 化。
  ;; work-dir / backend-ref は store-of-record が行作成に要る launch 所有
  ;; field(oracle backend_ref = session_name / pane_id / command)。
  ;; ADR-006: 会話 identity の初期値 — resume は親会話(事前確定)、fork は
  ;; None(CLI が新 ID を鋳造 → DiscoverConversation で事後発見)、fresh の
  ;; claude は鋳造済み UUID(--session-id 注入と同値)、fresh の codex /
  ;; 明示 command は None(事後発見)。
  (setv row-conversation
        (cond
          (is-not resume-context None)
            (if (= (get resume-context "mode") "resume")
                (get resume-context "conversation")
                None)
          (and (= agent-type "claude") (not has-override)) minted-conversation
          True None))
  ;; awaiting latch は登録時点から武装する(prompt を配送する launch のみ)。
  ;; latch の意味は「agent への prompt が owed — 見かけの turn-end を評価するな」
  ;; であり、配送中の窓も含む。conformance の await_monitor_ack(行が存在 &&
  ;; latch クリア)はこれを配送完了 + 監視引き継ぎの同期点として使う — 登録が
  ;; 先行しても latch が先に武装されていれば ack が早発しない(S6 実測)。
  (setv prompt (or (.get params "prompt") ""))
  (setv awaiting (bool (.strip prompt)))
  (<- now (clock-now))
  (setv row (SessionRow
              :session-id session-id
              :session-name session-name
              :pane-id pane-id
              :agent-type agent-type
              :lifecycle lifecycle
              :status "booting"
              :started-at (iso-format now)
              :awaiting-response awaiting
              :expected-result expected-result
              :effective-identity identity
              :work-dir (get params "work_dir")
              :backend-kind (.get params "backend_kind" "tmux")
              :backend-ref {"session_name" session-name
                            "pane_id" pane-id
                            "command" command-line}
              ;; ADR-006: 非 auth の launch 意図を行に永続化(resume の復元源)。
              :launch-overlay {"session_env" session-env
                               "model" (.get params "model")
                               "effort" (.get params "effort")
                               "mcp_servers" (or (.get params "mcp_servers") {})}
              ;; 発注者申告の帰属 metadata(opaque verbatim — 解釈しない)。
              ;; overlay(launch 意図 = resume 復元源)とは別欄: これは
              ;; 「この走行がどの機能の仕事か」の出自申告で復元には使わない。
              :launch-attribution (.get params "launch_attribution")
              :conversation row-conversation
              :generation (if (is resume-context None)
                              1
                              (get resume-context "generation"))
              :resumed-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "resumed_from_session_id"))
              :forked-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "forked_from_session_id"))))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id "session_started" row))

  (when (.strip command-line)
    (<- _ (tmux-send-keys pane-id command-line True True)))

  ;; --- prompt の live REPL 配送(argv / print-mode 禁止 — session が task
  ;; 完了後も生き、monitor が validate / 再促せるように)。
  (when (.strip prompt)
    (setv full-prompt
          (if (is-not expected-result None)
              (+ prompt RESULT-PROTOCOL-INSTRUCTION)
              prompt))
    (when (and (not has-override) (in agent-type INTERACTIVE-AGENT-TYPES))
      ;; 予算は host 注入 knob(DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS、
      ;; max_running と同じ注入パターン)が優先、無ければ oracle 定数 120s。
      (setv repl-idle-max-wait
            (or (.get params "repl_idle_max_wait_seconds")
                REPL-IDLE-MAX-WAIT-SECONDS))
      (<- gate (wait-for-launch-ready agent-type pane-id repl-idle-max-wait))
      (when (not gate.ready)
        ;; fail-closed(2026-07-07 契約修正): idle 未達のまま paste すると
        ;; prompt が R9 外の未知 dialog に送出され session は silent hang に
        ;; なる(trust dialog 実障害)。paste せず、画面 tail を証拠として
        ;; 積んだ typed error で launch を fail させる。登録済みの booting 行は
        ;; terminal(failed / timed_out)へ遷移して残る —「誰にも観測されない
        ;; 行をリークさせない」保証のライフサイクル版(issue
        ;; agentd-session-registration-after-ready-gate、oracle main.rs と
        ;; 同一契約)。
        ;;
        ;; terminal-first(#542 レビュー由来の順序): FAILED 行の永続化
        ;; (+event)を tmux cleanup より先に — capture/kill の失敗が終端化を
        ;; スキップして booting 残置・typed error のマスクを生まないように。
        ;; capture は best-effort の証拠収集、cleanup 成否は cleaned-at の
        ;; 有無で表現(失敗時は NULL のまま・行は既に terminal)。
        ;; 証拠は gate が局面ごとに保持した frame 列(ADR-DOE-AGENTS-011
        ;; R-evidence-frames-9c17 — 受入条件 (g) の保持数増量)。追加 capture は
        ;; しない: 予算切れ後の 1 枚は既に別の画面かもしれず、gate が見た
        ;; ものを残すのが逐語性の要件。
        (setv screen-tail (format-evidence-frames gate.frames))
        (<- fail-now (clock-now))
        (setv gate-reason
              (launch-not-ready-reason agent-type repl-idle-max-wait
                                       gate.failure-class))
        (setv failed-row
              (replace row
                       :status "failed"
                       :finished-at (iso-format fail-now)
                       :last-observed-at (iso-format fail-now)
                       :output-snippet screen-tail
                       ;; last_validation_error にも同じ reason を置く: DB 面の
                       ;; 分類(受入条件 (e))はこの 2 列のどちらからでも同じ
                       ;; class token で 3 分できる。
                       :last-validation-error gate-reason
                       :terminal-cause
                         (make-cause (launch-not-ready-category
                                       gate.failure-class)
                                     gate-reason
                                     (iso-format fail-now))))
        (<- _ (session-store-upsert failed-row))
        (<- _ (session-store-record-event session-id "session_failed" failed-row))
        ;; cleanup は終端化の後: 失敗しても行は既に terminal — cleaned-at を
        ;; NULL のまま残すだけで、typed error は下でそのまま raise される。
        (setv cleanup-ok True)
        (try
          (<- _ (tmux-kill-session session-name))
          (except [kill-err Exception]
            (setv cleanup-ok False)))
        (when cleanup-ok
          (<- cleaned-now (clock-now))
          (setv failed-row (replace failed-row :cleaned-at (iso-format cleaned-now)))
          (<- _ (session-store-upsert failed-row)))
        ;; typed error の文言は「何が見えていたか」の分類を先頭に置く
        ;; (旧文言は毎回「unrecognized screen (a dialog outside the R9
        ;; fast-path set?)」と推測を断定していた — 実測 321 件中 0 件が
        ;; その形だった。誤った断定は steward の診断を毎回誤らせる)。
        ;; 「did not become ready」の逐語は保持する: conformance S18/S22 と
        ;; argus sensor(sensor_sessionhost_health.py)がこの substring で
        ;; 起動段の失敗を拾っている。
        (raise (RuntimeError
                 (+ f"session.launch: {agent-type} REPL did not become ready "
                    f"within {repl-idle-max-wait}s — launch ready gate failed "
                    f"[{gate.failure-class}] after {gate.polls} poll(s). "
                    "The prompt was NOT delivered; the session row was marked "
                    "failed (prompt_undelivered) and tmux cleanup was attempted "
                    f"(success is recorded as cleaned_at). Evidence frames "
                    f"({(len gate.frames)}):\n"
                    screen-tail)))))
    (if (in agent-type INTERACTIVE-AGENT-TYPES)
        (<- _ (deliver-message pane-id full-prompt))
        (<- _ (tmux-send-keys pane-id full-prompt True True))))

  ;; --- monitor への手渡し(launch pipeline 完了)。BOOTING は launch pipeline
  ;; 所有(monitor は boot watchdog 以外触らない — policy.hy booting 所有権 arm)
  ;; なので、配送完了をもって running を書き、以降の観測を monitor に引き渡す。
  ;; awaiting latch は登録時点から armed(上記)。行の存在は登録時点で確定済み。
  (setv row (replace row :status "running"))
  (<- _ (session-store-upsert row))
  row)


;; ---------------------------------------------------------------------------
;; resume / fork program(ADR-DOE-AGENTS-006 R4)
;; ---------------------------------------------------------------------------

(defclass ResumeRejected [RuntimeError]
  "session.resume / session.fork の typed reject(ADR-DOE-AGENTS-006 改訂
   R9)。code は wire の error_code に verbatim で載る安定語彙(effects.hy
   RESUME-ERR-*)— host dispatch が RpcHostError へ写し、機械消費者(ACP)は
   message substring ではなく code を照合する。message は後方互換で不変。"
  (defn __init__ [self code message]
    (.__init__ RuntimeError self message)
    (setv self.code code)))


(deff strip-incarnation-suffix [value]
  {:pre [(: value str)]
   :post [(: % str)]}
  "incarnation 命名の基底を得る(末尾の `~g<N>` / `~fork<N>` を 1 つ剥がす)。"
  (re.sub "~(g|fork)[0-9]+$" "" value))


(defk resume-session [params]
  {:pre [(: params dict)
         (in (.get params "mode") #{"resume" "fork"})]
   :post [(: % SessionRow)]}
  "会話の新しい incarnation を宿す(ADR-006 R4)。resume = 同一会話 ref の
   新行(generation + 1)/ fork = 新会話の新行(generation 1、CLI が新 ID を
   鋳造)。admission: 蘇生元の実在 → kind capability → identity-unknown の
   typed 失敗(R1)→ one-live-incarnation(resume のみ)。宿し自体は
   launch-session を再利用する — 並行実装を作らない(R3)。auth は蘇生元行の
   effective_identity から typed binding を再構成し、呼び手の binding param が
   それを上書きする(ADR-006 改訂 R4 — cross-binding failover の受け口。
   home が異なるときは per-kind transplant 前処理が transcript を symlink で
   敷設する: R7)。typed reject は ResumeRejected(error_code 語彙: R9)。
   params: session_id(蘇生元)/ mode / prompt? / model? / effort? /
   mcp_servers? / binding? / new_session_id? / expected_result?(+
   expected_result_specified — 明示 null と未指定の区別)+ host 注入
   (socket_path / max_running / backend_kind / repl_idle_max_wait_seconds)。"
  (setv source-sid (get params "session_id"))
  (setv mode (get params "mode"))
  (<- source (session-store-get source-sid))
  (when (is source None)
    (raise (RuntimeError f"session is not registered: {source-sid}")))
  (when (not-in source.agent-type INTERACTIVE-AGENT-TYPES)
    (raise (ResumeRejected RESUME-ERR-KIND-NOT-SUPPORTED
             (+ f"session.{mode}: agent_type '{source.agent-type}' does not "
                f"support {mode} (supported: codex, claude)"))))
  (setv conv source.conversation)
  (when (is conv None)
    (raise (ResumeRejected RESUME-ERR-IDENTITY-UNKNOWN
             (+ f"session.{mode}: session '{source-sid}' has no stored "
                "conversation identity (identity-unknown) — it cannot be "
                "resumed or forked (ADR-DOE-AGENTS-006 R1: revivability is a "
                "stored fact)"))))
  (setv conv-id (get conv "session_id"))
  (when (= mode "resume")
    (<- active-rows (session-store-list-active))
    (for [row active-rows]
      (when (and (is-not row.conversation None)
                 (= (.get row.conversation "session_id") conv-id))
        (raise (ResumeRejected RESUME-ERR-ONE-LIVE-INCARNATION
                 (+ f"session.resume: conversation '{conv-id}' already has a "
                    f"live incarnation '{row.session-id}' "
                    f"(status {row.status}) — "
                    "one-live-incarnation-per-conversation "
                    "(ADR-DOE-AGENTS-006 R4)"))))))

  ;; 宿り先 work_dir の物理実在(ADR-006 R10 — 全副作用より前の typed
  ;; reject)。R4 の source copy 固定は transcript 発見可能性の保証だが、
  ;; その保証は dir が実在する間しか成立しない: ACP は invocation 終了後に
  ;; workspace を削除するため、消えた宿り先への再開発注が実在した
  ;; (2026-08-16〜17 の resume 全滅 91/91 — tmux の $HOME 黙変換 → claude の
  ;; cwd 鍵会話解決全滅 → 120s 予算切れ no-agent-frame)。宿り先を再建する/
  ;; 諦めるの政策判定は呼び手(ACP engine)の家 — ここは loud に返すだけ。
  (<- source-workdir-present (fs-dir-exists source.work-dir))
  (when (not source-workdir-present)
    (raise (ResumeRejected RESUME-ERR-WORKDIR-NOT-FOUND
             (+ f"session.{mode}: work_dir does not exist: "
                f"{source.work-dir} — the source incarnation's workspace is "
                "gone, so the conversation cannot be rehosted there (tmux "
                "would silently fall back to $HOME and the CLI's cwd-keyed "
                "conversation lookup would fail loud; ADR-DOE-AGENTS-006 "
                "R10 workdir_not_found). Recreate the workspace or abandon "
                "the resume — the decision is the caller's."))))

  ;; 呼び手 binding の admission(ADR-006 改訂 R4)— launch の R7 admission と
  ;; 同一実装(policy binding-admission-error)を共有する(複製実装禁止)。
  ;; 全副作用(transplant の symlink 敷設・tmux)より前に typed reject。
  (setv requested-binding (.get params "binding"))
  (when (is-not requested-binding None)
    (setv requested-binding-error
          (binding-admission-error requested-binding source.agent-type))
    (when (is-not requested-binding-error None)
      (raise (RuntimeError
               f"session.{mode}: invalid binding — {requested-binding-error}"))))

  ;; 新 incarnation の id / name。呼び手指定(new_session_id — ACP は
  ;; agent_<invocationId> 規約で鋳造する)が優先し、session_name も同値で立つ。
  ;; 既存 id との重複は launch と同語彙で即 reject(transplant より前 —
  ;; 副作用ゼロで落とす。launch-session の重複 admission が最終 backstop)。
  ;; 未指定は従来のサーバー鋳造 ~g<N> / ~fork<N> 系列(既使用は前進で回避 —
  ;; 古い incarnation からの resume でも序数が単調に進む)。
  (setv requested-sid (.get params "new_session_id"))
  (setv gen (if (= mode "resume") (+ source.generation 1) 1))
  (if (is-not requested-sid None)
      (do
        (<- requested-clash (session-store-get requested-sid))
        (when (is-not requested-clash None)
          (raise (RuntimeError
                   f"session is already registered: {requested-sid}")))
        (setv new-sid requested-sid)
        (setv new-name requested-sid))
      (do
        (setv base-sid (strip-incarnation-suffix source-sid))
        (setv base-name (strip-incarnation-suffix source.session-name))
        (setv counter (if (= mode "resume") gen 1))
        (setv suffix-kind (if (= mode "resume") "g" "fork"))
        (setv new-sid f"{base-sid}~{suffix-kind}{counter}")
        (<- clash (session-store-get new-sid))
        (while (is-not clash None)
          (setv counter (+ counter 1))
          (setv new-sid f"{base-sid}~{suffix-kind}{counter}")
          (<- next-clash (session-store-get new-sid))
          (setv clash next-clash))
        (when (= mode "resume")
          (setv gen counter))
        (setv new-name f"{base-name}~{suffix-kind}{counter}")))

  ;; auth binding の再構成(行の effective_identity が auth の家)— 呼び手
  ;; 指定の binding が優先(ADR-006 改訂 R4)。
  (setv identity (or source.effective-identity {}))
  (setv binding
        (cond
          (is-not requested-binding None) requested-binding
          (and (= source.agent-type "claude")
               (is-not (.get identity "CLAUDE_CONFIG_DIR") None))
            {"kind" "claude-code"
             "config_dir" (get identity "CLAUDE_CONFIG_DIR")}
          (and (= source.agent-type "codex")
               (is-not (.get identity "CODEX_HOME") None))
            {"kind" "codex"
             "codex_home" (get identity "CODEX_HOME")}
          True None))

  ;; transcript の解決可能性検査 + cross-binding transplant 前処理(ADR-006
  ;; R7/R10)。発火判定(同一 home は敷設 no-op・実在検査のみ)と transcript
  ;; 所在の path 物理は per-kind impl 所有 — resume-physics-has-one-home の
  ;; 延長。source transcript 不在は typed reject(row 不生成 — 実 CLI の loud
  ;; 失敗を launch 前に前倒しする)。R10 改訂(2026-08-18): 呼び手指定の
  ;; binding だけでなく実効 binding(行からの再構成を含む)で常に走らせる —
  ;; same-home の resume/fork も transcript の実在を発注時に検査する(旧形は
  ;; 起動段で死んだ行〔identity 鋳造済み・transcript 未実体化〕の resume を
  ;; 素通しし、実 CLI の『No conversation found』120s 死に落としていた)。
  ;; binding を再構成できない行(identity に home が無い)は従来どおり検査
  ;; なしで通る — transcript の所在自体が導出できないため。
  (when (is-not binding None)
    (<- transplant (transplant-conversation
                     source.agent-type
                     {"conversation" conv
                      "work_dir" source.work-dir
                      "source_identity" identity
                      "binding" binding}))
    (when (not (get transplant "ok"))
      (raise (ResumeRejected (get transplant "code")
                             (get transplant "message")))))

  ;; 未達成の result contract は resume が引き継ぐ(会話の続きなので)。
  ;; fork は新しい仕事 — contract は引き継がない。呼び手の明示指定
  ;; (expected_result_specified — 明示 null 含む)は carry より優先
  ;; (ADR-006 改訂 R4。schema admission は host が launch と共有)。
  (setv carried-expected
        (if (and (= mode "resume")
                 (is-not source.expected-result None)
                 (is source.result-payload None))
            source.expected-result
            None))
  (setv effective-expected
        (if (bool (.get params "expected_result_specified" False))
            (.get params "expected_result")
            carried-expected))

  ;; 非 auth の launch 意図は蘇生元行の launch_overlay から復元し、呼び手の
  ;; params が per-key で上書きする(行が意図の家 — 呼び手は差分だけ言う)。
  (setv overlay (or source.launch-overlay {}))
  (setv overlay-env (dict (or (.get overlay "session_env") {})))
  (.update overlay-env (or (.get params "session_env") {}))

  (setv launch-params
        {"session_id" new-sid
         "session_name" new-name
         "agent_type" source.agent-type
         "work_dir" source.work-dir
         ;; law context-file-rides-the-wire の resume 面: 新 invocation の
         ;; 指示メモは resume でも wire で運ばれ、宿り先(= source.work-dir —
         ;; R4 の cwd 鍵保証で resume はここに宿る)へ launch-session の既存
         ;; 実体化(spawn 前・atomic write)がそのまま書く。素通し 1 点 —
         ;; 並行実装を作らない(R3)。workspace_seed は素通ししない(resume の
         ;; 宿り先は再割当でなく蘇生元 dir — seed 対応は宿り先意味論の設計後)
         "context_file" (.get params "context_file")
         ;; 帰属 metadata の resume 面(one law, both faces): 新 incarnation は
         ;; 新しい invocation を宿すので、帰属も呼び手の申告を素通しする
         ;; (蘇生元行からの復元はしない — 帰属は復元源ではなく出自申告)。
         "launch_attribution" (.get params "launch_attribution")
         "command" None
         "prompt" (.get params "prompt")
         "model" (or (.get params "model") (.get overlay "model"))
         "effort" (or (.get params "effort") (.get overlay "effort"))
         "mcp_servers" (or (.get params "mcp_servers")
                           (.get overlay "mcp_servers")
                           {})
         "skip_trust_setup" False
         "lifecycle" source.lifecycle
         "binding" binding
         "session_env" overlay-env
         "expected_result" effective-expected
         "socket_path" (.get params "socket_path" "")
         "max_running" (.get params "max_running")
         "repl_idle_max_wait_seconds" (.get params "repl_idle_max_wait_seconds")
         "backend_kind" (.get params "backend_kind" "tmux")
         "resume_context" {"mode" mode
                           "conversation" conv
                           "generation" gen
                           "resumed_from_session_id"
                             (when (= mode "resume") source-sid)
                           "forked_from_session_id"
                             (when (= mode "fork") source-sid)}})
  (<- row (launch-session launch-params))
  (<- _ (session-store-record-event row.session-id
                                    (if (= mode "resume")
                                        "session_resumed"
                                        "session_forked")
                                    row))
  row)
