;;; F-* marker 物理(ADR-DOE-AGENTS-004 C2) — pane capture のテキスト事実。
;;;
;;; observation 形式の readiness/idle 物理の家(ADR-DOE-AGENTS-008 R1)。
;;; gate 形式(単発判定の pattern 文字列・screen-reader trust prompt)は
;;; doeff-free leaf の ready_physics.hy が家 — codex の両形式の一致は
;;; tests/test_ready_physics_single_home.py の parity 検定が執行する。
;;;
;;; 出自: 退役 Rust 実装 agentd-rust-final:src/main.rs の output_has_*
;;; (main.rs:2969-3229)の verbatim 移植 — 退役 Rust は rollback 専用保存で
;;; あり正しさの基準ではない(ADR-DOE-AGENTS-004 R7/U1)。物理の正当性根拠は
;;; conformance README の F-* 表(CONTRACT FIXED 2026-07-05)と verbatim
;;; capture。以下の docstring の「oracle」表記は移植出典関数名の指示のみ。
;;; marker→検出は impl 所有・分類の順序と意味づけは policy 所有
;;; (PaneObservation は事実だけを運ぶ)。例外: has-claude-trust-dialog のみ
;;; 移植元に非在 — 2026-07-07 の R9 カバレッジ欠落(実物 frame で実証)の修正。
;;;
;;; 検出関数は kind 非依存(codex の `› ` と claude の `❯` を同じ関数が
;;; 見る)なので、ここも共有モジュールとして両 impl から使う。kind 分岐が
;;; 生まれたら(C5 opencode)その kind のモジュールへ物理を移す。
;;; 純テキスト関数のみ — IO ゼロ(substrate-clean 領域)。

(require doeff-hy.macros [deff])

(import re)

(import doeff_agents.sessionhost.effects [PaneObservation])
(import doeff_agents.sessionhost.policy [tail-lower])


;; ACP ADR 0049 R9 改訂(2026-08-07。2026-08-06 実 incident、Fable 週次枠):
;; 所有格〜limit 間へ provider 可変語(AI 名・プラン名・期間名)が挟まる
;; exhausted 告知の族。実物告知は「You've reached your Fable 5 limit.
;; /model to switch models.」のように可変語が差し込まれ、逐語列挙は
;; 2026-07-20(usage limit 形)/ 07-26(monthly spend 形)/ 08-06(Fable 5
;; 形・22 件 run_failed 落ち)と 3 度同型で破れた — 語の追加ではなく、
;; 有界可変挿入だけを許す族照合で根治する。有界の設計:
;; - 動詞は exhausted 側(hit|reached)のみ — approaching 側(used NN% of
;;   your … limit)は対象外のまま(まだ動ける pane を blocked_api にしない)
;; - 挿入は空白区切り 0〜4 語。各語は英数開始・英数と +&- の連なり・
;;   内部ピリオド(バージョン番号 4.5 等)のみ許す = 文末ピリオドで族が
;;   切れ、文境界を越えた誤検知(無限定の緩い照合)を構造的に禁じる
;; 対象は tail-lower 済みテキスト(小文字)。apostrophe は ASCII と U+2019
;; の両実物形を許す。
(setv API-LIMIT-EXHAUSTED-FAMILY-RE
  (re.compile
    (+ "you['’]ve (?:hit|reached) your"
       "(?: [a-z0-9][a-z0-9+&-]*(?:\\.[a-z0-9]+)*){0,4} limit\\b")))


;; ---------------------------------------------------------------------------
;; 基本 marker(oracle output_has_* — 窓幅も凍結物理)
;; ---------------------------------------------------------------------------

(deff has-failure-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "hard failure marker(tail 10 行窓、oracle output_has_failure_marker)。"
  (setv text (tail-lower output 10))
  (bool (or (in "fatal error" text)
            (in "unrecoverable error" text)
            (in "agent crashed" text)
            (in "session terminated" text)
            (in "authentication failed" text))))

(deff has-api-limit-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "provider rate-limit / quota marker(tail 30 行窓、oracle
   output_has_api_limit_marker の 9 パターン + issue #557 の追補 +
   ACP ADR 0049 R9 改訂の所有格族照合)。"
  (setv text (tail-lower output 30))
  (bool (or (in "cost limit reached" text)
            (in "rate limit exceeded" text)
            (in "rate limit reached" text)
            (in "quota exceeded" text)
            (in "insufficient quota" text)
            (in "resource exhausted" text)
            (in "/rate-limit-options" text)
            (in "stop and wait for limit to reset" text)
            ;; issue #557 二次補強: 現行 claude TUI の exhausted 側文言
            ;; (2026-07-20 実測、旧 9 パターンのどれにも一致しない)。
            ;; approaching 側(`used NN% of your … limit · resets`)は対象外 —
            ;; まだ動ける pane を blocked_api にしない。
            (in "usage limit reached" text)
            (in "limit reached · resets" text)
            (in "limit reached ∙ resets" text)
            ;; ACP ADR 0049 R9 改訂(2026-08-07): 所有格族(you've
            ;; hit/reached your … limit)は逐語でなく族照合 — 旧逐語 4 語
            ;; (you've hit your limit / you've reached your usage limit /
            ;; you've hit your usage limit / you've hit your monthly spend
            ;; limit)を包含し、provider 可変語の未知形(Fable 5 等)にも
            ;; 有界内で追従する。
            (is-not (.search API-LIMIT-EXHAUSTED-FAMILY-RE text) None)
            ;; credits 枯渇(2026-07-26 実 incident)は所有格族の外の実物
            ;; 文言 — exhausted 側 = blocked_api が正。
            (in "out of usage credits" text))))

(deff has-waiting-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "interactive 待ち marker(raw 一致、oracle output_has_waiting_marker)。"
  (bool (or (in "tell Claude what to do differently" output)
            (in "Type your message" output)
            (in "accept edits" output)
            (in "bypass permissions" output)
            (in "shift+tab to cycle" output)
            (in "Esc to cancel" output)
            (in "to show all projects" output))))

(deff has-idle-prompt [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "REPL idle prompt(codex `› ` / claude 行頭 `❯` 単独 — claude は U+00A0
   区切りなので 2 文字一致 `❯ ` は使わない。oracle
   output_has_agent_idle_prompt)。"
  (bool (or (.startswith output "› ")
            (in "\n› " output)
            (any (gfor line (.splitlines output) (.startswith line "❯"))))))

(deff is-starting-mcp-servers [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "MCP boot 中(oracle output_is_starting_mcp_servers)— boot は work ではない
   (16h-stuck 実障害: boot spinner を active に数えると launch watchdog が死ぬ)。"
  (in "starting mcp servers" (tail-lower output 30)))

(deff has-codex-active-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "codex の active-work marker(oracle output_has_codex_active_marker)。
   `ctrl + t to view transcript` は故意に見ない(collapsed 履歴に残留する)。"
  (if (is-starting-mcp-servers output)
      False
      (do
        (setv text (tail-lower output 30))
        (bool (or (in "working (" text)
                  (in "esc to interrupt" text))))))

(deff is-claude-composer-border [line]
  {:pre [(: line str)] :post [(: % bool)]}
  "claude composer の罫線行(issue #573): 現行 TUI は入力欄を全幅の水平罫線で
   囲む — strip 後が罫線文字だけの行。spinner 探索では空行と同様に skip する
   (skip しないと探索が罫線で止まり、稼働中 pane の active-marker が常に
   false になる — 2026-07-29 実 incident の一次原因)。"
  (setv trimmed (.strip line))
  (bool (and trimmed (all (gfor ch trimmed (in ch "─━═"))))))

(deff has-live-claude-spinner [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の live spinner(oracle output_has_live_claude_spinner_marker +
   issue #573 の罫線跨ぎ): 最終 `❯` 行から上へ、空行と composer 罫線行を
   skip した最初の本文行に `… (`。`❯` が無ければ tail 30 の `… (`。
   本文行は 1 行しか見ない — 履歴に残留した過去 spinner 行は live ではない。"
  (setv lines (.splitlines output))
  (setv prompt-index None)
  (for [[index line] (enumerate lines)]
    (when (.startswith line "❯")
      (setv prompt-index index)))
  (if (is None prompt-index)
      (in "… (" (tail-lower output 30))
      (do
        (setv found False)
        (for [line (reversed (cut lines 0 prompt-index))]
          (setv trimmed (.strip line))
          (when (and trimmed (not (is-claude-composer-border line)))
            (setv found (in "… (" trimmed))
            (break)))
        found)))

(deff has-active-marker [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "kind 横断の active-work marker(oracle output_has_agent_active_marker)。"
  (bool (or (has-codex-active-marker output)
            (has-live-claude-spinner output))))

(deff has-turn-activity [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の turn activity(⏺ / ⎿)。active marker ではない — idle 画面にも
   残留する。latch clear と startup watchdog 解除にのみ使う(oracle
   output_has_claude_turn_activity)。"
  (bool (or (in "⏺" output) (in "⎿" output))))

(deff has-unsubmitted-paste [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "未 submit の paste / 添付残留(oracle output_has_unsubmitted_paste_input の
   sent-text=None 面 — monitor 経路はこの面だけを使う。issue #568 /
   ADR-DOE-AGENTS-010 R1 で composer 領域へ拡張): 末尾 20 行の composer 領域
   (最終 prompt 行 ❯ / › とそれ以降の行)に collapsed paste marker・添付
   チップ([Image #N])・queued ヒントが居る。添付チップは prompt 行の外
   (直下の行・行頭空白)に描かれる — prompt 行 1 行だけの走査は空 prompt +
   チップ形(2026-07-28 実 wedge)に盲目だった。最終 prompt 行より上
   (送信済み履歴)は対象外のまま。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-index None)
  (for [[index line] (enumerate recent)]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-index index)))
  (if (is None last-prompt-index)
      False
      (do
        (setv composer (.join "\n" (cut recent last-prompt-index None)))
        (bool (or (in "[Pasted text" composer)
                  (in "[Pasted Content" composer)
                  (in "[Image #" composer)
                  (in "Press up to edit queued messages" composer))))))

(deff has-queued-messages [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の queued-messages marker(issue #573): mid-turn に配送された
   message は composer に積まれ、入力行が `❯ Press up to edit queued messages`
   になる。未消費 queue = turn 走行中の明白な busy 証拠 — 中断キー送出の
   安全壁が参照する。判定は has-unsubmitted-paste と同じ最終 prompt 行規律
   (履歴に写った同文言を事実にしない)。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-line None)
  (for [line recent]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-line trimmed)))
  (if (is None last-prompt-line)
      False
      (in "Press up to edit queued messages" last-prompt-line)))


;; ---------------------------------------------------------------------------
;; R9 dialog(検出 + 決定的 dismissal キー列 — S18 で verbatim 確定した物理)
;; ---------------------------------------------------------------------------

(deff has-codex-update-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "codex の Update available! dialog(oracle output_has_codex_update_dialog:
   headline でなく menu options で判定 — headline は capture 窓から溢れる)。"
  (setv lower (tail-lower output 10))
  (bool (and (in "update now" lower)
             (in "skip until next version" lower)
             (in "press enter to continue" lower))))

(deff codex-update-selected-option [output]
  {:pre [(: output str)] :post [(: % int)]}
  "update dialog の現在選択(`›` の直後の数字、末尾 10 行、無ければ 1 —
   oracle codex_update_dialog_selected_option + 既定)。"
  (setv selected 1)
  (setv lines (list (reversed (.splitlines output))))
  (for [line (cut lines 0 10)]
    (when (in "›" line)
      (setv after (.strip (get (.split line "›" 1) 1)))
      (when after
        (setv head (get after 0))
        (when (in head "123")
          (setv selected (int head))
          (break)))))
  selected)

(deff codex-update-dismiss-keys [output]
  {:pre [(: output str)] :post [(: % tuple)]}
  "Skip until next version(option 3)への決定的キー列(oracle
   codex_update_dialog_down_steps_to_skip_until_next: (3+3-sel)%3 回の Down
   + Enter)。"
  (setv steps (% (- 6 (codex-update-selected-option output)) 3))
  (tuple (+ (* ["Down"] steps) ["Enter"])))

(deff has-claude-bypass-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の bypass-permissions 確認 dialog(選択 marker が `❯` なので idle
   判定より先に見ること — oracle output_has_claude_bypass_permissions_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "bypass permissions mode" lower)
             (in "no, exit" lower)
             (in "yes, i accept" lower)
             (in "enter to confirm" lower))))

(deff has-claude-fullscreen-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の fullscreen renderer opt-in dialog(oracle
   output_has_claude_fullscreen_renderer_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "try the new fullscreen renderer?" lower)
             (in "yes, try it" lower)
             (in "not now" lower)
             (in "enter to confirm" lower))))

(deff has-claude-trust-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の workspace-trust gate(未 trust の cwd で startup を塞ぐ —
   dismiss しないと wait-for-repl-idle は永久に idle を見ず 120s 上限縮退 →
   prompt が dialog に送出されて死ぬ)。Rust oracle に対応関数は無い:
   2026-07-07 に実物 frame(R9 カバレッジ欠落の実障害)から追加した
   5 つ目の R9 dialog。長文の質問文(Is this a project you created …)は
   pane 幅で reflow するため marker にしない — 折返しは部分文字列一致を
   殺す(幾何学物理)。"
  (setv lower (.lower output))
  (bool (and (in "yes, i trust this folder" lower)
             (in "no, exit" lower)
             (in "enter to confirm" lower))))

(deff has-claude-managed-dialog [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "組織 managed-settings 承認 dialog(mid-turn にも出る — monitor loop でも
   発火する唯一の R9 dialog。oracle
   output_has_claude_managed_settings_approval_dialog)。"
  (setv lower (.lower output))
  (bool (and (in "managed settings require approval" lower)
             (in "settings requiring approval" lower))))

(deff detect-dialog [output]
  {:pre [(: output str)] :post [(: % tuple)]}
  "R9 dialog の検出と決定的 dismissal キー列。検査順は oracle
   wait_for_repl_idle と同じ(trust は oracle 非在 — fullscreen の後に挿入):
   codex-update → bypass → fullscreen → trust → managed。
   dismissal(S18 verbatim): update = selected 依存 Down×n + Enter /
   bypass = Down,Enter(既定 No,exit → Yes,I accept)/
   fullscreen = Down,Enter(既定 Yes,try it → Not now)/
   trust = Enter(既定 Yes,I trust this folder — pre-seed
   hasTrustDialogAccepted と同じ意図で doeff 制御の work_dir を信頼)/
   managed = Enter。戻り値: #(dialog-name keys) — 検出なしは #(None #())。"
  (cond
    (has-codex-update-dialog output)
      #("codex-update" (codex-update-dismiss-keys output))
    (has-claude-bypass-dialog output)
      #("bypass" #("Down" "Enter"))
    (has-claude-fullscreen-dialog output)
      #("fullscreen" #("Down" "Enter"))
    (has-claude-trust-dialog output)
      #("trust" #("Enter"))
    (has-claude-managed-dialog output)
      #("managed" #("Enter"))
    True #(None #())))

(deff startup-finished [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "launch watchdog の解除信号(oracle output_indicates_startup_finished):
   REPL input box / active / turn-activity のどれかが見え、かつ MCP boot・
   update・bypass・fullscreen・trust dialog 中でない(それらは watchdog が
   刈り続けるべき stuck-in-startup 状態そのもの)。"
  (if (or (is-starting-mcp-servers output)
          (has-codex-update-dialog output)
          (has-claude-bypass-dialog output)
          (has-claude-fullscreen-dialog output)
          (has-claude-trust-dialog output))
      False
      (bool (or (has-codex-active-marker output)
                (has-idle-prompt output)
                (has-turn-activity output)))))


;; ---------------------------------------------------------------------------
;; PaneObservation の組み立て(事実の束 — 分類は policy 所有)
;; ---------------------------------------------------------------------------

(deff classify-output [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  "pane capture → PaneObservation(kind 横断の oracle 検出関数の束)。"
  (setv [dialog dismiss-keys] (detect-dialog output))
  (PaneObservation
    :has-failure-marker (has-failure-marker output)
    :has-api-limit-marker (has-api-limit-marker output)
    :has-waiting-marker (has-waiting-marker output)
    :has-idle-prompt (has-idle-prompt output)
    :has-active-marker (has-active-marker output)
    :has-turn-activity (has-turn-activity output)
    :startup-finished (startup-finished output)
    :has-unsubmitted-paste (has-unsubmitted-paste output)
    :has-queued-messages (has-queued-messages output)
    :dialog dialog
    :dialog-dismiss-keys dismiss-keys))
