;;; session-host effect 語彙(ADR-DOE-AGENTS-004 R1、C1)。
;;;
;;; 署名と docstring が契約。凍結物理の出典は
;;; packages/doeff-agents/conformance/README.md(CONTRACT FIXED 2026-07-05)と
;;; oracle = agentd-rust-final:src/main.rs。
;;;
;;; 二層構造(R1/R2):
;;;   - interface effects(BuildLaunch / PreLaunchSetup / ClassifyPane /
;;;     DeliverMessage / WireResultChannel)— kind 別の protocol 物理。
;;;     per-kind defhandler モジュール(C2、packages/doeff-agents/impls/)が解釈する。
;;;   - substrate effects(SessionStore* / Tmux* / Clock* / Proc*)— 寿命の外部性を
;;;     持つ側(agentd host / 直接束縛の fake)が解釈する生 IO の境界。
;;;     impls/ は substrate effect を yield するのみ(substrate-clean defsemgrep が守る)。
;;;
;;; 各 effect の deff 構築子が契約面: :pre が引数型を fail-fast 検査し、
;;; docstring が凍結物理(F-* マーカー・TerminalCause 表・knob 表)への束縛を書く。

(require doeff-hy.macros [deff])

(import dataclasses [dataclass])
(import datetime [datetime])
(import typing [Any])
(import doeff [EffectBase])


;; ===========================================================================
;; データ型(契約の語彙)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] TerminalCause []
  "終端理由。category / retryable は conformance README『TerminalCause 凍結表』の
   契約所有 — 変更は ADR 改訂が先(黙った変更は conformance red)。
   first-write-wins(set_terminal_cause_if_absent + DB COALESCE)も契約。"
  #^ str category
  #^ (| str None) reason
  #^ bool retryable
  #^ str observed-at)


(defclass [(dataclass :frozen True :kw-only True)] SessionRow []
  "agent_sessions 行の policy 可視部分。真実は行のみ(DOE-004 R3:
   truth-is-rows-not-continuations)— monitor は毎 cycle この行から再導出する。"
  #^ str session-id
  #^ str session-name
  #^ str pane-id
  #^ str agent-type
  #^ str lifecycle
  #^ str status
  #^ str started-at
  #^ (| str None) last-observed-at
  (setv last-observed-at None)
  #^ (| str None) finished-at
  (setv finished-at None)
  #^ (| str None) cleaned-at
  (setv cleaned-at None)
  #^ (| str None) output-snippet
  (setv output-snippet None)
  #^ (| str None) last-output-change-at
  (setv last-output-change-at None)
  #^ bool awaiting-response
  (setv awaiting-response False)
  #^ (| str None) observed-active-at
  (setv observed-active-at None)
  #^ Any expected-result
  (setv expected-result None)
  #^ (| str None) result-payload
  (setv result-payload None)
  #^ (| str None) last-validation-error
  (setv last-validation-error None)
  #^ int result-solicitations-used
  (setv result-solicitations-used 0)
  #^ int prompt-unblock-attempts
  (setv prompt-unblock-attempts 0)
  #^ (| TerminalCause None) terminal-cause
  (setv terminal-cause None)
  ;; 解決済み実効 identity(CODEX_HOME / CLAUDE_CONFIG_DIR)。S14 は oracle
  ;; expected-red(agent_sessions に identity 列なし)— Hy 実装はここに永続化
  ;; して positive assert に差し替える(DOE-004 契約拡張、C2 で追加)。
  #^ (| dict None) effective-identity
  (setv effective-identity None)
  ;; --- C3 拡張: launch 所有の store-of-record field。行の作成が SessionRow
  ;; だけで完結する(host の store が別口の登記簿を持たない)ための追加 —
  ;; oracle SessionSnapshot の work_dir / backend_kind / backend_ref
  ;; ({"session_name", "pane_id", "command"}、main.rs:1813-1816)に対応。
  ;; policy はこれらを読まない。
  #^ str work-dir
  (setv work-dir "")
  #^ str backend-kind
  (setv backend-kind "tmux")
  #^ (| dict None) backend-ref
  (setv backend-ref None)
  ;; --- ADR-DOE-AGENTS-006: 会話 identity(耐久エンティティ)と incarnation 系譜。
  ;; conversation = kind 判別 union(claude {"session_id"} / codex
  ;; {"session_id", "rollout_path"})。None = identity-unknown — その行への
  ;; resume / fork は typed 失敗(R1)。行は会話の 1 回の宿り(incarnation)で
  ;; あり、terminal 行は決して再活性しない — 会話が行を乗り換える。
  #^ (| dict None) conversation
  (setv conversation None)
  #^ int generation
  (setv generation 1)
  #^ (| str None) resumed-from-session-id
  (setv resumed-from-session-id None)
  #^ (| str None) forked-from-session-id
  (setv forked-from-session-id None)
  ;; 非 auth の launch 意図({session_env, model, effort, mcp_servers})。
  ;; resume はここから再現する(行が launch 意図の家 — auth は
  ;; effective_identity、意図はここ、と住み分ける)。auth キーは launch
  ;; admission が session_env から締め出すため構造的に混入しない。
  #^ (| dict None) launch-overlay
  (setv launch-overlay None)
  ;; --- koine session surface v0(ADR-DOE-AGENTS-007): adopt 起点の登記か。
  ;; 安全条項 1 の ownership marker — policy の刈り取り免除判定(reap-exempt)
  ;; が読む唯一の追加 field。turn_holder / turn_since / turn_wait は policy
  ;; 契約外(writer は turn RPC のみ・liveness は wire 出力時の導出のみ)
  ;; なので SessionRow には載せない — monitor の stale な書き戻しが打刻を
  ;; 上書きする経路を構造的に持たないため。
  #^ bool adopted
  (setv adopted False)
  ;; issue #557: attempt 中に一度でも blocked_api(api-limit marker)を観測した
  ;; 事実の durable latch(初回観測時刻)。終端時の tail-30 snapshot は本質的に
  ;; racy(上限文言は terminal 前に scroll out する)— turn-end-without-result /
  ;; reason 無し failed の終端分類はこれを参照して rate_limited/retryable=true
  ;; へ蒸留する。first-write-wins(store は COALESCE 保護)・clear 経路なし
  ;; (行 = 1 attempt の incarnation なので latch の寿命は attempt と一致)。
  ;; wire には載せない — 下流(ACP ADR 0042)が読むのは terminal_cause のみ。
  #^ (| str None) api-limit-observed-at
  (setv api-limit-observed-at None)
  ;; ACP ADR 0049 R9 第 3 改訂(2026-08-12): 上限族の外の provider 失敗を
  ;; attempt 中に観測した事実の durable latch(族名 + 初回観測時刻の対)。
  ;; api-limit latch とまったく同じ理由で必要 — 終端時の tail は racy で、
  ;; 認証断の告知も solicitation の貼り付けや後続描画で押し流される
  ;; (実測 2026-08-10 wi_86957315d9157a2c: transcript に認証断が在るのに
  ;;  終端 snapshot は催促文だけだった)。first-write-wins(store は
  ;; COALESCE 保護)・clear 経路なし・行 = 1 attempt なので寿命は attempt と
  ;; 一致。wire には載せない — 下流(ACP ADR 0042)が読むのは terminal_cause
  ;; のみで、族の意味は category へ蒸留してから渡す。
  #^ (| str None) provider-failure-class
  (setv provider-failure-class None)
  #^ (| str None) provider-failure-observed-at
  (setv provider-failure-observed-at None)
  ;; ADR-DOE-AGENTS-009: 観測断(supply cut)の最終検出時刻。stale watchdog は
  ;; terminal 化せずここへ刻印する(観測断 ≠ 死亡 — 2026-07-27 wedge の
  ;; false-lost 根治)。launch-timeout watchdog は watch 窓の基点を
  ;; max(started_at, observation_gap_at) に取る(観測断は「観測し続けたのに
  ;; active 未観測」premise を void にする)。last-write-wins(再検出で前進 —
  ;; gap event の有界化と watch 窓再スタートの基点)+ None 保護(store は
  ;; COALESCE(excluded, existing))。wire には載せない — 下流(ACP)が読むのは
  ;; terminal_cause のみ(api_limit_observed_at と同じ扱い)。
  #^ (| str None) observation-gap-at
  (setv observation-gap-at None)
  ;; issue #568(ADR-DOE-AGENTS-010 R2): paste 再送補償の durable counter。
  ;; budget(paste-resubmit-limit)超過は loud typed terminal — 実測 2026-08-06
  ;; で無上限の再送(32 回 / 13 回)が沈黙 blocked に落ち runner 7/7 を飽和
  ;; させた。last-write-wins(単一 writer = monitor)。wire には載せない。
  #^ int paste-resubmit-attempts
  (setv paste-resubmit-attempts 0)
  ;; issue #568(ADR-DOE-AGENTS-010 R3): awaiting_response latch の武装時刻
  ;; (期限の基点)。launch は打刻しない — None は started_at へ fallback
  ;; (登録時武装 = started_at と同時刻)。solicitation の再武装で更新し、
  ;; 正の作業証拠で latch と同時に clear する。wire には載せない。
  #^ (| str None) awaiting-response-since
  (setv awaiting-response-since None))


(defclass [(dataclass :frozen True :kw-only True)] PaneObservation []
  "ClassifyPane の結果 = pane capture の marker 事実。marker→検出は impl 所有
   (F-* 表: F-idle-codex `› ` / F-idle-claude `❯` / F-active-codex `working (`
   or `esc to interrupt` / F-active-claude `… (` / F-turn-activity `⏺` `⎿` /
   F-failed tail10 / F-api-limit tail30 / F-waiting raw 一致)。
   分類順(failure → api-limit → waiting → running)は policy 所有。"
  #^ bool has-failure-marker
  (setv has-failure-marker False)
  #^ bool has-api-limit-marker
  (setv has-api-limit-marker False)
  ;; ACP ADR 0049 R9 第 3 改訂(2026-08-12): 上限族の外の provider 終端事実
  ;; (再認証要求 / 組織 access 剥奪 / 文脈枯渇 / transport 障害)の族名。
  ;; None = 立っていない。bool を族の数だけ増やさず単一 field にしてあるのは、
  ;; 族の追加が「markers に 1 行 + policy の表に 1 行」で閉じる形を守るため。
  ;; status には効かせない — 凍結分類順は has-api-limit-marker までで不変。
  #^ (| str None) provider-failure-class
  (setv provider-failure-class None)
  #^ bool has-waiting-marker
  (setv has-waiting-marker False)
  #^ bool has-idle-prompt
  (setv has-idle-prompt False)
  #^ bool has-active-marker
  (setv has-active-marker False)
  #^ bool has-turn-activity
  (setv has-turn-activity False)
  #^ bool startup-finished
  (setv startup-finished False)
  #^ bool has-unsubmitted-paste
  (setv has-unsubmitted-paste False)
  ;; issue #573: claude の queued-messages 表示(入力行 `❯ Press up to edit
  ;; queued messages`)。未消費 queue = turn 走行中の明白な busy 証拠 —
  ;; policy の中断キー安全壁(send-unblock-keys の busy veto)が参照する。
  #^ bool has-queued-messages
  (setv has-queued-messages False)
  #^ (| str None) dialog
  (setv dialog None)
  ;; dialog の決定的 dismissal キー列(R9 fast-path、S18 で Rust detector と
  ;; verbatim 一致まで確定した物理)。dialog 検出と同じく per-kind impl 所有 —
  ;; policy はこの keys を送るだけで、per-kind のキー物理を知らない
  ;; (protocol-physics-has-one-home)。C2 で追加(oracle:
  ;; dismiss_codex_update_dialog の selected-option 依存 Down 数 /
  ;; bypass・fullscreen = Down,Enter / managed = Enter)。
  #^ Any dialog-dismiss-keys
  (setv dialog-dismiss-keys #())
  ;; ADR-DOE-AGENTS-011 R-frame-class-6f3d: agent 自身が frame を描いたか。
  ;; 「起動画面が読めなかった」の 2 状態((A) 認識できない画面が在る /
  ;; (B) 何も描画されていない)を事後に弁別するための事実 — 旧実装は
  ;; どちらも同一文言「unrecognized screen」で終端し、実測 321 件のうち
  ;; 318 件が (B) 側(空 24 / shell echo のみ 294)だったことが読めなかった。
  #^ bool has-agent-frame
  (setv has-agent-frame False)
  ;; ADR-DOE-AGENTS-011 R-paste-ready-a71c: composer に未送信の内容が
  ;; 座っていない(領域が描かれている ∧ 未送信チップなし)。ready gate の
  ;; 必要条件 — 他人の内容が座っている composer へ prompt を重ね貼りしない。
  #^ bool composer-clear
  (setv composer-clear False)
  ;; ADR-DOE-AGENTS-011 R-paste-ready-a71c: 入力 loop が配線済み
  ;; (idle prompt 可視 ∧ MCP boot 中でない)。旧 gate は idle prompt 単独で
  ;; ready を主張し、MCP boot 窓(composer は描かれているが Enter が
  ;; ロード画面に食われる)を通していた。
  #^ bool input-loop-wired
  (setv input-loop-wired False)
  ;; composer 領域の逐語(最終 prompt 行とそれ以降。未描画は None)。
  ;; ready gate の probe が「reader が我々の byte を消費したか」を
  ;; 領域の変化で判定する材料 — 程度ではなく変化の有無だけを使う。
  #^ (| str None) composer-text
  (setv composer-text None)
  ;; ADR-DOE-AGENTS-011 R-unknown-dialog-loud-4e02: 選択式 dialog の幾何
  ;; (prompt glyph の直後に番号つき option / 確認文言)。R9 fast-path の
  ;; 外側の dialog を loud に落とす判定材料(dialog=None かつこれが True)。
  #^ bool dialog-shaped
  (setv dialog-shaped False)
  ;; ADR-DOE-AGENTS-006 R10 / ADR-DOE-AGENTS-011 改訂(2026-08-18): CLI が
  ;; 会話を解決できず loud 終了した画面の事実(claude『No conversation found
  ;; with session ID』/ codex『no rollout found for thread id』—
  ;; resume-physics.md プローブ (a)(c) の逐語)。status には効かせない —
  ;; 起動 gate だけが consume し、予算を待たず conversation-not-found class で
  ;; 即終端する(2026-08-16〜17 実測: この画面の席 91 件が全件 120s 待って
  ;; no-agent-frame に誤分類されていた)。
  #^ bool has-conversation-not-found-marker
  (setv has-conversation-not-found-marker False))


(defclass [(dataclass :frozen True :kw-only True)] PaneFrame []
  "起動段の証拠 frame 1 枚(ADR-DOE-AGENTS-011 R-evidence-frames-9c17)。
   at-seconds = gate 開始からの経過秒(どの局面の画面かを事後に置ける)。
   text = capture の tail(保持数は READY-GATE-FRAME-RETENTION で有界)。"
  #^ float at-seconds
  #^ str text)


(defclass [(dataclass :frozen True :kw-only True)] ReadyGateVerdict []
  "launch ready gate の裁定(ADR-DOE-AGENTS-011)。ready=False は必ず
   failure-class(閉語彙 LAUNCH-NOT-READY-CLASSES)を伴う — 分類なしの
   不成立は構造的に作れない(旧実装の固定文言への回帰を型で塞ぐ)。
   frames は保持数 1 の旧実装に対する増量分(受入条件 (g))。"
  #^ bool ready
  #^ (| str None) failure-class
  (setv failure-class None)
  #^ Any frames
  (setv frames #())
  #^ int polls
  (setv polls 0)
  #^ float elapsed-seconds
  (setv elapsed-seconds 0.0))


(defclass [(dataclass :frozen True :kw-only True)] JudgeVerdict []
  "prompt judge の strict JSON verdict {blocked, keys, reason}
   (main.rs PromptJudgeVerdict)。keys は whitelist 内のみ・最大 8。"
  #^ bool blocked
  #^ Any keys
  (setv keys #())
  #^ str reason
  (setv reason ""))


(defclass [(dataclass :frozen True :kw-only True)] ProcResult []
  "Proc substrate の完了結果(sh -c 同等)。exit-code != 0 は raise ではなく
   値 — 解釈(judge error 等)は policy 所有。"
  #^ int exit-code
  #^ str stdout
  (setv stdout "")
  #^ str stderr
  (setv stderr ""))


;; repl-idle 予算の既定値 — literal の家はここだけ(ADR-DOE-AGENTS-008 R2、
;; installed semgrep rule doeff-agents-repl-idle-budget-literal-single-home)。
;; launch 側 ready gate の予算 fallback(launch.hy)と boot watchdog 予算材料
;; (host.hy)は import 参照。値の出自は退役 Rust 移植(rollback 専用保存・
;; 正しさの基準ではない — ADR-DOE-AGENTS-004 R7/U1)。
(setv REPL-IDLE-MAX-WAIT-SECONDS 120)

;; issue #568(ADR-DOE-AGENTS-010 R2/R3)の凍結既定値 — literal の家はここだけ
;; (REPL-IDLE-MAX-WAIT-SECONDS と同じ規律)。host の env knob
;; (DOEFF_AGENTD_PASTE_RESUBMIT_LIMIT / DOEFF_AGENTD_AWAITING_RESPONSE_TIMEOUT_SECS)
;; は import 参照で fallback する。
(setv PASTE-RESUBMIT-LIMIT 5)
(setv AWAITING-RESPONSE-TIMEOUT-SECONDS 600)

;; turn 生死のデータ層証拠(ADR-DOE-AGENTS-002 R-conversation-evidence)の
;; 凍結既定値 — literal の家はここだけ(上と同じ規律)。
;; quiescence: 会話記録(transcript / rollout)の最終更新がこの秒数以内なら
;; turn は走行中 — pane の描画がどうであれ turn-end を宣言しない。値の根拠:
;; 実弾(2026-08-18 claude CLI 2.1.234)の誤爆は turn 開始 4〜60 秒での静止
;; 判定 — 120s はそれを全滅させ、真の turn-end 検出を最大 120s 遅らせるだけ
;; (result-first が先に勝つので正常席には影響しない)。
;; margin: awaiting_response の解除に使う「配送後の進行」判定の余白 —
;; 配送そのもの(prompt / solicitation の queue 書き込み)が transcript の
;; mtime を動かすため、配送時刻 + margin 以降の更新だけを進行と数える。
(setv CONVERSATION-QUIESCENCE-SECONDS 120)
(setv CONVERSATION-PROGRESS-MARGIN-SECONDS 10)

;; ADR-DOE-AGENTS-011 の凍結物理 — literal の家はここだけ(上と同じ規律)。
;; 貼り付け可能性 probe: ready gate は「入力欄が描かれた」ではなく
;; 「この composer が我々の bracketed paste を消費した」を必要条件にする。
;; 実測(2026-08-11、25 席の unsubmitted-prompt 終端): idle prompt を見て
;; 即 paste した prompt が composer に collapsed chip として座り(12 席は
;; chip 3〜10 個 = 1 回の bracketed paste が断片化して着弾)、Enter は
;; 断片の隙間に食われて turn は一度も始まらなかった。probe は
;; 「reader が我々の byte を消費している」ことの唯一の positive 証拠。
;; text は空白なし ASCII(TUI の折返しで断片化しない)・誤送信しても無害な語。
(setv READY-PROBE-TEXT "doeff-ready-probe")
;; probe の消去は文字単位の後退キー(kind 非依存の terminal 物理 — submit の
;; "Enter" が substrate 所有であるのと同じ層)。collapsed chip 形で着弾した
;; 場合は 1 打で消えるため、残りの打鍵は空 composer への no-op になる。
(setv READY-PROBE-CLEAR-KEY "BSpace")
;; 失敗時に保持する証拠 frame の数(受入条件 (g): 旧実装は 1 件)。
;; 有界(3)— output_snippet は wire に載る行の列であり無制限に育てない。
(setv READY-GATE-FRAME-RETENTION 3)
;; 1 frame あたりの保持行数(旧実装の tail-15 と同値 — 逐語性を落とさない)。
(setv READY-GATE-FRAME-LINES 15)


(defclass [(dataclass :frozen True :kw-only True)] MonitorKnobs []
  "testability knob 表(conformance README、契約凍結値):
   stall T 180s / solicitation budget 2 / unblock budget 3 /
   launch timeout 60s / stale-observation 300s / repl-idle 予算 120s /
   paste 再送 budget 5 / awaiting 期限 600s。
   judge-cmd None = judge 無効 — ハザード 1: 既定 judge が実 claude を
   起動する事故の防止。conformance 実行時も同じ既定を維持する)。
   repl-idle-max-wait-seconds は launch 側 ready gate の予算と同じ値
   (DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS)— booting 所有権 arm の boot
   watchdog 予算(launch timeout + repl-idle 予算)の材料。"
  #^ int prompt-stall-seconds
  (setv prompt-stall-seconds 180)
  #^ int result-solicitation-limit
  (setv result-solicitation-limit 2)
  #^ int prompt-unblock-limit
  (setv prompt-unblock-limit 3)
  #^ int launch-timeout-seconds
  (setv launch-timeout-seconds 60)
  #^ int stale-observation-seconds
  (setv stale-observation-seconds 300)
  #^ int repl-idle-max-wait-seconds
  (setv repl-idle-max-wait-seconds REPL-IDLE-MAX-WAIT-SECONDS)
  ;; koine 条項 4(ADR-DOE-AGENTS-007 R4): open turn(holder='agent')の
  ;; stalled 導出閾値。env knob DOEFF_AGENTD_TURN_STALL_SECS(既定 1800 —
  ;; 対話席の自走 turn は 15 分を常態的に超える実測に基づく保守初期値。
  ;; attention ledger の turn 長分布で後日較正)。導出は wire 出力時のみ
  ;; (store に stalled を書かない・status を変えない — signal only)。
  #^ int turn-stall-seconds
  (setv turn-stall-seconds 1800)
  ;; issue #568(ADR-DOE-AGENTS-010 R2): 未送信 paste / 添付の Enter 再送
  ;; budget。超過は typed terminal(沈黙 blocked の禁止)。
  #^ int paste-resubmit-limit
  (setv paste-resubmit-limit PASTE-RESUBMIT-LIMIT)
  ;; issue #568(ADR-DOE-AGENTS-010 R3): awaiting_response latch の期限。
  ;; 基点 = max(awaiting_response_since | started_at, observation_gap_at)。
  #^ int awaiting-response-timeout-seconds
  (setv awaiting-response-timeout-seconds AWAITING-RESPONSE-TIMEOUT-SECONDS)
  ;; ADR-DOE-AGENTS-002 R-conversation-evidence: turn 生死のデータ層証拠。
  ;; 会話記録の鮮度窓(turn-end の反証)と配送後進行の余白(awaiting 解除)。
  #^ int conversation-quiescence-seconds
  (setv conversation-quiescence-seconds CONVERSATION-QUIESCENCE-SECONDS)
  #^ int conversation-progress-margin-seconds
  (setv conversation-progress-margin-seconds CONVERSATION-PROGRESS-MARGIN-SECONDS)
  #^ (| str None) judge-cmd
  (setv judge-cmd None))


;; ===========================================================================
;; interface effects(kind 別 protocol 物理 — per-kind defhandler が解釈)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] BuildLaunch [EffectBase]
  "kind の起動 argv を組み立てる。戻り値: list[str]。
   凍結配線(S13 / 49b3549b 傷跡): claude は既定で
   `--settings {\"disableAllHooks\":true}`(params session_hooks = \"inherit\"
   〔daemon env knob DOEFF_AGENTD_SESSION_HOOKS〕では出さない — 安全 hook まで
   切れる実測 2026-08-18 の根治)+ `--mcp-config`(doeff_result stdio)+
   `--strict-mcp-config`、codex は `-c mcp_servers.\"doeff_result\".command=` /
   `.args=[...]`。prompt は argv に載せない(live terminal transport のみ)。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] PreLaunchSetup [EffectBase]
  "tmux 起動前の kind 別 home / trust 準備。凍結物理(S11/S12 / DOE-003 R1/R3 /
   42fb28fa 傷跡): codex は CODEX_HOME 必須 — 無ければ tmux 呼び出し前に fail
   (tmux 痕跡ゼロ・session 行無し)。claude は CLAUDE_CONFIG_DIR 無しは warning のみ、
   `<CLAUDE_CONFIG_DIR>/.claude.json` に canonicalized work_dir の
   `hasTrustDialogAccepted=true` を temp+rename で pre-seed する。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] ClassifyPane [EffectBase]
  "pane capture(tail 100 行)を kind 別 marker で観測する。戻り値: PaneObservation。
   marker は lowercase tail の部分文字列一致(oracle main.rs:2775-3229、F-* 表)。
   分類の順序・意味づけは policy 所有 — この effect は事実だけを返す。"
  #^ str agent-type
  #^ str output)

(defclass [(dataclass :frozen True :kw-only True)] DeliverMessage [EffectBase]
  "live REPL へメッセージを paste + submit する(prompt / solicitation の配送路)。
   凍結物理(ハザード 4): 配送は同期 confirm ループを含み、その間 monitor は
   session を観測できない(盲窓)。tty echo の paste 残留で confirm が Enter を
   再送し得る — 受信側はこれに耐えること。"
  #^ str pane-id
  #^ str text)

(defclass [(dataclass :frozen True :kw-only True)] WireResultChannel [EffectBase]
  "kind の result 報告チャネル(report-result-mcp)を session に配線する。
   凍結物理(S1/S13 / main.rs:1306 mcp_command_args):
   `$DOEFF_AGENTD_BIN report-result-mcp --session $DOEFF_RESULT_SESSION_ID
   --socket $DOEFF_AGENTD_SOCKET` と同物理。結果は常にこのデータチャネル経由 —
   pane を result として parse することは禁止(result-first、ADR 0035)。"
  #^ str agent-type
  #^ str session-id
  #^ str socket-path)

(defclass [(dataclass :frozen True :kw-only True)] BuildResume [EffectBase]
  "kind の resume / fork argv を組み立てる(ADR-DOE-AGENTS-006 R3)。
   params = launch params 相当 + \"resume_mode\"(\"resume\" | \"fork\")+
   \"conversation\"(親会話 ref、kind 判別 union)。物理: claude =
   `--resume <uuid>`(fork はさらに `--fork-session`)、codex =
   `codex resume <uuid>` / `codex fork <uuid>`。BuildLaunch と同じく
   prompt は決して argv に載せない(live terminal transport のみ)。
   argv 物理は impls/ の kind モジュール単一所有
   (law resume-physics-has-one-home)。戻り値: list[str]。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] DiscoverConversation [EffectBase]
  "session の会話 identity を kind 物理で事後発見する(ADR-DOE-AGENTS-006 R1)。
   codex の launch と両 kind の fork は identity を CLI 側が鋳造するため、
   事後発見が唯一の捕獲経路 — monitor の level-triggered arm が
   conversation 未確定の行に対して毎 cycle これを試みる。impl は
   Fs substrate effect のみで実装する(substrate-clean)。
   params: {\"work_dir\", \"effective_identity\"(auth home の解決結果),
   \"exclude_session_ids\"(親会話等、発見対象から除外する id 集合)}。
   戻り値: conversation dict | None(未発見 — 次 cycle で再試行)。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] TransplantConversation [EffectBase]
  "cross-binding resume の transcript transplant 前処理(ADR-DOE-AGENTS-006
   改訂 R7)。transcript の所在物理は kind 所有(claude = projects jsonl の
   4 対 / codex = rollout の sessions 相対 path — resume-physics.md
   2026-08-11 プローブ)なので、per-kind impl が FsLinkArtifact substrate
   effect で source home → binding home へ symlink を敷設する。発火条件
   (binding 指定かつ source 行の effective_identity と異なる home)の判定も
   impl 所有 — 同一 home は no-op {\"ok\" True}。
   params: {\"conversation\"(source 行の会話 ref), \"work_dir\",
   \"source_identity\"(source 行の effective_identity), \"binding\"
   (呼び手指定の typed binding — admission 通過済み)}。
   戻り値: {\"ok\" True} | {\"ok\" False \"code\" <RESUME-ERR-*>
   \"message\" str}(source transcript 不在は
   RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE — program が typed reject にする)。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] ProbeConversationActivity [EffectBase]
  "会話記録(kind 別 transcript 実体)の最終更新時刻の観測
   (ADR-DOE-AGENTS-002 R-conversation-evidence)。turn の生死の真理条件は
   データ層にある — pane の描画は CLI 版更新で変わる非契約面で、走行中の
   turn を描かない実物(claude CLI 2.1.234、2026-08-18 の 22 件
   run_failed 誤爆)が観測されている。record が書かれている限り turn は
   生きている。所在物理は kind 所有(claude = projects jsonl / codex =
   rollout — TransplantConversation と同じ家)、impl は Fs substrate effect
   のみで実装する(substrate-clean)。材料不足(identity / conversation /
   work_dir の欠け)や実体不在は None — probe は反証面であって門ではなく、
   None は従来の表示層物理へ fallback する。
   params: {\"work_dir\", \"effective_identity\", \"conversation\"}。
   戻り値: float(epoch 秒)| None。"
  #^ str agent-type
  #^ dict params)


;; resume / fork の typed reject 語彙(ADR-DOE-AGENTS-006 改訂)。wire の
;; error_code に verbatim で載る安定文字列 — 機械消費者(ACP Haskell client)は
;; message substring ではなくこの code を照合する。koine 由来契約の typed 文字列
;; error_code(adopt_target_not_found)と同系。message 文言は後方互換で自由。
(setv RESUME-ERR-ONE-LIVE-INCARNATION "one_live_incarnation")
(setv RESUME-ERR-IDENTITY-UNKNOWN "identity_unknown")
(setv RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE "transcript_not_discoverable")
(setv RESUME-ERR-KIND-NOT-SUPPORTED "kind_not_supported")
;; ADR-DOE-AGENTS-006 R10(2026-08-18): 宿り先 work_dir(R4 の source copy
;; 固定)が物理に実在しない再開発注の typed reject。tmux は不在
;; start-directory を黙って $HOME に差し替えるため、claude の cwd 鍵の会話
;; 解決(projects/<mangle(cwd)>)ごと別の家を見て『No conversation found』で
;; 即死する — 2026-08-16〜17 の resume 全滅 91/91 件の一次原因。
(setv RESUME-ERR-WORKDIR-NOT-FOUND "workdir_not_found")


;; ===========================================================================
;; substrate effects(SessionStore / Tmux / Clock / Proc — DOE-004 R1)
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreListActive [EffectBase]
  "非終端 session 行の一覧(active_statuses = pending / booting / running /
   blocked / blocked_api、main.rs:1931)。戻り値: list[SessionRow]。
   monitor cycle はこの level-triggered 再読からのみ駆動される(R3)。"
  )

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreListCleanupPending [EffectBase]
  "単一掃き取り(issue #568 / ADR-DOE-AGENTS-010 R5)の対象集合: 終端 status ∧
   cleaned_at IS NULL ∧ lifecycle=run_to_completion ∧ 非 adopted の行一覧。
   刈り取り免除(ADR-DOE-AGENTS-007 安全条項 1)は集合の定義で継承する —
   対話席・adopt 席の substrate には掃き取りが触れない。戻り値: list[SessionRow]。"
  )

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreGet [EffectBase]
  "session 行の単読。戻り値: SessionRow | None。"
  #^ str session-id)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreUpsert [EffectBase]
  "session 行の書き戻し(単一 writer、R3)。COALESCE 規律(main.rs:2339):
   永続化済み result_payload_json を upsert が消すことは禁止。戻り値: None。"
  #^ SessionRow row)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreResultPayload [EffectBase]
  "result_payload_json の fresh read。report_result は別 connection で書くため、
   monitor は手元の行を信じず毎回この読みで result-first を判定する
   (main.rs current_result_payload)。戻り値: str | None(byte-faithful payload)。"
  #^ str session-id)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreRecordEvent [EffectBase]
  "監査 event の追記(session_done / session_failed / session_blocked /
   session_observed / session_result_solicited / session_prompt_unblocked /
   session_prompt_unblock_vetoed / session_prompt_judge_inconclusive /
   session_stale_reaped / session_launch_timeout / session_exited /
   session_unsubmitted_paste_resubmitted / session_resumed / session_forked /
   session_conversation_discovered / session_cleaned)。戻り値: None。"
  #^ str session-id
  #^ str event-type
  #^ SessionRow row)

(defclass [(dataclass :frozen True :kw-only True)] SessionStoreKnownConversationIds [EffectBase]
  "store が知る全会話 ID の集合読み(terminal 行を含む全行の conversation_json
   から。ADR-006 R1 の発見 arm が除外集合として使う — 既知の会話を fork の
   新会話と誤認しないため)。戻り値: list[str]。"
  )

(defclass [(dataclass :frozen True :kw-only True)] TmuxHasSession [EffectBase]
  "tmux session の生存確認。戻り値: bool。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] TmuxPaneCurrentCommand [EffectBase]
  "pane の foreground command 名。戻り値: str | None。zombie 判定
   (idle shell 列挙: zsh/bash/sh/dash/fish/ksh)は policy 所有。"
  #^ str pane-id)

(defclass [(dataclass :frozen True :kw-only True)] TmuxSessionPaneIds [EffectBase]
  "session が現に所有する pane id の一覧(tmux list-panes -s / herdr は
   agent の pane 解決)。戻り値: list[str](session 不在は空 list)。宛先
   pane の帰属検証(issue #568 / ADR-DOE-AGENTS-010 R4 — pane 番号は再利用
   される)の観測面。帰属の裁定(recorded pane ∉ 集合 = vanished)は
   policy 所有。session→pane の向きなのは herdr substrate(agent = pane の
   2 層・pane→agent の逆引き API なし)と対称に実装できるため。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] TmuxCapture [EffectBase]
  "pane の末尾 LINES 行を capture する(monitor は 100 行窓)。戻り値: str。
   turn-end の stable 判定は 500 字 tail の前回一致(main.rs:2832/2932)。"
  #^ str pane-id
  #^ int lines
  (setv lines 100))

(defclass [(dataclass :frozen True :kw-only True)] TmuxSendKeys [EffectBase]
  "pane へのキー送出。literal=True はテキスト paste、submit=True は Enter 付き。
   literal=False はキー名(Up/Down/Enter/Escape 等)。戻り値: None。"
  #^ str pane-id
  #^ str text
  #^ bool literal
  #^ bool submit)

(defclass [(dataclass :frozen True :kw-only True)] TmuxKillSession [EffectBase]
  "tmux session の破棄(RunToCompletion の done/failed cleanup)。戻り値: None。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] ClockNow [EffectBase]
  "現在時刻(timezone-aware UTC datetime)。watchdog(stall 180s / launch 60s /
   stale 300s)の時間算術はすべてこの effect 経由 — policy は wall clock を
   直接読まない。戻り値: datetime。"
  )

(defclass [(dataclass :frozen True :kw-only True)] ProcRun [EffectBase]
  "子 process の実行(sh -c 同等、stdin 供給、wall-clock cap は substrate 所有)。
   戻り値: ProcResult — exit-code != 0 も値として返す(raise しない)。
   monitor policy では prompt judge の実行にのみ使う。"
  #^ str command
  #^ (| str None) stdin
  (setv stdin None))


;; --- C2 拡張(DOE-004 R1 改訂と同一チェンジセット): launch 経路と per-kind
;; trust 物理(S11/S12)が要求する substrate。impls/ は生 IO を持てない
;; (substrate-clean defsemgrep)ため、FS / env / session 作成もすべて effect 境界。

(defclass [(dataclass :frozen True :kw-only True)] TmuxNewSession [EffectBase]
  "detached tmux session の作成(oracle tmux_new_session: new-session -d -P -F #D、
   -c work-dir、session_env を -e で注入)。戻り値: pane-id str。
   禁止 env(ANTHROPIC_API_KEY*)の hard reject は substrate 所有(oracle
   ensure_no_forbidden_agent_env — C0 README 実装メモの凍結物理)。"
  #^ str session-name
  #^ str work-dir
  #^ dict env)

(defclass [(dataclass :frozen True :kw-only True)] FsCanonicalPath [EffectBase]
  "path の canonicalize(realpath。S12: claude trust は canonicalized work_dir を
   project key にする — /tmp は macOS で /private/tmp)。解決不能なら入力を
   そのまま返す(oracle: canonicalize(...).unwrap_or(work_dir))。戻り値: str。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsReadText [EffectBase]
  "テキストファイルの読み。不存在は None(oracle: exists 分岐)。戻り値: str | None。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsWriteTextAtomic [EffectBase]
  "temp+rename の原子的書き込み(S12: 並走 claude が torn state を読まない・
   temp 残骸を残さない)。substrate は path + tmp-suffix の一時ファイルに書いて
   rename する。戻り値: None。"
  #^ str path
  #^ str text
  #^ str tmp-suffix
  (setv tmp-suffix ".agentd-tmp"))

(defclass [(dataclass :frozen True :kw-only True)] FsMakeDirs [EffectBase]
  "ディレクトリの再帰作成(exist-ok。oracle: fs::create_dir_all)。戻り値: None。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsListDir [EffectBase]
  "ディレクトリ直下のエントリ名の列挙(ADR-006 の会話 identity 発見用の
   読み取り面)。不在・非ディレクトリは空 list — raise しない(discovery は
   level-triggered に再試行されるので、観測不能 = 未発見)。戻り値: list[str]。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsComposeHomeView [EffectBase]
  "二軸宣言 (auth-file, profile-dir) から home view を実体化する(#15、
   DOE-004 R5 v2 / ACP 0040 R3 後継: 合成 CODEX_HOME は adapter 物理で、
   その家は host — apps の ensure-agent-home 退役の受け皿)。物理:
   view-root 配下の決定的な名前(resolved realpath ペア由来 — basename 衝突
   排除)のディレクトリに auth.json → auth-file の symlink、profile-dir の
   各 entry(auth.json 名は skip)の symlink、profile-dir/sessions の mkdir
   (bundle 側 = session 履歴は profile 単位で共有、incumbent 意味論)。
   view は全 symlink — config.toml も symlink のままにし、trust 書きは
   fs-canonical-path 経由で bundle に届く。substrate は view 単位の lock で
   合成を直列化する(並行 launch の race 面を閉じる)。冪等(level-triggered
   再 ensure)。fail-loud: auth-file/profile-dir 不在は typed RuntimeError
   (登録時実在検証の launch-time 移設、ACP 0040 R2 改訂)、symlink である
   べき場所の実ファイルも typed RuntimeError(erosion guard — 黙って置換
   しない)。戻り値: 実体化した view の絶対パス str。"
  #^ str auth-file
  #^ str profile-dir
  #^ str view-root)

(defclass [(dataclass :frozen True :kw-only True)] FsLinkArtifact [EffectBase]
  "会話 artifact の cross-home symlink 敷設(ADR-DOE-AGENTS-006 改訂 R7 の
   transplant プリミティブ — dotfiles agentcli share.py link_session_artifact
   の意味移植)。物理: source 不在(実体でも symlink でもない)= 触らず
   \"source-missing\" / target 実在: 同一実体 = no-op \"same-entity\"、
   別実体 = 触らず \"target-conflict\"(silent 置換はしない — share.py 同型の
   no-op)/ それ以外 = 親 dir を mkdir して symlink、\"linked\"。冪等。
   方針判断(必須 artifact の不在を typed reject にする等)は呼び手所有 —
   substrate は観測結果の 4 値を返すだけ。戻り値: str(上記 4 値)。"
  #^ str source-path
  #^ str target-path)

(defclass [(dataclass :frozen True :kw-only True)] FsDirExists [EffectBase]
  "ディレクトリの実在観測(ADR-DOE-AGENTS-006 R10 — 発注の物理前提検査)。
   symlink は解決して判定する(解決先が dir なら実在)。FsListDir は不在と
   空 dir をどちらも空 list に潰すためこの用途に使えない — 空だが実在する
   work_dir は正当な宿り先。戻り値: bool。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] GitRun [EffectBase]
  "git の 1 発実行(ACP W2 — workspace seed の実体化にのみ使う制約面。
   launcher と session host が別機械に分かれた系では、worktree はセッションの
   走る機械 = この host でしか作れない。launcher は判断を data(workspace_seed)
   で送り、実行だけがここへ来る)。cwd は repo に固定(`git -C <repo>`)。
   失敗判断(loud / fetch 再試行)は呼び手 = launch program 所有 — substrate は
   subprocess 物理のみ。戻り値: {\"code\" int, \"stdout\" str, \"stderr\" str}。"
  #^ str repo
  #^ "tuple" args)

(defclass [(dataclass :frozen True :kw-only True)] FsFileExists [EffectBase]
  "ファイルの実在観測(ADR-DOE-AGENTS-006 R10 — same-home resume の
   transcript 実在検査)。symlink は解決して判定する — 壊れた symlink は
   不在(R7 の『実体でも symlink 解決先としても不在』と同義)。戻り値: bool。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] FsFileMtime [EffectBase]
  "ファイルの最終更新時刻の観測(ADR-DOE-AGENTS-002
   R-conversation-evidence — 会話記録の鮮度読み)。symlink は解決する
   (transplant 済み会話の追記は解決先実体へ届く — ADR-006 R7 と同じ物理)。
   不在・観測不能(OSError)は None — raise しない(probe は反証面であって
   門ではない。観測不能 = 証拠なし = 従来物理へ fallback)。
   戻り値: float(epoch 秒)| None。"
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] EnvGet [EffectBase]
  "呼び手 process env の単読(S11 caveat: trust writer は session_env に無い
   home を process env から fallback 参照する — daemon 束縛では daemon env、
   直接束縛では呼び手 env)。戻り値: str | None。"
  #^ str name)

(defclass [(dataclass :frozen True :kw-only True)] ClockSleep [EffectBase]
  "実時間待ち(wait-for-repl-idle の poll 間隔・dialog 再描画待ち)。
   時間算術が ClockNow 経由であるのと同じく、待ちも effect 経由 — program は
   wall clock に直接触れない。戻り値: None。"
  #^ float seconds)


;; ===========================================================================
;; deff 構築子(署名 = 契約面)
;; ===========================================================================

(deff build-launch [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % BuildLaunch)]}
  "BuildLaunch を構築する。kind 追加 = per-kind defhandler モジュール 1 個 +
   kind スキーマ + conformance green(R2)— Rust agentd への物理追加は禁止。"
  (BuildLaunch :agent-type agent-type :params params))

(deff pre-launch-setup [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % PreLaunchSetup)]}
  "PreLaunchSetup を構築する(S11/S12 の trust / home 物理)。"
  (PreLaunchSetup :agent-type agent-type :params params))

(deff classify-pane [agent-type output]
  {:pre [(: agent-type str) (: output str)]
   :post [(: % ClassifyPane)]}
  "ClassifyPane を構築する(F-* marker 事実の観測)。"
  (ClassifyPane :agent-type agent-type :output output))

(deff deliver-message [pane-id text]
  {:pre [(: pane-id str) (: text str) (> (len text) 0)]
   :post [(: % DeliverMessage)]}
  "DeliverMessage を構築する(live REPL への paste + submit)。"
  (DeliverMessage :pane-id pane-id :text text))

(deff wire-result-channel [agent-type session-id socket-path]
  {:pre [(: agent-type str) (: session-id str) (: socket-path str)]
   :post [(: % WireResultChannel)]}
  "WireResultChannel を構築する(report-result-mcp 配線)。"
  (WireResultChannel :agent-type agent-type :session-id session-id
                     :socket-path socket-path))

(deff build-resume [agent-type params]
  {:pre [(: agent-type str) (: params dict)
         (in (.get params "resume_mode") #{"resume" "fork"})
         (: (.get params "conversation") dict)]
   :post [(: % BuildResume)]}
  "BuildResume を構築する(ADR-006 R3: resume / fork の kind argv 物理)。"
  (BuildResume :agent-type agent-type :params params))

(deff discover-conversation [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % DiscoverConversation)]}
  "DiscoverConversation を構築する(ADR-006 R1: 会話 identity の事後発見)。"
  (DiscoverConversation :agent-type agent-type :params params))

(deff transplant-conversation [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % TransplantConversation)]}
  "TransplantConversation を構築する(ADR-006 改訂 R7: cross-binding
   transplant 前処理)。"
  (TransplantConversation :agent-type agent-type :params params))

(deff probe-conversation-activity [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % ProbeConversationActivity)]}
  "ProbeConversationActivity を構築する(ADR-002 R-conversation-evidence:
   turn 生死のデータ層証拠 — 会話記録の最終更新時刻)。"
  (ProbeConversationActivity :agent-type agent-type :params params))

(deff session-store-list-active []
  {:pre [True]
   :post [(: % SessionStoreListActive)]}
  "SessionStoreListActive を構築する(active_statuses の level-triggered 再読)。"
  (SessionStoreListActive))

(deff session-store-list-cleanup-pending []
  {:pre [True]
   :post [(: % SessionStoreListCleanupPending)]}
  "SessionStoreListCleanupPending を構築する(ADR-DOE-AGENTS-010 R5 の
   単一掃き取り対象集合)。"
  (SessionStoreListCleanupPending))

(deff session-store-get [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionStoreGet)]}
  "SessionStoreGet を構築する。"
  (SessionStoreGet :session-id session-id))

(deff session-store-upsert [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionStoreUpsert)]}
  "SessionStoreUpsert を構築する(単一 writer・COALESCE 規律)。"
  (SessionStoreUpsert :row row))

(deff session-store-result-payload [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionStoreResultPayload)]}
  "SessionStoreResultPayload を構築する(result-first の fresh read)。"
  (SessionStoreResultPayload :session-id session-id))

(deff session-store-record-event [session-id event-type row]
  {:pre [(: session-id str) (: event-type str) (: row SessionRow)]
   :post [(: % SessionStoreRecordEvent)]}
  "SessionStoreRecordEvent を構築する(監査 event 追記)。"
  (SessionStoreRecordEvent :session-id session-id :event-type event-type :row row))

(deff session-store-known-conversation-ids []
  {:pre [True]
   :post [(: % SessionStoreKnownConversationIds)]}
  "SessionStoreKnownConversationIds を構築する(発見 arm の除外集合読み)。"
  (SessionStoreKnownConversationIds))

(deff tmux-has-session [session-name]
  {:pre [(: session-name str)]
   :post [(: % TmuxHasSession)]}
  "TmuxHasSession を構築する。"
  (TmuxHasSession :session-name session-name))

(deff tmux-pane-current-command [pane-id]
  {:pre [(: pane-id str)]
   :post [(: % TmuxPaneCurrentCommand)]}
  "TmuxPaneCurrentCommand を構築する(zombie 判定の観測面)。"
  (TmuxPaneCurrentCommand :pane-id pane-id))

(deff tmux-session-pane-ids [session-name]
  {:pre [(: session-name str)]
   :post [(: % TmuxSessionPaneIds)]}
  "TmuxSessionPaneIds を構築する(宛先 pane の帰属検証 — ADR-DOE-AGENTS-010 R4)。"
  (TmuxSessionPaneIds :session-name session-name))

(deff tmux-capture [pane-id lines]
  {:pre [(: pane-id str) (: lines int) (> lines 0)]
   :post [(: % TmuxCapture)]}
  "TmuxCapture を構築する(monitor は 100 行窓)。"
  (TmuxCapture :pane-id pane-id :lines lines))

(deff tmux-send-keys [pane-id text literal submit]
  {:pre [(: pane-id str) (: text str) (: literal bool) (: submit bool)]
   :post [(: % TmuxSendKeys)]}
  "TmuxSendKeys を構築する(unblock keys / dialog dismiss / Enter 再送)。"
  (TmuxSendKeys :pane-id pane-id :text text :literal literal :submit submit))

(deff tmux-kill-session [session-name]
  {:pre [(: session-name str)]
   :post [(: % TmuxKillSession)]}
  "TmuxKillSession を構築する(RTC 終端 cleanup)。"
  (TmuxKillSession :session-name session-name))

(deff clock-now []
  {:pre [True]
   :post [(: % ClockNow)]}
  "ClockNow を構築する(watchdog 時間算術の唯一の時刻源)。"
  (ClockNow))

(deff proc-run [command stdin]
  {:pre [(: command str) (> (len command) 0) (: stdin (| str None))]
   :post [(: % ProcRun)]}
  "ProcRun を構築する(prompt judge 実行)。"
  (ProcRun :command command :stdin stdin))

(deff tmux-new-session [session-name work-dir env]
  {:pre [(: session-name str) (: work-dir str) (: env dict)]
   :post [(: % TmuxNewSession)]}
  "TmuxNewSession を構築する(launch 経路。禁止 env reject は substrate 所有)。"
  (TmuxNewSession :session-name session-name :work-dir work-dir :env env))

(deff fs-canonical-path [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsCanonicalPath)]}
  "FsCanonicalPath を構築する(S12 の canonicalized project key)。"
  (FsCanonicalPath :path path))

(deff fs-read-text [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsReadText)]}
  "FsReadText を構築する(不存在は None)。"
  (FsReadText :path path))

(deff fs-write-text-atomic [path text tmp-suffix]
  {:pre [(: path str) (> (len path) 0) (: text str)
         (: tmp-suffix str) (> (len tmp-suffix) 0)]
   :post [(: % FsWriteTextAtomic)]}
  "FsWriteTextAtomic を構築する(temp+rename、S12)。"
  (FsWriteTextAtomic :path path :text text :tmp-suffix tmp-suffix))

(deff fs-make-dirs [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsMakeDirs)]}
  "FsMakeDirs を構築する(exist-ok 再帰作成)。"
  (FsMakeDirs :path path))

(deff fs-list-dir [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsListDir)]}
  "FsListDir を構築する(発見用の非破壊読み — 不在は空 list)。"
  (FsListDir :path path))

(deff fs-compose-home-view [auth-file profile-dir view-root]
  {:pre [(: auth-file str) (> (len auth-file) 0)
         (: profile-dir str) (> (len profile-dir) 0)
         (: view-root str) (> (len view-root) 0)]
   :post [(: % FsComposeHomeView)]}
  "FsComposeHomeView を構築する(#15 二軸 → home view の実体化)。"
  (FsComposeHomeView :auth-file auth-file :profile-dir profile-dir :view-root view-root))

(deff fs-link-artifact [source-path target-path]
  {:pre [(: source-path str) (> (len source-path) 0)
         (: target-path str) (> (len target-path) 0)]
   :post [(: % FsLinkArtifact)]}
  "FsLinkArtifact を構築する(transplant の symlink 敷設プリミティブ)。"
  (FsLinkArtifact :source-path source-path :target-path target-path))

(deff fs-dir-exists [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsDirExists)]}
  "FsDirExists を構築する(発注の物理前提検査 — ADR-DOE-AGENTS-006 R10)。"
  (FsDirExists :path path))

(deff git-run [repo args]
  {:pre [(: repo str) (> (len repo) 0) (: args "value")]
   :post [(: % GitRun)]}
  "GitRun を構築する(ACP W2 workspace seed の実体化専用)。"
  (GitRun :repo repo :args (tuple args)))

(deff fs-file-exists [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsFileExists)]}
  "FsFileExists を構築する(transcript 実在検査 — ADR-DOE-AGENTS-006 R10)。"
  (FsFileExists :path path))

(deff fs-file-mtime [path]
  {:pre [(: path str) (> (len path) 0)]
   :post [(: % FsFileMtime)]}
  "FsFileMtime を構築する(会話記録の鮮度読み — ADR-002
   R-conversation-evidence)。"
  (FsFileMtime :path path))

(deff env-get [name]
  {:pre [(: name str) (> (len name) 0)]
   :post [(: % EnvGet)]}
  "EnvGet を構築する(process env fallback、S11 caveat)。"
  (EnvGet :name name))

(deff clock-sleep [seconds]
  {:pre [(: seconds (| int float)) (>= seconds 0)]
   :post [(: % ClockSleep)]}
  "ClockSleep を構築する(poll 間隔・再描画待ち)。"
  (ClockSleep :seconds (float seconds)))
