;;; F-* marker 物理(ADR-DOE-AGENTS-004 C2) — pane capture のテキスト事実。
;;;
;;; oracle: packages/doeff-agentd/src/main.rs の output_has_*(main.rs:2969-3229)
;;; を verbatim 移植。marker→検出は impl 所有・分類の順序と意味づけは policy 所有
;;; (PaneObservation は事実だけを運ぶ)。凍結出典は conformance README の
;;; F-* 表(CONTRACT FIXED 2026-07-05)。
;;;
;;; oracle の検出関数は kind 非依存(codex の `› ` と claude の `❯` を同じ関数が
;;; 見る)なので、ここも共有モジュールとして両 impl から使う。kind 分岐が
;;; 生まれたら(C5 opencode)その kind のモジュールへ物理を移す。
;;; 純テキスト関数のみ — IO ゼロ(substrate-clean 領域)。

(require doeff-hy.macros [deff])

(import doeff_agents.sessionhost.effects [PaneObservation])
(import doeff_agents.sessionhost.policy [tail-lower])


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
   output_has_api_limit_marker)。"
  (setv text (tail-lower output 30))
  (bool (or (in "cost limit reached" text)
            (in "rate limit exceeded" text)
            (in "rate limit reached" text)
            (in "quota exceeded" text)
            (in "insufficient quota" text)
            (in "resource exhausted" text)
            (in "you've hit your limit" text)
            (in "/rate-limit-options" text)
            (in "stop and wait for limit to reset" text))))

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

(deff has-live-claude-spinner [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "claude の live spinner(oracle output_has_live_claude_spinner_marker):
   最終 `❯` 行の直上の非空行に `… (`。`❯` が無ければ tail 30 の `… (`。"
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
          (when trimmed
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
  "未 submit の paste 残留(oracle output_has_unsubmitted_paste_input の
   sent-text=None 面 — monitor 経路はこの面だけを使う): 末尾 20 行の最終
   prompt 行(❯ / ›)に collapsed paste marker が居る。"
  (setv lines (.splitlines output))
  (setv recent (cut lines (max 0 (- (len lines) 20)) None))
  (setv last-prompt-line None)
  (for [line recent]
    (setv trimmed (.lstrip line))
    (when (or (.startswith trimmed "❯") (.startswith trimmed "›"))
      (setv last-prompt-line trimmed)))
  (if (is None last-prompt-line)
      False
      (bool (or (in "[Pasted text" last-prompt-line)
                (in "[Pasted Content" last-prompt-line)
                (in "Press up to edit queued messages" last-prompt-line)))))


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
   wait_for_repl_idle と同じ: codex-update → bypass → fullscreen → managed。
   dismissal(S18 verbatim): update = selected 依存 Down×n + Enter /
   bypass = Down,Enter(既定 No,exit → Yes,I accept)/
   fullscreen = Down,Enter(既定 Yes,try it → Not now)/ managed = Enter。
   戻り値: #(dialog-name keys) — 検出なしは #(None #())。"
  (cond
    (has-codex-update-dialog output)
      #("codex-update" (codex-update-dismiss-keys output))
    (has-claude-bypass-dialog output)
      #("bypass" #("Down" "Enter"))
    (has-claude-fullscreen-dialog output)
      #("fullscreen" #("Down" "Enter"))
    (has-claude-managed-dialog output)
      #("managed" #("Enter"))
    True #(None #())))

(deff startup-finished [output]
  {:pre [(: output str)] :post [(: % bool)]}
  "launch watchdog の解除信号(oracle output_indicates_startup_finished):
   REPL input box / active / turn-activity のどれかが見え、かつ MCP boot・
   update・bypass・fullscreen dialog 中でない(それらは watchdog が刈り続ける
   べき stuck-in-startup 状態そのもの)。"
  (if (or (is-starting-mcp-servers output)
          (has-codex-update-dialog output)
          (has-claude-bypass-dialog output)
          (has-claude-fullscreen-dialog output))
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
    :dialog dialog
    :dialog-dismiss-keys dismiss-keys))
